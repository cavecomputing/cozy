import { state, el, icons } from './state.js';
import { API } from './api.js';
import { showToast, savePrefs, applyAvatar, AVATAR, MOBILE_SHELL_QUERY } from './utils.js';
import { confirmDialog } from './confirm.js';
import { updateContextViews } from './context-meter.js';
import { rerenderMessageText } from './messages.js';

// ═══════════════════════════════════════════════════════════════════════════
// PERSONAS
// ═══════════════════════════════════════════════════════════════════════════

export function renderPersonaList() {
    if (!el.personaList) return;
    // The open form may be seated among the rows about to be discarded, so park
    // it before the wipe and seat it again once the rows it belongs to exist.
    parkPersonaForm();
    el.personaList.innerHTML = '';

    state.personas.forEach(p => {
        const opt = document.createElement('div');
        opt.className = 'persona-option' + (state.activePersona?.id === p.id ? ' active' : '');
        opt.dataset.personaId = p.id;

        const avatar = document.createElement('div');
        avatar.className = 'avatar small user-avatar';
        applyAvatar(avatar, p, '?', AVATAR.SM);

        const info = document.createElement('div');
        info.className = 'persona-info';
        // A lone persona is the default by definition, so the badge would say nothing.
        if (p.is_default && state.personas.length > 1) {
            const badge = document.createElement('span');
            badge.className = 'persona-default-badge';
            badge.innerHTML = `${icons.STAR}<span>Default</span>`;
            info.appendChild(badge);
        }
        const name = document.createElement('span');
        name.className = 'persona-name';
        name.textContent = p.name;
        name.title = p.name;
        info.appendChild(name);
        if (p.tagline) {
            const tag = document.createElement('span');
            tag.className = 'persona-tagline';
            tag.textContent = p.tagline;
            info.appendChild(tag);
        }

        opt.append(avatar, info);

        // Editing and deleting are in the row's ⋯ menu, as on the chat rows.
        const actions = document.createElement('div');
        actions.className = 'persona-actions';
        const more = document.createElement('button');
        more.className = 'icon-btn persona-menu-btn';
        more.title = 'More actions';
        more.setAttribute('aria-label', 'More actions');
        more.setAttribute('aria-haspopup', 'menu');
        more.setAttribute('aria-expanded', 'false');
        more.innerHTML = icons.MORE;
        more.addEventListener('click', e => {
            e.stopPropagation();
            // A click with no detail came from Enter or Space, not a pointer.
            togglePersonaMenu(more, p, e.detail === 0);
        });
        actions.appendChild(more);
        opt.appendChild(actions);

        opt.addEventListener('click', () => selectPersona(p));

        el.personaList.appendChild(opt);
    });
    seatPersonaForm();
}

/** Delete persona `p` once confirmed, speaking as the default if it was speaking. */
export async function deletePersona(p) {
    if (!(await confirmDialog({ title: `Delete persona "${p.name}"?` }))) return;
    try {
        await API.deletePersona(p.id);
        state.personas = state.personas.filter(x => x.id !== p.id);
        if (state.activePersona?.id === p.id) {
            state.activePersona = state.personas.find(x => x.is_default) || state.personas[0];
            updateUserProfile();
            rerenderMessageText();
            savePrefs();
        }
        renderPersonaList();
    } catch (err) {
        console.error(err);
        showToast('Failed to delete persona: ' + err.message);
    }
}

/** Close the persona rows' menu. Returns true when it was open. */
export function closePersonaMenu() {
    const menu = el.personaRowMenu;
    if (menu.hidden) return false;
    menu.hidden = true;
    const trigger = el.personaList.querySelector('.persona-menu-btn[aria-expanded="true"]');
    trigger?.setAttribute('aria-expanded', 'false');
    // As closeCharMenu(): a pointer's ⋯ lets go of focus, or it keeps showing.
    if (!menu.dataset.keyboard && document.activeElement === trigger) trigger.blur();
    return true;
}

/** Open the menu for persona `p` from its ⋯, or close it when it is that
 *  row's menu already showing. Works as toggleCharMenu() does, in the
 *  sidebar's coordinates, since the menu is its child. */
function togglePersonaMenu(trigger, p, fromKeyboard) {
    const menu = el.personaRowMenu;
    const sameRow = !menu.hidden && menu.dataset.personaId === String(p.id);
    closePersonaMenu();
    if (sameRow) return;

    // The default persona can't be deleted.
    menu.querySelectorAll('[data-row-delete]').forEach(li => { li.hidden = !!p.is_default; });
    menu.dataset.personaId = p.id;
    menu.dataset.keyboard = fromKeyboard ? '1' : '';
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    (fromKeyboard ? menu.querySelector('button') : trigger).focus({ preventScroll: true });

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
        // Out past the Switch Persona menu's right edge, level with the row,
        // and no lower than that menu's foot.
        const dropup = el.personaDropup.getBoundingClientRect();
        const row = trigger.closest('.persona-option').getBoundingClientRect();
        menu.style.top = `${Math.min(row.top, dropup.bottom - height) - box.top}px`;
        menu.style.left = `${dropup.right + 6 - box.left}px`;
        menu.style.right = 'auto';
        menu.style.transformOrigin = 'top left';
    }
}

