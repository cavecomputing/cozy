import { state, el, icons } from './state.js';
import { API } from './api.js';
import { confirmDialog } from './confirm.js';
import { applyAvatar, AVATAR, compareCharacters, showToast, updateComposerState, showEmptyState, savePrefs, closeMobileSidebar } from './utils.js';
import { loadChats, renderChats } from './chats.js';
import { renderMessages, flushEdit } from './messages.js';

// ═══════════════════════════════════════════════════════════════════════════
// SIDEBAR — CHARACTER LIST
// ═══════════════════════════════════════════════════════════════════════════
export function renderCharList() {
    // The one place the list is ordered. Sorting state in place keeps its order
    // the displayed one, which the startup and after-delete picks rely on.
    state.characters.sort(compareCharacters);
    closeCharMenu();   // its row is about to be replaced
    el.charList.innerHTML = '';
    if (state.characters.length === 0) {
        const li = document.createElement('li');
        li.className = 'char-list-empty';
        li.innerHTML = `
            <span>Create or import a character to start chatting.</span>
            <button type="button" class="btn btn-secondary btn-sm char-list-create-btn">Create Character</button>
        `;
        el.charList.appendChild(li);
        return;
    }
    state.characters.forEach(char => {
        const li = document.createElement('li');
        li.className = `char-item${char.id === state.activeCharacter?.id ? ' active' : ''}${char.missing ? ' missing' : ''}${char.pinned ? ' pinned' : ''}`;
        li.dataset.charId = char.id;

        const selectBtn = document.createElement('button');
        selectBtn.type = 'button';
        selectBtn.className = 'char-select-btn';
        selectBtn.disabled = !!char.missing;
        selectBtn.setAttribute('aria-label', char.missing ? `${char.name} is missing` : `Select ${char.name}`);
        if (char.id === state.activeCharacter?.id) {
            selectBtn.setAttribute('aria-current', 'true');
        }

        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'avatar';
        applyAvatar(avatarDiv, char, '?', AVATAR.SM);

        const nameSpan = document.createElement('span');
        nameSpan.className = 'char-name';
        nameSpan.textContent = char.missing ? `${char.name} (missing)` : char.name;
        nameSpan.title = nameSpan.textContent;
        selectBtn.append(avatarDiv, nameSpan);

        const actions = document.createElement('div');
        actions.className = 'char-item-actions';
        const pinIcon = char.pinned ? icons.STAR_FILLED : icons.STAR;
        const pinTitle = char.pinned ? 'Unpin character' : 'Pin character';
        const menuBtn = `<button class="icon-btn char-menu-btn" title="More actions" aria-label="More actions" aria-haspopup="menu" aria-expanded="false">${icons.MORE}</button>`;
        if (char.missing) {
            actions.innerHTML = menuBtn;
        } else {
            actions.innerHTML = `
                <button class="icon-btn char-pin-btn" title="${pinTitle}" aria-label="${pinTitle}">${pinIcon}</button>
                ${menuBtn}
            `;
        }

        li.append(selectBtn, actions);
        el.charList.appendChild(li);
    });
}

/** Close the row menu. Returns true when it was open, so Escape knows to stop. */
export function closeCharMenu() {
    if (el.charRowMenu.hidden) return false;
    el.charRowMenu.hidden = true;
    el.charList.querySelector('.char-menu-btn[aria-expanded="true"]')?.setAttribute('aria-expanded', 'false');
    return true;
}

/** Open the row menu for `char` beside its ⋯ button, or close it when that
 *  row's menu is the one already showing. */
export function toggleCharMenu(trigger, char) {
    const menu = el.charRowMenu;
    const sameRow = !menu.hidden && menu.dataset.charId === String(char.id);
    closeCharMenu();
    if (sameRow) return;

    // A card whose file is missing can only be deleted.
    menu.querySelectorAll('[data-row-edit]').forEach(li => { li.hidden = !!char.missing; });
    menu.dataset.charId = char.id;
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    // Safari doesn't focus a clicked button, and Escape is only caught while
    // focus is inside the sidebar. No scroll: a scroll closes the menu.
    trigger.focus({ preventScroll: true });

    // Right edges aligned under the ⋯, or above it when the sidebar has no
    // room left below. Coordinates are the sidebar's: the menu is its child.
    const box = el.sidebar.getBoundingClientRect();
    const at = trigger.getBoundingClientRect();
    const fitsBelow = at.bottom + 4 + menu.offsetHeight <= box.bottom;
    const top = fitsBelow ? at.bottom + 4 : at.top - 4 - menu.offsetHeight;
    menu.style.top = `${top - box.top}px`;
    menu.style.right = `${box.right - at.right}px`;
}

function clearActiveCharacterState() {
    state.activeCharacter = null;
    state.chats = [];
    state.activeChat = null;
    state.messages = [];
    el.currentCharName.textContent = 'Cozy';
    el.chatHistory.querySelectorAll('.message-container').forEach(c => c.remove());
    updateComposerState();
    renderChats();
    renderMessages();
}

export async function loadCharacters() {
    try {
        state.characters = await API.getCharacters();
        renderCharList();
        const available = state.characters.filter(c => !c.missing);
        if (available.length === 0) {
            clearActiveCharacterState();
            showEmptyState('No characters yet', 'Create a character to start your first conversation.', true);
            savePrefs();
            return;
        }
        const target = (
            state._savedActiveId
                ? available.find(c => c.id === state._savedActiveId)
                : null
        ) || available[0];
        if (target) await selectCharacter(target.id);
    } catch (err) {
        console.error('Could not load characters:', err);
        showToast('Failed to load characters: ' + err.message);
    }
}

export async function selectCharacter(charId) {
    const char = state.characters.find(c => c.id === charId);
    if (!char || char.missing) return;

    // Before the message list is torn down below — selectChat's own flush comes
    // too late for a character switch.
    flushEdit();
    closeMobileSidebar();

    state.activeCharacter = char;
    state.activeChat = null;
    el.currentCharName.textContent = char.name;
    updateComposerState();

    document.querySelectorAll('.char-item').forEach(i => {
        i.classList.remove('active');
        i.querySelector('.char-select-btn')?.removeAttribute('aria-current');
    });
    const activeItem = document.querySelector(`.char-item[data-char-id="${charId}"]`);
    activeItem?.classList.add('active');
    activeItem?.querySelector('.char-select-btn')?.setAttribute('aria-current', 'true');

    // Clear current chat view while chats load
    state.chats    = [];
    state.activeChat = null;
    state.messages  = [];
    el.chatHistory.querySelectorAll('.message-container').forEach(c => c.remove());

    await loadChats(charId);
    savePrefs();
}

export async function deleteCharacter(charId, name) {
    const label = name || 'this character';
    const ok = await confirmDialog({
        title: `Delete ${label}?`,
        message: 'All of their chats will be deleted too. This cannot be undone.',
    });
    if (!ok) return false;
    try {
        await API.deleteCharacter(charId);
        showToast('Character deleted', 'success');
        state.characters = state.characters.filter(c => c.id !== charId);
        if (state.activeCharacter?.id === charId) {
            clearActiveCharacterState();
            const next = state.characters.find(c => !c.missing);
            if (next) await selectCharacter(next.id);
        }
        renderCharList();
        return true;
    } catch (err) {
        showToast('Could not delete character: ' + err.message, 'error');
        return false;
    }
}
