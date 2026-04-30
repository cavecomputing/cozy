import { state, el, icons, llm } from './state.js';
import { API } from './api.js';
import { syslogStamp, showToast, updateComposerState } from './utils.js';
import { renderMessages, appendMessage } from './messages.js';
import { savePrefs } from './utils.js';
import { renderLorebookFlyout, renderLorebookNotice } from './lorebooks.js';

// ═══════════════════════════════════════════════════════════════════════════
// SIDEBAR — CHAT LIST
// ═══════════════════════════════════════════════════════════════════════════
export function renderChats() {
    el.flyoutChatList.innerHTML = '';

    if (!state.activeCharacter || state.chats.length === 0) return;

    state.chats.forEach(chat => {
        el.flyoutChatList.appendChild(buildChatItem(chat));
    });
}

function buildChatItem(chat) {
    const li = document.createElement('li');
    li.className = `chat-item${chat.id === state.activeChat?.id ? ' active' : ''}`;
    li.dataset.chatId = chat.id;

    const nameSpan = document.createElement('span');
    nameSpan.className = 'chat-name';
    nameSpan.textContent = chat.name;

    // Double-click the name to rename
    nameSpan.addEventListener('dblclick', e => {
        e.stopPropagation();
        startChatRename(li, chat);
    });

    const actions = document.createElement('div');
    actions.className = 'chat-item-actions';
    actions.innerHTML = `
        <button class="icon-btn chat-export-btn" title="Export chat" aria-label="Export chat">${icons.DOWNLOAD}</button>
        <button class="icon-btn chat-rename-btn" title="Rename chat" aria-label="Rename chat">${icons.PENCIL}</button>
        <button class="icon-btn chat-delete-btn" title="Delete chat" aria-label="Delete chat">${icons.TRASH}</button>
    `;

    li.append(nameSpan, actions);
    return li;
}

/** Replace the name span with an inline input for renaming. */
export function startChatRename(li, chat) {
    const nameSpan = li.querySelector('.chat-name');
    if (!nameSpan || li.querySelector('.chat-rename-input')) return; // already renaming

    const original = nameSpan.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'chat-rename-input';
    input.value = original;

    nameSpan.replaceWith(input);
    input.focus();
    input.select();

    let committed = false;

    const commit = async (save) => {
        if (committed) return;
        committed = true;

        const newName = save ? input.value.trim() : original;
        const finalName = newName || original;

        // Swap input back to span
        const newSpan = document.createElement('span');
        newSpan.className = 'chat-name';
        newSpan.textContent = finalName;
        newSpan.addEventListener('dblclick', e => { e.stopPropagation(); startChatRename(li, chat); });
        input.replaceWith(newSpan);

        if (save && finalName !== original) {
            try {
                const updated = await API.renameChat(chat.id, finalName);
                const idx = state.chats.findIndex(c => c.id === chat.id);
                if (idx >= 0) state.chats[idx] = updated;
                if (state.activeChat?.id === chat.id) state.activeChat = updated;
                li.dataset.chatId = updated.id;
                showToast('Chat renamed', 'success');
            } catch (err) {
                newSpan.textContent = original;
                console.error('Rename failed:', err);
            }
        }
    };

    input.addEventListener('keydown', e => {
        if (e.key === 'Enter')  { e.preventDefault(); commit(true); }
        if (e.key === 'Escape') { e.preventDefault(); commit(false); }
    });
    input.addEventListener('blur', () => commit(true));
    input.addEventListener('click', e => e.stopPropagation()); // don't fire chat select
}

export async function loadChats(charId) {
    try {
        state.chats = await API.getChats(charId);
        renderChats();

        if (state.chats.length === 0) {
            // Auto-create a first chat for a new character
            await createNewChat(/*autoSelect=*/true, /*silent=*/true);
        } else {
            // Restore last chat or pick most recent
            const target = state._savedChatId
                ? state.chats.find(c => c.id === state._savedChatId)
                : null;
            await selectChat(target ?? state.chats[state.chats.length - 1]);
        }
    } catch (err) {
        console.error('Could not load chats:', err);
    }
}

