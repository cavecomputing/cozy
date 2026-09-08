import { state, el } from './state.js';
import { API } from './api.js';
import { applyAvatar, AVATAR, showToast, Flyouts, updateComposerState, markUnusedVar, withBusy } from './utils.js';
import { renderCharList, selectCharacter, deleteCharacter } from './characters.js';
import { renderLorebookFlyout, renderLorebookList, renderLorebookNotice } from './lorebooks.js';
import { renderMessages } from './messages.js';
import { createTagEditor, createGreetingEditor } from './field-editors.js';
import { confirmDialog } from './confirm.js';
import { updateContextMeter, updateContextBoundary } from './context-meter.js';
import { validateCharacter } from './validate.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHARACTER MODAL
// ═══════════════════════════════════════════════════════════════════════════
const overlay   = document.getElementById('char-modal');
const titleEl   = document.getElementById('modal-title');
const saveBtn   = document.getElementById('modal-save-btn');
const cancelBtn = document.getElementById('modal-cancel-btn');
const closeBtn  = document.getElementById('modal-close-btn');
const deleteBtn = document.getElementById('modal-delete-btn');
const exportWrap    = document.getElementById('export-dropdown-wrap');
const exportTrigger = document.getElementById('export-card-btn');
const exportMenu    = document.getElementById('export-menu');
const exportLabel   = document.getElementById('export-menu-export-label');
const exportSep     = document.getElementById('export-menu-sep');
const exportItems   = exportMenu.querySelectorAll('[data-export-item]');

const avatarPreview = document.getElementById('modal-avatar-preview');
const avatarInput   = document.getElementById('avatar-file-input');
const avatarRequired = document.getElementById('modal-avatar-required');
const identityRow   = overlay.querySelector('.modal-identity');
const importInput   = document.getElementById('import-file-input');

const tabBtns   = overlay.querySelectorAll('.tab-btn');
const tabPanels = overlay.querySelectorAll('.tab-panel');

const fields = {
    name:          document.getElementById('cf-name'),
    description:   document.getElementById('cf-description'),
    personality:   document.getElementById('cf-personality'),
    scenario:      document.getElementById('cf-scenario'),
    first_mes:     document.getElementById('cf-first-mes'),
    mes_example:   document.getElementById('cf-mes-example'),
    system_prompt: document.getElementById('cf-system-prompt'),
    post_history:  document.getElementById('cf-post-history'),
    creator_notes: document.getElementById('cf-creator-notes'),
    creator:       document.getElementById('cf-creator'),
    version:       document.getElementById('cf-version'),
};

// Fields that map to prompt template variables. When a field has content but
// the active template doesn't reference its {{variable}}, that content is
// silently dropped from the prompt — so we surface a neutral marker next to the
// label. (mes_example → mesExamples camelCase does not auto-derive.)
const PROMPT_FIELD_VARS = [
    ['description',   'description'],
    ['personality',   'personality'],
    ['scenario',      'scenario'],
    ['mes_example',   'mesExamples'],
    ['system_prompt', 'system_prompt'],
    ['post_history',  'post_history_instructions'],
];

const fieldMarkers = {
    description:   document.getElementById('cf-description-marker'),
    personality:   document.getElementById('cf-personality-marker'),
    scenario:      document.getElementById('cf-scenario-marker'),
    mes_example:   document.getElementById('cf-mes-example-marker'),
    system_prompt: document.getElementById('cf-system-prompt-marker'),
    post_history:  document.getElementById('cf-post-history-marker'),
};

/** Show/hide the ⊘ marker for each prompt field based on content + template use. */
function updateFieldMarkers() {
    for (const [key, varName] of PROMPT_FIELD_VARS) {
        markUnusedVar(fieldMarkers[key], varName, fields[key].value.trim() !== '');
    }
}

// Live-update markers as the user types into (or clears) any prompt field.
for (const [key] of PROMPT_FIELD_VARS) {
    fields[key].addEventListener('input', updateFieldMarkers);
}

const altGreetingsList = document.getElementById('alt-greetings-list');
const addGreetingBtn   = document.getElementById('add-greeting-btn');
const tagsChipList     = document.getElementById('tags-chip-list');
const tagsTextInput    = document.getElementById('tags-text-input');
const tagsWrap         = document.getElementById('tags-input-wrap');

let editingCharId     = null;
let pendingAvatarFile = null;

const tagEditor = createTagEditor({
    chipList: tagsChipList,
    textInput: tagsTextInput,
    wrap: tagsWrap,
});
const greetingEditor = createGreetingEditor({
    listEl: altGreetingsList,
    addBtn: addGreetingBtn,
});

function switchTab(tabId) {
    tabBtns.forEach(b => {
        const active = b.dataset.tab === tabId;
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', active);
    });
    tabPanels.forEach(p => {
        const show = p.id === `tab-${tabId}`;
        p.hidden = !show;
        p.classList.toggle('active', show);
    });
}
tabBtns.forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

