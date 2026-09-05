import { state, el } from './state.js';
import { loadSamplerSettings, updateContextSizeWarning, SAMPLER_FIELDS } from './sampler.js';
import { API } from './api.js';
import { showToast } from './utils.js';
import { confirmDialog } from './confirm.js';

const MODEL_SEARCH_DEBOUNCE_MS = 250;
const SETTINGS_SAVE_DEBOUNCE_MS = 500;
let settingsSaveTimer = null;
let pendingSettings = {};
let settingsDrainInFlight = null;
const modelPickers = {
    main: {
        input: 'apiModel',
        button: 'refreshModels',
        menu: 'modelPickerMenu',
        models: [],
        details: {},
        searchTimer: null,
        requestId: 0,
        loaded: false,
    },
    summary: {
        input: 'summaryModel',
        button: 'summaryRefreshModels',
        menu: 'summaryModelPickerMenu',
        models: [],
        details: {},
        searchTimer: null,
        requestId: 0,
        loaded: false,
    },
};

// ═══════════════════════════════════════════════════════════════════════════
// LLM SETTINGS
// ═══════════════════════════════════════════════════════════════════════════

export async function loadLLMSettings() {
    try {
        const s = await API.getSettings();
        applySettingsToUI(s);
        // Load sampler settings from the same response
        loadSamplerSettings(s);
        // Load presets
        await loadPresets();
        return s;
    } catch (e) { console.warn('Failed to load LLM settings:', e); }
}

export async function saveLLMSettings(fields) {
    try {
        await API.saveSettings(fields);
    } catch (e) {
        console.warn('Failed to save LLM settings:', e);
        showToast('Failed to save settings: ' + e.message);
    }
}

/** Queue API/sampler edits so typing produces one settings + preset update. */
export function queueLLMSettingsSave(fields) {
    Object.assign(pendingSettings, fields);
    clearTimeout(settingsSaveTimer);
    settingsSaveTimer = setTimeout(flushLLMSettingsSave, SETTINGS_SAVE_DEBOUNCE_MS);
}

/** Queue a main API key edit, including an explicit empty-string clear. */
export function queueMainApiKeySave(value) {
    const v = value == null ? '' : String(value);
    // A rendered mask is only a placeholder for the stored secret. Empty is a
    // real edit and must reach the backend so users can clear the key.
    if (v && (v.startsWith('\u2022\u2022') || v.includes('\u2026'))) return false;
    clearModelListCache();
    queueLLMSettingsSave({ api_key: v });
    return true;
}

async function drainLLMSettingsQueue() {
    // There is only ever one drain. Besides making the barrier deterministic,
    // this prevents an older failed snapshot from being restored behind a
    // newer same-field snapshot and then overwriting it on retry.
    while (Object.keys(pendingSettings).length > 0) {
        clearTimeout(settingsSaveTimer);
        settingsSaveTimer = null;

        const fields = pendingSettings;
        pendingSettings = {};
        const presetId = state.activePresetId ? String(state.activePresetId) : null;

        try {
            const presetSnapshot = presetId ? collectPresetSettings() : null;
            await API.saveSettings(fields);
            if (presetId) await API.updatePreset(presetId, presetSnapshot);
        } catch (error) {
            // Newer edits accumulated while this snapshot was saving. They win
            // field-by-field when the failed snapshot is put back for retry.
            pendingSettings = { ...fields, ...pendingSettings };
            console.warn('Failed to autosave LLM settings:', error);
            showToast('Failed to save settings: ' + error.message);
            return { ok: false, error };
        }
    }
    return { ok: true, error: null };
}

/** Persist queued edits immediately, preserving their active-preset target. */
export async function flushLLMSettingsSave({ strict = false } = {}) {
    clearTimeout(settingsSaveTimer);
    settingsSaveTimer = null;

    // Strict and debounced callers share the same drain and therefore observe
    // the same result. A background flush cannot consume an error and let a
    // simultaneous send continue with stale server settings.
    if (!settingsDrainInFlight) {
        const drain = drainLLMSettingsQueue();
        settingsDrainInFlight = drain;
        const clearDrain = () => {
            if (settingsDrainInFlight === drain) settingsDrainInFlight = null;
        };
        // Register both branches explicitly so cleanup never creates a second
        // unobserved rejected promise.
        void drain.then(clearDrain, clearDrain);
    }
    const result = await settingsDrainInFlight;
    if (!result.ok && strict) {
        throw new Error(`Settings could not be saved: ${result.error.message}`);
    }
    return result;
}

