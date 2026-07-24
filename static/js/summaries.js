// ═══════════════════════════════════════════════════════════════════════════
// AUTO SUMMARIES
// ═══════════════════════════════════════════════════════════════════════════
// Per-chat running summary of aged-out history. Enablement + pinning happen in
// the memory flyout; the actual summarization runs as a background job on the
// server (see routes/summaries.py). Completed turns start updates in the
// background; the send preflight waits only when history would otherwise fall
// into the gap between raw context and the persisted summary.

import { state, el, icons } from './state.js';
import { API } from './api.js';
import { estimateMessageTokens, estimateTextTokens } from './tokenizer.js';
import { getContextTokenBudget, getRawHistoryMessages } from './context-budget.js';
import { analyzeContext } from './context-analysis.js';
import { flushLLMSettingsSave } from './llm-settings.js';
import { showToast } from './utils.js';
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

// ── Rendering the summary object to text (mirror of summarizer.summary_to_text) ──
export function summaryToText(obj) {
    const lines = (obj && Array.isArray(obj.lines)) ? obj.lines : [];
    const story = lines.filter(l => (l.section || 'story') !== 'bonds');
    const bonds = lines.filter(l => (l.section || 'story') === 'bonds');
    const out = [];
    if (story.length) {
        out.push(STORY_HEADING);
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

function triggerInterval() {
    const parsed = parseInt(state.summaryTriggerInterval || '20', 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 20;
}

/**
 * Estimate the token headroom needed for roughly ``targetMessages`` future
 * messages. A capped average follows genuinely longer conversations without
 * letting one exceptional paste/reply make an automatic update retire a huge
 * raw-history block.
 */
export function estimateTargetHeadroomTokens(messages, targetMessages, {
    stripThinking = !el.sendThinking?.checked,
} = {}) {
    const count = Number.isFinite(targetMessages) && targetMessages > 0
        ? Math.floor(targetMessages)
        : 0;
    if (!count || !Array.isArray(messages) || messages.length === 0) return 0;

    const costs = messages.slice(-count).map(message => estimateMessageTokens(
        { content: message?.text || '' },
        { stripThinking },
    ));
    const sorted = [...costs].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    const median = sorted.length % 2
        ? sorted[middle]
        : (sorted[middle - 1] + sorted[middle]) / 2;
    const outlierCap = Math.max(1, median * 2);
    const cappedTotal = costs.reduce(
        (sum, cost) => sum + Math.min(cost, outlierCap),
        0,
    );
    return Math.ceil((cappedTotal / costs.length) * count);
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

export function untrustedContextAssessment({ candidates, agedOut, analysis }, label) {
    if (!analysis || agedOut.length === 0 || analysis.maxTokens <= 0) return false;
    const nextMessage = agedOut[agedOut.length - 1];
    if (!nextMessage) return false;
    const nextCost = estimateMessageTokens(
        { content: nextMessage.text },
        { stripThinking: !el.sendThinking?.checked },
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

function windowMeasurementUntrusted(excludeLastN, label, options = {}) {
    return untrustedContextAssessment(windowAssessment(excludeLastN, options), label);
}

function agedOutUnsummarized(excludeLastN = 0) {
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    return agedOutMessages(excludeLastN).filter(m => (m.id || 0) > wm);
}

/**
 * Pick the inclusive watermark for an automatic update. Once any unsummarized
 * history falls outside the raw token budget, retire enough additional fitting
 * history to make token headroom for roughly ``triggerInterval()`` more messages.
 * The interval remains the per-call message ceiling as well as the user-facing
 * target, so saved settings and server batching keep the same contract.
 *
 * Prefer to keep the newest two eligible messages verbatim. Under severe token
 * pressure we may consume the older of those two, but the newest message is never
 * selected; this also preserves the live user turn during swipe regeneration.
 */
function automaticRunTarget(excludeLastN = 0) {
    const assessment = windowAssessment(excludeLastN);
    const { candidates, agedOut, analysis } = assessment;
    const agedCount = agedOut.length;
    if (agedCount === 0 || candidates.length <= 1) return null;
    if (untrustedContextAssessment(assessment, 'automatic memory update')) return null;

    const interval = triggerInterval();
    const preferredMax = Math.max(0, candidates.length - 2);
    const absoluteMax = candidates.length - 1;
    // Prepaying headroom must never consume more than half of the currently
    // fitting messages. This bounds estimator drift and prevents the 32k-era
    // wipe where a small live window was collapsed to almost nothing.
    const fittingCount = candidates.length - agedCount;
    const headroomMax = agedCount + Math.floor(fittingCount / 2);
    const maxTargetCount = Math.min(
        absoluteMax,
        interval,
        Math.max(agedCount, Math.min(preferredMax, headroomMax)),
    );
    let targetCount = Math.min(agedCount, interval);
    if (targetCount < agedCount) {
        // A large aged-out backlog drains in bounded interval-sized calls.
        return oldestRetirableId(candidates, targetCount);
    }

    const desiredHeadroom = estimateTargetHeadroomTokens(candidates, interval);
    let projectedHeadroom = analysis.unusedTokens;
    while (targetCount < maxTargetCount && projectedHeadroom < desiredHeadroom) {
        const next = candidates[targetCount];
        projectedHeadroom += estimateMessageTokens(
            { content: next?.text || '' },
            { stripThinking: !el.sendThinking?.checked },
        );
        targetCount += 1;
    }
    if (targetCount <= 0) return null;

    return oldestRetirableId(candidates, targetCount);
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
 * used while stabilizing an explicit rebuild: normal background updates round
 * up to an interval for headroom, but doing that here would make a completed
 * rebuild unexpectedly discard another full block of raw context.
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
 * Before generation, close any gap between the stored watermark and the raw
 * context boundary. The loop matters because a newly enlarged summary can
 * itself move the boundary and age out one more message.
 */
export async function ensureSummaryReadyForSend(signal, { excludeLastN = 0 } = {}) {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    if (agedOutUnsummarized(excludeLastN).length === 0) return;
    // A refused (self-contradictory) measurement must not stall the send:
    // proceed without updating memory rather than retiring history wrongly.
    if (windowMeasurementUntrusted(excludeLastN, 'pre-send memory update')) {
        showToast(UNTRUSTED_WINDOW_TOAST);
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

        await triggerRun({ awaitCompletion: true, chatId, signal, excludeLastN });
        assertSendStillActive(chatId, signal);
        if (!summariesActive(state.activeChat)) return;

        const watermark = state.activeChat.summary_up_to_msg_id || 0;
        if (agedOutUnsummarized(excludeLastN).length === 0) return;
        if (watermark <= previousWatermark) {
            stalledRuns += 1;
            if (stalledRuns >= 2) {
                throw new Error('Chat memory did not advance; response generation was paused to avoid forgetting history.');
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
    if (windowMeasurementUntrusted(0, 'automatic memory update')) {
        showToast(UNTRUSTED_WINDOW_TOAST);
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
    // Start back-filling the out-of-context backlog; runs fold one interval
    // block at a time, and the send preflight loop drains whatever remains.
    return triggerRun({ chatId: chat.id });
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

async function rebuildSummary() {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    const chatId = chat.id;
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
        // A rebuild recomputes the boundary over the full transcript; refuse it
        // outright on a self-contradictory measurement instead of rewriting the
        // watermark from bad numbers.
        if (windowMeasurementUntrusted(0, 'summary rebuild', { includeSummarized: true })) {
            showToast(UNTRUSTED_WINDOW_TOAST);
            return;
        }
        await triggerRun({ rebuild: true, awaitCompletion: true, chatId });

        // The replacement summary's final size is unknowable before the rebuild
        // finishes. Injecting it can move the context boundary and age out a few
        // more messages. Fold those in now, to a fixed point, so reopening the
        // chat cannot discover and launch a surprise follow-up batch.
        let previousWatermark = state.activeChat?.summary_up_to_msg_id || 0;
        let stalledRuns = 0;
        while (state.activeChat?.id === chatId && summariesActive(state.activeChat)
            && agedOutUnsummarized().length > 0
            && !windowMeasurementUntrusted(0, 'rebuild stabilization')) {
            await triggerRun({
                awaitCompletion: true,
                chatId,
                exactTarget: true,
            });
            if (state.activeChat?.id !== chatId || !summariesActive(state.activeChat)) return;

            const watermark = state.activeChat.summary_up_to_msg_id || 0;
            if (watermark <= previousWatermark) {
                stalledRuns += 1;
                if (stalledRuns >= 2) {
                    throw new Error('Summary rebuild could not stabilize the context boundary.');
                }
            } else {
                stalledRuns = 0;
                previousWatermark = watermark;
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') showToast('Summary rebuild failed: ' + e.message);
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

/** Reset button: confirm (pins are discarded), then clear to a blank slate. */
async function resetSummary() {
    const chat = state.activeChat;
    if (!summariesActive(chat)) return;
    const hasContent = (chat.summary?.lines?.length || 0) > 0;
    if (hasContent) {
        const ok = await confirmDialog({
            title: 'Reset summary?',
            message: 'This clears the generated summary and any pinned lines for this chat. '
                + 'You can rebuild it from history afterward.',
            confirmLabel: 'Reset',
        });
        if (!ok) return;
    }
    await clearSummary(chat.id);
}

async function togglePin(index) {
    const chat = state.activeChat;
    const lines = chat?.summary?.lines;
    if (!lines || !lines[index] || !summariesActive(chat)
        || chat.summary_status === 'running') return;
    const line = lines[index];
    const pinned = !line.pinned;
    line.pinned = pinned;
    renderMemorySummaryCard();  // optimistic
    try {
        const updated = await API.updateSummaryPin(chat.id, {
            text: line.text,
            section: line.section || 'story',
            pinned,
        });
        applySummaryState(updated, chat.id);
    } catch (e) {
        line.pinned = !pinned;  // revert the object that was changed optimistically
        if (state.activeChat?.id === chat.id) renderMemorySummaryCard();
        showToast('Could not update pin: ' + e.message);
    }
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
        box.textContent = `Up to date · ${count} message${count === 1 ? '' : 's'} summarized · ${size}`;
    }
    if (chat.summary_status_detail) {
        // A non-empty detail on idle is the pins-over-cap warning.
        box.className = 'summary-status is-warning';
        box.appendChild(document.createElement('br'));
        box.appendChild(document.createTextNode(chat.summary_status_detail));
    }
}

function renderLines(enabled) {
    const box = el.summaryLines;
    if (!box) return;
    box.innerHTML = '';
    const lines = (enabled && state.activeChat?.summary?.lines) || [];
    const busy = state.activeChat?.summary_status === 'running';
    if (!lines.length) { box.hidden = true; return; }
    box.hidden = false;

    for (const [section, label] of [['story', 'Story so far'], ['bonds', 'Bonds']]) {
        const inSection = lines
            .map((l, i) => ({ l, i }))
            .filter(x => (x.l.section || 'story') === section);
        if (!inSection.length) continue;

        const heading = document.createElement('div');
        heading.className = 'summary-group-label';
        heading.textContent = label;
        box.appendChild(heading);

        for (const { l, i } of inSection) {
            const row = document.createElement('div');
            row.className = 'summary-line' + (l.pinned ? ' pinned' : '');

            const pin = document.createElement('button');
            pin.type = 'button';
            pin.className = 'summary-pin-btn' + (l.pinned ? ' pinned' : '');
            pin.setAttribute('aria-pressed', l.pinned ? 'true' : 'false');
            pin.disabled = busy;
            pin.title = busy
                ? 'Pinning is unavailable while the summary updates'
                : (l.pinned ? 'Unpin (allow rewording)' : 'Pin (keep word-for-word)');
            pin.innerHTML = l.pinned ? icons.STAR_FILLED : icons.STAR;
            pin.addEventListener('click', () => togglePin(i));

            const text = document.createElement('span');
            text.className = 'summary-line-text';
            text.textContent = l.text;

            row.appendChild(pin);
            row.appendChild(text);
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
    const busy = chat?.summary_status === 'running';
    if (el.summaryRebuildBtn) el.summaryRebuildBtn.disabled = !enabled || busy;
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
    el.summaryResetBtn?.addEventListener('click', resetSummary);
}