export function selectPersona(p) {
    state.activePersona = p;
    updateUserProfile();
    rerenderMessageText();
    renderPersonaList();
    savePrefs();
    updateContextViews();
    // Record the choice on the open chat, not just in this browser, so the
    // next machine to open it speaks as the same person. Sending a message
    // stores this too; here it covers switching and then walking away.
    if (state.activeChat) {
        state.activeChat.persona_id = p.id;
        API.updateChat(state.activeChat.id, { persona_id: p.id })
            .catch(err => console.error('Could not save chat persona:', err));
    }
}

export function updateUserProfile() {
    const p = state.activePersona;
    if (!p) return;
    if (el.userName) el.userName.textContent = p.name;
    if (el.userTagline) el.userTagline.textContent = p.tagline || p.description || '';
    if (el.userAvatar) {
        applyAvatar(el.userAvatar, p, '?', AVATAR.SM);
    }
}

// Teardown for the currently-open inline form, or null when none is open. Only
// one form may be live at a time: each showPersonaForm() call attaches a Save
// listener closed over its own `editPersona`, so a second open without a
// teardown would leave two listeners on #pf-save and a single Save click would
// write the same name/tagline/description to *both* personas.
let activePersonaFormCleanup = null;

// Which persona the open form is editing, so it can be put back beside that row
// after a re-render. Null while creating, when the form belongs at the foot of
// the menu under the button that opened it.
let editingPersonaId = null;

/** Send the form back to the foot of the menu, out of the rows' way. */
function parkPersonaForm() {
    if (el.personaForm && el.personaForm.parentElement !== el.personaDropup) {
        el.personaDropup.appendChild(el.personaForm);
    }
}

/**
 * Put the form where the persona it edits is, replacing that row — the fields
 * then sit under the pointer rather than at the foot of a menu that jumped.
 * Falls back to the parked position if the row is gone (deleted elsewhere).
 */
function seatPersonaForm() {
    if (editingPersonaId === null) return;
    const row = el.personaList?.querySelector(`[data-persona-id="${editingPersonaId}"]`);
    if (!row) return;
    row.hidden = true;
    row.after(el.personaForm);
}

/** Tear down the inline persona form if it is open. Safe to call anytime. */
export function closePersonaForm() {
    activePersonaFormCleanup?.();
}

