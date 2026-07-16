// ═══════════════════════════════════════════════════════════════════════════
// AUTO SUMMARIES
// ═══════════════════════════════════════════════════════════════════════════
// Per-chat running summary of aged-out history. Enablement + pinning happen in
// the memory flyout; the actual summarization runs as a background job on the
// server (see routes/summaries.py). Everything here is fire-and-forget + polling
// so the send path is never blocked.

import { state, el, icons } from './state.js';
import { API } from './api.js';
import { getContextBoundaryMsgId, estimateTextTokens } from './tokenizer.js';
import { SAMPLER_DEFAULTS } from './sampler.js';
import { showToast } from './utils.js';
import { confirmDialog } from './confirm.js';

const STORY_HEADING = 'STORY SO FAR';
const BONDS_HEADING = 'BONDS';
const POLL_MS = 2500;

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
    const ep = state.summaryApiEndpoint || state.apiEndpoint;
    const model = state.summaryApiModel || state.apiModel;
    return !!(ep && model);
}

function contextBudget() {
    return parseInt(el.settingsContextTokens?.value || state.contextMaxTokens || '0', 10) || 0;
}

function capTokens() {
    const ctx = contextBudget();
    const pct = parseFloat(state.summaryCapPct || '10') || 10;
    return ctx > 0 ? Math.floor(ctx * pct / 100) : 0;
}

// Token budget left for raw messages once the reply space and the injected
// summary are carved out — the mirror of buildChatPayload's calculation, so the
// "which messages have aged out" boundary matches what actually gets sent.
function messageBudget() {
    const ctx = contextBudget();
    if (ctx <= 0) return 0;
    const maxResp = parseInt(el.samplerMaxTokens?.value || SAMPLER_DEFAULTS.sampler_max_tokens, 10) || 0;
    const sumTok = state.activeChat?.summary_enabled
        ? estimateTextTokens(summaryToText(state.activeChat.summary)) : 0;
    return Math.max(1, ctx - maxResp - sumTok);
}

// ── Aged-out message detection ──────────────────────────────────────────────
function agedOutMessages() {
    if (!state.activeChat) return [];
    const budget = messageBudget();
    if (budget <= 0) return [];
    const stripThinking = !el.sendThinking?.checked;
    const boundaryId = getContextBoundaryMsgId(state.messages, { maxTokens: budget, stripThinking });
    if (boundaryId == null) return [];  // everything fits (or no cap) → nothing aged out
    const idx = state.messages.findIndex(m => m.id === boundaryId);
    if (idx <= 0) return [];
    return state.messages.slice(0, idx);  // messages older than the recent window
}

function agedOutUnsummarized() {
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    return agedOutMessages().filter(m => (m.id || 0) > wm);
}

function summarizedCount() {
    const wm = state.activeChat?.summary_up_to_msg_id || 0;
    if (!wm) return 0;
    return state.messages.filter(m => (m.id || 0) <= wm).length;
}

// ── State merge + polling ───────────────────────────────────────────────────
const SUMMARY_KEYS = ['summary_enabled', 'summary', 'summary_up_to_msg_id',
                      'summary_status', 'summary_status_detail'];

function applySummaryState(st) {
    const chat = state.activeChat;
    if (!chat || !st) return;
    if (st.id != null && st.id !== chat.id) return;  // response for a different chat
    for (const k of SUMMARY_KEYS) {
        if (k in st) chat[k] = st[k];
    }
    const inList = state.chats.find(c => c.id === chat.id);
    if (inList) for (const k of SUMMARY_KEYS) if (k in st) inList[k] = st[k];
    renderMemorySummaryCard();
}

export function startStatusPolling() {
    stopStatusPolling();
    state._summaryPollTimer = setInterval(async () => {
        const chat = state.activeChat;
        if (!chat) return stopStatusPolling();
        try {
            const st = await API.getSummaryStatus(chat.id);
            if (!state.activeChat || state.activeChat.id !== chat.id) return stopStatusPolling();
            applySummaryState(st);
            if (st.summary_status !== 'running') stopStatusPolling();
        } catch {
            stopStatusPolling();
        }
    }, POLL_MS);
}

export function stopStatusPolling() {
    if (state._summaryPollTimer) {
        clearInterval(state._summaryPollTimer);
        state._summaryPollTimer = null;
    }
}