export async function selectChat(chat) {
    if (llm.abortController) llm.abortController.abort();

    state.activeChat   = chat;
    state.greetingIndex = 0;    // reset greeting switcher for each new chat
    updateComposerState();

    // Highlight active item
    document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
    document.querySelector(`.chat-item[data-chat-id="${chat.id}"]`)?.classList.add('active');

    // Load messages from DB
    try {
        const rows = await API.getMessages(chat.id);
        // Normalise DB rows to the shape appendMessage expects
        state.messages = rows.map(r => {
            const swipes = r.swipes || [{ content: r.content }];
            // Find the swipe matching the message's saved content; fall back to first
            let idx = swipes.findIndex(s => s.content === r.content);
            if (idx === -1) idx = 0;
            // Build persona snapshot from DB join data
            const persona = r.persona_id ? {
                name: r.persona_name || 'You',
                avatar_url: r.persona_avatar_url || null,
            } : null;
            return {
                role: r.role, text: r.content, id: r.id,
                created_at: r.created_at, swipes, activeSwipeIndex: idx,
                persona,
            };
        });
    } catch (err) {
        state.messages = [];
        console.error('Could not load messages:', err);
    }

    // Seed the character's greeting into the DB if the chat is brand-new
    if (state.messages.length === 0 && state.activeCharacter) {
        const char = state.activeCharacter;
        const greeting = char.first_mes;
        if (greeting) {
            try {
                const saved = await API.addMessage(chat.id, 'character', greeting);
                const altGreetings = Array.isArray(char.alternate_greetings) ? char.alternate_greetings.filter(Boolean) : [];
                // Seed alternate greetings as swipes (backend auto-seeds original as first swipe)
                for (const ag of altGreetings) {
                    await API.addSwipe(saved.id, ag).catch(err => console.error('Could not seed alt greeting:', err));
                }
                // Restore message content to original greeting (addSwipe updates it to the last swipe)
                if (altGreetings.length > 0) {
                    await API.updateMessage(saved.id, greeting).catch(() => {});
                }
                const swipes = [{ content: greeting }, ...altGreetings.map(ag => ({ content: ag }))];
                state.messages.push({
                    role: saved.role, text: greeting, id: saved.id,
                    created_at: saved.created_at,
                    swipes,
                    activeSwipeIndex: 0,
                });
            } catch (err) {
                console.error('Could not seed greeting:', err);
            }
        }
    }

    renderMessages();
    updateComposerState();
    renderLorebookFlyout();
    renderLorebookNotice();
    savePrefs();
}

export async function createNewChat(autoSelect = true, silent = false) {
    if (!state.activeCharacter) return;
    const name = syslogStamp();
    try {
        const chat = await API.createChat(state.activeCharacter.id, name);
        state.chats.push(chat);
        renderChats();
        if (autoSelect) await selectChat(chat);
        return chat;
    } catch (err) {
        if (!silent) showToast('Could not create chat: ' + err.message, 'error');
    }
}

export async function deleteChat(chatId) {
    if (!confirm('Delete this chat and all its messages? This cannot be undone.')) return;
    try {
        await API.deleteChat(chatId);
        const idx = state.chats.findIndex(c => c.id === chatId);
        if (idx >= 0) state.chats.splice(idx, 1);

        if (state.activeChat?.id === chatId) {
            state.activeChat = null;
            state.messages   = [];
            updateComposerState();
            if (state.chats.length > 0) {
                await selectChat(state.chats[state.chats.length - 1]);
            } else {
                renderMessages();
                await createNewChat(true, true);
            }
        }
        renderChats();
        showToast('Chat deleted', 'success');
    } catch (err) {
        showToast('Could not delete chat: ' + err.message, 'error');
    }
}
