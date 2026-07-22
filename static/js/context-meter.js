import { state, el } from './state.js';
import { scrollToBottom } from './utils.js';
import { summaryToText } from './summaries.js';
import { getContextTokenBudget, getRawHistoryMessages } from './context-budget.js';
import { analyzeContext } from './context-analysis.js';

export function getContextMaxTokens() {
    return getContextTokenBudget();
}

function activeSummaryText() {
    return state.autoSummariesEnabled !== false
        && state.activeChat?.summary_enabled
        ? summaryToText(state.activeChat.summary)
        : '';
}

export function getCurrentContextAnalysis({ includeDraft = false } = {}) {
    return analyzeContext({
        summaryText: activeSummaryText(),
        draftText: includeDraft ? el.userInput?.value || '' : '',
    });
}

function tooltipForSegment(segment, analysis) {
    const formatted = segment.tokens.toLocaleString();
    const pct = analysis.maxTokens > 0
        ? `${((segment.tokens / analysis.maxTokens) * 100).toFixed(1)}% of context`
        : `${((segment.tokens / Math.max(1, analysis.allocatedTokens)) * 100).toFixed(1)}% of accounted tokens`;
    let detail = `≈ ${formatted} tokens · ${pct}`;
    if (segment.id === 'unused') detail += '<br>Available for future conversation context.';
    if (segment.id === 'response_reserve') detail += '<br>Held back so the model has room to answer.';
    return `<strong>${segment.label}</strong><br>${detail}`;
}

function renderSegments(analysis) {
    const bar = el.contextTokenBar;
    if (!bar) return;
    bar.replaceChildren();

    const denominator = analysis.maxTokens > 0 && analysis.overflowTokens === 0
        ? analysis.maxTokens
        : Math.max(1, analysis.allocatedTokens);

    for (const segment of analysis.segments) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'context-meter-segment';
        button.dataset.source = segment.id;
        button.dataset.tip = tooltipForSegment(segment, analysis);
        button.style.flexBasis = `${(segment.tokens / denominator) * 100}%`;
        button.setAttribute('aria-label', `${segment.label}: approximately ${segment.tokens.toLocaleString()} tokens`);
        bar.appendChild(button);
    }
}

export function updateContextMeter() {
    if (!el.contextTokenMeter || !el.contextTokenLabel || !el.contextTokenBar) return;
    if (!state.showContextTokenMeter || !state.activeChat) {
        el.contextTokenMeter.hidden = true;
        return;
    }
    const wasHidden = el.contextTokenMeter.hidden;
    const analysis = getCurrentContextAnalysis({ includeDraft: true });
    renderSegments(analysis);

    if (analysis.maxTokens <= 0) {
        el.contextTokenLabel.textContent = `≈ ${analysis.allocatedTokens.toLocaleString()} used · no limit`;
        el.contextTokenMeter.dataset.level = 'ok';
    } else if (analysis.overflowTokens > 0) {
        el.contextTokenLabel.textContent = `≈ ${analysis.allocatedTokens.toLocaleString()} used / ${analysis.maxTokens.toLocaleString()} · ${analysis.overflowTokens.toLocaleString()} over`;
        el.contextTokenMeter.dataset.level = 'danger';
    } else {
        const pct = Math.min(100, Math.round((analysis.allocatedTokens / analysis.maxTokens) * 100));
        el.contextTokenLabel.textContent = `≈ ${analysis.allocatedTokens.toLocaleString()} used / ${analysis.maxTokens.toLocaleString()}`;
        el.contextTokenMeter.dataset.level = pct >= 90 ? 'danger' : (pct >= 70 ? 'warn' : 'ok');
    }

    const track = el.contextTokenBar.parentElement;
    if (track) {
        track.setAttribute('aria-valuemin', '0');
        const ariaMax = analysis.maxTokens > 0
            ? analysis.maxTokens
            : Math.max(1, analysis.allocatedTokens);
        track.setAttribute('aria-valuemax', String(ariaMax));
        track.setAttribute('aria-valuenow', String(Math.min(analysis.allocatedTokens, ariaMax)));
        track.setAttribute('aria-valuetext', el.contextTokenLabel.textContent);
    }
    el.contextTokenMeter.hidden = false;

    // Revealing the meter grows the composer and shrinks the chat area, which
    // can otherwise strand a bottom-anchored scroll just above the last turn.
    if (wasHidden && el.chatHistory) {
        const scroller = el.chatHistory;
        if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60) {
            scrollToBottom();
        }
    }
}

export function updateContextBoundary() {
    const existing = el.chatHistory?.querySelector('.context-boundary');
    if (existing) existing.remove();

    if (getContextMaxTokens() <= 0 || state.messages.length === 0) return;

    const rawMessages = getRawHistoryMessages(state.messages);
    const analysis = getCurrentContextAnalysis();
    let boundaryMessageId = analysis.firstSelectedMessageId;

    // Summarized transcript remains visible even though only the post-watermark
    // suffix is eligible for raw context.
    if (boundaryMessageId == null && rawMessages.length > 0
        && rawMessages.length < state.messages.length) {
        boundaryMessageId = rawMessages[0].id ?? null;
    }

    const boundary = document.createElement('div');
    boundary.className = 'context-boundary';
    boundary.textContent = 'Context window';

    if (rawMessages.length === 0) {
        el.chatHistory.appendChild(boundary);
        return;
    }

    if (boundaryMessageId != null) {
        const target = el.chatHistory.querySelector(`.message[data-msg-id="${boundaryMessageId}"]`);
        if (target) {
            el.chatHistory.insertBefore(boundary, target.closest('.message-container') || target);
            return;
        }
    }

    const firstContainer = el.chatHistory.querySelector('.message-container');
    if (firstContainer) el.chatHistory.insertBefore(boundary, firstContainer);
}
