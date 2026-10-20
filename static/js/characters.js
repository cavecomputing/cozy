import { state, el, icons } from './state.js';
import { API } from './api.js';
import { confirmDialog } from './confirm.js';
import { applyAvatar, AVATAR, compareCharacters, recentCharacters, parseDbStamp, timeAgo, showToast, updateComposerState, showEmptyState, savePrefs, closeMobileSidebar, MOBILE_SHELL_QUERY } from './utils.js';
import { loadChats, renderChats } from './chats.js';
import { renderMessages, flushEdit } from './messages.js';
import { forgetCharacterLorebook } from './lorebooks.js';

// ═══════════════════════════════════════════════════════════════════════════
// SIDEBAR — CHARACTER LIST
// ═══════════════════════════════════════════════════════════════════════════

// Whether the Archived section is unfolded. Not saved: it starts folded on
// every load and unfolds by itself when the character on screen is archived.
let archivedOpen = false;

// Character id → when you last wrote to them, for the Recent block. Kept apart
// from state.characters, whose rows the pin, archive and edit responses replace
// with ones that don't carry it.
const lastChatAt = new Map();

export function renderCharList() {
    // The one place the list is ordered. Sorting state in place keeps its order
    // the displayed one, which the startup and after-delete picks rely on.
    state.characters.sort(compareCharacters);
    closeCharMenu();   // its row is about to be replaced
    el.charList.innerHTML = '';
    // Recent comes first and takes its characters out of the list under it,
    // so nobody is listed twice.
    const recent = recentCharacters(state.characters, lastChatAt);
    el.recentChars.hidden = recent.length === 0;
    el.recentList.replaceChildren(...recent.map(char => charRow(char, lastChatAt.get(char.id))));
    if (state.characters.length === 0) {
        const li = document.createElement('li');
        li.className = 'char-list-empty';
        li.textContent = 'Create or import a character to start chatting.';
        el.charList.appendChild(li);
        return;
    }
    const listed = state.characters.filter(c => !recent.includes(c));
    // Archived characters sort last, so the first one is where their section starts.
    const firstArchived = listed.find(c => c.archived);
    listed.forEach(char => {
        if (char === firstArchived) {
            const toggle = document.createElement('li');
            toggle.className = 'char-archived-toggle';
            toggle.innerHTML = `
                <button type="button" class="char-archived-btn section-label" aria-expanded="${archivedOpen}">
                    ${icons.CHEVRIGHT}<span>Archived (${listed.filter(c => c.archived).length})</span>
                </button>
            `;
            el.charList.appendChild(toggle);
        }
        if (char.archived && !archivedOpen) return;
        el.charList.appendChild(charRow(char));
    });
}

/** One character's row, in Recent or the list: avatar, name, a star if pinned,
 *  when you last wrote to them if `lastWrote` is given, and the ⋯ menu. */
function charRow(char, lastWrote) {
    const active = char.id === state.activeCharacter?.id;
    const li = document.createElement('li');
    li.className = `char-item${active ? ' active' : ''}${char.missing ? ' missing' : ''}${char.archived ? ' archived' : ''}`;
    li.dataset.charId = char.id;

    const selectBtn = document.createElement('button');
    selectBtn.type = 'button';
    selectBtn.className = 'char-select-btn';
    selectBtn.disabled = !!char.missing;
    selectBtn.setAttribute('aria-label', char.missing ? `${char.name} is missing` : `Select ${char.name}`);
    if (active) selectBtn.setAttribute('aria-current', 'true');

    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'avatar';
    applyAvatar(avatarDiv, char, '?', AVATAR.SM);

    const nameSpan = document.createElement('span');
    nameSpan.className = 'char-name';
    nameSpan.textContent = char.missing ? `${char.name} (missing)` : char.name;
    nameSpan.title = nameSpan.textContent;
    selectBtn.append(avatarDiv, nameSpan);
    // A pinned row says so after the name; pinning itself is in the ⋯ menu.
    if (char.pinned && !char.archived) {
        selectBtn.insertAdjacentHTML('beforeend', `<span class="char-pin-mark" title="Pinned" aria-label="Pinned">${icons.STAR_FILLED}</span>`);
    }
    if (lastWrote) {
        const time = document.createElement('span');
        time.className = 'recent-time';
        time.textContent = timeAgo(parseDbStamp(lastWrote));
        selectBtn.append(time);
    }

    const actions = document.createElement('div');
    actions.className = 'char-item-actions';
    actions.innerHTML = `<button class="icon-btn char-menu-btn" title="More actions" aria-label="More actions" aria-haspopup="menu" aria-expanded="false">${icons.MORE}</button>`;

    li.append(selectBtn, actions);
    return li;
}

