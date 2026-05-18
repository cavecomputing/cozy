import { state, el, icons } from './state.js';
import { API } from './api.js';
import { applyAvatar, showToast, updateComposerState, showEmptyState, savePrefs, closeMobileSidebar } from './utils.js';
import { loadChats, renderChats } from './chats.js';
import { renderMessages } from './messages.js';

// ═══════════════════════════════════════════════════════════════════════════
// SIDEBAR — CHARACTER LIST
// ═══════════════════════════════════════════════════════════════════════════
export function renderCharList() {
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
        applyAvatar(avatarDiv, char);

        const nameSpan = document.createElement('span');
        nameSpan.className = 'char-name hide-on-collapse';
        nameSpan.textContent = char.missing ? `${char.name} (missing)` : char.name;
        selectBtn.append(avatarDiv, nameSpan);

        const actions = document.createElement('div');
        actions.className = 'char-item-actions hide-on-collapse';
        const pinIcon = char.pinned ? icons.STAR_FILLED : icons.STAR;
        const pinTitle = char.pinned ? 'Unpin character' : 'Pin character';
        if (char.missing) {
            actions.innerHTML = `
                <button class="icon-btn char-delete-btn" title="Delete character" aria-label="Delete character">${icons.TRASH}</button>
            `;
        } else {
            actions.innerHTML = `
                <button class="icon-btn char-pin-btn" title="${pinTitle}" aria-label="${pinTitle}">${pinIcon}</button>
                <button class="icon-btn char-edit-btn" title="Edit character" aria-label="Edit character">${icons.EDIT}</button>
                <button class="icon-btn char-delete-btn" title="Delete character" aria-label="Delete character">${icons.TRASH}</button>
            `;
        }

        li.append(selectBtn, actions);
        el.charList.appendChild(li);
    });
}

export async function loadCharacters() {
    try {
        state.characters = await API.getCharacters();
        renderCharList();
        const available = state.characters.filter(c => !c.missing);
        if (available.length === 0) {
            showEmptyState('No characters yet', 'Create a character to start your first conversation.', true);
            updateComposerState();
            return;
        }
        const target = state._savedActiveId
            ? available.find(c => c.id === state._savedActiveId)
            : available[0];
        if (target) await selectCharacter(target.id);
    } catch (err) {
        console.error('Could not load characters:', err);
    }
}

export async function selectCharacter(charId) {
    const char = state.characters.find(c => c.id === charId);
    if (!char || char.missing) return;

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
    if (!confirm(`Delete ${label} and all their chats? This cannot be undone.`)) return;
    try {
        await API.deleteCharacter(charId);
        showToast('Character deleted', 'success');
        state.characters = state.characters.filter(c => c.id !== charId);
        if (state.activeCharacter?.id === charId) {
            state.activeCharacter = null;
            state.chats           = [];
            state.activeChat      = null;
            state.messages        = [];
            el.currentCharName.textContent = 'Cozy';
            updateComposerState();
            renderChats();
            renderMessages();
            const next = state.characters.find(c => !c.missing);
            if (next) await selectCharacter(next.id);
        }
        renderCharList();
    } catch (err) {
        showToast('Could not delete character: ' + err.message, 'error');
    }
}
