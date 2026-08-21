import { state, el } from './state.js';
import { API } from './api.js';
import { SAMPLER_FIELDS, SAMPLER_DEFAULTS, FIELD_TO_GROUP, INT_PARAMS, API_PARAM_ALIASES } from './sampler.js';
import { summaryToText } from './summaries.js';
import { analyzeContext } from './context-analysis.js';

// ═══════════════════════════════════════════════════════════════════════════
// REQUEST BUILDER
// ═══════════════════════════════════════════════════════════════════════════

// Auto Summaries: inject the chat's running summary when enabled. The stored
// summary is already held within its size cap server-side (enforce_cap), so
// it's injected as-is.
function activeSummaryText() {
    return state.activeChat?.summary_enabled ? summaryToText(state.activeChat?.summary) : '';
}

/**
 * Build an OpenAI-compatible chat completion payload from current state.
 * @param {number} [excludeLastN=0] — drop the last N messages (used for regen)
 * @param {string|null} [nudge=null] — hidden user message appended to nudge continuation
 */
export function buildChatPayload(excludeLastN = 0, nudge = null) {
    // Context assembly (character/persona/system prompt and the token-budgeted
    // history slice) is handled by analyzeContext.
    const analysis = analyzeContext({ excludeLastN, nudge, summaryText: activeSummaryText() });

    // Sampler settings (only include active sampler groups)
    const samplers = {};
    for (const [key, elName] of Object.entries(SAMPLER_FIELDS)) {
        const group = FIELD_TO_GROUP[key];
        if (state.activeSamplers && group && !state.activeSamplers.has(group)) continue;
        const val = el[elName]?.value || SAMPLER_DEFAULTS[key];
        let paramName = key.replace('sampler_', '');
        paramName = API_PARAM_ALIASES[paramName] || paramName;
        const num = INT_PARAMS.has(paramName)
            ? parseInt(val, 10) : parseFloat(val);
        if (!isNaN(num)) samplers[paramName] = num;
    }
    if (samplers.seed === -1) samplers.seed = Math.floor(Math.random() * 2147483647);

    const payload = { model: state.apiModel || '', messages: analysis.messages, ...samplers };

    if (state.extraRequestParams) {
        try {
            const extra = JSON.parse(state.extraRequestParams);
            if (extra && typeof extra === 'object' && !Array.isArray(extra)) {
                Object.assign(payload, extra);
            }
        } catch (e) {
            console.warn('Invalid extra_request_params JSON:', e.message);
        }
    }

    return payload;
}

/** Preview helper — returns the same payload buildChatPayload would send right now. */
export function previewChatPayload() {
    return buildChatPayload(0, null);
}

/**
 * Preview helper — the System and User templates as this same analysis pass
 * rendered them, before assembly adds the memory fallback and the alternation
 * shims. Those belong to the whole-request preview.
 */
export function previewRenderedTemplates() {
    return analyzeContext({ summaryText: activeSummaryText() }).renderedTemplates;
}

/**
 * Generate a character response via the LLM.
 * @param {number} [excludeLastN=0] — drop last N messages for regen
 * @param {(text: string) => void} onToken — receives the accumulated streamed text
 */
export async function generateResponse(excludeLastN = 0, onToken, signal = null, nudge = null) {
    const payload = buildChatPayload(excludeLastN, nudge);
    if (!payload.model) throw new Error('No model configured \u2014 check Settings');
    if (payload.messages.length === 0) throw new Error('No messages to send');
    if (typeof onToken !== 'function') throw new TypeError('Streaming token callback is required');
    return API.streamChatCompletion(payload, onToken, signal);
}
