import { state, el } from './state.js';
import { API } from './api.js';
import { saveLLMSettings } from './llm-settings.js';
import { cardPrompt } from './context-budget.js';
import { downloadUrl, sanitize, showToast, flashSettingsSavedTick } from './utils.js';
import { confirmDialog, promptDialog } from './confirm.js';
import { previewChatPayload, previewRenderedPrompts } from './request-builder.js';
import { newestPreset } from './preset-match.js';

// ═══════════════════════════════════════════════════════════════════════════
// PROMPT PRESETS
// ═══════════════════════════════════════════════════════════════════════════
// A preset is two boxes of plain text, System and Post-History. The layout
// they are sent in belongs to context-analysis.js.

// Standard house prompt ("NanoBear"); an "Author" second word ("NanoBear
// Author") is a variant, never the default. Mirrors STANDARD_NANOBEAR_RE in
// cozy/defaults.py.
const STANDARD_NANOBEAR_RE = /^NanoBear(?!\s+Author\b)/;

// A version is a plain number, optionally with one decimal part: "2", "2.1".
// Mirrors VERSION_RE in cozy/routes/settings.py. The "v" belongs to the badge,
// never to the stored value, or the two would stack up as "vv2.1".
const VERSION_RE = /^\d+(?:\.\d+)?$/;

function defaultPromptId() {
    // The greatest version among standard NanoBears, mirroring what
    // seed_default_prompts() activates on a fresh install. Picking by name
    // would be arbitrary now that every edition shares one.
    const matches = state.systemPrompts.filter(p => STANDARD_NANOBEAR_RE.test(p.name || ''));
    return (matches.length ? newestPreset(matches) : state.systemPrompts[0]).id;
}

function activePrompt() {
    return state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
}

export function syncActivePromptFromEditors() {
    const p = activePrompt();
    if (!p) return null;
    if (el.syspromptDescription) p.description = el.syspromptDescription.value;
    p.version = readVersionEditor(p.version || '');
    if (el.syspromptContent) p.content = el.syspromptContent.value;
    if (el.postHistoryContent) p.post_history_content = el.postHistoryContent.value;
    decorateOption(p);
    return p;
}

/**
 * The version box's value, flagged when it doesn't parse. Half-typed is
 * normal — "2." on the way to "2.1" — so an invalid box keeps the last value
 * that saved rather than sending the route something it would reject.
 */
function readVersionEditor(current) {
    if (!el.syspromptVersion) return current;
    const typed = el.syspromptVersion.value.trim();
    const valid = typed === '' || VERSION_RE.test(typed);
    el.syspromptVersion.setAttribute('aria-invalid', String(!valid));
    return valid ? typed : current;
}

/** On blur, put a half-typed version back to the value that actually saved. */
export function normalizeVersionEditor() {
    if (!el.syspromptVersion) return;
    el.syspromptVersion.value = activePrompt()?.version || '';
    el.syspromptVersion.setAttribute('aria-invalid', 'false');
}

/**
 * Put the name and version on an <option> as the parts the themed dropdown
 * renders — `label` plain, `badge` as a pill. The option's own text stays the
 * full "name — vX" so the native select, typeahead and screen readers read it
 * whole. A prompt with no version gets the name alone, no empty pill.
 */
function decorateOption(prompt) {
    const opt = el.syspromptSelect?.querySelector(`option[value="${prompt.id}"]`);
    if (!opt) return;
    const version = (prompt.version || '').trim();
    opt.dataset.label = prompt.name;
    opt.dataset.badge = version ? `v${version}` : '';
    opt.textContent = version ? `${prompt.name} — v${version}` : prompt.name;
}

function setEditorValues(prompt) {
    if (el.syspromptDescription) el.syspromptDescription.value = prompt ? (prompt.description || '') : '';
    if (el.syspromptVersion) {
        el.syspromptVersion.value = prompt ? (prompt.version || '') : '';
        el.syspromptVersion.setAttribute('aria-invalid', 'false');
    }
    if (el.syspromptContent) el.syspromptContent.value = prompt ? prompt.content : '';
    if (el.postHistoryContent) {
        el.postHistoryContent.value = prompt ? (prompt.post_history_content || '') : '';
    }
}

async function savePromptFields({ showSuccess = false } = {}) {
    if (!state.activeSystemPromptId) return;
    const p = syncActivePromptFromEditors();
    if (!p) return;
    try {
        await API.updateSystemPrompt(state.activeSystemPromptId, {
            description: p.description || '',
            version: p.version || '',
            content: p.content || '',
            post_history_content: p.post_history_content || '',
        });
        flashSettingsSavedTick();
        if (showSuccess) showToast('Prompt saved', 'success');
    } catch (e) {
        if (showSuccess) showToast('Failed to save prompt');
        console.warn('Failed to update prompt:', e);
    }
}

