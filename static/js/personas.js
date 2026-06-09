import { state, el, icons } from './state.js';
import { API } from './api.js';
import { showToast, savePrefs, applyAvatar } from './utils.js';

// ═══════════════════════════════════════════════════════════════════════════
// PERSONAS
// ═══════════════════════════════════════════════════════════════════════════

export function renderPersonaList() {
    if (!el.personaList) return;
    el.personaList.innerHTML = '';

    state.personas.forEach(p => {
        const opt = document.createElement('div');
        opt.className = 'persona-option' + (state.activePersona?.id === p.id ? ' active' : '');
        opt.dataset.personaId = p.id;

        const avatar = document.createElement('div');
        avatar.className = 'avatar small user-avatar';
        applyAvatar(avatar, p);

        const info = document.createElement('div');
        info.className = 'persona-info';
        if (p.is_default) {
            const badge = document.createElement('span');
            badge.className = 'persona-default-badge';
            badge.innerHTML = `${icons.STAR}<span>Default</span>`;
            info.appendChild(badge);
        }
        const name = document.createElement('span');
        name.className = 'persona-name';
        name.textContent = p.name;
        info.appendChild(name);
        if (p.tagline) {
            const tag = document.createElement('span');
            tag.className = 'persona-tagline';
            tag.textContent = p.tagline;
            info.appendChild(tag);
        }

        opt.append(avatar, info);

        // Action buttons container (pushed to right)
        const actions = document.createElement('div');
        actions.className = 'persona-actions';

        // Edit button — available for all personas
        const edit = document.createElement('button');
        edit.className = 'persona-edit-btn icon-btn';
        edit.title = 'Edit persona';
        edit.innerHTML = icons.EDIT;
        edit.addEventListener('click', e => {
            e.stopPropagation();
            showPersonaForm(p);
        });
        actions.appendChild(edit);

        // Delete button — not for default persona
        if (!p.is_default) {
            const del = document.createElement('button');
            del.className = 'persona-delete-btn icon-btn';
            del.title = 'Delete persona';
            del.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
            del.addEventListener('click', async e => {
                e.stopPropagation();
                if (!confirm(`Delete persona "${p.name}"?`)) return;
                try {
                    await API.deletePersona(p.id);
                    state.personas = state.personas.filter(x => x.id !== p.id);
                    if (state.activePersona?.id === p.id) {
                        state.activePersona = state.personas.find(x => x.is_default) || state.personas[0];
                        updateUserProfile();
                        savePrefs();
                    }
                    renderPersonaList();
                } catch (err) {
                    console.error(err);
                    showToast('Failed to delete persona: ' + err.message);
                }
            });
            actions.appendChild(del);
        }
        opt.appendChild(actions);

        opt.addEventListener('click', () => {
            state.activePersona = p;
            updateUserProfile();
            renderPersonaList();
            savePrefs();
        });

        el.personaList.appendChild(opt);
    });
}

export function updateUserProfile() {
    const p = state.activePersona;
    if (!p) return;
    if (el.userName) el.userName.textContent = p.name;
    if (el.userTagline) el.userTagline.textContent = p.tagline || p.description || '';
    if (el.userAvatar) {
        applyAvatar(el.userAvatar, p);
    }
}

export function showPersonaForm(editPersona = null) {
    if (!el.personaForm) return;
    el.personaForm.hidden = false;
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
    applyAvatar(avatarPreview, editPersona);

    let selectedFile = null;
    let objectUrl = null;
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

    const cleanup = () => {
        if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
        el.personaForm.hidden = true;
        fileInput.removeEventListener('change', onFileChange);
        saveBtnEl.replaceWith(saveBtnEl.cloneNode(true));
        cancelBtnEl.replaceWith(cancelBtnEl.cloneNode(true));
    };

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
        if (!name) { nameInput.focus(); return; }
        const tagline = taglineInput.value.trim();
        const desc = descInput.value.trim();

        saveBtnEl.disabled = true;
        try {
            let persona;
            if (editPersona) {
                persona = await API.updatePersona(editPersona.id, { name, tagline, description: desc });
            } else {
                persona = await API.createPersona({ name, tagline, description: desc });
            }
            if (selectedFile) {
                persona = await API.uploadPersonaAvatar(persona.id, selectedFile);
            }
            // Refresh list
            state.personas = await API.getPersonas();
            state.activePersona = state.personas.find(p => p.id === persona.id) || state.activePersona;
            updateUserProfile();
            renderPersonaList();
            savePrefs();
        } catch (err) { console.error(err); showToast(err.message || 'Connection failed', 'error'); }
        saveBtnEl.disabled = false;
        cleanup();
    });

    nameInput.focus();
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
