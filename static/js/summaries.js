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
import { parseThinkingContent } from './thinking.js';
import { getContextTokenBudget, getRawHistoryMessages } from './context-budget.js';
import { analyzeContext } from './context-analysis.js';
import { flushLLMSettingsSave } from './llm-settings.js';
import { showToast, markUnusedVar } from './utils.js';
import { confirmDialog } from './confirm.js';

const STORY_HEADING = 'STORY SO FAR';
const BONDS_HEADING = 'BONDS';
const POLL_MS = 2500;
// While a send or backfill is waiting on a run, check far more often: every
// tick of POLL_MS was up to 2.5s added to the wait, and a short job's progress never
// got past "Starting…". The status request is a single-row read.
const WAIT_POLL_MS = 500;
const MAX_SEND_POLL_FAILURES = 3;
// Tokens of chat per story entry. Mirrors BATCH_TARGET_TOKENS in cozy/summarizer.py,
// which splits the run this module picks the end of.
const BATCH_TARGET_TOKENS = 3200;
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
// Deliberately NOT a byte-for-byte mirror of summarizer.section_to_text: this render is
// read by the roleplay model, so the story heading spells out that the beats are a
// timeline rather than an unordered pile of facts. The Python renderer stays bare
// because it feeds the summary back to the summarizer and is matched by _norm_heading.
const STORY_ORDER_NOTE = ' (in order, oldest first)';

