import { state, el } from './state.js';
import { loadSamplerSettings, updateContextSizeWarning } from './sampler.js';
import { API } from './api.js';
import { showToast } from './utils.js';

// ═══════════════════════════════════════════════════════════════════════════
// LLM SETTINGS
// ═══════════════════════════════════════════════════════════════════════════

export async function loadLLMSettings() {
    try {
        const res = await fetch('/api/settings');
        const s = await res.json();
        applySettingsToUI(s);
        // Load sampler settings from the same response
        loadSamplerSettings(s);
        // Load thinking settings
        if (el.sendThinking) el.sendThinking.checked = s.send_thinking === '1';
        // Load presets
        await loadPresets();
        return s;
    } catch (e) { console.warn('Failed to load LLM settings:', e); }
}

export async function saveLLMSettings(fields) {
    try {
        await fetch('/api/settings', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(fields),
        });
    } catch (e) { console.warn('Failed to save LLM settings:', e); }
}

function renderModelMenu(models) {
    if (!el.modelPickerMenu) return;
    el.modelPickerMenu.innerHTML = '';
    if (!models || models.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'model-picker-empty';
        empty.textContent = 'No models found';
        el.modelPickerMenu.appendChild(empty);
        return;
    }
    const current = el.apiModel?.value || '';
    for (const m of models) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'model-picker-item';
        btn.dataset.model = m;
        btn.textContent = m;
        btn.setAttribute('role', 'option');
        if (m === current) btn.classList.add('active');
        el.modelPickerMenu.appendChild(btn);
    }
}

export function closeModelMenu() {
    if (!el.modelPickerMenu) return;
    el.modelPickerMenu.hidden = true;
    if (el.refreshModels) el.refreshModels.setAttribute('aria-expanded', 'false');
}

export function selectModelFromMenu(name) {
    if (!el.apiModel) return;
    el.apiModel.value = name;
    state.apiModel = name;
    state.modelContextLength = state.modelDetails[name] ?? null;
    saveLLMSettings({ api_model: name });
    updateContextSizeWarning();
    closeModelMenu();
}

/** Fetch the model list from the configured endpoint and open the picker menu. */
export async function fetchModels() {
    if (!el.refreshModels || !el.apiModel || !el.modelPickerMenu) return;
    // Toggle off if already open
    if (!el.modelPickerMenu.hidden) { closeModelMenu(); return; }

    el.refreshModels.classList.add('spinning');
    el.testResult.textContent = '';
    el.testResult.className = 'settings-test-result';
    try {
        const res = await fetch('/api/llm/models');
        const body = await res.json();
        if (!res.ok) throw new Error(body.error || 'Failed');
        state.modelDetails = body.model_details || {};
        renderModelMenu(body.models || []);
        el.modelPickerMenu.hidden = false;
        el.refreshModels.setAttribute('aria-expanded', 'true');
        state.modelContextLength = state.modelDetails[el.apiModel.value] ?? null;
        updateContextSizeWarning();
    } catch (e) {
        renderModelMenu([]);
        const empty = el.modelPickerMenu.querySelector('.model-picker-empty');
        if (empty) empty.textContent = 'Failed to fetch: ' + e.message;
        el.modelPickerMenu.hidden = false;
        el.refreshModels.setAttribute('aria-expanded', 'true');
    } finally {
        el.refreshModels.classList.remove('spinning');
    }
}