avatarInput.addEventListener('change', () => {
    const file = avatarInput.files[0];
    if (!file) return;
    pendingAvatarFile = file;
    const reader = new FileReader();
    reader.onload = e => {
        avatarPreview.style.backgroundImage = `url('${e.target.result}')`;
        avatarPreview.dataset.hasImage = 'true';
        avatarPreview.textContent = '';
        avatarPreview.classList.remove('is-required');
        if (avatarRequired) avatarRequired.hidden = true;
    };
    reader.readAsDataURL(file);
});

function populate(char) {
    fields.name.value          = char.name                      || '';
    fields.description.value   = char.description               || '';
    fields.personality.value   = char.personality               || '';
    fields.scenario.value      = char.scenario                  || '';
    fields.first_mes.value     = char.first_mes                 || '';
    fields.mes_example.value   = char.mes_example               || '';
    fields.system_prompt.value = char.system_prompt             || '';
    fields.post_history.value  = char.post_history_instructions || '';
    fields.creator_notes.value = char.creator_notes             || '';
    fields.creator.value       = char.creator                   || '';
    fields.version.value       = char.character_version         || '';
    tagEditor.set(char.tags);
    greetingEditor.set(char.alternate_greetings);
    applyAvatar(avatarPreview, char, '?', AVATAR.SM);
}

function clearForm() {
    Object.values(fields).forEach(f => { f.value = ''; });
    tagEditor.set([]);
    greetingEditor.set([]);
    avatarPreview.style.backgroundImage = '';
    avatarPreview.dataset.hasImage = 'false';
    avatarPreview.textContent = '?';
}

/** Drop every error message and invalid mark left by an earlier attempt. */
function clearFieldErrors() {
    overlay.querySelectorAll('.field-error').forEach(n => n.remove());
    overlay.querySelectorAll('[aria-invalid]').forEach(n => {
        n.removeAttribute('aria-invalid');
        n.removeAttribute('aria-describedby');
    });
    avatarPreview.classList.remove('is-required');
    if (avatarRequired) avatarRequired.hidden = true;
}

/**
 * Put each message beside the control it belongs to and focus the first one.
 * The message stays until the field is fixed — an error that erases itself on
 * a timer is gone before a slow reader reaches it.
 */
function showFieldErrors(errors) {
    clearFieldErrors();
    let first = null;

    for (const { field, message } of errors) {
        if (field === 'avatar') {
            // The image well is not a text field: it carries the mark on the
            // well itself, and its message goes under the whole identity row
            // rather than inside the flex row beside the name.
            avatarPreview.classList.add('is-required');
            if (avatarRequired) avatarRequired.hidden = false;
            const note = document.createElement('p');
            note.className = 'field-error field-error--identity';
            note.id = 'cf-avatar-error';
            note.textContent = message;
            identityRow.insertAdjacentElement('afterend', note);
            avatarInput.setAttribute('aria-invalid', 'true');
            avatarInput.setAttribute('aria-describedby', note.id);
            first = first || avatarPreview;
            continue;
        }
        const input = fields[field];
        if (!input) continue;

        const note = document.createElement('p');
        note.className = 'field-error';
        note.id = `${input.id}-error`;
        note.textContent = message;
        input.insertAdjacentElement('afterend', note);

        input.setAttribute('aria-invalid', 'true');
        input.setAttribute('aria-describedby', note.id);
        first = first || input;
    }

    // Switch to the tab holding the first problem before focusing it, or the
    // focus lands on a control the user cannot see.
    const panel = first?.closest('.tab-panel');
    if (panel) overlay.querySelector(`.tab-btn[data-tab="${panel.id.replace('tab-', '')}"]`)?.click();
    first?.focus?.();
}

function collect() {
    return {
        name:                      fields.name.value.trim(),
        description:               fields.description.value,
        personality:               fields.personality.value,
        scenario:                  fields.scenario.value,
        first_mes:                 fields.first_mes.value,
        mes_example:               fields.mes_example.value,
        system_prompt:             fields.system_prompt.value,
        post_history_instructions: fields.post_history.value,
        creator_notes:             fields.creator_notes.value,
        creator:                   fields.creator.value,
        character_version:         fields.version.value,
        tags:                      tagEditor.get(),
        alternate_greetings:       greetingEditor.get(),
    };
}

function open(char = null) {
    Flyouts.closeAllExcept('modal');
    clearFieldErrors();
    editingCharId = char ? char.id : null;
    pendingAvatarFile = null;
    avatarInput.value = '';
    importInput.value = '';
    switchTab('basic');
    titleEl.textContent = char ? 'Edit Character' : 'New Character';
    // Export only makes sense when editing an existing card; import is always available
    exportLabel.hidden = !char;
    exportSep.hidden = !char;
    exportItems.forEach(li => { li.hidden = !char; });
    exportMenu.hidden = true;                       // always close dropdown on open
    exportWrap.classList.remove('open');
    exportTrigger.setAttribute('aria-expanded', 'false');
    deleteBtn.hidden = !char;                       // only show delete on edit, not create
    avatarPreview.classList.toggle('is-required', !char);
    if (avatarRequired) avatarRequired.hidden = !!char;
    if (char) populate(char);
    else      clearForm();
    updateFieldMarkers();
    overlay.hidden = false;
    requestAnimationFrame(() => fields.name.focus());
}