export async function loadSystemPrompts(existingSettings = null) {
    try {
        state.systemPrompts = await API.getSystemPrompts();
        const settings = existingSettings || await API.getSettings();
        state.activeSystemPromptId = settings.active_system_prompt
            ? Number(settings.active_system_prompt) : null;

        if (!el.syspromptSelect) return;
        el.syspromptSelect.innerHTML = '';
        if (state.systemPrompts.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.disabled = true;
            opt.selected = true;
            opt.textContent = 'No prompts - create one';
            el.syspromptSelect.appendChild(opt);
            setEditorValues(null);
            return;
        }
        state.systemPrompts.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.selected = p.id === state.activeSystemPromptId;
            el.syspromptSelect.appendChild(opt);
            decorateOption(p);
        });
        if (!state.activeSystemPromptId || !state.systemPrompts.find(p => p.id === state.activeSystemPromptId)) {
            state.activeSystemPromptId = defaultPromptId();
            el.syspromptSelect.value = state.activeSystemPromptId;
            saveLLMSettings({ active_system_prompt: String(state.activeSystemPromptId) });
        }
        setEditorValues(activePrompt());
    } catch (e) { console.warn('Failed to load system prompts:', e); }
}

export async function selectSystemPrompt(id) {
    state.activeSystemPromptId = Number(id);
    saveLLMSettings({ active_system_prompt: String(id) });
    setEditorValues(activePrompt());
}

export async function createSystemPrompt() {
    const name = await promptDialog({ title: 'New prompt', confirmLabel: 'Create' });
    if (!name || !name.trim()) return;
    // A new prompt starts as a copy of the selected one, editors included, so
    // a prompt you have been working on can be forked without retyping it.
    const source = syncActivePromptFromEditors();
    try {
        const created = await API.createSystemPrompt({
            name: name.trim(),
            // The version is deliberately not copied: it belongs to the
            // release the fork came from, not to the prompt you are now
            // writing, and carrying it over would claim a number someone
            // else's updates own.
            ...(source ? {
                description: source.description || '',
                content: source.content || '',
                post_history_content: source.post_history_content || '',
            } : {}),
        });
        await loadSystemPrompts();
        selectSystemPrompt(created.id);
        if (el.syspromptSelect) el.syspromptSelect.value = created.id;
        showToast('Prompt created', 'success');
    } catch (e) {
        showToast('Failed to create prompt: ' + e.message);
        console.warn('Failed to create system prompt:', e);
    }
}

export async function renameSystemPrompt() {
    if (!state.activeSystemPromptId) return;
    const active = activePrompt();
    const name = await promptDialog({
        title: 'Rename prompt', value: active?.name || '', confirmLabel: 'Rename',
    });
    if (!name || !name.trim() || name.trim() === active?.name) return;
    try {
        await API.updateSystemPrompt(state.activeSystemPromptId, { name: name.trim() });
        await loadSystemPrompts();
        showToast('Prompt renamed', 'success');
    } catch (e) {
        showToast('Failed to rename prompt: ' + e.message);
        console.warn('Failed to rename system prompt:', e);
    }
}

export async function deleteSystemPrompt() {
    if (!state.activeSystemPromptId) return;
    const active = activePrompt();
    if (!(await confirmDialog({ title: `Delete prompt "${active?.name || 'this prompt'}"?` }))) return;
    try {
        await API.deleteSystemPrompt(state.activeSystemPromptId);
        state.activeSystemPromptId = null;
        await loadSystemPrompts();
        showToast('Prompt deleted', 'success');
    } catch (e) {
        showToast('Failed to delete prompt: ' + e.message);
        console.warn('Failed to delete system prompt:', e);
    }
}

export async function updateSystemPromptContent() {
    await savePromptFields();
}

// ── Import / export ───────────────────────────────────────────────────────

export function importSystemPrompt() {
    el.syspromptImportFile?.click();
}

export async function handleSystemPromptImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    try {
        const created = await API.importSystemPrompt(file);
        await loadSystemPrompts();
        await selectSystemPrompt(created.id);
        if (el.syspromptSelect) el.syspromptSelect.value = created.id;
        showToast('Prompt imported', 'success');
    } catch (err) {
        showToast('Import failed: ' + err.message);
    }
}

export async function exportSystemPrompt() {
    if (!state.activeSystemPromptId) {
        showToast('No prompt selected');
        return;
    }
    // Persist any unsaved editor edits so the export reflects both the
    // System and Post-History text currently on screen.
    await savePromptFields();
    window.location.href = `/api/system-prompts/${state.activeSystemPromptId}/export`;
}

export function previewSystemPrompt() {
    syncActivePromptFromEditors();

    const payload = previewChatPayload();
    if (el.promptPreviewContent) {
        el.promptPreviewContent.textContent = JSON.stringify(payload, null, 2);
    }
}

