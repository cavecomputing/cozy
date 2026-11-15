import { state, el } from './state.js';
import { API } from './api.js';
import { applyAvatar, AVATAR, showToast, withBusy } from './utils.js';
import { flushLLMSettingsSave } from './llm-settings.js';
import { renderThemePicker } from './themes.js';
import { selectCharacter } from './characters.js';
import { showSavedPersona } from './personas.js';

// ═══════════════════════════════════════════════════════════════════════════
// FIRST-RUN SETUP — the whole window on a fresh install, while the
// `setup_pending` setting is '1', until Skip or a character is picked.
//
// It keeps no copy of anything: the API page's Endpoint, API key and Model rows
// and the Theme picker are moved in while it is open, with their own handlers,
// and put back when it closes. So Settings saves what is typed here, and the
// rest of Settings is out of reach until the setup is over.
// ═══════════════════════════════════════════════════════════════════════════

// Looked up when the setup opens, never at import: bare node imports this
// module (through characters.js) in the frontend tests, where there is no page.
let setup, connectBtn, connectStatus, connectHint, characterList, steps;

// Where each borrowed node lives in Settings, to put it back.
let borrowed = [];

function borrow(node, into, before = null) {
    borrowed.push([node, node.parentNode, node.nextSibling]);
    into.insertBefore(node, before);
}

function giveBack() {
    // In reverse, so each node's old next sibling is home again before it is.
    for (const [node, parent, next] of borrowed.reverse()) parent.insertBefore(node, next);
    borrowed = [];
}

/** Mark the done steps, and dim the ones after the first step left to do. */
function renderSteps() {
    const current = steps.findIndex(step => !step.classList.contains('is-done'));
    steps.forEach((step, i) => {
        const done = step.classList.contains('is-done');
        step.classList.toggle('is-later', !done && i > current);
        step.querySelector('.setup-step-num').textContent = done ? '✓' : String(i + 1);
        step.querySelector('.setup-step-act').hidden = done;
        // Only the step to do now gets the filled button; importing stays the other way.
        step.querySelectorAll('.setup-step-act .btn:not(.setup-import)').forEach(btn => {
            btn.classList.toggle('btn-primary', i === current);
            btn.classList.toggle('btn-secondary', i !== current);
        });
    });
}

function finishStep(step, text) {
    step.classList.add('is-done');
    step.querySelector('.setup-step-text').textContent = text;
    renderSteps();
}

/** Step 3's list: everyone not archived, each with a button that ends the setup there. */
export function renderSetupCharacters() {
    if (!setup || setup.hidden) return;
    characterList.replaceChildren(...state.characters
        .filter(char => !char.archived && !char.missing)
        .map(char => {
            const li = document.createElement('li');
            li.className = 'setup-character';
            const avatar = document.createElement('div');
            avatar.className = 'avatar';
            applyAvatar(avatar, char, char.name, AVATAR.SM);
            const name = document.createElement('span');
            name.textContent = char.name;
            const chat = document.createElement('button');
            chat.type = 'button';
            chat.className = 'btn btn-secondary btn-sm';
            chat.textContent = `Chat with ${char.name}`;
            chat.addEventListener('click', () => closeSetup(char.id));
            li.append(avatar, name, chat);
            return li;
        }));
    renderSteps();
}

/** Back to the line under Connect that says what it does, from a failed test's reason. */
function clearConnectError() {
    connectStatus.textContent = connectHint;
    connectStatus.classList.remove('is-error');
}

async function connect() {
    clearConnectError();
    try {
        await withBusy(connectBtn, 'Connecting…', async () => {
            await flushLLMSettingsSave({ strict: true });
            await API.testLLM();
        });
    } catch (err) {
        connectStatus.textContent = err.message || 'The connection test failed.';
        connectStatus.classList.add('is-error');
        return;
    }
    finishStep(steps[0], `Connected to ${el.apiEndpoint.value}, model ${el.apiModel.value}.`);
}

const finishName = name => finishStep(steps[1], `Characters will call you ${name}.`);

async function saveName(e) {
    e.preventDefault();
    const nameInput = document.getElementById('setup-name');
    const name = nameInput.value.trim();
    const persona = state.activePersona;
    if (!name || !persona) return nameInput.focus();
    try {
        // The seeded persona's "Change me!" has done its job once it is renamed.
        const tagline = persona.tagline === 'Change me!' ? '' : persona.tagline;
        await API.updatePersona(persona.id, { name, tagline });
        await showSavedPersona(persona.id);
    } catch (err) {
        showToast(err.message || 'Could not save your name', 'error');
        return;
    }
    finishName(name);
}

export function openSetup() {
    const $ = id => document.getElementById(id);
    setup = $('setup');
    connectBtn = $('setup-connect');
    connectStatus = $('setup-connect-status');
    connectHint = connectStatus.textContent;
    characterList = $('setup-characters');
    steps = ['connect', 'name', 'character'].map(id => $(`setup-step-${id}`));

    const service = $('setup-service');
    service.addEventListener('change', () => {
        const other = service.value === 'other';
        el.apiEndpoint.value = other ? '' : service.value;
        // The field's own handler saves it and forgets the old server's models.
        el.apiEndpoint.dispatchEvent(new Event('input'));
        if (other) el.apiEndpoint.focus();
    });
    connectBtn.addEventListener('click', connect);
    // A failed test's reason is about the connection as it was. Once any part
    // of it changes, the reason left under Connect read as the new one's.
    // Capturing, because the events the fields are sent by hand don't bubble,
    // and a model picked from the menu is a click, not a change.
    const fields = $('setup-connect-fields');
    for (const type of ['input', 'change']) fields.addEventListener(type, clearConnectError, true);
    el.modelPickerMenu.addEventListener('click', clearConnectError);
    $('setup-name-form').addEventListener('submit', saveName);
    // The character editor's own file input: the card is imported, opened
    // behind the setup, and renderCharList() adds it to the list above.
    $('setup-import').addEventListener('click', () => $('import-file-input').click());
    $('setup-skip').addEventListener('click', () => closeSetup());

    borrow(el.settingsThemeSelect.closest('.cozy-select'), $('setup-theme'));
    for (const field of [el.apiEndpoint, el.apiKey, el.apiModel]) {
        borrow(field.closest('.settings-row'), $('setup-connect-fields'), connectBtn.parentNode);
    }
    renderThemePicker();
    el.sidebar.hidden = true;
    $('main-content').hidden = true;
    setup.hidden = false;
    // Opened again by a reload part-way: show what was already saved. That a
    // connection passed its test is not saved, so Connect is asked for again.
    const endpoint = el.apiEndpoint.value;
    if (endpoint) service.value = [...service.options].some(option => option.value === endpoint) ? endpoint : 'other';
    const persona = state.activePersona;
    if (persona && persona.tagline !== 'Change me!') finishName(persona.name);
    renderSetupCharacters();
}

/** End the setup for good, in the chat of `charId` when one was picked. */
async function closeSetup(charId = null) {
    setup.hidden = true;
    giveBack();
    el.sidebar.hidden = false;
    document.getElementById('main-content').hidden = false;
    API.saveSettings({ setup_pending: '0' })
        .catch(err => showToast('Could not save that setup is done: ' + err.message, 'error'));
    if (charId && charId !== state.activeCharacter?.id) await selectCharacter(charId);
}
