// ═══════════════════════════════════════════════════════════════════════════
// API LAYER
// ═══════════════════════════════════════════════════════════════════════════
import { downloadUrl, sanitizeFilename } from './utils.js';

export async function apiError(r, fallback) {
    try { const e = await r.json(); return e.error || fallback; } catch { return fallback; }
}

async function jsonRequest(url, { method = 'GET', body, fallback = 'Request failed' } = {}) {
    const options = { method };
    if (body !== undefined) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify(body);
    }
    const r = await fetch(url, options);
    if (!r.ok) throw new Error(await apiError(r, fallback));
    return r.json();
}

async function formRequest(url, fields, fallback) {
    const fd = new FormData();
    for (const [key, value] of Object.entries(fields)) {
        if (value != null) fd.append(key, value);
    }
    const r = await fetch(url, { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await apiError(r, fallback));
    return r.json();
}

export const API = {
    // Characters
    async getCharacters() {
        return jsonRequest('/api/characters', { fallback: 'Failed to load characters' });
    },
    async createCharacter(data, imageFile) {
        return formRequest('/api/characters', {
            data: JSON.stringify(data),
            image: imageFile,
        }, 'Create failed');
    },
    async updateCharacter(id, data) {
        return jsonRequest(`/api/characters/${id}`, {
            method: 'PUT',
            body: data,
            fallback: 'Update failed',
        });
    },
    async deleteCharacter(id) {
        return jsonRequest(`/api/characters/${id}`, { method: 'DELETE', fallback: 'Delete failed' });
    },
    async toggleCharacterPin(id) {
        return jsonRequest(`/api/characters/${id}/pin`, { method: 'POST', fallback: 'Pin toggle failed' });
    },
    async uploadAvatar(id, file) {
        return formRequest(`/api/characters/${id}/avatar`, { avatar: file }, 'Upload failed');
    },
    async importCard(file) {
        return formRequest('/api/characters/import', { file }, 'Import failed');
    },
    // Import onto an existing character — replaces its card, keeps id and chats
    async importOverCard(id, file) {
        return formRequest(`/api/characters/${id}/import`, { file }, 'Import failed');
    },
    exportCard(id, name, fmt = 'json') {
        // Trigger a browser download — fmt is 'json' or 'png'
        const safeName = sanitizeFilename(name || 'character');
        const ext      = fmt === 'png' ? 'png' : 'json';
        downloadUrl(`/api/characters/${id}/export?fmt=${fmt}`, `${safeName}.${ext}`);
    },
    // Chats
    async getChats(charId) {
        return jsonRequest(`/api/characters/${charId}/chats`, { fallback: 'Failed to load chats' });
    },
    async createChat(charId, name) {
        return jsonRequest(`/api/characters/${charId}/chats`, {
            method: 'POST',
            body: { name },
            fallback: 'Chat create failed',
        });
    },
    async renameChat(chatId, name) {
        return jsonRequest(`/api/chats/${chatId}`, {
            method: 'PUT',
            body: { name },
            fallback: 'Rename failed',
        });
    },
    async updateChat(chatId, fields) {
        return jsonRequest(`/api/chats/${chatId}`, {
            method: 'PUT',
            body: fields,
            fallback: 'Update failed',
        });
    },
    async deleteChat(chatId) {
        return jsonRequest(`/api/chats/${chatId}`, { method: 'DELETE', fallback: 'Delete failed' });
    },
    async importChat(characterId, file) {
        return formRequest(`/api/chats/import?character_id=${characterId}`, { file }, 'Import failed');
    },
    async forkChat(chatId, messageId) {
        return jsonRequest(`/api/chats/${chatId}/fork?message_id=${messageId}`, {
            method: 'POST',
            fallback: 'Fork failed',
        });
    },

    // Auto Summaries
    async runSummary(chatId, { up_to_msg_id = null, rebuild = false } = {}) {
        const r = await fetch(`/api/chats/${chatId}/summary/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ up_to_msg_id, rebuild }),
        });
        // 202 = started. Only an explicitly already-running 409 is joinable;
        // other conflicts must remain visible to the caller.
        if (r.status === 202) return r.json();
        if (r.status === 409) {
            const body = await r.json().catch(() => ({}));
            if (body.already_running && body.summary_status) return body;
            throw new Error(body.error || 'Summary run failed');
        }
        if (!r.ok) throw new Error(await apiError(r, 'Summary run failed'));
        return r.json();
    },
    async getSummaryStatus(chatId) {
        return jsonRequest(`/api/chats/${chatId}/summary/status`, { fallback: 'Failed to load summary status' });
    },
    async resetSummary(chatId) {
        return jsonRequest(`/api/chats/${chatId}/summary/reset`, { method: 'POST', fallback: 'Summary reset failed' });
    },
    // Messages
    async getMessages(chatId) {
        return jsonRequest(`/api/chats/${chatId}/messages`, { fallback: 'Failed to load messages' });
    },
    async addMessage(chatId, role, content, personaId = null) {
        const payload = { role, content };
        if (personaId != null) payload.persona_id = personaId;
        return jsonRequest(`/api/chats/${chatId}/messages`, {
            method: 'POST',
            body: payload,
            fallback: 'Message save failed',
        });
    },
    async addSwipe(msgId, content) {
        return jsonRequest(`/api/messages/${msgId}/swipes`, {
            method: 'POST',
            body: { content },
            fallback: 'Swipe save failed',
        });
    },

    // Personas
    async getPersonas() {
        return jsonRequest('/api/personas', { fallback: 'Failed to load personas' });
    },
    async createPersona(data) {
        return jsonRequest('/api/personas', {
            method: 'POST',
            body: data,
            fallback: 'Create failed',
        });
    },
    async updatePersona(id, data) {
        return jsonRequest(`/api/personas/${id}`, {
            method: 'PUT',
            body: data,
            fallback: 'Update failed',
        });
    },
    async deletePersona(id) {
        return jsonRequest(`/api/personas/${id}`, { method: 'DELETE', fallback: 'Delete failed' });
    },
    async streamChatCompletion(payload, onToken, signal) {
        const res = await fetch('/api/llm/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal,
        });
        if (!res.ok) {
            let msg = 'LLM request failed';
            try { const b = await res.json(); msg = b.error || msg; } catch {}
            throw new Error(msg);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let reasoning = '';
        let content = '';
        let buffer = '';

        const processLine = (line) => {
            if (line === 'data: [DONE]' || !line.startsWith('data: ')) return;
            let json;
            // Ignore unparseable fragments (e.g. a line truncated mid-stream)
            try { json = JSON.parse(line.slice(6)); } catch { return; }
            if (json.error) throw new Error(json.error);
            const delta = json.choices?.[0]?.delta || {};
            const reasonTok = delta.reasoning_content || delta.reasoning || '';
            const contentTok = delta.content || '';
            if (reasonTok) reasoning += reasonTok;
            if (contentTok) content += contentTok;
            if (reasonTok || contentTok) {
                // Build combined text: wrap reasoning in thinking tags
                let fullText = '';
                if (reasoning) {
                    fullText += '<think>' + reasoning + (content ? '</think>' : '');
                }
                fullText += content;
                onToken(fullText);
            }
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                // Flush any trailing data left in the buffer. A stream that ends
                // without a final newline (e.g. a cut-off llama.cpp response)
                // strands its last `data:` event here; without this the tail of
                // the message is silently dropped. See issue #7.
                buffer += decoder.decode();
                if (buffer) processLine(buffer);
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) processLine(line);
        }
        // Build final combined text
        let fullText = '';
        if (reasoning) fullText += '<think>' + reasoning + '</think>';
        fullText += content;
        return fullText;
    },
    // `updateSwipe` rewrites the matching swipe row too (message edits);
    // swipe *selection* leaves it false so existing swipes aren't overwritten.
    async updateMessage(msgId, content, updateSwipe = false) {
        const body = { content };
        if (updateSwipe) body.update_swipe = true;
        return jsonRequest(`/api/messages/${msgId}`, {
            method: 'PUT',
            body,
            fallback: 'Update failed',
        });
    },
    async deleteMessage(msgId) {
        return jsonRequest(`/api/messages/${msgId}`, { method: 'DELETE', fallback: 'Delete failed' });
    },

    // Settings
    async getSettings() {
        return jsonRequest('/api/settings', { fallback: 'Failed to load settings' });
    },
    async saveSettings(fields) {
        return jsonRequest('/api/settings', {
            method: 'PUT',
            body: fields,
            fallback: 'Failed to save settings',
        });
    },
    async getStorageStats() {
        return jsonRequest('/api/storage-stats', { fallback: 'Failed to load storage statistics' });
    },

    // System prompts
    async getSystemPrompts() {
        return jsonRequest('/api/system-prompts', { fallback: 'Failed to load prompts' });
    },
    async createSystemPrompt(data) {
        return jsonRequest('/api/system-prompts', {
            method: 'POST',
            body: data,
            fallback: 'Create prompt failed',
        });
    },
    async updateSystemPrompt(id, data) {
        return jsonRequest(`/api/system-prompts/${id}`, {
            method: 'PUT',
            body: data,
            fallback: 'Update prompt failed',
        });
    },
    async deleteSystemPrompt(id) {
        return jsonRequest(`/api/system-prompts/${id}`, {
            method: 'DELETE',
            fallback: 'Delete prompt failed',
        });
    },
    async importSystemPrompt(file) {
        return formRequest('/api/system-prompts/import', { file }, 'Import failed');
    },
    async getDefaultPromptTemplates() {
        return jsonRequest('/api/system-prompts/default-template', {
            fallback: 'Failed to load default template',
        });
    },

    // Regex presets
    async getRegexPresets() {
        return jsonRequest('/api/regex-presets', { fallback: 'Failed to load regex presets' });
    },
    async createRegexPreset(data) {
        return jsonRequest('/api/regex-presets', {
            method: 'POST',
            body: data,
            fallback: 'Create regex preset failed',
        });
    },
    async updateRegexPreset(id, data) {
        return jsonRequest(`/api/regex-presets/${id}`, {
            method: 'PUT',
            body: data,
            fallback: 'Update regex preset failed',
        });
    },
    async deleteRegexPreset(id) {
        return jsonRequest(`/api/regex-presets/${id}`, {
            method: 'DELETE',
            fallback: 'Delete regex preset failed',
        });
    },
    async importRegexPreset(file) {
        return formRequest('/api/regex-presets/import', { file }, 'Import failed');
    },

    // LLM utility endpoints
    async getModels(profile = 'main') {
        const query = profile === 'main' ? '' : `?profile=${encodeURIComponent(profile)}`;
        return jsonRequest(`/api/llm/models${query}`, { fallback: 'Failed to fetch models' });
    },
    async testLLM() {
        return jsonRequest('/api/llm/test', { method: 'POST', fallback: 'Connection test failed' });
    },

    // Presets
    async getPresets() {
        return jsonRequest('/api/presets', { fallback: 'Failed to load presets' });
    },
    async createPreset(data) {
        return jsonRequest('/api/presets', {
            method: 'POST',
            body: data,
            fallback: 'Create preset failed',
        });
    },
    async updatePreset(id, data) {
        return jsonRequest(`/api/presets/${id}`, {
            method: 'PUT',
            body: data,
            fallback: 'Update preset failed',
        });
    },
    async deletePreset(id) {
        return jsonRequest(`/api/presets/${id}`, { method: 'DELETE', fallback: 'Delete preset failed' });
    },
    async activatePreset(id) {
        return jsonRequest(`/api/presets/${id}/activate`, {
            method: 'POST',
            fallback: 'Activate preset failed',
        });
    },

    async uploadPersonaAvatar(id, file) {
        return formRequest(`/api/personas/${id}/avatar`, { avatar: file }, 'Upload failed');
    },

    // Lorebooks (standalone DB-backed)
    async getLorebooks() {
        return jsonRequest('/api/lorebooks', { fallback: 'Failed to load lorebooks' });
    },
    async getLorebook(id) {
        return jsonRequest(`/api/lorebooks/${id}`, { fallback: 'Failed to load lorebook' });
    },
    async createLorebook(data) {
        return jsonRequest('/api/lorebooks', {
            method: 'POST',
            body: data,
            fallback: 'Create lorebook failed',
        });
    },
    async updateLorebook(id, data) {
        return jsonRequest(`/api/lorebooks/${id}`, {
            method: 'PUT',
            body: data,
            fallback: 'Update lorebook failed',
        });
    },
    async deleteLorebook(id) {
        return jsonRequest(`/api/lorebooks/${id}`, { method: 'DELETE', fallback: 'Delete lorebook failed' });
    },
    async embedLorebookInCharacter(bookId, charId, deleteStandalone = false) {
        const qs = deleteStandalone ? '?delete_standalone=1' : '';
        return jsonRequest(`/api/lorebooks/${bookId}/embed-in-character/${charId}${qs}`, {
            method: 'POST',
            fallback: 'Embed failed',
        });
    },
    async extractCharacterLorebook(charId, clearEmbedded = false) {
        const qs = clearEmbedded ? '?clear_embedded=1' : '';
        return jsonRequest(`/api/characters/${charId}/extract-lorebook${qs}`, {
            method: 'POST',
            fallback: 'Extract failed',
        });
    },
    async importLorebook(file, name) {
        return formRequest('/api/lorebooks/import', { file, name }, 'Import failed');
    },
    exportLorebookUrl(id) {
        return `/api/lorebooks/${id}/export`;
    },
    exportCharacterLorebookUrl(charId) {
        return `/api/characters/${charId}/export-lorebook`;
    },
};
