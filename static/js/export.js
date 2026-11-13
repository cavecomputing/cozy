import { state } from './state.js';
import { downloadUrl } from './utils.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT EXPORT
// ═══════════════════════════════════════════════════════════════════════════
export function exportChat(chatId) {
    if (!state.chats.some(c => c.id === chatId)) return;
    // The server names the file, and its name is the one a browser uses.
    downloadUrl(`/api/chats/${chatId}/export`, '');
}