/** Download the request preview as shown, formatted JSON. */
export function exportPreviewPayload() {
    const shown = (el.promptPreviewContent?.textContent || '').trim();
    const text = shown || JSON.stringify(previewChatPayload(), null, 2);
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    downloadUrl(url, 'request-preview.json');
    URL.revokeObjectURL(url);
}

// ── Filled-in preview flyout ───────────────────────────────────────────────
// The two boxes in the layout Cozy sends them in, as the request builder's own
// analysis pass rendered them — nothing is resolved a second time here. Scoped
// to the layout on purpose: the [Start] alternation shim assembly may add has
// no counterpart in the editor, and belongs to the whole-request preview behind
// the eye icon. Compiled on open, discarded on close.

// Run over already-escaped text, so a literal tag in the prompt reads as one.
const RENDERED_TAG = /&lt;\/?[A-Za-z][\w.:-]*(?:\s[^&]*?)?&gt;/g;

function prettifyPrompt(text) {
    return sanitize(text)
        .replace(RENDERED_TAG, tag => `<span class="prompt-rendered-tag">${tag}</span>`);
}

function renderedBlock(title, caption, text, emptyNote) {
    const body = text
        ? `<pre class="prompt-rendered-text">${prettifyPrompt(text)}</pre>`
        : `<p class="prompt-rendered-empty">${emptyNote}</p>`;
    return `<section class="prompt-rendered-block">
        <h5 class="prompt-rendered-block-title">${title}<span>${caption}</span></h5>
        ${body}
    </section>`;
}

export function openRenderedPrompts() {
    if (!el.promptRenderedFlyout || !el.promptRenderedBody) return;
    syncActivePromptFromEditors();

    const { system, postHistory } = previewRenderedPrompts();

    el.promptRenderedBody.innerHTML =
        renderedBlock('System', 'sent before the chat', system,
            'Nothing — the System box and every part after it are empty.')
        + renderedBlock('Post-History', 'sent after the chat', postHistory,
            'Nothing — the Post-History box is empty.');
    el.promptRenderedBody.scrollTop = 0;
    el.promptRenderedFlyout.hidden = false;
    el.promptRenderedBtn?.setAttribute('aria-expanded', 'true');
}

export function closeRenderedPrompts() {
    if (!el.promptRenderedFlyout || el.promptRenderedFlyout.hidden) return;
    el.promptRenderedFlyout.hidden = true;
    if (el.promptRenderedBody) el.promptRenderedBody.innerHTML = '';
    el.promptRenderedBtn?.setAttribute('aria-expanded', 'false');
}

export function toggleRenderedPrompts() {
    if (el.promptRenderedFlyout?.hidden) openRenderedPrompts();
    else closeRenderedPrompts();
}

export function switchPromptBuilderMode(mode) {
    const selected = mode === 'post-history' ? 'post-history' : 'system';
    document.querySelectorAll('[data-prompt-builder-tab]').forEach(btn => {
        const active = btn.dataset.promptBuilderTab === selected;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
    });
    showPromptBoxes();
}

// What the card calls each box's prompt, and how the line saying it has none ends.
const CARD_PROMPTS = {
    system: ['system_prompt', 'system prompt', 'so yours is sent.'],
    'post-history': ['post_history_instructions', 'post-history instructions', 'so yours are sent.'],
};

/**
 * Show the open tab's box as the open chat sends it: the user's own text, or
 * the card's, read-only under a warning, while Use character prompts has the
 * card's sent instead. The user's box is only hidden then, never written to,
 * so its text is there again when the switch goes off.
 */
export function showPromptBoxes() {
    const tab = el.promptBuilderTabs?.querySelector('.active')?.dataset.promptBuilderTab || 'system';
    const [field, called, yoursSent] = CARD_PROMPTS[tab];
    const character = state.useCharacterPrompts ? state.activeCharacter : null;
    const cardText = cardPrompt(field);
    document.querySelectorAll('[data-prompt-box]').forEach(node => {
        const replaced = node.matches('textarea') ? cardText : character;
        node.hidden = node.dataset.promptBox !== tab || Boolean(replaced);
    });
    el.promptCardCallout.hidden = el.promptCardText.hidden = !cardText;
    el.promptCardNone.hidden = !character || Boolean(cardText);
    if (!character) return;
    el.promptCardCallout.querySelector('strong').textContent = `${character.name}'s card replaces this prompt.`;
    el.promptCardCallout.querySelector('span').textContent =
        `Use character prompts is on, so the card's ${called} below ${tab === 'system' ? 'is' : 'are'} sent instead of yours. `
        + `Edit ${tab === 'system' ? 'it' : 'them'} in ${character.name}'s character editor. Your text is kept for when you turn the switch off.`;
    el.promptCardText.value = cardText;
    el.promptCardNone.textContent = `${character.name}'s card has no ${called}, ${yoursSent}`;
}
