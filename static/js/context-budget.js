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
 * is here — not the Max response tokens field — is what the model gets.
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

function resolveActiveLorebook(chat, character, lorebooks) {
    if (!chat) return character?.character_book || character?.data?.character_book || null;
    if (chat.active_lorebook_embedded) {
        return character?.character_book || character?.data?.character_book || null;
    }
    if (chat.active_lorebook_id != null) {
        const entry = lorebooks?.find(book => book.id === chat.active_lorebook_id);
        return entry?.book || null;
    }
    return null;
}

/**
 * Everything a context analysis is built from, gathered in one place.
 * context-analysis.js works from this alone and cannot see `state`, which is
 * what lets it hand back the analysis it already has when nothing here has
 * changed. Anything new the analysis needs from the app has to be added here.
 *
 * `candidates` are copies holding only what assembly reads: the raw-history
 * messages, then the draft if there is one.
 */
export function contextInputs({
    excludeLastN = 0,
    draftText = '',
    summaryText = '',
    includeSummarized = false,
    messages = state.messages,
} = {}) {
    const candidates = getRawHistoryMessages(messages, { excludeLastN, includeSummarized })
        .map(message => ({
            role: message.role,
            text: message.text,
            id: message.id,
            _contextSource: message._contextSource || 'message_history',
        }));
    const draft = String(draftText || '').trim();
    if (draft) {
        candidates.push({ role: 'user', text: draft, _contextSource: 'current_draft', _isDraft: true });
    }

    const character = state.activeCharacter || {};
    const persona = state.activePersona || {};
    const prompt = state.systemPrompts.find(item => item.id === state.activeSystemPromptId);
    return {
        candidates,
        maxTokens: getContextTokenBudget(),
        responseTokens: getResponseTokenReserve(),
        summaryText,
        authorNote: state.activeChat?.author_note || '',
        character: {
            name: character.name,
            personality: character.personality,
            scenario: character.scenario,
            description: character.description,
            mes_example: character.mes_example,
            system_prompt: character.system_prompt,
            post_history_instructions: character.post_history_instructions,
        },
        persona: { name: persona.name, description: persona.description },
        systemTemplate: prompt?.content || '',
        userTemplate: prompt?.post_history_content || '',
        lorebook: resolveActiveLorebook(state.activeChat, character, state.lorebooks),
    };
}
