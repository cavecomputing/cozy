import { state, el } from './state.js';
import { SAMPLER_DEFAULTS } from './sampler.js';

/** Return the configured total context window, or 0 when it is unlimited. */
export function getContextTokenBudget() {
    const raw = el.settingsContextTokens?.value || state.contextMaxTokens || '0';
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

/**
 * A `max_tokens` in extra_request_params, or null when there isn't a usable
 * one. buildChatPayload merges those params *over* the samplers, so whatever
 * is here — not the Max Response Tokens field — is what the model gets.
 */
function extraParamsResponseTokens() {
    try {
        const extra = JSON.parse(state.extraRequestParams || '');
        if (!extra || typeof extra !== 'object' || Array.isArray(extra)) return null;
        if (!('max_tokens' in extra)) return null;
        const parsed = parseInt(extra.max_tokens, 10);
        // An unusable override still lands in the payload and still wins, so
        // reserve nothing rather than falling back to a value we won't send.
        return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
    } catch {
        return null;
    }
}

/** Return the response-token allowance that must remain outside raw history. */
export function getResponseTokenReserve() {
    const override = extraParamsResponseTokens();
    if (override !== null) return override;
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
