import { state, el } from './state.js';
import { scrollToBottom } from './utils.js';
import { estimateMessagesTokens, estimateMessageTokens, selectContextMessages, getContextBoundaryMsgId } from './tokenizer.js';

export function getContextMaxTokens() {
    const raw = el.settingsContextTokens?.value ?? state.contextMaxTokens ?? '32768';
    const parsed = parseInt(raw, 10);
    // 0 (or invalid) means "no cap" — return 0 and let callers handle that.
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

export function updateContextMeter() {
    if (!el.contextTokenMeter || !el.contextTokenLabel || !el.contextTokenBar) return;
    if (!state.showContextTokenMeter || !state.activeChat) {
        el.contextTokenMeter.hidden = true;
        return;
    }
    const wasHidden = el.contextTokenMeter.hidden;

    const maxTokens = getContextMaxTokens();
    const stripThinking = !el.sendThinking?.checked;
    const selected = selectContextMessages(state.messages, { maxTokens, stripThinking });
    let used = estimateMessagesTokens(selected, { stripThinking });
    const draft = el.userInput?.value.trim();
    if (draft) used += estimateMessageTokens({ content: draft });

    if (maxTokens <= 0) {
        // No cap — show usage without a percentage bar.
        el.contextTokenLabel.textContent = `≈ ${used.toLocaleString()} tokens`;
        el.contextTokenBar.style.width = '0%';
        el.contextTokenMeter.dataset.level = 'ok';
    } else {
        const pct = Math.min(100, Math.round((used / maxTokens) * 100));
        el.contextTokenLabel.textContent = `≈ ${used.toLocaleString()} / ${maxTokens.toLocaleString()}`;
        el.contextTokenBar.style.width = `${pct}%`;
        el.contextTokenMeter.dataset.level = pct >= 90 ? 'danger' : (pct >= 70 ? 'warn' : 'ok');
    }
    el.contextTokenMeter.hidden = false;

    // Revealing the meter grows the composer and shrinks the chat area, which
    // strands a bottom-anchored scroll ~20px short and clips the last message
    // (chat load and the settings toggle both reveal it after messages render).
    // Re-anchor when the view was at the bottom; 60px matches the autoScroll
    // threshold in bindScrollHandlers.
    if (wasHidden && el.chatHistory) {
        const sc = el.chatHistory;
        if (sc.scrollHeight - sc.scrollTop - sc.clientHeight < 60) scrollToBottom();
    }
}

export function updateContextBoundary() {
    const existing = el.chatHistory?.querySelector('.context-boundary');
    if (existing) existing.remove();

    const maxTokens = getContextMaxTokens();
    if (maxTokens <= 0) return;

    const stripThinking = !el.sendThinking?.checked;
    const boundaryMsgId = getContextBoundaryMsgId(state.messages, { maxTokens, stripThinking });
    if (state.messages.length === 0) return;

    const boundaryEl = document.createElement('div');
    boundaryEl.className = 'context-boundary';
    boundaryEl.textContent = 'Context window';

    if (boundaryMsgId != null) {
        const target = el.chatHistory.querySelector(`.message[data-msg-id="${boundaryMsgId}"]`);
        if (target) {
            el.chatHistory.insertBefore(boundaryEl, target.closest('.message-container') || target);
            return;
        }
    }

    // All messages fit — insert at the top
    const firstContainer = el.chatHistory.querySelector('.message-container');
    if (firstContainer) {
        el.chatHistory.insertBefore(boundaryEl, firstContainer);
    }
}