// ── Kicking off runs (all fire-and-forget) ──────────────────────────────────
async function triggerRun({ rebuild = false } = {}) {
    const chat = state.activeChat;
    if (!chat) return;
    const agedOut = agedOutMessages();
    const upTo = agedOut.length ? agedOut[agedOut.length - 1].id : null;
    if (upTo == null) {
        // Nothing is outside the context window. On an explicit rebuild that means
        // a grown context now fits everything — clear any lingering stale summary.
        if (rebuild) await clearSummary();
        else renderMemorySummaryCard();
        return;
    }
    try {
        const st = await API.runSummary(chat.id, { up_to_msg_id: upTo, rebuild });
        applySummaryState(st);
        if (st.summary_status === 'running') startStatusPolling();
    } catch (e) {
        showToast('Summary run failed: ' + e.message);
    }
}

/** After a completed turn: fold in aged-out history once enough has accumulated. */
export function maybeTriggerSummary() {
    const chat = state.activeChat;
    if (!chat || !chat.summary_enabled) return;
    if (chat.summary_status === 'running') return;
    if (!summarizerConfigured()) return;
    const interval = parseInt(state.summaryTriggerInterval || '20', 10) || 20;
    if (agedOutUnsummarized().length < interval) return;
    triggerRun({});
}

async function enableSummariesForChat() {
    const chat = state.activeChat;
    if (!chat) return;
    try {
        const updated = await API.updateChat(chat.id, { summary_enabled: true });
        applySummaryState(updated);
    } catch (e) {
        showToast('Could not enable summaries: ' + e.message);
        renderMemorySummaryCard();
        return;
    }
    if (!summarizerConfigured()) {
        renderMemorySummaryCard();  // arms the feature; hint tells the user to configure
        return;
    }
    // Immediately back-fill the entire out-of-context backlog.
    triggerRun({});
}

async function disableSummariesForChat() {
    const chat = state.activeChat;
    if (!chat) return;
    stopStatusPolling();
    try {
        const updated = await API.updateChat(chat.id, { summary_enabled: false });
        applySummaryState(updated);
    } catch (e) {
        showToast('Could not disable summaries: ' + e.message);
        renderMemorySummaryCard();
    }
}

function rebuildSummary() {
    if (!state.activeChat?.summary_enabled) return;
    triggerRun({ rebuild: true });
}

/** Wipe the summary + watermark to empty (no LLM call). */
async function clearSummary() {
    const chat = state.activeChat;
    if (!chat) return;
    stopStatusPolling();
    try {
        const st = await API.resetSummary(chat.id);
        applySummaryState(st);
    } catch (e) {
        showToast('Could not reset summary: ' + e.message);
    }
}

/** Reset button: confirm (pins are discarded), then clear to a blank slate. */
async function resetSummary() {
    const chat = state.activeChat;
    if (!chat?.summary_enabled) return;
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
    await clearSummary();
}

async function togglePin(index) {
    const chat = state.activeChat;
    const lines = chat?.summary?.lines;
    if (!lines || !lines[index]) return;
    lines[index].pinned = !lines[index].pinned;
    renderMemorySummaryCard();  // optimistic
    try {
        const updated = await API.updateChat(chat.id, { summary_json: chat.summary });
        applySummaryState(updated);
    } catch (e) {
        lines[index].pinned = !lines[index].pinned;  // revert
        renderMemorySummaryCard();
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
        box.textContent = `Up to date · ${count} message${count === 1 ? '' : 's'} summarized · `
            + `≈ ${toks.toLocaleString()} / ${cap.toLocaleString()} tokens`;
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
            pin.title = l.pinned ? 'Unpin (allow rewording)' : 'Pin (keep word-for-word)';
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

    if (el.summaryConfigHint) el.summaryConfigHint.hidden = !(enabled && !summarizerConfigured());
    const busy = chat?.summary_status === 'running';
    if (el.summaryRebuildBtn) el.summaryRebuildBtn.disabled = !enabled || busy;
    if (el.summaryResetBtn) {
        el.summaryResetBtn.disabled = !enabled || busy || !(chat?.summary?.lines?.length);
    }
    renderStatus();
    renderLines(enabled);
}

/** Called from selectChat: reflect the loaded chat's summary state, resume polling. */
export function onChatSelected() {
    stopStatusPolling();
    renderMemorySummaryCard();
    if (state.activeChat?.summary_status === 'running') startStatusPolling();
}

/** Wire the memory-card controls. Call once at startup. */
export function initSummaryHandlers() {
    el.summaryToggle?.addEventListener('change', () => {
        if (el.summaryToggle.checked) enableSummariesForChat();
        else disableSummariesForChat();
    });
    el.summaryRebuildBtn?.addEventListener('click', rebuildSummary);
    el.summaryResetBtn?.addEventListener('click', resetSummary);
}
