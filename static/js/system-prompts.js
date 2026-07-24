import { state, el } from './state.js';
import { API } from './api.js';
import { saveLLMSettings } from './llm-settings.js';
import { showToast } from './utils.js';
import { confirmDialog } from './confirm.js';
import { previewChatPayload } from './request-builder.js';

// ═══════════════════════════════════════════════════════════════════════════
// PAIRED PROMPT BUILDER
// ═══════════════════════════════════════════════════════════════════════════

function activePrompt() {
    return state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
}

export function syncActivePromptFromEditors() {
    const p = activePrompt();
    if (!p) return null;
    if (el.syspromptContent) p.content = el.syspromptContent.value;
    if (el.postHistoryContent) p.post_history_content = el.postHistoryContent.value;
    return p;
}

function setEditorValues(prompt) {
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
            content: p.content || '',
            post_history_content: p.post_history_content || '',
        });
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
            opt.textContent = p.name;
            opt.selected = p.id === state.activeSystemPromptId;
            el.syspromptSelect.appendChild(opt);
        });
        if (!state.activeSystemPromptId || !state.systemPrompts.find(p => p.id === state.activeSystemPromptId)) {
            state.activeSystemPromptId = state.systemPrompts[0].id;
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
    const name = prompt('New prompt name:');
    if (!name || !name.trim()) return;
    try {
        const created = await API.createSystemPrompt({ name: name.trim() });
        await loadSystemPrompts();
        selectSystemPrompt(created.id);
        if (el.syspromptSelect) el.syspromptSelect.value = created.id;
        showToast('Prompt created', 'success');
    } catch (e) {
        showToast('Failed to create prompt: ' + e.message);
        console.warn('Failed to create system prompt:', e);
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

let _defaultTemplate = null;
let _defaultPostHistoryTemplate = null;
async function getDefaultTemplates() {
    if (_defaultTemplate !== null && _defaultPostHistoryTemplate !== null) {
        return {
            template: _defaultTemplate,
            postHistoryTemplate: _defaultPostHistoryTemplate,
        };
    }
    const data = await API.getDefaultPromptTemplates();
    _defaultTemplate = data.template || '';
    _defaultPostHistoryTemplate = data.post_history_template || '';
    return {
        template: _defaultTemplate,
        postHistoryTemplate: _defaultPostHistoryTemplate,
    };
}

/** Fill the help modal's read-only default-template blocks (for copy/paste). */
export async function populateDefaultTemplateHelp() {
    const sysEl = document.getElementById('prompt-help-default-system');
    const postEl = document.getElementById('prompt-help-default-post-history');
    if (!sysEl && !postEl) return;
    try {
        const defaults = await getDefaultTemplates();
        if (sysEl) sysEl.textContent = defaults.template;
        if (postEl) postEl.textContent = defaults.postHistoryTemplate;
    } catch (e) {
        console.warn('Failed to load default templates:', e);
    }
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
    // System and User content currently on screen.
    await savePromptFields();
    window.location.href = `/api/system-prompts/${state.activeSystemPromptId}/export`;
}

export function previewSystemPrompt() {
    syncActivePromptFromEditors();

    const payload = previewChatPayload();
    if (el.promptPreviewContent) {
        el.promptPreviewContent.textContent = JSON.stringify(payload, null, 2);
    }
    if (el.promptPreviewModal) el.promptPreviewModal.hidden = false;
}

export function switchPromptBuilderMode(mode) {
    const selected = mode === 'post-history' ? 'post-history' : 'system';
    document.querySelectorAll('[data-prompt-builder-tab]').forEach(btn => {
        const active = btn.dataset.promptBuilderTab === selected;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
    });
    if (el.syspromptContent) el.syspromptContent.hidden = selected !== 'system';
    if (el.postHistoryContent) el.postHistoryContent.hidden = selected !== 'post-history';
}