/** Move Recent's "5 minutes ago" on, in place, so a focused row keeps focus. */
export function refreshRecentTimes() {
    el.recentList.querySelectorAll('.char-item').forEach(li => {
        li.querySelector('.recent-time').textContent =
            timeAgo(parseDbStamp(lastChatAt.get(Number(li.dataset.charId))));
    });
}

/** You just sent this character a message: move them to the top of Recent. */
export function noteChatActivity(charId, stamp) {
    lastChatAt.set(charId, stamp);
    renderCharList();
}

/** Close the row menu. Returns true when it was open, so Escape knows to stop. */
export function closeCharMenu() {
    if (el.charRowMenu.hidden) return false;
    el.charRowMenu.hidden = true;
    el.sidebar.querySelector('.char-menu-btn[aria-expanded="true"]')?.setAttribute('aria-expanded', 'false');
    return true;
}

/** Open the row menu for `char` beside its ⋯ button, or close it when that
 *  row's menu is the one already showing. `fromKeyboard` puts focus on the
 *  first item, the way a menu button opened with Enter or Space should. */
export function toggleCharMenu(trigger, char, fromKeyboard = false) {
    const menu = el.charRowMenu;
    const sameRow = !menu.hidden && menu.dataset.charId === String(char.id);
    closeCharMenu();
    if (sameRow) return;

    // A card whose file is missing has nothing to edit.
    menu.querySelectorAll('[data-row-edit]').forEach(li => { li.hidden = !!char.missing; });
    // Pinning only orders the unarchived list, as the star did.
    menu.querySelector('[data-row-pin]').hidden = !!(char.missing || char.archived);
    menu.querySelector('[data-action="pin"] span').textContent = char.pinned ? 'Unpin' : 'Pin';
    menu.querySelector('[data-action="archive"] span').textContent = char.archived ? 'Unarchive' : 'Archive';
    menu.dataset.charId = char.id;
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    // By pointer, focus stays on the ⋯: Safari doesn't focus a clicked button,
    // and Escape is only caught while focus is inside the sidebar. No scroll
    // either way: a scroll closes the menu.
    const focusTarget = fromKeyboard ? menu.querySelector('li:not([hidden]) > button') : trigger;
    focusTarget.focus({ preventScroll: true });

    // Coordinates are the sidebar's: the menu is its child.
    const box = el.sidebar.getBoundingClientRect();
    const at = trigger.getBoundingClientRect();
    const height = menu.offsetHeight;
    if (window.matchMedia(MOBILE_SHELL_QUERY).matches) {
        // The drawer clips anything past its edge, so the menu drops under the
        // ⋯ with right edges aligned, or sits above it when there's no room.
        const fitsBelow = at.bottom + 4 + height <= box.bottom;
        menu.style.top = `${(fitsBelow ? at.bottom + 4 : at.top - 4 - height) - box.top}px`;
        menu.style.left = 'auto';
        menu.style.right = `${box.right - at.right}px`;
        menu.style.transformOrigin = fitsBelow ? 'top right' : 'bottom right';
    } else {
        // Out past the sidebar's right edge, level with the row, the way the
        // persona switcher flies out: it opens toward the pointer instead of
        // back across the list, and leaves the row it acts on in view.
        const row = trigger.closest('.char-item').getBoundingClientRect();
        menu.style.top = `${Math.min(row.top, box.bottom - height) - box.top}px`;
        menu.style.left = `${box.width + 6}px`;
        menu.style.right = 'auto';
        menu.style.transformOrigin = 'top left';
    }
}

/** Fold the Archived section open or shut, keeping focus on its toggle. */
export function toggleArchivedSection() {
    archivedOpen = !archivedOpen;
    renderCharList();
    el.charList.querySelector('.char-archived-btn')?.focus();
}

/** Archive or unarchive a character. The one on screen stays open when
 *  archived, so the section unfolds with it rather than hiding its row. */
export async function toggleArchived(charId) {
    try {
        const updated = await API.toggleCharacterArchive(charId);
        const idx = state.characters.findIndex(c => c.id === charId);
        if (idx !== -1) state.characters[idx] = updated;
        if (state.activeCharacter?.id === charId) {
            state.activeCharacter = updated;
            if (updated.archived) archivedOpen = true;
        }
        renderCharList();
    } catch (err) {
        showToast('Could not archive character: ' + err.message, 'error');
    }
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
        lastChatAt.clear();
        state.characters.forEach(c => c.last_chat_at && lastChatAt.set(c.id, c.last_chat_at));
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
    // A restored or fallback pick can be archived while its section is folded.
    if (char.archived) archivedOpen = true;
    updateComposerState();
    renderCharList();

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
        forgetCharacterLorebook(charId);
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
