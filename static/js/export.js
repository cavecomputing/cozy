import { state } from './state.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT EXPORT
// ═══════════════════════════════════════════════════════════════════════════
export async function exportChat(chatId) {
    const chat = state.chats.find(c => c.id === chatId);
    if (!chat) return;
    const char = state.activeCharacter;
    const persona = state.activePersona;

    // Use in-memory messages if this is the active chat, otherwise fetch
    let messages;
    if (chatId === state.activeChat?.id) {
        messages = state.messages;
    } else {
        const r = await fetch(`/api/chats/${chatId}/messages`);
        if (!r.ok) return;
        messages = await r.json();
    }
    if (!messages.length) return;

    const lines = messages.map(m => {
        const speaker = m.role === 'user'
            ? (m.persona?.name || m.persona_name || persona?.name || 'User')
            : (char?.name || 'Character');
        return `**${speaker}**: ${m.text || m.content}`;
    });
    const blob = new Blob([lines.join('\n\n')], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${chat.name || 'chat'}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
}
