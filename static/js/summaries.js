// ═══════════════════════════════════════════════════════════════════════════
// AUTO SUMMARIES
// ═══════════════════════════════════════════════════════════════════════════
// Per-chat running summary of aged-out history. Enablement happens in the memory
// flyout; the actual summarization runs as a background job on the server (see
// routes/summaries.py), which turns each batch of messages into exactly one entry.
// Completed turns start updates in the background; the send preflight waits only
// when history would otherwise fall into the gap between raw context and the
// persisted summary.

import { state, el } from './state.js';
import { API } from './api.js';
import { estimateMessageTokens, estimateTextTokens } from './tokenizer.js';
import { getContextTokenBudget, getRawHistoryMessages } from './context-budget.js';
import { analyzeContext } from './context-analysis.js';
import { flushLLMSettingsSave } from './llm-settings.js';
import { showToast, markUnusedVar } from './utils.js';
import { confirmDialog } from './confirm.js';

const STORY_HEADING = 'STORY SO FAR';
const BONDS_HEADING = 'BONDS';
const POLL_MS = 2500;
const MAX_SEND_POLL_FAILURES = 3;
let pollEpoch = 0;
let summaryBudgetChangeHandler = null;

/** Register the context-meter refresh hook without importing its cyclic module. */
export function setSummaryBudgetChangeHandler(handler) {
    summaryBudgetChangeHandler = typeof handler === 'function' ? handler : null;
}

function summariesActive(chat = state.activeChat) {
    return !!chat?.summary_enabled;
}

// ── Rendering the summary object to text (for injection into the chat prompt) ──
// Deliberately NOT a byte-for-byte mirror of summarizer.summary_to_text: this render is
// read by the roleplay model, so the story heading spells out that the beats are a
// timeline rather than an unordered pile of facts. The Python renderer stays bare
// because it feeds the summary back to the summarizer and is matched by _norm_heading.
const STORY_ORDER_NOTE = ' (in order, oldest first)';

export function summaryToText(obj) {
    const lines = (obj && Array.isArray(obj.lines)) ? obj.lines : [];
    const story = lines.filter(l => (l.section || 'story') !== 'bonds');
    const bonds = lines.filter(l => (l.section || 'story') === 'bonds');
    const out = [];
    if (story.length) {
        out.push(STORY_HEADING + STORY_ORDER_NOTE);
        story.forEach(l => out.push(`- ${l.text}`));
    }
    if (bonds.length) {
        if (out.length) out.push('');
        out.push(BONDS_HEADING);
        bonds.forEach(l => out.push(`- ${l.text}`));
    }
    return out.join('\n');
}

// ── Config helpers ──────────────────────────────────────────────────────────
function summarizerConfigured() {
    // The main endpoint lives in the settings input rather than state. Each
    // blank dedicated field falls back independently on the server.
    const ep = state.summaryApiEndpoint || el.apiEndpoint?.value;
    const model = state.summaryApiModel || state.apiModel;
    return !!(ep && model);
}

function capTokens() {
    const ctx = getContextTokenBudget();
    const pct = parseFloat(state.summaryCapPct || '10') || 10;
    return ctx > 0 ? Math.floor(ctx * pct / 100) : 0;
}