function pickerElements(profile) {
    const picker = modelPickers[profile];
    return {
        picker,
        input: el[picker.input],
        button: el[picker.button],
        menu: el[picker.menu],
    };
}

function setModelMenuOpen(profile, open) {
    const { input, button, menu } = pickerElements(profile);
    if (!menu) return;
    menu.hidden = !open;
    button?.setAttribute('aria-expanded', String(open));
    input?.setAttribute('aria-expanded', String(open));
}

function renderModelMenu(profile, models, emptyText = 'No models found') {
    const { input, menu } = pickerElements(profile);
    if (!menu) return;
    menu.innerHTML = '';
    if (!models || models.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'model-picker-empty';
        empty.textContent = emptyText;
        menu.appendChild(empty);
        return;
    }
    const current = input?.value || '';
    models.forEach((m, i) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'model-picker-item';
        btn.dataset.model = m;
        btn.textContent = m;
        btn.setAttribute('role', 'option');
        btn.id = `${profile}-model-picker-option-${i}`;
        if (m === current) btn.classList.add('active');
        menu.appendChild(btn);
    });
}

function closePicker(profile) {
    setModelMenuOpen(profile, false);
}

function selectPickerModel(profile, name) {
    const { input, picker } = pickerElements(profile);
    if (!input) return;
    input.value = name;
    if (profile === 'summary') {
        state.summaryApiModel = name;
        queueLLMSettingsSave({ summary_api_model: name });
    } else {
        state.apiModel = name;
        state.modelContextLength = picker.details[name] ?? null;
        queueLLMSettingsSave({ api_model: name });
        updateContextSizeWarning();
    }
    closePicker(profile);
}

function filterModels(profile, query) {
    const { picker } = pickerElements(profile);
    const q = query.trim().toLowerCase();
    if (!q) return picker.models;
    return picker.models.filter(m => m.toLowerCase().includes(q));
}

async function loadModels(profile) {
    const { picker, input } = pickerElements(profile);
    const requestId = ++picker.requestId;
    const body = await API.getModels(profile);
    if (requestId !== picker.requestId) return false;
    picker.models = body.models || [];
    picker.details = body.model_details || {};
    picker.loaded = true;
    if (profile === 'main') {
        state.modelList = picker.models;
        state.modelDetails = picker.details;
        state.modelContextLength = picker.details[input?.value || ''] ?? null;
        updateContextSizeWarning();
    }
    return true;
}

function openModelSearchResults(profile, query) {
    const trimmed = query.trim();
    const matches = filterModels(profile, query);
    renderModelMenu(profile, matches, trimmed ? 'No matching models' : 'No models found');
    setModelMenuOpen(profile, true);
}

async function fetchPickerModels(profile, { force = false, filter = '' } = {}) {
    const { picker, input, button, menu } = pickerElements(profile);
    if (!button || !input || !menu) return;
    if (!force && !filter && !menu.hidden) {
        closePicker(profile);
        return;
    }

    button.classList.add('spinning');
    if (profile === 'main' && el.testResult) {
        el.testResult.textContent = '';
        el.testResult.className = 'settings-test-result';
    }
    try {
        await flushLLMSettingsSave({ strict: true });
        if (force || !picker.loaded) await loadModels(profile);
        if (filter) {
            openModelSearchResults(profile, filter);
        } else {
            renderModelMenu(profile, picker.models);
            setModelMenuOpen(profile, true);
        }
    } catch (e) {
        renderModelMenu(profile, [], 'Failed to fetch: ' + e.message);
        const empty = menu.querySelector('.model-picker-empty');
        if (empty) empty.textContent = 'Failed to fetch: ' + e.message;
        setModelMenuOpen(profile, true);
    } finally {
        button.classList.remove('spinning');
    }
}

function browsePickerModels(profile) {
    return fetchPickerModels(profile, { force: true });
}

function searchPickerModels(profile) {
    const { picker, input, menu } = pickerElements(profile);
    if (!input || !menu) return;
    clearTimeout(picker.searchTimer);
    const query = input.value;
    picker.searchTimer = setTimeout(async () => {
        const trimmed = query.trim();
        if (!trimmed) {
            closePicker(profile);
            return;
        }

        if (picker.loaded) {
            openModelSearchResults(profile, query);
            return;
        }

        renderModelMenu(profile, [], 'Loading models...');
        setModelMenuOpen(profile, true);
        await fetchPickerModels(profile, { filter: query });
    }, MODEL_SEARCH_DEBOUNCE_MS);
}

