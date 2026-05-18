import { state } from './state.js';
import { downloadUrl, sanitizeFilename } from './utils.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT EXPORT
// ═══════════════════════════════════════════════════════════════════════════
export function exportChat(chatId) {
    const chat = state.chats.find(c => c.id === chatId);
    if (!chat) return;
    downloadUrl(`/api/chats/${chatId}/export`, `${sanitizeFilename(chat.name || 'chat')}.jsonl`);
}
