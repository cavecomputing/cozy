// ═══════════════════════════════════════════════════════════════════════════
// API LAYER
// ═══════════════════════════════════════════════════════════════════════════
export async function apiError(r, fallback) {
    try { const e = await r.json(); return e.error || fallback; } catch { return fallback; }
}

export const API = {
    // Characters
    async getCharacters() {
        const r = await fetch('/api/characters');
        if (!r.ok) throw new Error('Failed to load characters');
        return r.json();
    },
    async createCharacter(data, imageFile) {
        const fd = new FormData();
        fd.append('data', JSON.stringify(data));
        fd.append('image', imageFile);
        const r = await fetch('/api/characters', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await apiError(r, 'Create failed'));
        return r.json();
    },
    async updateCharacter(id, data) {
        const r = await fetch(`/api/characters/${id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Update failed'));
        return r.json();
    },
    async deleteCharacter(id) {
        const r = await fetch(`/api/characters/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await apiError(r, 'Delete failed'));
        return r.json();
    },
    async uploadAvatar(id, file) {
        const fd = new FormData();
        fd.append('avatar', file);
        const r = await fetch(`/api/characters/${id}/avatar`, { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await apiError(r, 'Upload failed'));
        return r.json();
    },
    async importCard(file) {
        const fd = new FormData();
        fd.append('file', file);
        const r = await fetch('/api/characters/import', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await apiError(r, 'Import failed'));
        return r.json();
    },
    exportCard(id, name, fmt = 'json') {
        // Trigger a browser download — fmt is 'json' or 'png'
        const safeName = (name || 'character').replace(/[\\/:*?"<>|]/g, '_');
        const ext      = fmt === 'png' ? 'png' : 'json';
        const a = document.createElement('a');
        a.href     = `/api/characters/${id}/export?fmt=${fmt}`;
        a.download = `${safeName}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    },

    // Chats
    async getChats(charId) {
        const r = await fetch(`/api/characters/${charId}/chats`);
        if (!r.ok) throw new Error('Failed to load chats');
        return r.json();
    },
    async createChat(charId, name) {
        const r = await fetch(`/api/characters/${charId}/chats`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Chat create failed'));
        return r.json();
    },
    async renameChat(chatId, name) {
        const r = await fetch(`/api/chats/${chatId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Rename failed'));
        return r.json();
    },
    async updateChat(chatId, fields) {
        const r = await fetch(`/api/chats/${chatId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(fields),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Update failed'));
        return r.json();
    },
    async deleteChat(chatId) {
        const r = await fetch(`/api/chats/${chatId}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await apiError(r, 'Delete failed'));
        return r.json();
    },

    // Messages
    async getMessages(chatId) {
        const r = await fetch(`/api/chats/${chatId}/messages`);
        if (!r.ok) throw new Error('Failed to load messages');
        return r.json();
    },
    async addMessage(chatId, role, content, personaId = null) {
        const payload = { role, content };
        if (personaId != null) payload.persona_id = personaId;
        const r = await fetch(`/api/chats/${chatId}/messages`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Message save failed'));
        return r.json();
    },
    async addSwipe(msgId, content) {
        const r = await fetch(`/api/messages/${msgId}/swipes`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Swipe save failed'));
        return r.json();
    },

    // Personas
    async getPersonas() {
        const r = await fetch('/api/personas');
        if (!r.ok) throw new Error('Failed to load personas');
        return r.json();
    },
    async createPersona(data) {
        const r = await fetch('/api/personas', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Create failed'));
        return r.json();
    },
    async updatePersona(id, data) {
        const r = await fetch(`/api/personas/${id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Update failed'));
        return r.json();
    },
    async deletePersona(id) {
        const r = await fetch(`/api/personas/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await apiError(r, 'Delete failed'));
        return r.json();
    },
    async chatCompletion(payload) {
        const r = await fetch('/api/llm/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        let body;
        try { body = await r.json(); } catch { throw new Error('LLM request failed'); }
        if (!r.ok || !body.ok) throw new Error(body.error || 'LLM request failed');
        return body.reply;
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

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (line === 'data: [DONE]' || !line.startsWith('data: ')) continue;
                try {
                    const json = JSON.parse(line.slice(6));
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
                } catch (e) {
                    if (e.message && !e.message.startsWith('Unexpected')) throw e;
                }
            }
        }
        // Build final combined text
        let fullText = '';
        if (reasoning) fullText += '<think>' + reasoning + '</think>';
        fullText += content;
        return fullText;
    },
    async updateMessage(msgId, content) {
        const r = await fetch(`/api/messages/${msgId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Update failed'));
        return r.json();
    },
    async deleteMessage(msgId) {
        const r = await fetch(`/api/messages/${msgId}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await apiError(r, 'Delete failed'));
        return r.json();
    },

    // Presets
    async getPresets() {
        const r = await fetch('/api/presets');
        if (!r.ok) throw new Error('Failed to load presets');
        return r.json();
    },
    async createPreset(data) {
        const r = await fetch('/api/presets', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Create preset failed'));
        return r.json();
    },
    async updatePreset(id, data) {
        const r = await fetch(`/api/presets/${id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Update preset failed'));
        return r.json();
    },
    async deletePreset(id) {
        const r = await fetch(`/api/presets/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await apiError(r, 'Delete preset failed'));
        return r.json();
    },
    async activatePreset(id) {
        const r = await fetch(`/api/presets/${id}/activate`, { method: 'POST' });
        if (!r.ok) throw new Error(await apiError(r, 'Activate preset failed'));
        return r.json();
    },

    async uploadPersonaAvatar(id, file) {
        const fd = new FormData();
        fd.append('avatar', file);
        const r = await fetch(`/api/personas/${id}/avatar`, { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await apiError(r, 'Upload failed'));
        return r.json();
    },

    // Lorebooks (standalone DB-backed)
    async getLorebooks() {
        const r = await fetch('/api/lorebooks');
        if (!r.ok) throw new Error('Failed to load lorebooks');
        return r.json();
    },
    async getLorebook(id) {
        const r = await fetch(`/api/lorebooks/${id}`);
        if (!r.ok) throw new Error(await apiError(r, 'Failed to load lorebook'));
        return r.json();
    },
    async createLorebook(data) {
        const r = await fetch('/api/lorebooks', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Create lorebook failed'));
        return r.json();
    },
    async updateLorebook(id, data) {
        const r = await fetch(`/api/lorebooks/${id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(await apiError(r, 'Update lorebook failed'));
        return r.json();
    },
    async deleteLorebook(id) {
        const r = await fetch(`/api/lorebooks/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await apiError(r, 'Delete lorebook failed'));
        return r.json();
    },
    async embedLorebookInCharacter(bookId, charId, deleteStandalone = false) {
        const qs = deleteStandalone ? '?delete_standalone=1' : '';
        const r = await fetch(`/api/lorebooks/${bookId}/embed-in-character/${charId}${qs}`, {
            method: 'POST',
        });
        if (!r.ok) throw new Error(await apiError(r, 'Embed failed'));
        return r.json();
    },
    async extractCharacterLorebook(charId, clearEmbedded = false) {
        const qs = clearEmbedded ? '?clear_embedded=1' : '';
        const r = await fetch(`/api/characters/${charId}/extract-lorebook${qs}`, {
            method: 'POST',
        });
        if (!r.ok) throw new Error(await apiError(r, 'Extract failed'));
        return r.json();
    },
    async importLorebook(file, name) {
        const fd = new FormData();
        fd.append('file', file);
        if (name) fd.append('name', name);
        const r = await fetch('/api/lorebooks/import', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await apiError(r, 'Import failed'));
        return r.json();
    },
    exportLorebookUrl(id) {
        return `/api/lorebooks/${id}/export`;
    },
    exportCharacterLorebookUrl(charId) {
        return `/api/characters/${charId}/export-lorebook`;
    },
};
