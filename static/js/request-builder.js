import { state, el } from './state.js';
import { resolveTemplateVariables } from './utils.js';
import { resolveLorebookEntries } from './lorebook.js';
import { parseThinkingContent } from './thinking.js';
import { API } from './api.js';
import { SAMPLER_FIELDS, SAMPLER_DEFAULTS, SAMPLER_GROUPS, FIELD_TO_GROUP, INT_PARAMS } from './sampler.js';

// ═══════════════════════════════════════════════════════════════════════════
// REQUEST BUILDER
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Pick the active lorebook for the current chat.
 * Priority: chat says "use embedded" → character's character_book.
 *           chat has standalone id → that DB lorebook's `book` JSON.
 *           otherwise null.
 */
function resolveActiveLorebook(chat, character, lorebooks) {
    if (!chat) return character?.character_book || character?.data?.character_book || null;
    if (chat.active_lorebook_embedded) {
        return character?.character_book || character?.data?.character_book || null;
    }
    if (chat.active_lorebook_id != null) {
        const entry = lorebooks?.find(b => b.id === chat.active_lorebook_id);
        return entry?.book || null;
    }
    return null;
}

/**
 * Build an OpenAI-compatible chat completion payload from current state.
 * @param {number} [excludeLastN=0] — drop the last N messages (used for regen)
 * @param {string|null} [nudge=null] — hidden user message appended to nudge continuation
 */
export function buildChatPayload(excludeLastN = 0, nudge = null) {
    const c = state.activeCharacter || {};
    const p = state.activePersona || {};
    const sp = state.systemPrompts.find(s => s.id === state.activeSystemPromptId);

    // 1. Build context-limited message slice — lorebook scan needs it before
    //    template resolution.
    let msgs = excludeLastN > 0 ? state.messages.slice(0, -excludeLastN) : state.messages;
    const ctxLimit = parseInt(el.settingsContextSize?.value || '0', 10);
    if (!isNaN(ctxLimit) && ctxLimit > 0) msgs = msgs.slice(-ctxLimit);

    // 2. Resolve lorebook entries against the context window.
    const activeBook = resolveActiveLorebook(state.activeChat, c, state.lorebooks);
    const override = parseInt(state.lorebookScanDepthOverride, 10) || 0;
    const bookForResolver = (activeBook && override > 0)
        ? { ...activeBook, scan_depth: override }
        : activeBook;
    const lorebookContents = resolveLorebookEntries(bookForResolver, msgs, {
        alwaysInjectAll: state.lorebookAlwaysInjectAll,
    });
    const lorebookText = lorebookContents.join('\n---\n');

    // 3. Build the template context.
    const ctx = {
        user:          p.name || 'User',
        char:          c.name || '',
        personality:   c.personality || '',
        scenario:      c.scenario || '',
        description:   c.description || '',
        persona:       p.description || '',
        mesExamples:   c.mes_example || '',
        lorebook:      lorebookText,
        system_prompt: c.system_prompt || '',
    };

    // 4. Resolve the active prompt-builder template into the system message.
    const template = sp ? sp.content : '';
    const sysContent = resolveTemplateVariables(template, ctx);

    const messages = [];
    if (sysContent) messages.push({ role: 'system', content: sysContent });

    // 5. Chat history (map character → assistant), optionally limited
    const stripThinking = !el.sendThinking?.checked;
    for (const msg of msgs) {
        let content = msg.text;
        if (stripThinking) {
            const parsed = parseThinkingContent(content);
            content = parsed.response;
        }
        if (!content) continue;
        messages.push({
            role: msg.role === 'user' ? 'user' : 'assistant',
            content,
        });
    }

    // 6. Post-history instructions — injected as user role to avoid mid-conversation system messages
    if (c.post_history_instructions) {
        const resolved = resolveTemplateVariables(c.post_history_instructions, ctx);
        messages.push({ role: 'user', content: '[Post-History Instructions]\n' + resolved });
    }

    // 7. Nudge — hidden continuation prompt, not persisted to chat
    if (nudge) messages.push({ role: 'user', content: nudge });

    // Sampler settings (only include active sampler groups)
    const samplers = {};
    for (const [key, elName] of Object.entries(SAMPLER_FIELDS)) {
        const group = FIELD_TO_GROUP[key];
        if (state.activeSamplers && group && !state.activeSamplers.has(group)) continue;
        const val = el[elName]?.value || SAMPLER_DEFAULTS[key];
        const paramName = key.replace('sampler_', '');
        const num = INT_PARAMS.has(paramName)
            ? parseInt(val, 10) : parseFloat(val);
        if (!isNaN(num)) samplers[paramName] = num;
    }
    if (samplers.seed === -1) samplers.seed = Math.floor(Math.random() * 2147483647);

    // Enforce strict user/assistant alternation (required by many LLM backends).
    // 1. Merge consecutive same-role messages.
    // 2. If the first non-system message is assistant, fold it into the system message
    //    (or prepend a placeholder user message if there's no system message).
    // 3. If alternation is still broken, merge adjacent same-role pairs.
    const enforceAlternation = (arr) => {
        // Step 1: merge consecutive same-role
        let out = [];
        for (const msg of arr) {
            if (out.length > 0 && out[out.length - 1].role === msg.role) {
                out[out.length - 1].content += '\n\n' + msg.content;
            } else {
                out.push({ ...msg });
            }
        }

        // Step 2: first non-system message must be 'user'
        const firstNonSys = out.findIndex(m => m.role !== 'system');
        if (firstNonSys !== -1 && out[firstNonSys].role === 'assistant') {
            if (firstNonSys > 0 && out[firstNonSys - 1].role === 'system') {
                // Fold greeting into the system message
                out[firstNonSys - 1].content += '\n\n[Character Greeting]\n' + out[firstNonSys].content;
                out.splice(firstNonSys, 1);
            } else {
                // No system message — prepend a minimal user turn
                out.splice(firstNonSys, 0, { role: 'user', content: '[Start]' });
            }
        }

        // Step 3: final pass — merge any remaining violations
        const final = [];
        for (const msg of out) {
            if (final.length > 0 && final[final.length - 1].role === msg.role) {
                final[final.length - 1].content += '\n\n' + msg.content;
            } else {
                final.push(msg);
            }
        }
        return final;
    };

    return { model: state.apiModel || '', messages: enforceAlternation(messages), ...samplers };
}

/** Preview helper — returns the same payload buildChatPayload would send right now. */
export function previewChatPayload() {
    return buildChatPayload(0, null);
}

/**
 * Generate a character response via the LLM.
 * @param {number} [excludeLastN=0] — drop last N messages for regen
 */
export async function generateResponse(excludeLastN = 0, onToken = null, signal = null, nudge = null) {
    const payload = buildChatPayload(excludeLastN, nudge);
    if (!payload.model) throw new Error('No model configured \u2014 check Settings');
    if (payload.messages.length === 0) throw new Error('No messages to send');
    if (onToken) return API.streamChatCompletion(payload, onToken, signal);
    return API.chatCompletion(payload);
}
