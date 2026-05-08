import { state, el } from './state.js';
import { autoResize } from './utils.js';

const DRAFT_PREFIX = 'cozy/draft/';

function draftKey(chatId = state.activeChat?.id) {
    return chatId ? `${DRAFT_PREFIX}${chatId}` : null;
}

export function saveDraft() {
    const key = draftKey();
    if (!key || !el.userInput) return;
    const text = el.userInput.value;
    if (text) localStorage.setItem(key, text);
    else localStorage.removeItem(key);
}

export function restoreDraft() {
    if (!el.userInput) return;
    const key = draftKey();
    el.userInput.value = key ? (localStorage.getItem(key) || '') : '';
    autoResize(el.userInput);
}

export function clearDraft(chatId = state.activeChat?.id) {
    const key = draftKey(chatId);
    if (key) localStorage.removeItem(key);
}