function clearPickerCache(profile) {
    const { picker } = pickerElements(profile);
    picker.requestId += 1;
    picker.loaded = false;
    picker.models = [];
    picker.details = {};
    closePicker(profile);
    if (profile === 'main') {
        state.modelList = [];
        state.modelDetails = {};
        state.modelContextLength = null;
    }
}

export const fetchModels = options => fetchPickerModels('main', options);
export const browseModels = () => fetchModels({ force: true });
export const browseSummaryModels = () => browsePickerModels('summary');
export const searchModelsFromInput = () => searchPickerModels('main');
export const searchSummaryModelsFromInput = () => searchPickerModels('summary');
export const closeModelMenu = () => closePicker('main');
export const closeSummaryModelMenu = () => closePicker('summary');
export const selectModelFromMenu = name => selectPickerModel('main', name);
export const selectSummaryModelFromMenu = name => selectPickerModel('summary', name);
export const clearModelListCache = () => clearPickerCache('main');
export const clearSummaryModelListCache = () => clearPickerCache('summary');

export async function testLLMConnection() {
    if (!el.testApi || !el.testResult) return;
    el.testApi.disabled = true;
    el.testResult.textContent = 'Testing\u2026';
    el.testResult.className = 'settings-test-result';
    try {
        await flushLLMSettingsSave({ strict: true });
        const body = await API.testLLM();
        el.testResult.textContent = 'Connected! Reply: ' + body.reply;
        el.testResult.className = 'settings-test-result success';
    } catch (e) {
        el.testResult.textContent = e.message || 'Failed';
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
    if (el.presetDelete) el.presetDelete.disabled = !hasActive;
}

function renderPresetDropdown() {
    if (!el.apiPreset) return;
    const selected = el.apiPreset.value || (state.activePresetId ? String(state.activePresetId) : '');
    const selectedExists = state.apiPresets.some(p => String(p.id) === selected);
    el.apiPreset.innerHTML = '';

    // Placeholder option — visible label when nothing is selected.
    // When presets exist, hide it from the menu so users can't re-select "nothing".
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.disabled = true;
    placeholder.textContent = state.apiPresets.length === 0 ? '(no presets)' : 'Select preset…';
    if (state.apiPresets.length > 0) placeholder.hidden = true;
    placeholder.selected = !selectedExists;
    el.apiPreset.appendChild(placeholder);

    for (const p of state.apiPresets) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        opt.selected = String(p.id) === selected && selectedExists;
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
    const apiEndpoint = s.api_endpoint || '';
    const apiKeySet = s.api_key_set || false;
    state.apiModel    = s.api_model || '';
    state.activePresetId = s.active_api_preset || null;
    if (el.apiEndpoint) el.apiEndpoint.value = apiEndpoint;
    if (el.apiKey) {
        el.apiKey.value = apiKeySet ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : '';
        el.apiKey.placeholder = apiKeySet ? 'Key saved (edit to change)' : 'sk-...';
    }
    if (el.apiModel) el.apiModel.value = state.apiModel;
    if (el.settingsContextTokens) el.settingsContextTokens.value = s.context_max_tokens || '32768';
    state.contextMaxTokens = s.context_max_tokens || '32768';
    state.showContextTokenMeter = s.show_context_token_meter !== '0';
    if (el.settingsContextMeterToggle) el.settingsContextMeterToggle.checked = state.showContextTokenMeter;
    state.showAdvancedConfiguration = s.show_advanced_configuration === '1';
    if (el.settingsAdvancedToggle) el.settingsAdvancedToggle.checked = state.showAdvancedConfiguration;
    state.extraRequestParams = s.extra_request_params || '';
    if (el.extraParams) el.extraParams.value = state.extraRequestParams;
    state.lorebookScanDepthOverride = parseInt(s.lorebook_scan_depth_override || '0', 10) || 0;
    if (el.lorebookScanOverride) el.lorebookScanOverride.value = String(state.lorebookScanDepthOverride);
    state.lorebookAlwaysInjectAll = s.lorebook_always_inject_all === '1';
    if (el.lorebookAlwaysInjectAll) el.lorebookAlwaysInjectAll.checked = state.lorebookAlwaysInjectAll;
    // Auto Summaries config
    state.summaryApiEndpoint = s.summary_api_endpoint || '';
    state.summaryApiKeySet   = s.summary_api_key_set || false;
    state.summaryApiModel    = s.summary_api_model || '';
    state.summaryCapPct      = s.summary_cap_pct || '10';
    state.summaryTriggerInterval = s.summary_trigger_interval || '10';
    if (el.summaryEndpoint) el.summaryEndpoint.value = state.summaryApiEndpoint;
    if (el.summaryKey) {
        el.summaryKey.value = state.summaryApiKeySet ? '••••••••' : '';
        el.summaryKey.placeholder = state.summaryApiKeySet ? 'Key saved (edit to change)' : 'sk-...';
    }
    if (el.summaryModel) el.summaryModel.value = state.summaryApiModel;
    if (el.summaryCapInput) el.summaryCapInput.value = state.summaryCapPct;
    if (el.summaryIntervalInput) el.summaryIntervalInput.value = state.summaryTriggerInterval;
    state.modelContextLength = state.modelDetails[state.apiModel] ?? null;
}

// ── Advanced configuration gate ──────────────────────────────────────────
// Surfaces hidden unless "Show advanced configuration" is checked in the
// General tab: the Regex tab (nav item and page), the Prompt template
// editors + Variables help (the prompt selector stays visible), the
// Summaries Behavior card, the Extra request parameters card, and the
// Lorebooks global-overrides card.
export function applyAdvancedConfigurationVisibility() {
    const visible = Boolean(state.showAdvancedConfiguration);
    document.querySelectorAll(
        '.settings-section[data-section="prompt"] .prompt-vars-panel,'
        + ' .settings-section[data-section="prompt"] .prompt-section-right,'
        + ' #summary-behavior-card,'
        + ' #extra-params-card,'
        + ' #lorebook-overrides-card,'
        + ' .settings-nav-item[data-section="regex"]'
    ).forEach(node => { node.hidden = !visible; });
    // The Regex page itself still follows section switching: it is only
    // shown when advanced is on AND it is the current section.
    const regexSection = document.querySelector('.settings-section[data-section="regex"]');
    if (regexSection) regexSection.hidden = !visible || state.settingsSection !== 'regex';
}

/**
 * Show or hide the advanced configuration UI and remember the choice.
 * Shared by the General-tab checkbox, so gated pages update live.
 */
export function setAdvancedConfigurationVisible(visible) {
    state.showAdvancedConfiguration = visible;
    if (el.settingsAdvancedToggle) el.settingsAdvancedToggle.checked = visible;
    saveLLMSettings({ show_advanced_configuration: visible ? '1' : '0' });
    applyAdvancedConfigurationVisibility();
}

export async function activatePreset(id) {
    if (!id) { updatePresetButtonStates(); return; }
    try {
        await flushLLMSettingsSave({ strict: true });
        const s = await API.activatePreset(id);
        clearModelListCache();
        applySettingsToUI(s);
        loadSamplerSettings(s);
        updateContextSizeWarning();
        state.activePresetId = id;
        if (el.apiPreset) el.apiPreset.value = String(id);
        updatePresetButtonStates();
    } catch (e) {
        showToast('Failed to activate preset');
        console.warn(e);
    }
}

/** Snapshot the page's sampler/extra state so it can be bundled into
 *  a preset alongside the connection fields. */
function collectPresetSettings() {
    const out = {
        api_endpoint: el.apiEndpoint?.value || '',
        api_key: el.apiKey?.value || '',
        api_model: el.apiModel?.value || '',
        context_max_tokens: el.settingsContextTokens?.value || '32768',
    };
    for (const [key, elName] of Object.entries(SAMPLER_FIELDS)) {
        if (el[elName]) out[key] = el[elName].value;
    }
    out.active_samplers = [...state.activeSamplers].join(',');
    out.extra_request_params = el.extraParams?.value || '';
    return out;
}

export async function createNewPreset() {
    const name = prompt('Preset name:');
    if (!name || !name.trim()) return;
    try {
        await flushLLMSettingsSave({ strict: true });
        const created = await API.createPreset({
            name: name.trim(),
            ...collectPresetSettings(),
        });
        await loadPresets();
        if (created?.id && el.apiPreset) {
            await activatePreset(String(created.id));
            updatePresetButtonStates();
        }
        showToast('Preset created', 'success');
    } catch (e) {
        showToast(e?.message || 'Failed to create preset');
        console.warn(e);
    }
}

export async function deletePreset() {
    const id = el.apiPreset?.value;
    if (!id) return;
    const preset = state.apiPresets.find(p => String(p.id) === id);
    if (!(await confirmDialog({ title: `Delete preset "${preset?.name || id}"?` }))) return;
    try {
        await flushLLMSettingsSave({ strict: true });
        await API.deletePreset(id);
        state.activePresetId = null;
        await loadPresets();
        showToast('Preset deleted', 'success');
    } catch (e) {
        showToast('Failed to delete preset');
        console.warn(e);
    }
}
