import { state, el } from './state.js';
import { estimateMessagesTokens, estimateMessageTokens, selectContextMessages } from './tokenizer.js';

export function getContextMaxTokens() {
    const raw = el.settingsContextTokens?.value || state.contextMaxTokens || '4096';
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 4096;
}

export function updateContextMeter() {
    if (!el.contextTokenMeter || !el.contextTokenLabel || !el.contextTokenBar) return;
    if (!state.activeChat) {
        el.contextTokenMeter.hidden = true;
        return;
    }

    const maxTokens = getContextMaxTokens();
    const maxMessages = parseInt(el.settingsContextSize?.value || '0', 10) || 0;
    const stripThinking = !el.sendThinking?.checked;
    const selected = selectContextMessages(state.messages, { maxMessages, maxTokens, stripThinking });
    let used = estimateMessagesTokens(selected, { stripThinking });
    const draft = el.userInput?.value.trim();
    if (draft) used += estimateMessageTokens({ content: draft });

    const pct = maxTokens > 0 ? Math.min(100, Math.round((used / maxTokens) * 100)) : 0;
    el.contextTokenLabel.textContent = `≈ ${used.toLocaleString()} / ${maxTokens.toLocaleString()}`;
    el.contextTokenBar.style.width = `${pct}%`;
    el.contextTokenMeter.dataset.level = pct >= 90 ? 'danger' : (pct >= 70 ? 'warn' : 'ok');
    el.contextTokenMeter.hidden = false;
}
