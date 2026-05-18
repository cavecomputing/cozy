import { state, el } from './state.js';
import { estimateMessagesTokens, estimateMessageTokens, selectContextMessages } from './tokenizer.js';

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
}
