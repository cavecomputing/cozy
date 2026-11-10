import { state, el, icons, llm } from './state.js';
import { API } from './api.js';
import {
    chatStamp, displayChatName, DEFAULT_CHAT_NAME_RE, showToast, updateComposerState, savePrefs, branchesAt,
    parseDbStamp, timeAgo, MOBILE_SHELL_QUERY,
} from './utils.js';
import { confirmDialog } from './confirm.js';
import { renderMessages, appendMessage, flushEdit, revealMessage, refreshBranchPills } from './messages.js';
import { renderLorebookFlyout, loadAuthorNote, flushAuthorNote } from './lorebooks.js';
import { restoreDraft, saveDraft } from './drafts.js';
import { renderPersonaList, updateUserProfile } from './personas.js';
import { updateContextViews } from './context-meter.js';
import { onChatSelected } from './summaries.js';

// ═══════════════════════════════════════════════════════════════════════════
// SIDEBAR — CHAT LIST
// ═══════════════════════════════════════════════════════════════════════════
export function renderChats() {
    closeChatMenu();   // its row is about to be replaced
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

    const selectBtn = buildChatSelectButton(chat);
    if (chat.id === state.activeChat?.id) {
        selectBtn.setAttribute('aria-current', 'true');
    }

    const actions = document.createElement('div');
    actions.className = 'chat-item-actions';
    actions.innerHTML = `<button class="icon-btn chat-menu-btn" title="More actions" aria-label="More actions" aria-haspopup="menu" aria-expanded="false">${icons.MORE}</button>`;

    li.append(selectBtn, actions);
    return li;
}

/** Close the chat rows' menu. Returns true when it was open. */
export function closeChatMenu() {
    if (el.chatRowMenu.hidden) return false;
    el.chatRowMenu.hidden = true;
    const trigger = el.flyoutChatList.querySelector('.chat-menu-btn[aria-expanded="true"]');
    trigger?.setAttribute('aria-expanded', 'false');
    // As closeCharMenu(): a pointer's ⋯ lets go of focus, or it keeps showing.
    if (!el.chatRowMenu.dataset.keyboard && document.activeElement === trigger) trigger.blur();
    return true;
}

/** Open the menu for chat `chatId` from its ⋯, or close it when it is that
 *  row's menu already showing. Works as toggleCharMenu() does, placed in the
 *  chat flyout's coordinates instead of the sidebar's. */
export function toggleChatMenu(trigger, chatId, fromKeyboard = false) {
    const menu = el.chatRowMenu;
    const sameRow = !menu.hidden && menu.dataset.chatId === String(chatId);
    closeChatMenu();
    if (sameRow) return;

    menu.dataset.chatId = chatId;
    menu.dataset.keyboard = fromKeyboard ? '1' : '';
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    (fromKeyboard ? menu.querySelector('button') : trigger).focus({ preventScroll: true });

    const box = el.chatFlyout.getBoundingClientRect();
    const at = trigger.getBoundingClientRect();
    const height = menu.offsetHeight;
    if (window.matchMedia(MOBILE_SHELL_QUERY).matches) {
        // The sheet spans the screen, so the menu drops under the ⋯ with right
        // edges aligned, or sits above it when the sheet's bottom is too near.
        const fitsBelow = at.bottom + 4 + height <= box.bottom;
        menu.style.top = `${(fitsBelow ? at.bottom + 4 : at.top - 4 - height) - box.top}px`;
        menu.style.left = 'auto';
        menu.style.right = `${box.right - at.right}px`;
        menu.style.transformOrigin = fitsBelow ? 'top right' : 'bottom right';
    } else {
        // Out past the flyout's right edge, level with the row, and no lower
        // than the flyout's foot, which sits on the composer.
        const row = trigger.closest('.chat-item').getBoundingClientRect();
        menu.style.top = `${Math.min(row.top, box.bottom - height) - box.top}px`;
        menu.style.left = `${at.right + 6 - box.left}px`;
        menu.style.right = 'auto';
        menu.style.transformOrigin = 'top left';
    }
}