export function showPersonaForm(editPersona = null) {
    if (!el.personaForm) return;
    // Discard any form still open for another persona before rebinding.
    closePersonaForm();
    editingPersonaId = editPersona?.id ?? null;
    el.personaForm.hidden = false;
    // "New Persona" does nothing useful while a form is open — creating discards
    // the fields being typed, editing swaps them for a blank set with no warning.
    if (el.personaCreateBtn) el.personaCreateBtn.hidden = true;
    seatPersonaForm();
    const nameInput = el.personaForm.querySelector('#pf-name');
    const taglineInput = el.personaForm.querySelector('#pf-tagline');
    const descInput = el.personaForm.querySelector('#pf-description');
    const avatarPreview = el.personaForm.querySelector('#pf-avatar-preview');
    const saveBtnEl = el.personaForm.querySelector('#pf-save');
    const cancelBtnEl = el.personaForm.querySelector('#pf-cancel');
    const fileInput = el.personaForm.querySelector('#pf-avatar-input');

    nameInput.value = editPersona?.name || '';
    taglineInput.value = editPersona?.tagline || '';
    descInput.value = editPersona?.description || '';
    applyAvatar(avatarPreview, editPersona, '?', AVATAR.SM);

    // The message sits under the whole avatar-beside-name row rather than
    // inside it, or the flex row would seat it as a third column next to the
    // field. cleanup() rebinds by cloning the buttons, which leaves this note
    // behind, so clear it on the way in as well as on the way out.
    const clearNameError = () => {
        nameInput.removeAttribute('aria-invalid');
        nameInput.removeAttribute('aria-describedby');
        el.personaForm.querySelector('#pf-name-error')?.remove();
    };
    clearNameError();
    // Enter saves and Escape cancels, as in the inline chat rename. Escape has
    // to stop here: the document handler would otherwise take the dropup down
    // with the form. Both listeners are named so cleanup() can drop them —
    // the name field is not cloned the way the buttons are, so a listener left
    // behind would fire again on the next open, against a stale Save button.
    const onNameKeydown = e => {
        if (e.key === 'Enter')  { e.preventDefault(); saveBtnEl.click(); }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancelBtnEl.click(); }
    };
    nameInput.addEventListener('input', clearNameError);
    nameInput.addEventListener('keydown', onNameKeydown);

    let selectedFile = null;
    let objectUrl = null;
    // Clear the previous pick: re-choosing the same file wouldn't fire `change`
    // (the value is unchanged), so the avatar would silently not upload.
    fileInput.value = '';
    const onFileChange = () => {
        if (fileInput.files[0]) {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            selectedFile = fileInput.files[0];
            objectUrl = URL.createObjectURL(selectedFile);
            avatarPreview.style.backgroundImage = `url(${objectUrl})`;
            avatarPreview.dataset.hasImage = 'true';
            avatarPreview.textContent = '';
        }
    };
    fileInput.addEventListener('change', onFileChange);

    let cleanedUp = false;
    const cleanup = () => {
        if (cleanedUp) return;
        cleanedUp = true;
        // A late cleanup (Save finishing after the form was reopened for someone
        // else) must not hide or unbind the form that replaced this one — only
        // drop its own listeners. replaceWith() on the already-detached buttons
        // is a no-op.
        if (activePersonaFormCleanup === cleanup) {
            activePersonaFormCleanup = null;
            el.personaForm.hidden = true;
            el.personaList?.querySelector(`[data-persona-id="${editingPersonaId}"]`)?.removeAttribute('hidden');
            editingPersonaId = null;
            if (el.personaCreateBtn) el.personaCreateBtn.hidden = false;
            parkPersonaForm();
        }
        if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
        el.personaForm.querySelector('#pf-name-error')?.remove();
        nameInput.removeEventListener('input', clearNameError);
        nameInput.removeEventListener('keydown', onNameKeydown);
        fileInput.removeEventListener('change', onFileChange);
        saveBtnEl.replaceWith(saveBtnEl.cloneNode(true));
        cancelBtnEl.replaceWith(cancelBtnEl.cloneNode(true));
    };
    activePersonaFormCleanup = cleanup;

    // Stop propagation: cleanup() detaches this button via replaceWith, so by
    // the time the click bubbles to the document outside-click handler, e.target
    // is no longer inside #persona-dropup and the popup would close.
    cancelBtnEl.addEventListener('click', e => {
        e.stopPropagation();
        cleanup();
    }, { once: true });

    // Not `{ once: true }` — a validation early-return must leave the listener
    // alive so Save still works after the user fills in the name. cleanup()
    // strips listeners by cloning the button on every exit path.
    saveBtnEl.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        clearNameError();
        if (!name) {
            const note = document.createElement('p');
            note.className = 'field-error';
            note.id = 'pf-name-error';
            note.textContent = 'A persona needs a name.';
            nameInput.closest('.pf-row').insertAdjacentElement('afterend', note);
            nameInput.setAttribute('aria-invalid', 'true');
            nameInput.setAttribute('aria-describedby', note.id);
            nameInput.focus({ preventScroll: true });
            return;
        }
        const tagline = taglineInput.value.trim();
        const desc = descInput.value.trim();

        saveBtnEl.disabled = true;
        let persona;
        try {
            persona = editPersona
                ? await API.updatePersona(editPersona.id, { name, tagline, description: desc })
                : await API.createPersona({ name, tagline, description: desc });
        } catch (err) {
            // Nothing was saved, so the form stays open with what was typed.
            console.error(err);
            showToast(err.message || 'Connection failed', 'error');
            saveBtnEl.disabled = false;
            return;
        }
        // Saved. A picture that then fails (wrong type, too large) is reported
        // on its own, and the list below still gets the persona it was for.
        if (selectedFile) {
            try {
                persona = await API.uploadPersonaAvatar(persona.id, selectedFile);
            } catch (err) {
                showToast(`Persona saved, but its picture was not: ${err.message}`, 'error');
            }
        }
        try {
            await showSavedPersona(persona.id);
        } catch (err) { console.error(err); showToast(err.message || 'Connection failed', 'error'); }
        saveBtnEl.disabled = false;
        cleanup();
    });

    // preventScroll: the form sits inside the menu's own scroller, and letting
    // the browser scroll the focused field into view drags the page instead —
    // the jump that made this form hard to type into on a phone. The form is
    // already where the user just tapped; scroll only if seating it left an
    // edge off-screen.
    el.personaForm.scrollIntoView({ block: 'nearest' });
    nameInput.focus({ preventScroll: true });
}

/** Reload the personas after one was saved, and show it everywhere it appears. */
export async function showSavedPersona(id) {
    state.personas = await API.getPersonas();
    state.activePersona = state.personas.find(p => p.id === id) || state.activePersona;
    updateUserProfile();
    rerenderMessageText();
    renderPersonaList();
    savePrefs();
    updateContextViews();
}

export async function loadPersonas() {
    try {
        state.personas = await API.getPersonas();
        if (state._savedPersonaId) {
            state.activePersona = state.personas.find(p => p.id === state._savedPersonaId);
        }
        if (!state.activePersona) {
            state.activePersona = state.personas.find(p => p.is_default) || state.personas[0];
        }
        renderPersonaList();
        updateUserProfile();
    } catch (err) {
        console.error('Failed to load personas:', err);
        showToast('Failed to load personas: ' + err.message);
    }
}
