import { state, el } from './state.js';
import { saveLLMSettings } from './llm-settings.js';
import { showToast } from './utils.js';
import { previewChatPayload } from './request-builder.js';

// ═══════════════════════════════════════════════════════════════════════════
// SYSTEM PROMPTS
// ═══════════════════════════════════════════════════════════════════════════

export async function loadSystemPrompts(existingSettings = null) {
    try {
        const promptsRes = await fetch('/api/system-prompts');
        state.systemPrompts = await promptsRes.json();
        const settings = existingSettings || await fetch('/api/settings').then(r => r.json());
        state.activeSystemPromptId = settings.active_system_prompt
            ? Number(settings.active_system_prompt) : null;

        // Populate dropdown
        if (!el.syspromptSelect) return;
        el.syspromptSelect.innerHTML = '';
        if (state.systemPrompts.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.disabled = true;
            opt.selected = true;
            opt.textContent = 'No prompts \u2014 create one';
            el.syspromptSelect.appendChild(opt);
            if (el.syspromptContent) el.syspromptContent.value = '';
            return;
        }
        state.systemPrompts.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            opt.selected = p.id === state.activeSystemPromptId;
            el.syspromptSelect.appendChild(opt);
        });
        // If no active prompt set, select the first one
        if (!state.activeSystemPromptId || !state.systemPrompts.find(p => p.id === state.activeSystemPromptId)) {
            state.activeSystemPromptId = state.systemPrompts[0].id;
            el.syspromptSelect.value = state.activeSystemPromptId;
            saveLLMSettings({ active_system_prompt: String(state.activeSystemPromptId) });
        }
        // Load content of the active prompt
        const active = state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
        if (el.syspromptContent) el.syspromptContent.value = active ? active.content : '';
    } catch (e) { console.warn('Failed to load system prompts:', e); }
}

export async function selectSystemPrompt(id) {
    state.activeSystemPromptId = Number(id);
    saveLLMSettings({ active_system_prompt: String(id) });
    const active = state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
    if (el.syspromptContent) el.syspromptContent.value = active ? active.content : '';
}

export async function createSystemPrompt() {
    const name = prompt('New prompt name:');
    if (!name || !name.trim()) return;
    try {
        const res = await fetch('/api/system-prompts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim() }),
        });
        if (!res.ok) return;
        const created = await res.json();
        await loadSystemPrompts();
        selectSystemPrompt(created.id);
        if (el.syspromptSelect) el.syspromptSelect.value = created.id;
        showToast('Prompt created', 'success');
    } catch (e) { console.warn('Failed to create system prompt:', e); }
}

export async function deleteSystemPrompt() {
    if (!state.activeSystemPromptId) return;
    const active = state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
    if (!confirm(`Delete prompt "${active?.name || ''}"?`)) return;
    try {
        await fetch(`/api/system-prompts/${state.activeSystemPromptId}`, { method: 'DELETE' });
        state.activeSystemPromptId = null;
        await loadSystemPrompts();
        showToast('Prompt deleted', 'success');
    } catch (e) { console.warn('Failed to delete system prompt:', e); }
}

export async function updateSystemPromptContent() {
    if (!state.activeSystemPromptId || !el.syspromptContent) return;
    const content = el.syspromptContent.value;
    // Update in-memory state immediately so buildChatPayload always sees the latest
    const p = state.systemPrompts.find(sp => sp.id === state.activeSystemPromptId);
    if (p) p.content = content;
    try {
        await fetch(`/api/system-prompts/${state.activeSystemPromptId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
    } catch (e) { console.warn('Failed to update system prompt:', e); }
}

/** Explicit save (button-driven) — same write as the auto-save-on-blur, with a confirmation toast. */
export async function saveActiveSystemPrompt() {
    if (!state.activeSystemPromptId) return;
    try {
        await updateSystemPromptContent();
        showToast('Prompt saved', 'success');
    } catch (e) {
        showToast('Failed to save prompt');
        console.warn(e);
    }
}

let _defaultTemplate = null;
async function getDefaultTemplate() {
    if (_defaultTemplate !== null) return _defaultTemplate;
    const res = await fetch('/api/system-prompts/default-template');
    const data = await res.json();
    _defaultTemplate = data.template || '';
    return _defaultTemplate;
}

export async function resetSystemPromptToDefault() {
    if (!state.activeSystemPromptId) return;
    if (!confirm('Reset prompt content to the default template?')) return;
    const tpl = await getDefaultTemplate();
    if (el.syspromptContent) el.syspromptContent.value = tpl;
    const sp = state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
    if (sp) sp.content = tpl;
    await updateSystemPromptContent();
    showToast('Reset to default', 'success');
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
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch('/api/system-prompts/import', { method: 'POST', body: fd });
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.error || 'Import failed');
        }
        const created = await res.json();
        await loadSystemPrompts();
        await selectSystemPrompt(created.id);
        if (el.syspromptSelect) el.syspromptSelect.value = created.id;
        showToast('Prompt imported', 'success');
    } catch (err) {
        showToast('Import failed: ' + err.message);
    }
}

export function exportSystemPrompt() {
    if (!state.activeSystemPromptId) {
        showToast('No prompt selected');
        return;
    }
    // Server emits the file with a Content-Disposition header.
    window.location.href = `/api/system-prompts/${state.activeSystemPromptId}/export`;
}

export function previewSystemPrompt() {
    // Reflect any unsaved textarea edits into the in-memory active prompt so
    // the preview matches what the user is currently looking at.
    const sp = state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
    if (sp && el.syspromptContent) sp.content = el.syspromptContent.value;

    const payload = previewChatPayload();
    if (el.promptPreviewContent) {
        el.promptPreviewContent.textContent = JSON.stringify(payload, null, 2);
    }
    if (el.promptPreviewModal) el.promptPreviewModal.hidden = false;
}