function buildChatSelectButton(chat) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-select-btn';
    button.setAttribute('aria-label', `Select chat ${displayChatName(chat)}`);

    const prefix = document.createElement('span');
    prefix.className = 'chat-prefix';
    prefix.setAttribute('aria-hidden', 'true');
    prefix.textContent = '#';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'chat-name';
    nameSpan.textContent = displayChatName(chat);
    nameSpan.title = nameSpan.textContent;

    // What the name doesn't say: when the chat was last used, and what it
    // branched from. In that order, so a narrow row cuts the branch, not the time.
    const parent = chat.parent_chat_id && state.chats.find(c => c.id === chat.parent_chat_id);
    const meta = document.createElement('span');
    meta.className = 'chat-meta';
    meta.textContent = [
        chat.updated_at && timeAgo(parseDbStamp(chat.updated_at)),
        parent && `branch of ${displayChatName(parent)}`,
    ].filter(Boolean).join(' · ');

    const label = document.createElement('span');
    label.className = 'chat-label';
    label.append(nameSpan, meta);

    // Double-click the name to rename
    button.addEventListener('dblclick', e => {
        e.stopPropagation();
        startChatRename(button.closest('.chat-item'), chat);
    });

    button.append(prefix, label);
    return button;
}

/** Replace the name span with an inline input for renaming. */
export function startChatRename(li, chat) {
    const selectBtn = li.querySelector('.chat-select-btn');
    const nameSpan = li.querySelector('.chat-name');
    if (!selectBtn || !nameSpan || li.querySelector('.chat-rename-input')) return; // already renaming

    const original = chat.name;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'chat-rename-input';
    // Default timestamp names aren't worth editing — start from a blank slate
    input.value = DEFAULT_CHAT_NAME_RE.test(original) ? '' : original;
    input.placeholder = 'Chat name';
    input.setAttribute('aria-label', 'Chat name');

    selectBtn.replaceWith(input);
    input.focus();
    input.select();

    let committed = false;

    const commit = async (save) => {
        if (committed) return;
        committed = true;

        const newName = save ? input.value.trim() : original;
        const finalName = newName || original;

        const buttonChat = { ...chat, name: finalName };
        const newButton = buildChatSelectButton(buttonChat);
        if (chat.id === state.activeChat?.id) {
            newButton.setAttribute('aria-current', 'true');
        }
        input.replaceWith(newButton);

        if (save && finalName !== original) {
            try {
                const updated = await API.renameChat(chat.id, finalName);
                const idx = state.chats.findIndex(c => c.id === chat.id);
                if (idx >= 0) state.chats[idx] = updated;
                if (state.activeChat?.id === chat.id) state.activeChat = updated;
                li.dataset.chatId = updated.id;
                newButton.setAttribute('aria-label', `Select chat ${displayChatName(updated)}`);
                updateComposerState();
                refreshBranchPills();
                showToast('Chat renamed', 'success');
            } catch (err) {
                const restoredButton = buildChatSelectButton(chat);
                if (chat.id === state.activeChat?.id) {
                    restoredButton.setAttribute('aria-current', 'true');
                }
                newButton.replaceWith(restoredButton);
                console.error('Rename failed:', err);
                showToast('Failed to rename chat: ' + err.message);
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
        const chats = await API.getChats(charId);
        // Another character picked while these were on their way owns the
        // screen now; opening one of these chats would put it under them.
        if (state.activeCharacter?.id !== charId) return;
        state.chats = chats;
        renderChats();

        if (state.chats.length === 0) {
            // Auto-create a first chat for a new character
            await createNewChat(/*autoSelect=*/true, /*silent=*/true);
        } else {
            // Resume the chat last opened or written in, on any device. One
            // not opened since that was recorded goes by its last write.
            const usedAt = c => (c.opened_at > c.updated_at ? c.opened_at : c.updated_at) || '';
            await selectChat(state.chats.reduce((last, c) => (usedAt(c) >= usedAt(last) ? c : last)));
        }
    } catch (err) {
        console.error('Could not load chats:', err);
        showToast('Failed to load chats: ' + err.message);
    }
}

export async function selectChat(chat) {
    saveDraft();
    flushEdit();
    flushAuthorNote();
    // A bare abort, not stopGeneration(): leaving a chat discards the partial
    // reply. It belongs to the chat we're leaving, but persistence targets the
    // chat we're entering, so there is nowhere safe to put it.
    if (llm.abortController) llm.abortController.abort();

    state.activeChat = chat;
    API.markChatOpened(chat.id)
        .then(({ opened_at }) => { chat.opened_at = opened_at; })
        .catch(err => console.error('Could not record the open chat:', err));
    loadAuthorNote();
    restoreDraft();
    updateComposerState();

    // Speak as whoever this chat was last spoken by. The persona lives on the
    // chat row precisely so a second machine doesn't answer as whatever persona
    // it last selected; the local preference is only the fallback for chats
    // that have never recorded one, and a persona since deleted falls back too.
    const chatPersona = chat.persona_id
        ? state.personas.find(p => p.id === chat.persona_id)
        : null;
    if (chatPersona && chatPersona.id !== state.activePersona?.id) {
        state.activePersona = chatPersona;
        updateUserProfile();
        renderPersonaList();
    }

    // Highlight active item
    document.querySelectorAll('.chat-item').forEach(i => {
        i.classList.remove('active');
        i.querySelector('.chat-select-btn')?.removeAttribute('aria-current');
    });
    const activeItem = document.querySelector(`.chat-item[data-chat-id="${chat.id}"]`);
    activeItem?.classList.add('active');
    activeItem?.querySelector('.chat-select-btn')?.setAttribute('aria-current', 'true');

    // Load messages from DB
    let messagesLoaded = false;
    try {
        const rows = await API.getMessages(chat.id);
        // A chat picked while these were on their way owns the screen now.
        // Drawn under it, they would also be sent as its history.
        if (state.activeChat?.id !== chat.id) return;
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
        messagesLoaded = true;
    } catch (err) {
        if (state.activeChat?.id !== chat.id) return;
        state.messages = [];
        console.error('Could not load messages:', err);
        showToast('Failed to load messages: ' + err.message);
    }

    // Seed the character's greeting into the DB if the chat is brand-new
    if (messagesLoaded && state.messages.length === 0 && state.activeCharacter) {
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
                if (state.activeChat?.id !== chat.id) return;
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
    updateContextViews();
    onChatSelected();
    savePrefs();
    // Land in the composer, ready to type, as chat apps do on a switch and at
    // startup. Not on touch shells, where focusing throws up the keyboard over
    // the conversation before the user has asked for it.
    if (!el.userInput.disabled && !window.matchMedia('(pointer: coarse)').matches) {
        el.userInput.focus({ preventScroll: true });
    }
}

/**
 * Open the next chat that branches at this message, keeping the message where
 * it sits on screen so the page appears to turn rather than jump.
 */
export async function switchBranch(msgEl) {
    const branches = branchesAt(state.chats, state.activeChat?.id, Number(msgEl.dataset.msgId));
    if (!branches.length) return;
    const at = branches.findIndex(b => b.chatId === state.activeChat.id);
    const next = branches[(at + 1) % branches.length];
    const offset = msgEl.getBoundingClientRect().top - el.chatHistory.getBoundingClientRect().top;
    await selectChat(state.chats.find(c => c.id === next.chatId));
    revealMessage(next.msgId, offset);
}

export async function createNewChat(autoSelect = true, silent = false) {
    if (!state.activeCharacter) return;
    const name = chatStamp();
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

export function importChat() {
    el.flyoutImportChatFile?.click();
}

export async function handleChatImportFile() {
    const file = el.flyoutImportChatFile?.files?.[0];
    if (!file || !state.activeCharacter) return;
    try {
        const imported = await API.importChat(state.activeCharacter.id, file);
        state.chats = await API.getChats(state.activeCharacter.id);
        renderChats();
        const chat = state.chats.find(c => c.id === imported.id) || imported;
        await selectChat(chat);
        if (imported.warnings?.length) {
            showToast(`Imported with warnings:\n${imported.warnings.join('\n')}`, 'error', 9000);
        } else {
            showToast('Chat imported', 'success');
        }
    } catch (err) {
        showToast('Could not import chat: ' + err.message, 'error');
    } finally {
        if (el.flyoutImportChatFile) el.flyoutImportChatFile.value = '';
    }
}

export async function deleteChat(chatId) {
    const chat = state.chats.find(c => c.id === chatId);
    const ok = await confirmDialog({
        title: `Delete ${displayChatName(chat)}?`,
        message: 'All of its messages will be deleted. This cannot be undone.',
    });
    if (!ok) return;
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
        refreshBranchPills();
        showToast('Chat deleted', 'success');
    } catch (err) {
        showToast('Could not delete chat: ' + err.message, 'error');
    }
}