/** The active chat's summary as prompt text, or '' when summaries are off. */
export function activeSummaryText() {
    return summariesActive() ? summaryToText(state.activeChat?.summary) : '';
}

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
export function summarizerConfigured() {
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

/** Mirrors ``_batch_target_tokens`` in cozy/routes/summaries.py: at most an eighth of the context. */
function batchTargetTokens() {
    const ctx = getContextTokenBudget();
    if (ctx <= 0) return BATCH_TARGET_TOKENS;
    return Math.max(1, Math.min(BATCH_TARGET_TOKENS, Math.floor(ctx / 8)));
}

// ── Aged-out message detection ──────────────────────────────────────────────
function windowAssessment(excludeLastN = 0) {
    if (!state.activeChat || getContextTokenBudget() <= 0) {
        return { candidates: [], agedOut: [], analysis: null };
    }
    const candidates = getRawHistoryMessages(state.messages, { excludeLastN });
    const analysis = analyzeContext({
        excludeLastN,
        summaryText: activeSummaryText(),
    });
    const agedCount = candidates.length - analysis.selectedMessages.length;
    return {
        candidates,
        agedOut: agedCount > 0 ? candidates.slice(0, agedCount) : [],
        analysis,
    };
}

function agedOutMessages(excludeLastN = 0) {
    return windowAssessment(excludeLastN).agedOut;
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
const UNSATISFIABLE_BUDGET_TOAST = 'Memory update skipped: Max response tokens is at or '
    + 'above Max context tokens, so no chat history can fit. Lower it in Settings → API → '
    + 'Context & generation. Chat history was not touched.';

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
            `Cozy: ${label} refused — Max response tokens (${analysis.responseTokens}) is at or `
            + `above Max context tokens (${analysis.maxTokens}), so the reply reserve alone fills `
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
function windowMeasurementUntrusted(excludeLastN, label) {
    const assessment = windowAssessment(excludeLastN);
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
 * history falls outside the raw token budget, retire one batch: the oldest
 * messages up to ``batchTargetTokens()`` of text. The target is an amount of text,
 * not a message count, so long replies make a batch of fewer messages and short
 * ones a batch of more — each entry covers about the same amount of story.
 *
 * The target never depends on how full the window is. An earlier version sized
 * the batch by predicting the token cost of future messages and capping the
 * result at half the currently fitting history. That made the effective batch
 * shrink as the window filled, so updates fired far more often than intended and
 * grew the summary faster. The small-context ceiling reads the context *setting*
 * for the same reason.
 *
 * ``wholeBacklog`` is the catch-up before a send and after enabling: it covers
 * everything outside the window, still in whole batches. Retiring exactly what
 * had aged out instead left every catch-up a short last batch — before a send
 * usually a single message, whose new entry could push the next message out and
 * start another one-message job while the send waited.
 *
 * ``oldestRetirableId`` clamps an oversized batch to the messages that actually
 * exist and always leaves the newest one raw. Without ``wholeBacklog``, a backlog
 * larger than one batch drains over successive calls via
 * ``ensureSummaryReadyForSend``.
 */
function automaticRunTarget(excludeLastN = 0, { wholeBacklog = false } = {}) {
    const assessment = windowAssessment(excludeLastN);
    const { candidates, agedOut } = assessment;
    if (agedOut.length === 0 || candidates.length <= 1) return null;
    if (untrustedContextAssessment(assessment, 'automatic memory update')) return null;
    return oldestRetirableId(candidates, wholeBacklog ? agedOut.length : 1);
}

/**
 * Id ending the first batch that covers the ``coverCount`` oldest persisted
 * candidates. Batches are cut the way ``token_batches`` in cozy/summarizer.py
 * cuts them: one closes on the message that brings it to the target, so an
 * oversized message is a batch of its own. The server retires an id range
 * (everything at or below the target), so walk in id order rather than array
 * position — a mis-ordered or partially-saved state.messages must never widen
 * the range — and always leave the newest persisted message raw, retiring the
 * rest when less than a batch remains.
 */
function oldestRetirableId(candidates, coverCount) {
    const persisted = candidates
        .filter(message => Number.isInteger(message?.id) && message.id > 0)
        .sort((a, b) => a.id - b.id);
    const target = batchTargetTokens();
    let total = 0;
    for (let i = 0; i < persisted.length - 1; i += 1) {
        total += estimateTextTokens(parseThinkingContent(persisted[i].text).response);
        if (total < target) continue;
        if (i + 1 >= coverCount) return persisted[i].id;
        total = 0;
    }
    return persisted.length > 1 ? persisted[persisted.length - 2].id : null;
}

function summarizedCount() {
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    if (!wm) return 0;
    return state.messages.filter(m => (m.id || 0) <= wm).length;
}

// ── State merge + polling ───────────────────────────────────────────────────

// Chats whose latest finished run failed, with its error. While a chat is in here a
// send stops waiting on memory (see ensureSummaryReadyForSend), and the card keeps the
// error on show while a retry runs, since the retry's own progress would hide it. Any
// run that ends idle — a success, a cancel, a reset, a disable — takes the chat out.
const failedRuns = new Map();

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
    if (st.summary_status === 'error') {
        failedRuns.set(expectedChatId, st.summary_status_detail || 'Summary update failed.');
    } else if (st.summary_status === 'idle') {
        failedRuns.delete(expectedChatId);
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
// own: the send preflight loops until the watermark advances, so without this it would
// answer a cancel by starting the very next batch. A marker
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
        const timer = setTimeout(done, WAIT_POLL_MS);
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

async function triggerRun({ awaitCompletion = false,
                            chatId = state.activeChat?.id, signal,
                            excludeLastN = 0, wholeBacklog = false } = {}) {
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

    const upTo = automaticRunTarget(excludeLastN, { wholeBacklog });
    if (upTo == null) {
        renderMemorySummaryCard();
        return null;
    }
    try {
        const st = await API.runSummary(chatId, { up_to_msg_id: upTo });
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

/** Toast text for a send that goes ahead while memory is behind the context window. */
function memoryGapWarning(reason, excludeLastN) {
    const behind = agedOutUnsummarized(excludeLastN).length;
    const said = reason.replace(/\.$/, '');
    if (!behind) return said;
    return `${said} — this reply can't see ${behind} older message${behind === 1 ? '' : 's'} `
        + 'until memory catches up.';
}

/**
 * Close any gap between the stored watermark and the raw context boundary, so history
 * doesn't fall between the two before generation; enablement reuses the same loop to
 * backfill an existing chat. The loop matters because a newly enlarged summary can
 * itself move the boundary and age out one more message.
 *
 * Before a send this never blocks. When the update fails, stops advancing, or the user
 * stops it, the reply goes ahead with a warning: memory is behind, not lost — the
 * messages stay in the chat and the next successful run folds them in — and a reply
 * that can't see a few older messages beats no reply. A backfill rethrows instead, so
 * enabling the feature can report why it didn't finish.
 */
export async function ensureSummaryReadyForSend(signal, {
    excludeLastN = 0,
    backfill = false,
} = {}) {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    if (agedOutUnsummarized(excludeLastN).length === 0) return;
    // After a failed run a send stops waiting on memory. The retry that starts after
    // each reply would most likely fail the same way, and waiting on it held every
    // turn for a whole attempt — pause and retry included — only to warn anyway. The
    // reply goes ahead with the gap named; the background retries carry on, and the
    // first one that succeeds brings the wait back.
    if (!backfill && failedRuns.has(chat.id)) {
        showToast(memoryGapWarning('Memory is behind: its last update failed', excludeLastN));
        return;
    }
    // A refused (self-contradictory) measurement must not stall the send:
    // proceed without updating memory rather than retiring history wrongly.
    const refusal = windowMeasurementUntrusted(excludeLastN, 'pre-send memory update');
    if (refusal) {
        showToast(refusal);
        return;
    }

    const chatId = chat.id;
    try {
        if (!summarizerConfigured()) {
            throw new Error('Configure an Auto Summaries endpoint and model');
        }
        let previousWatermark = chat.summary_up_to_msg_id || 0;
        let stalledRuns = 0;
        for (;;) {
            assertSendStillActive(chatId, signal);
            if (!summariesActive(state.activeChat)) return;
            if (agedOutUnsummarized(excludeLastN).length === 0) return;

            // Submit the whole backlog currently outside the window as one server job,
            // in whole batches. The worker still calls the provider one batch of text
            // at a time, but status can now report meaningful cumulative progress (batch
            // 1/12, 2/12, …) instead of a client-side procession of unrelated batch 1/1
            // jobs.
            await triggerRun({
                awaitCompletion: true,
                chatId,
                signal,
                excludeLastN,
                wholeBacklog: true,
            });
            assertSendStillActive(chatId, signal);
            if (!summariesActive(state.activeChat)) return;
            // Stopping the run this send was waiting on means "don't wait", not "don't
            // send" — the send button is the way to cancel the send. Carry on without
            // starting the next batch.
            if (consumeCancellation(chatId)) {
                if (!backfill) showToast(memoryGapWarning('Memory update stopped', excludeLastN));
                return;
            }

            const watermark = state.activeChat.summary_up_to_msg_id || 0;
            if (agedOutUnsummarized(excludeLastN).length === 0) return;
            if (watermark <= previousWatermark) {
                stalledRuns += 1;
                if (stalledRuns >= 2) {
                    throw new Error(backfill
                        ? 'Chat memory did not advance; automatic backfill stopped.'
                        : 'the summary stopped advancing');
                }
            } else {
                stalledRuns = 0;
                previousWatermark = watermark;
            }
        }
    } catch (e) {
        if (backfill || e.name === 'AbortError') throw e;
        showToast(memoryGapWarning(`Memory update failed: ${e.message}`, excludeLastN));
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
    return backfillSummary();
}

/**
 * Drain the existing backlog now, one checkpointed batch at a time, so the summary
 * isn't left half-filled until the next send finishes it. Turning summaries on and
 * Reset both end here.
 */
async function backfillSummary() {
    if (!summarizerConfigured()) {
        renderMemorySummaryCard();  // arms the feature; hint tells the user to configure
        return null;
    }
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

/** Wipe the summary + watermark to empty (no LLM call). True when it was cleared. */
async function clearSummary(chatId) {
    if (state.activeChat?.id === chatId) stopStatusPolling();
    try {
        const st = await API.resetSummary(chatId);
        applySummaryState(st, chatId);
        return true;
    } catch (e) {
        showToast('Could not reset summary: ' + e.message);
        return false;
    }
}

/**
 * Reset button: confirm, clear to a blank slate, then summarize the history again.
 *
 * This is the from-scratch path, for a summary gone stale after editing old messages
 * or changing the context size. Continuing an interrupted run needs no button: the
 * next send, or turning summaries back on, folds in whatever is left.
 */
async function resetSummary() {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    const chatId = chat.id;
    const hasContent = (chat.summary?.lines?.length || 0) > 0;
    if (hasContent) {
        const ok = await confirmDialog({
            title: 'Reset summary?',
            message: 'This clears the summary for this chat and summarizes its '
                + 'history again from the start.',
            confirmLabel: 'Reset',
        });
        if (!ok) return;
    }
    if (!(await clearSummary(chatId))) return;
    if (state.activeChat?.id !== chatId) return;
    return backfillSummary();
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
        const failed = failedRuns.get(chat.id);
        box.appendChild(document.createTextNode(' ' + (failed
            ? `Retrying — the last update failed: ${failed}`
            : (chat.summary_status_detail || 'Summarizing…'))));
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
        // An empty summary can still have history waiting — after a Reset, say — and
        // "nothing aged out yet" then hid a backlog the next send would stop to fold in.
        const waiting = agedOutUnsummarized().length;
        box.textContent = waiting === 0
            ? 'No aged-out history yet — the summary fills in as the chat grows.'
            : `${waiting} older message${waiting === 1 ? '' : 's'} not summarized yet`
                + (summarizerConfigured() ? ' — folded in before your next message.' : '.');
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
    // Cancel is the mirror of Reset: live only while a run is in flight.
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
    el.summaryCancelBtn?.addEventListener('click', cancelSummary);
    el.summaryResetBtn?.addEventListener('click', resetSummary);
}