export async function testLLMConnection() {
    if (!el.testApi || !el.testResult) return;
    el.testApi.disabled = true;
    el.testResult.textContent = 'Testing\u2026';
    el.testResult.className = 'settings-test-result';
    try {
        const res = await fetch('/api/llm/test', {method: 'POST'});
        const body = await res.json();
        if (body.ok) {
            el.testResult.textContent = 'Connected! Reply: ' + body.reply;
            el.testResult.className = 'settings-test-result success';
        } else {
            el.testResult.textContent = body.error || 'Failed';
            el.testResult.className = 'settings-test-result error';
        }
    } catch (e) {
        el.testResult.textContent = 'Error: ' + e.message;
        el.testResult.className = 'settings-test-result error';
    } finally {
        el.testApi.disabled = false;
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// API PRESETS
// ═══════════════════════════════════════════════════════════════════════════

function updatePresetButtonStates() {
    const hasActive = !!el.apiPreset?.value;
    if (el.presetSave)   el.presetSave.disabled   = !hasActive;
    if (el.presetDelete) el.presetDelete.disabled = !hasActive;
}

function renderPresetDropdown() {
    if (!el.apiPreset) return;
    const selected = el.apiPreset.value || (state.activePresetId ? String(state.activePresetId) : '');
    el.apiPreset.innerHTML = '';

    // Placeholder option — visible label when nothing is selected.
    // When presets exist, hide it from the menu so users can't re-select "nothing".
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.disabled = true;
    placeholder.textContent = state.apiPresets.length === 0 ? '(no presets)' : 'Select preset…';
    if (state.apiPresets.length > 0) placeholder.hidden = true;
    placeholder.selected = !selected;
    el.apiPreset.appendChild(placeholder);

    for (const p of state.apiPresets) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        opt.selected = String(p.id) === selected;
        el.apiPreset.appendChild(opt);
    }
    updatePresetButtonStates();
}

export async function loadPresets() {
    try {
        state.apiPresets = await API.getPresets();
        renderPresetDropdown();
    } catch (e) { console.warn('Failed to load presets:', e); }
}

function applySettingsToUI(s) {
    state.apiEndpoint = s.api_endpoint || '';
    state.apiKeySet   = s.api_key_set || false;
    state.apiModel    = s.api_model || '';
    if (el.apiEndpoint) el.apiEndpoint.value = state.apiEndpoint;
    if (el.apiKey) {
        el.apiKey.value = state.apiKeySet ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : '';
        el.apiKey.placeholder = state.apiKeySet ? 'Key saved (edit to change)' : 'sk-...';
    }
    if (el.apiModel) el.apiModel.value = state.apiModel;
    if (el.settingsContextSize) el.settingsContextSize.value = s.context_max_messages || '0';
    state.lorebookScanDepthOverride = parseInt(s.lorebook_scan_depth_override || '0', 10) || 0;
    if (el.lorebookScanOverride) el.lorebookScanOverride.value = String(state.lorebookScanDepthOverride);
    state.lorebookAlwaysInjectAll = s.lorebook_always_inject_all === '1';
    if (el.lorebookAlwaysInjectAll) el.lorebookAlwaysInjectAll.checked = state.lorebookAlwaysInjectAll;
    state.modelContextLength = state.modelDetails[state.apiModel] ?? null;
    updateContextSizeWarning();
}

export async function activatePreset(id) {
    if (!id) { updatePresetButtonStates(); return; }
    try {
        const s = await API.activatePreset(id);
        applySettingsToUI(s);
        state.activePresetId = id;
        updatePresetButtonStates();
    } catch (e) {
        showToast('Failed to activate preset');
        console.warn(e);
    }
}

export async function createNewPreset() {
    const name = prompt('Preset name:');
    if (!name || !name.trim()) return;
    try {
        const created = await API.createPreset({
            name: name.trim(),
            api_endpoint: el.apiEndpoint?.value || '',
            api_key: el.apiKey?.value || '',
            api_model: el.apiModel?.value || '',
            context_max_messages: el.settingsContextSize?.value || '0',
        });
        await loadPresets();
        if (created?.id && el.apiPreset) {
            el.apiPreset.value = String(created.id);
            state.activePresetId = created.id;
            updatePresetButtonStates();
        }
        showToast('Preset created', 'success');
    } catch (e) {
        showToast('Failed to create preset');
        console.warn(e);
    }
}

export async function saveActivePreset() {
    const id = el.apiPreset?.value;
    if (!id) return;
    try {
        await API.updatePreset(id, {
            api_endpoint: el.apiEndpoint?.value || '',
            api_key: el.apiKey?.value || '',
            api_model: el.apiModel?.value || '',
            context_max_messages: el.settingsContextSize?.value || '0',
        });
        showToast('Preset updated', 'success');
    } catch (e) {
        showToast('Failed to update preset');
        console.warn(e);
    }
}

export async function deletePreset() {
    const id = el.apiPreset?.value;
    if (!id) return;
    const preset = state.apiPresets.find(p => String(p.id) === id);
    if (!confirm(`Delete preset "${preset?.name || id}"?`)) return;
    try {
        await API.deletePreset(id);
        state.activePresetId = null;
        await loadPresets();
        showToast('Preset deleted', 'success');
    } catch (e) {
        showToast('Failed to delete preset');
        console.warn(e);
    }
}