function close() {
    overlay.hidden = true;
    clearForm();
    editingCharId = null;
    pendingAvatarFile = null;
}

/** Fold a saved/imported character back into the app: list, then whatever the
 *  main view is showing. Only a brand new character gets selected outright. */
async function applyCharUpdate(char, isNew) {
    const idx = state.characters.findIndex(c => c.id === char.id);
    if (idx >= 0) state.characters[idx] = char;
    else          state.characters.push(char);
    renderCharList();
    renderLorebookList();

    if (state.activeCharacter?.id === char.id) {
        state.activeCharacter = char;
        el.currentCharName.textContent = char.name;
        renderMessages();
        renderLorebookFlyout();
        renderLorebookNotice();
        updateComposerState();
        updateContextMeter();
        updateContextBoundary();
    } else if (isNew) {
        await selectCharacter(char.id);
    }
}

async function save() {
    const data = collect();
    const isEditing = !!editingCharId;
    const errors = validateCharacter({
        name: data.name,
        isNew: !editingCharId,
        hasImage: !!pendingAvatarFile,
    });
    if (errors.length) {
        showFieldErrors(errors);
        return;
    }
    clearFieldErrors();
    try {
        await withBusy(saveBtn, 'Saving\u2026', async () => {
            let char;
            if (editingCharId) {
                char = await API.updateCharacter(editingCharId, data);
                if (pendingAvatarFile) char = await API.uploadAvatar(char.id, pendingAvatarFile);
            } else {
                char = await API.createCharacter(data, pendingAvatarFile);
            }
            await applyCharUpdate(char, !isEditing);
        });
        close();
        showToast(isEditing ? 'Character saved' : 'Character created', 'success');
    } catch (err) {
        showToast('Could not save character: ' + err.message, 'error');
    }
}

importInput.addEventListener('change', async () => {
    const file = importInput.files[0];
    if (!file) return;
    importInput.value = '';

    // Importing while editing replaces that character rather than adding a new
    // one \u2014 the way a card gets updated to a newer version.
    const replacingId = editingCharId;
    if (replacingId) {
        const keepsImage = file.name.toLowerCase().endsWith('.json');
        const ok = await confirmDialog({
            title: `Replace ${fields.name.value.trim() || 'this character'}?`,
            message: keepsImage
                ? 'Every field is overwritten by the imported card, including any edits you made here. The current image and your chats are kept.'
                : 'Every field and the image are overwritten by the imported card, including any edits you made here. Your chats are kept.',
            confirmLabel: 'Replace',
            danger: false,
        });
        if (!ok) return;
    }

    try {
        await withBusy(saveBtn, 'Importing\u2026', async () => {
            const char = replacingId
                ? await API.importOverCard(replacingId, file)
                : await API.importCard(file);
            await applyCharUpdate(char, !replacingId);
        });
        close();
        showToast(replacingId ? 'Character replaced' : 'Character imported', 'success');
    } catch (err) {
        showToast('Import failed: ' + err.message, 'error');
    }
});

// Import/Export dropdown — single trigger button toggles the menu
exportTrigger.addEventListener('click', e => {
    e.stopPropagation();
    const opening = exportMenu.hidden;
    exportMenu.hidden = !opening;
    exportWrap.classList.toggle('open', opening);
    exportTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
});
// Clicking a menu item triggers import or an export download, then closes the menu
exportMenu.addEventListener('click', e => {
    const btn = e.target.closest('[data-fmt], [data-action="import"]');
    if (!btn) return;
    if (btn.dataset.action === 'import') {
        importInput.click();
    } else if (editingCharId) {
        API.exportCard(editingCharId, fields.name.value.trim(), btn.dataset.fmt);
    }
    exportMenu.hidden = true;
    exportWrap.classList.remove('open');
    exportTrigger.setAttribute('aria-expanded', 'false');
});
// Close menu when clicking anywhere outside
document.addEventListener('click', () => {
    exportMenu.hidden = true;
    exportWrap.classList.remove('open');
    exportTrigger.setAttribute('aria-expanded', 'false');
});

deleteBtn.addEventListener('click', async () => {
    if (!editingCharId) return;
    const name = fields.name.value.trim();
    close();
    await deleteCharacter(editingCharId, name);
});
closeBtn.addEventListener('click',  close);
cancelBtn.addEventListener('click', close);
saveBtn.addEventListener('click',   save);
overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
overlay.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

// An error clears as soon as the field it is about is touched, rather than
// waiting for another save attempt to tell the user they have fixed it.
overlay.addEventListener('input', e => {
    const input = e.target.closest('[aria-invalid]');
    if (!input) return;
    document.getElementById(input.getAttribute('aria-describedby'))?.remove();
    input.removeAttribute('aria-invalid');
    input.removeAttribute('aria-describedby');
});

export const Modal = { open, close };

Flyouts.register('modal', () => Modal.close());