function batchSize() {
    const parsed = parseInt(state.summaryTriggerInterval || '10', 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 10;
}

// ── Aged-out message detection ──────────────────────────────────────────────
function windowAssessment(excludeLastN = 0, {
    includeSummarized = false,
    summaryTextOverride = null,
} = {}) {
    if (!state.activeChat || getContextTokenBudget() <= 0) {
        return { candidates: [], agedOut: [], analysis: null };
    }
    const candidates = getRawHistoryMessages(state.messages, {
        excludeLastN,
        includeSummarized,
    });
    const summaryText = summaryTextOverride == null
        ? (summariesActive() ? summaryToText(state.activeChat.summary) : '')
        : summaryTextOverride;
    const analysis = analyzeContext({
        excludeLastN,
        includeSummarized,
        summaryText,
    });
    const agedCount = candidates.length - analysis.selectedMessages.length;
    return {
        candidates,
        agedOut: agedCount > 0 ? candidates.slice(0, agedCount) : [],
        analysis,
    };
}

function agedOutMessages(excludeLastN = 0, options = {}) {
    return windowAssessment(excludeLastN, options).agedOut;
}

// When anything ages out of a contiguous window, the unused space left behind
// must be smaller than what the next message (the first one that failed to
// fit) would have cost — otherwise the selection would have grown to include
// it. A measurement that leaves far more room than that contradicts itself,
// and acting on it would summarize messages that still fit (observed once in
// production as a chat-wide history wipe from a transient mis-measure). The
// slack covers alternation/template overhead and estimate drift.
const UNTRUSTED_WINDOW_TOAST = 'Memory update skipped: the context measurement '
    + 'contradicts itself (details in the browser console). Chat history was not touched.';

// Naming the setting matters: the cause is a value the user typed, not a measurement
// fault, and the generic toast above would send them looking for the wrong thing.
const UNSATISFIABLE_BUDGET_TOAST = 'Memory update skipped: Max Response Tokens is at or '
    + 'above Max Context Tokens, so no chat history can fit. Lower it in Settings → API → '
    + 'Context & Generation. Chat history was not touched.';

/**
 * True when the reply reserve alone consumes the whole context window.
 *
 * Nothing about the *measurement* is wrong here — the arithmetic is simply
 * unsatisfiable, because `maxTokens - responseTokens` is zero or negative before a
 * single message is considered. No amount of trimming can make such a request fit, so
 * every message reads as aged out and the whole transcript would be retired into the
 * summary to no benefit. Separate from the self-contradiction test below, which looks
 * for *leftover* space; this failure mode pins `unusedTokens` to exactly 0 and is
 * invisible to it.
 */
function budgetUnsatisfiable(analysis) {
    return !!analysis && analysis.maxTokens > 0
        && analysis.responseTokens >= analysis.maxTokens;
}

export function untrustedContextAssessment({ candidates, agedOut, analysis }, label) {
    if (!analysis || agedOut.length === 0 || analysis.maxTokens <= 0) return false;
    if (budgetUnsatisfiable(analysis)) {
        console.warn(
            `Cozy: ${label} refused — Max Response Tokens (${analysis.responseTokens}) is at or `
            + `above Max Context Tokens (${analysis.maxTokens}), so the reply reserve alone fills `
            + `the window and all ${candidates.length} messages measure as aged out. Retiring `
            + 'history cannot make a request of this shape fit.',
            { maxTokens: analysis.maxTokens, responseTokens: analysis.responseTokens },
        );
        return true;
    }
    const nextMessage = agedOut[agedOut.length - 1];
    if (!nextMessage) return false;
    const nextCost = estimateMessageTokens(
        { content: nextMessage.text },
        { stripThinking: true },
    );
    const slack = Math.max(256, Math.floor(analysis.maxTokens * 0.05));
    if (analysis.unusedTokens <= 2 * nextCost + slack) return false;
    console.warn(
        `Cozy: ${label} refused — ${agedOut.length} of ${candidates.length} messages measured `
        + `as outside the context window while ${analysis.unusedTokens} of ${analysis.maxTokens} `
        + `tokens sit unused (the next message would only cost ≈${nextCost}). Retiring history `
        + 'on this measurement would summarize messages that still fit.',
        {
            maxTokens: analysis.maxTokens,
            responseReserve: analysis.responseTokens,
            promptTokens: analysis.promptTokens,
            unusedTokens: analysis.unusedTokens,
            nextMessageCost: nextCost,
            selectedMessages: analysis.selectedMessages.length,
            firstSelectedId: analysis.firstSelectedMessageId,
            segments: analysis.segments,
        },
    );
    return true;
}

/** The toast to show for a measurement that must not be acted on, or '' to proceed. */
function windowMeasurementUntrusted(excludeLastN, label, options = {}) {
    const assessment = windowAssessment(excludeLastN, options);
    if (!untrustedContextAssessment(assessment, label)) return '';
    return budgetUnsatisfiable(assessment.analysis)
        ? UNSATISFIABLE_BUDGET_TOAST
        : UNTRUSTED_WINDOW_TOAST;
}

function agedOutUnsummarized(excludeLastN = 0) {
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    return agedOutMessages(excludeLastN).filter(m => (m.id || 0) > wm);
}

/**
 * Pick the inclusive watermark for an automatic update. Once any unsummarized
 * history falls outside the raw token budget, retire one flat batch of
 * ``batchSize()`` oldest messages — the batch is a message count, so it never
 * depends on how large those messages happen to be.
 *
 * An earlier version sized the batch by predicting the token cost of future
 * messages and capping the result at half the currently fitting history. That
 * made the effective batch shrink as the window filled, so updates fired far
 * more often than configured and grew the summary faster than intended.
 *
 * ``oldestRetirableId`` clamps an oversized batch to the messages that actually
 * exist and always leaves the newest one raw. A backlog larger than one batch
 * drains over successive calls via ``ensureSummaryReadyForSend``.
 */
function automaticRunTarget(excludeLastN = 0) {
    const assessment = windowAssessment(excludeLastN);
    const { candidates, agedOut } = assessment;
    if (agedOut.length === 0 || candidates.length <= 1) return null;
    if (untrustedContextAssessment(assessment, 'automatic memory update')) return null;
    return oldestRetirableId(candidates, batchSize());
}

/**
 * Id of the targetCount-th oldest persisted candidate. The server retires an
 * id range (everything at or below the target), so pick by id order rather
 * than array position — a mis-ordered or partially-saved state.messages must
 * never widen the range — and always leave the newest persisted message raw.
 */
function oldestRetirableId(candidates, targetCount) {
    const ids = candidates
        .map(message => message?.id)
        .filter(id => Number.isInteger(id) && id > 0)
        .sort((a, b) => a - b);
    const count = Math.min(targetCount, ids.length - 1);
    return count > 0 ? ids[count - 1] : null;
}

/**
 * Retire only the messages that are currently outside the final prompt. This is
 * used while stabilizing an explicit rebuild: normal background updates also
 * prepay token headroom for future messages, but doing that here would make a
 * completed rebuild unexpectedly discard raw context that still fits.
 */
function exactRunTarget(excludeLastN = 0) {
    const assessment = windowAssessment(excludeLastN);
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    const pending = assessment.agedOut.filter(m => (m.id || 0) > wm);
    if (!pending.length) return null;
    if (untrustedContextAssessment(assessment, 'rebuild stabilization')) return null;
    const ids = pending
        .map(message => message?.id)
        .filter(id => Number.isInteger(id) && id > 0)
        .sort((a, b) => a - b);
    return ids.length ? ids[ids.length - 1] : null;
}

function summarizedCount() {
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    if (!wm) return 0;
    return state.messages.filter(m => (m.id || 0) <= wm).length;
}

// ── State merge + polling ───────────────────────────────────────────────────
const SUMMARY_KEYS = ['summary_enabled', 'summary', 'summary_up_to_msg_id',
                      'summary_status', 'summary_status_detail'];

function validSummaryState(st) {
    return !!st && typeof st === 'object'
        && ['running', 'idle', 'error'].includes(st.summary_status);
}

export function applySummaryState(st, expectedChatId = st?.id ?? state.activeChat?.id) {
    if (!st || expectedChatId == null) return;
    if (st.id != null && st.id !== expectedChatId) return;

    // Older server versions could leave progress text behind after a restart
    // reset a dead worker to idle. It is not a warning and should not be shown
    // as one in the memory card.
    if (st.summary_status === 'idle'
        && /^(Starting|Summarizing)/i.test(st.summary_status_detail || '')) {
        st = { ...st, summary_status_detail: '' };
    }

    const chat = state.activeChat?.id === expectedChatId ? state.activeChat : null;
    const previousSummaryText = chat ? summaryToText(chat.summary) : '';
    const wasActive = chat ? summariesActive(chat) : false;
    for (const k of SUMMARY_KEYS) {
        if (chat && k in st) chat[k] = st[k];
    }
    const inList = state.chats.find(c => c.id === expectedChatId);
    if (inList) for (const k of SUMMARY_KEYS) if (k in st) inList[k] = st[k];
    if (chat) {
        renderMemorySummaryCard();
        const budgetChanged = previousSummaryText !== summaryToText(chat.summary)
            || wasActive !== summariesActive(chat);
        if (budgetChanged) summaryBudgetChangeHandler?.();
    }
}

function scheduleStatusPoll(epoch, chatId) {
    if (epoch !== pollEpoch || state.activeChat?.id !== chatId) return;
    state._summaryPollTimer = setTimeout(() => {
        state._summaryPollTimer = null;
        void pollSummaryStatus(epoch, chatId);
    }, POLL_MS);
}

async function pollSummaryStatus(epoch, chatId) {
    if (epoch !== pollEpoch || state.activeChat?.id !== chatId) return;
    try {
        const st = await API.getSummaryStatus(chatId);
        if (!validSummaryState(st)) throw new Error('Invalid summary status response');
        if (epoch !== pollEpoch || state.activeChat?.id !== chatId) return;
        applySummaryState(st, chatId);
        if (st.summary_status === 'running') scheduleStatusPoll(epoch, chatId);
    } catch {
        // A single failed request should not strand a genuinely running job.
        // Keep retrying while this chat remains selected; switching chats or a
        // terminal success invalidates this poll generation.
        scheduleStatusPoll(epoch, chatId);
    }
}

export function startStatusPolling(chatId = state.activeChat?.id) {
    stopStatusPolling();
    if (chatId == null || state.activeChat?.id !== chatId) return;
    const epoch = ++pollEpoch;
    // Poll immediately so reopening a chat reconciles stale local status
    // without waiting for the first interval.
    void pollSummaryStatus(epoch, chatId);
}

export function stopStatusPolling() {
    pollEpoch += 1;
    if (state._summaryPollTimer) {
        clearTimeout(state._summaryPollTimer);
        state._summaryPollTimer = null;
    }
}

// ── Kicking off runs + send readiness ───────────────────────────────────────

// Chats whose current run the user cancelled. Stopping the worker is not enough on its
// own: the send preflight and the rebuild tail both loop until the watermark advances,
// so without this they would answer a cancel by starting the very next batch. A marker
// is consumed by whichever loop sees it first, and starting a deliberate run clears any
// stale one so a later send is never pre-cancelled.
const cancelledRuns = new Set();

function consumeCancellation(chatId) {
    return cancelledRuns.delete(chatId);
}

function abortError(message = 'Summary wait cancelled') {
    const err = new Error(message);
    err.name = 'AbortError';
    return err;
}

function assertSendStillActive(chatId, signal) {
    if (signal?.aborted || state.activeChat?.id !== chatId) throw abortError();
}

function waitForNextPoll(signal) {
    if (signal?.aborted) return Promise.reject(abortError());
    return new Promise((resolve, reject) => {
        const timer = setTimeout(done, POLL_MS);
        function done() {
            signal?.removeEventListener?.('abort', cancelled);
            resolve();
        }
        function cancelled() {
            clearTimeout(timer);
            reject(abortError());
        }
        signal?.addEventListener?.('abort', cancelled, { once: true });
    });
}

async function waitForSummaryCompletion(chatId, initialState, signal) {
    let st = initialState;
    let failures = 0;
    while (st?.summary_status === 'running') {
        assertSendStillActive(chatId, signal);
        if (!summariesActive(state.activeChat)) return st;
        await waitForNextPoll(signal);
        assertSendStillActive(chatId, signal);
        if (!summariesActive(state.activeChat)) return st;
        try {
            st = await API.getSummaryStatus(chatId);
            if (!validSummaryState(st)) {
                throw new Error('Summarizer returned an invalid status response.');
            }
            failures = 0;
        } catch (e) {
            failures += 1;
            if (failures >= MAX_SEND_POLL_FAILURES) {
                throw new Error(`Could not confirm that chat memory is ready: ${e.message}`);
            }
            continue;
        }
        assertSendStillActive(chatId, signal);
        applySummaryState(st, chatId);
    }
    if (st?.summary_status === 'error') {
        throw new Error(st.summary_status_detail || 'Summary update failed.');
    }
    return st;
}

async function triggerRun({ rebuild = false, awaitCompletion = false,
                            chatId = state.activeChat?.id, signal,
                            excludeLastN = 0, exactTarget = false } = {}) {
    if (chatId == null || state.activeChat?.id !== chatId) return null;
    // A deliberate new run supersedes any earlier cancel.
    cancelledRuns.delete(chatId);

    try {
        // Settings fields update local state immediately but persist through a
        // debounce. The worker reads server settings, so its POST must stay
        // behind a strict persistence barrier.
        await flushLLMSettingsSave({ strict: true });
    } catch (e) {
        if (awaitCompletion) throw e;
        showToast('Summary run failed: ' + e.message);
        return null;
    }
    if (awaitCompletion) assertSendStillActive(chatId, signal);
    if (state.activeChat?.id !== chatId || !summariesActive(state.activeChat)) return null;

    // A post-turn background update commonly overlaps the next send. Join that
    // job instead of issuing a redundant run request; the API's 409 handling
    // below still covers the race where a worker starts after this check.
    if (awaitCompletion && state.activeChat.summary_status === 'running') {
        let runningState;
        try {
            runningState = await API.getSummaryStatus(chatId);
            if (!validSummaryState(runningState)) {
                throw new Error('Summarizer returned an invalid status response.');
            }
            assertSendStillActive(chatId, signal);
            applySummaryState(runningState, chatId);
        } catch (e) {
            if (e.name === 'AbortError') throw e;
            // Let the resilient waiter retry transient status failures.
            runningState = { summary_status: 'running' };
        }
        return waitForSummaryCompletion(chatId, runningState, signal);
    }

    if (rebuild && agedOutMessages(excludeLastN, {
        includeSummarized: true,
        summaryTextOverride: '',
    }).length === 0) {
        // Removing stale memory may be enough for all history to fit verbatim.
        // In that case rebuilding would unnecessarily perpetuate the summary.
        await clearSummary(chatId);
        return null;
    }

    const agedOut = agedOutMessages(excludeLastN, { includeSummarized: rebuild });
    const agedOutIds = agedOut.map(m => m.id).filter(id => Number.isInteger(id) && id > 0);
    const upTo = rebuild
        ? (agedOutIds.length ? agedOutIds[agedOutIds.length - 1] : null)
        : (exactTarget ? exactRunTarget(excludeLastN) : automaticRunTarget(excludeLastN));
    if (upTo == null) {
        // Nothing is outside the context window. On an explicit rebuild that means
        // a grown context now fits everything — clear any lingering stale summary.
        if (rebuild) await clearSummary(chatId);
        else renderMemorySummaryCard();
        return null;
    }
    try {
        const st = await API.runSummary(chatId, { up_to_msg_id: upTo, rebuild });
        if (!validSummaryState(st)) {
            throw new Error('Summarizer returned an invalid status response.');
        }
        if (awaitCompletion) assertSendStillActive(chatId, signal);
        applySummaryState(st, chatId);
        if (st.summary_status === 'running') {
            if (awaitCompletion) return waitForSummaryCompletion(chatId, st, signal);
            if (state.activeChat?.id === chatId) startStatusPolling(chatId);
        } else if (awaitCompletion && st.summary_status === 'error') {
            throw new Error(st.summary_status_detail || 'Summary update failed.');
        }
        return st;
    } catch (e) {
        if (awaitCompletion) throw e;
        showToast('Summary run failed: ' + e.message);
        return null;
    }
}

/**
 * Close any gap between the stored watermark and the raw context boundary. Before
 * generation this prevents forgotten history; enablement reuses the same loop to backfill
 * an existing chat. The loop matters because a newly enlarged summary can itself move the
 * boundary and age out one more message.
 */
export async function ensureSummaryReadyForSend(signal, {
    excludeLastN = 0,
    backfill = false,
} = {}) {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    if (agedOutUnsummarized(excludeLastN).length === 0) return;
    // A refused (self-contradictory) measurement must not stall the send:
    // proceed without updating memory rather than retiring history wrongly.
    const refusal = windowMeasurementUntrusted(excludeLastN, 'pre-send memory update');
    if (refusal) {
        showToast(refusal);
        return;
    }
    if (!summarizerConfigured()) {
        throw new Error('Configure an Auto Summaries endpoint and model before sending so aged-out history is not forgotten.');
    }

    const chatId = chat.id;
    let previousWatermark = chat.summary_up_to_msg_id || 0;
    let stalledRuns = 0;
    for (;;) {
        assertSendStillActive(chatId, signal);
        if (!summariesActive(state.activeChat)) return;
        if (agedOutUnsummarized(excludeLastN).length === 0) return;

        // Submit the whole backlog currently outside the window as one server job. The
        // worker still calls the provider in configured-size chunks, but status can now
        // report meaningful cumulative progress (batch 1/12, 2/12, …) instead of a
        // client-side procession of unrelated batch 1/1 jobs.
        await triggerRun({
            awaitCompletion: true,
            chatId,
            signal,
            excludeLastN,
            exactTarget: true,
        });
        assertSendStillActive(chatId, signal);
        if (!summariesActive(state.activeChat)) return;
        // Cancelling the run this send was waiting on abandons the send too. Continuing
        // would either start the next batch (ignoring the cancel) or generate against a
        // known gap in memory, and this guard exists precisely to prevent the second.
        if (consumeCancellation(chatId)) {
            if (backfill) return;
            showToast('Memory update cancelled — send again when you are ready.');
            throw abortError('Summary cancelled during send');
        }

        const watermark = state.activeChat.summary_up_to_msg_id || 0;
        if (agedOutUnsummarized(excludeLastN).length === 0) return;
        if (watermark <= previousWatermark) {
            stalledRuns += 1;
            if (stalledRuns >= 2) {
                throw new Error(backfill
                    ? 'Chat memory did not advance; automatic backfill stopped.'
                    : 'Chat memory did not advance; response generation was paused to avoid forgetting history.');
            }
        } else {
            stalledRuns = 0;
            previousWatermark = watermark;
        }
    }
}

/** After a completed turn: start folding in any newly aged-out history. */
export function maybeTriggerSummary() {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    if (chat.summary_status === 'running') return;
    if (!summarizerConfigured()) return;
    if (agedOutUnsummarized().length === 0) return;
    const refusal = windowMeasurementUntrusted(0, 'automatic memory update');
    if (refusal) {
        showToast(refusal);
        return;
    }
    return triggerRun({ chatId: chat.id });
}

async function enableSummariesForChat() {
    const chat = state.activeChat;
    if (!chat) return;
    try {
        const updated = await API.updateChat(chat.id, { summary_enabled: true });
        applySummaryState(updated, chat.id);
    } catch (e) {
        showToast('Could not enable summaries: ' + e.message);
        if (state.activeChat?.id === chat.id) renderMemorySummaryCard();
        return;
    }
    // The update belongs to the chat where the toggle was clicked. Never use
    // the newly selected chat's messages if the user switched during the PUT.
    if (state.activeChat?.id !== chat.id) return;
    if (!summarizerConfigured()) {
        renderMemorySummaryCard();  // arms the feature; hint tells the user to configure
        return;
    }
    // Drain the existing backlog now, one checkpointed interval at a time. Keeping each
    // provider request bounded preserves the configured batch contract while avoiding a
    // half-filled summary that needs a send or a manual resume to finish.
    try {
        return await ensureSummaryReadyForSend(undefined, { backfill: true });
    } catch (e) {
        if (e.name !== 'AbortError') showToast('Summary backfill failed: ' + e.message);
        return null;
    }
}

async function disableSummariesForChat() {
    const chat = state.activeChat;
    if (!chat) return;
    stopStatusPolling();
    const wasEnabled = !!chat.summary_enabled;
    chat.summary_enabled = false;
    const inList = state.chats.find(c => c.id === chat.id);
    if (inList) inList.summary_enabled = false;
    renderMemorySummaryCard();
    try {
        const updated = await API.updateChat(chat.id, { summary_enabled: false });
        applySummaryState(updated, chat.id);
    } catch (e) {
        chat.summary_enabled = wasEnabled;
        if (inList) inList.summary_enabled = wasEnabled;
        showToast('Could not disable summaries: ' + e.message);
        if (state.activeChat?.id === chat.id) renderMemorySummaryCard();
    }
}

/**
 * True when the button should carry on from the watermark rather than start over.
 *
 * A summary that already holds entries while history sits unsummarized is a run that
 * stopped early — cancelled, failed, or interrupted by a closed browser. Continuing is
 * what the user means by pressing the button again, and it keeps the batches already
 * paid for. Starting from scratch is still available: Reset first, then rebuild.
 */
function shouldResumeInsteadOfRebuild(chat = state.activeChat) {
    return (chat?.summary?.lines?.length || 0) > 0 && agedOutUnsummarized().length > 0;
}

async function rebuildSummary() {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    const chatId = chat.id;
    // Pressing the button is a deliberate run, so it supersedes an earlier cancel the
    // same way triggerRun does. Cancelling a background update leaves a marker no loop
    // was waiting to consume, and the resume path below reaches consumeCancellation
    // without calling triggerRun first — without this the first press is swallowed.
    cancelledRuns.delete(chatId);
    let resuming = false;
    try {
        // A run already in flight would swallow this click through triggerRun's
        // join branch and never issue the actual rebuild. Wait it out first; a
        // failed background run must not block its own replacement.
        if (state.activeChat?.summary_status === 'running') {
            try {
                await waitForSummaryCompletion(chatId, { summary_status: 'running' });
            } catch (e) {
                if (e.name === 'AbortError') throw e;
            }
            if (state.activeChat?.id !== chatId || !summariesActive(state.activeChat)) return;
        }
        // Resuming skips the from-scratch pass entirely and falls through to the drain
        // loop below, which is already the "fold in everything past the watermark" path.
        resuming = shouldResumeInsteadOfRebuild();
        if (!resuming) {
            // A rebuild recomputes the boundary over the full transcript; refuse it
            // outright on a self-contradictory measurement instead of rewriting the
            // watermark from bad numbers.
            const refusal = windowMeasurementUntrusted(
                0, 'summary rebuild', { includeSummarized: true },
            );
            if (refusal) {
                showToast(refusal);
                return;
            }
            await triggerRun({ rebuild: true, awaitCompletion: true, chatId });
        }

        // The replacement summary's final size is unknowable before the rebuild
        // finishes. Injecting it can move the context boundary and age out a few
        // more messages. Fold those in now, to a fixed point, so reopening the
        // chat cannot discover and launch a surprise follow-up batch. On a resume
        // this same loop is the whole job.
        let previousWatermark = state.activeChat?.summary_up_to_msg_id || 0;
        let stalledRuns = 0;
        if (consumeCancellation(chatId)) return;
        while (state.activeChat?.id === chatId && summariesActive(state.activeChat)
            && agedOutUnsummarized().length > 0
            && !windowMeasurementUntrusted(0, 'rebuild stabilization')) {
            await triggerRun({
                awaitCompletion: true,
                chatId,
                exactTarget: true,
            });
            if (state.activeChat?.id !== chatId || !summariesActive(state.activeChat)) return;
            // Stop chasing the boundary once the user has said stop.
            if (consumeCancellation(chatId)) return;

            const watermark = state.activeChat.summary_up_to_msg_id || 0;
            if (watermark <= previousWatermark) {
                stalledRuns += 1;
                if (stalledRuns >= 2) {
                    throw new Error('Chat memory could not stabilize the context boundary.');
                }
            } else {
                stalledRuns = 0;
                previousWatermark = watermark;
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') {
            showToast(`${resuming ? 'Continuing the summary' : 'Summary rebuild'} failed: ${e.message}`);
        }
    }
}

/**
 * Cancel button: stop the run at its next batch boundary.
 *
 * Every batch already folded in stays — the worker checkpoints after each one — so this
 * keeps the entries written so far and leaves the watermark on the last completed batch.
 * Only the batch in flight is discarded, and a later run resumes from there.
 */
async function cancelSummary() {
    const chat = state.activeChat;
    if (!chat || chat.summary_status !== 'running') return;
    const chatId = chat.id;
    // Mark before the request so a loop waking mid-flight already sees the cancel.
    cancelledRuns.add(chatId);
    try {
        const st = await API.cancelSummary(chatId);
        if (!validSummaryState(st)) {
            throw new Error('Summarizer returned an invalid status response.');
        }
        applySummaryState(st, chatId);
        if (state.activeChat?.id === chatId && st.summary_status !== 'running') {
            stopStatusPolling();
        }
    } catch (e) {
        cancelledRuns.delete(chatId);
        showToast('Could not cancel the summary run: ' + e.message);
    }
}

/** Wipe the summary + watermark to empty (no LLM call). */
async function clearSummary(chatId = state.activeChat?.id) {
    if (chatId == null) return;
    if (state.activeChat?.id === chatId) stopStatusPolling();
    try {
        const st = await API.resetSummary(chatId);
        applySummaryState(st, chatId);
    } catch (e) {
        showToast('Could not reset summary: ' + e.message);
    }
}

/** Reset button: confirm, then clear to a blank slate. */
async function resetSummary() {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    const hasContent = (chat.summary?.lines?.length || 0) > 0;
    if (hasContent) {
        const ok = await confirmDialog({
            title: 'Reset summary?',
            message: 'This clears the generated summary for this chat. '
                + 'You can rebuild it from history afterward.',
            confirmLabel: 'Reset',
        });
        if (!ok) return;
    }
    await clearSummary(chat.id);
}

// ── Rendering the memory card ───────────────────────────────────────────────
function renderStatus() {
    const box = el.summaryStatus;
    if (!box) return;
    const chat = state.activeChat;
    box.className = 'summary-status';
    box.textContent = '';
    if (!chat || !chat.summary_enabled) return;

    const status = chat.summary_status || 'idle';
    if (status === 'running') {
        box.className = 'summary-status is-running';
        const spin = document.createElement('span');
        spin.className = 'summary-spinner';
        box.appendChild(spin);
        box.appendChild(document.createTextNode(' ' + (chat.summary_status_detail || 'Summarizing…')));
        return;
    }
    if (status === 'error') {
        box.className = 'summary-status is-error';
        box.textContent = chat.summary_status_detail || 'Summary failed.';
        return;
    }
    // idle
    const lineCount = chat.summary?.lines?.length || 0;
    if (lineCount === 0) {
        box.textContent = 'No aged-out history yet — the summary fills in as the chat grows.';
    } else {
        const count = summarizedCount();
        const toks = estimateTextTokens(summaryToText(chat.summary));
        const cap = capTokens();
        const size = cap > 0
            ? `≈ ${toks.toLocaleString()} / ${cap.toLocaleString()} tokens`
            : `≈ ${toks.toLocaleString()} tokens · no cap`;
        // A cancelled backfill leaves real history unsummarized, so "Up to date" would be
        // a lie. Report the backlog instead — the next send or turn will drain it.
        const pending = agedOutUnsummarized().length;
        const state_ = pending > 0
            ? `${pending} message${pending === 1 ? '' : 's'} still to summarize`
            : 'Up to date';
        box.textContent = `${state_} · ${count} message${count === 1 ? '' : 's'} summarized · ${size}`;
    }
    if (chat.summary_status_detail) {
        // A non-empty detail on idle is a cap warning.
        box.className = 'summary-status is-warning';
        box.appendChild(document.createElement('br'));
        box.appendChild(document.createTextNode(chat.summary_status_detail));
    }
}

/**
 * Label for the messages one story entry covers, or '' when it cannot be resolved.
 *
 * A stored range is a pair of message *ids*, which are global and mean nothing to a
 * reader, so they are shown as positions within this chat. An entry whose messages have
 * since been deleted — and every entry written before ranges existed — resolves to
 * nothing and simply gets no label rather than a misleading one.
 */
export function rangeLabel(line, messages = state.messages) {
    const start = line?.start_msg_id;
    const end = line?.end_msg_id;
    if (!Number.isFinite(start) || !Number.isFinite(end)) return '';
    const list = Array.isArray(messages) ? messages : [];
    const first = list.findIndex(m => m?.id === start);
    const last = list.findIndex(m => m?.id === end);
    if (first < 0 || last < 0) return '';
    return first === last
        ? `message ${first + 1}`
        : `messages ${first + 1}–${last + 1}`;
}

function renderLines(enabled) {
    const box = el.summaryLines;
    if (!box) return;
    box.innerHTML = '';
    const lines = (enabled && state.activeChat?.summary?.lines) || [];
    if (!lines.length) { box.hidden = true; return; }
    box.hidden = false;

    for (const [section, label] of [['story', 'Story so far'], ['bonds', 'Bonds']]) {
        const inSection = lines.filter(l => (l.section || 'story') === section);
        if (!inSection.length) continue;

        const heading = document.createElement('div');
        heading.className = 'summary-group-label';
        heading.textContent = label;
        box.appendChild(heading);

        for (const l of inSection) {
            const row = document.createElement('div');
            row.className = 'summary-line';

            const text = document.createElement('span');
            text.className = 'summary-line-text';
            text.textContent = l.text;
            row.appendChild(text);

            // Bonds span the whole chat, so only story entries can name a range.
            const range = section === 'story' ? rangeLabel(l) : '';
            if (range) {
                const tag = document.createElement('span');
                tag.className = 'summary-line-range';
                tag.textContent = range;
                row.appendChild(tag);
            }
            box.appendChild(row);
        }
    }
}

export function renderMemorySummaryCard() {
    const toggle = el.summaryToggle;
    if (!toggle) return;  // card not in the DOM
    const chat = state.activeChat;
    const enabled = !!(chat && chat.summary_enabled);

    toggle.checked = enabled;
    toggle.disabled = !chat;
    toggle.title = 'Summarize aged-out history for this chat';

    if (el.summaryConfigHint) {
        el.summaryConfigHint.hidden = !(enabled && !summarizerConfigured());
    }
    markUnusedVar(el.summaryMarker, 'summary');
    const busy = chat?.summary_status === 'running';
    if (el.summaryRebuildBtn) {
        el.summaryRebuildBtn.disabled = !enabled || busy;
        // The same button continues an interrupted run or starts over, and which one it
        // will do is not guessable from the icon — say so.
        const resuming = enabled && !busy && shouldResumeInsteadOfRebuild(chat);
        el.summaryRebuildBtn.title = resuming
            ? 'Continue summarizing from where it stopped'
            : 'Rebuild from history';
        el.summaryRebuildBtn.setAttribute?.(
            'aria-label',
            resuming
                ? 'Continue summarizing the remaining history'
                : 'Rebuild the summary from history',
        );
    }
    // Cancel is the mirror of the other two: live only while a run is in flight.
    if (el.summaryCancelBtn) el.summaryCancelBtn.disabled = !enabled || !busy;
    if (el.summaryResetBtn) {
        el.summaryResetBtn.disabled = !enabled || busy
            || !(chat?.summary?.lines?.length);
    }
    renderStatus();
    renderLines(enabled);
}

/** Called from selectChat: reflect the loaded chat's summary state, resume polling. */
export function onChatSelected() {
    stopStatusPolling();
    renderMemorySummaryCard();
    if (summariesActive()) {
        // Always reconcile once on reopen. This recovers both a job that began
        // elsewhere and stale local status after a transient polling failure.
        startStatusPolling(state.activeChat.id);
        maybeTriggerSummary();
    }
}

/** Wire the memory-card controls. Call once at startup. */
export function initSummaryHandlers() {
    el.summaryToggle?.addEventListener('change', async () => {
        if (el.summaryToggle.checked) await enableSummariesForChat();
        else await disableSummariesForChat();
    });
    el.summaryRebuildBtn?.addEventListener('click', rebuildSummary);
    el.summaryCancelBtn?.addEventListener('click', cancelSummary);
    el.summaryResetBtn?.addEventListener('click', resetSummary);
}
