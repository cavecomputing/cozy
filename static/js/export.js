import { state } from './state.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT EXPORT
// ═══════════════════════════════════════════════════════════════════════════
export function exportChat(chatId) {
    const chat = state.chats.find(c => c.id === chatId);
    if (!chat) return;
    const a = document.createElement('a');
    a.href = `/api/chats/${chatId}/export`;
    a.download = `${(chat.name || 'chat').replace(/[\\/:*?"<>|]/g, '_')}.jsonl`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}
