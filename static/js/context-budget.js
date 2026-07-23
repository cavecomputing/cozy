import { state, el } from './state.js';
import { SAMPLER_DEFAULTS } from './sampler.js';
import { estimateTextTokens } from './tokenizer.js';

/** Return the configured total context window, or 0 when it is unlimited. */
export function getContextTokenBudget() {
    const raw = el.settingsContextTokens?.value || state.contextMaxTokens || '0';
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

/** Return the response-token allowance that must remain outside raw history. */
export function getResponseTokenReserve() {
    const raw = el.samplerMaxTokens?.value || SAMPLER_DEFAULTS.sampler_max_tokens;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

/**
 * Return the raw-history zone used by requests, memory aging, and the context
 * UI. Once an active summary covers a positive message id, keeping that same
 * message verbatim would double-count it. Rebuilds opt back into full history.
 */
export function getRawHistoryMessages(messages = state.messages, {
    excludeLastN = 0,
    includeSummarized = false,
} = {}) {
    const source = Array.isArray(messages) ? messages : [];
    const candidates = excludeLastN > 0
        ? source.slice(0, -excludeLastN)
        : source;
    if (includeSummarized || !state.activeChat?.summary_enabled) {
        return candidates;
    }

    const watermark = Number(state.activeChat.summary_up_to_msg_id) || 0;
    if (watermark <= 0) return candidates;
    return candidates.filter(message => {
        const id = Number(message?.id);
        return !(Number.isFinite(id) && id > 0 && id <= watermark);
    });
}

/**
 * Return the budget available to recent raw messages after reserving room for
 * the model response and the running summary. A zero total context means no
 * limit; otherwise retain a one-token minimum for deterministic selection.
 */
export function getRawMessageTokenBudget(summaryText = '') {
    const contextTokens = getContextTokenBudget();
    if (contextTokens <= 0) return 0;
    return Math.max(
        1,
        contextTokens - getResponseTokenReserve() - estimateTextTokens(summaryText),
    );
}
