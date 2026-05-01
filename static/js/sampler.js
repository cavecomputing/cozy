import { state, el } from './state.js';

// ═══════════════════════════════════════════════════════════════════════════
// SAMPLER SETTINGS
// ═══════════════════════════════════════════════════════════════════════════

export const SAMPLER_DEFAULTS = {
    sampler_temperature:        '1.0',
    sampler_dynatemp_range:     '0',
    sampler_dynatemp_exponent:  '1.0',
    sampler_top_p:              '1.0',
    sampler_top_k:              '0',
    sampler_min_p:              '0',
    sampler_typical_p:          '1.0',
    sampler_top_n_sigma:        '-1',
    sampler_tfs_z:              '1.0',
    sampler_repetition_penalty: '1.0',
    sampler_repeat_last_n:      '64',
    sampler_presence_penalty:   '0',
    sampler_frequency_penalty:  '0',
    sampler_dry_multiplier:     '0',
    sampler_dry_base:           '1.75',
    sampler_dry_allowed_length: '2',
    sampler_dry_penalty_last_n: '-1',
    sampler_mirostat:           '0',
    sampler_mirostat_tau:       '5.0',
    sampler_mirostat_eta:       '0.1',
    sampler_xtc_probability:    '0',
    sampler_xtc_threshold:      '0.1',
    sampler_max_tokens:         '512',
    sampler_seed:               '-1',
};

export const SAMPLER_FIELDS = {
    sampler_temperature:        'samplerTemperature',
    sampler_dynatemp_range:     'samplerDyntempRange',
    sampler_dynatemp_exponent:  'samplerDyntempExp',
    sampler_top_p:              'samplerTopP',
    sampler_top_k:              'samplerTopK',
    sampler_min_p:              'samplerMinP',
    sampler_typical_p:          'samplerTypicalP',
    sampler_top_n_sigma:        'samplerTopNSigma',
    sampler_tfs_z:              'samplerTfsZ',
    sampler_repetition_penalty: 'samplerRepPenalty',
    sampler_repeat_last_n:      'samplerRepeatLastN',
    sampler_presence_penalty:   'samplerPresencePen',
    sampler_frequency_penalty:  'samplerFrequencyPen',
    sampler_dry_multiplier:     'samplerDryMult',
    sampler_dry_base:           'samplerDryBase',
    sampler_dry_allowed_length: 'samplerDryAllowed',
    sampler_dry_penalty_last_n: 'samplerDryLastN',
    sampler_mirostat:           'samplerMirostat',
    sampler_mirostat_tau:       'samplerMirostatTau',
    sampler_mirostat_eta:       'samplerMirostatEta',
    sampler_xtc_probability:    'samplerXtcProb',
    sampler_xtc_threshold:      'samplerXtcThresh',
    sampler_max_tokens:         'samplerMaxTokens',
    sampler_seed:               'samplerSeed',
};

export const SAMPLER_GROUPS = {
    temperature:        { label: 'Temperature',        fields: ['sampler_temperature'] },
    dynatemp:           { label: 'Dynamic Temp',       fields: ['sampler_dynatemp_range', 'sampler_dynatemp_exponent'] },
    top_p:              { label: 'Top-P',              fields: ['sampler_top_p'] },
    top_k:              { label: 'Top-K',              fields: ['sampler_top_k'] },
    min_p:              { label: 'Min-P',              fields: ['sampler_min_p'] },
    typical_p:          { label: 'Typical-P',          fields: ['sampler_typical_p'] },
    top_n_sigma:        { label: 'Top-N Sigma',        fields: ['sampler_top_n_sigma'] },
    tfs_z:              { label: 'TFS-Z',              fields: ['sampler_tfs_z'] },
    repetition_penalty: { label: 'Rep. Penalty',       fields: ['sampler_repetition_penalty', 'sampler_repeat_last_n'] },
    presence_penalty:   { label: 'Presence Penalty',   fields: ['sampler_presence_penalty'] },
    frequency_penalty:  { label: 'Frequency Penalty',  fields: ['sampler_frequency_penalty'] },
    dry:                { label: 'DRY',                fields: ['sampler_dry_multiplier', 'sampler_dry_base', 'sampler_dry_allowed_length', 'sampler_dry_penalty_last_n'] },
    mirostat:           { label: 'Mirostat',           fields: ['sampler_mirostat', 'sampler_mirostat_tau', 'sampler_mirostat_eta'] },
    xtc:                { label: 'XTC',                fields: ['sampler_xtc_probability', 'sampler_xtc_threshold'] },
    max_tokens:         { label: 'Max Tokens',         fields: ['sampler_max_tokens'] },
    seed:               { label: 'Seed',               fields: ['sampler_seed'] },
};

// Reverse map: field key → group key
export const FIELD_TO_GROUP = {};
for (const [group, info] of Object.entries(SAMPLER_GROUPS)) {
    for (const field of info.fields) FIELD_TO_GROUP[field] = group;
}

export const DEFAULT_ACTIVE_GROUPS = new Set([
    'temperature', 'top_p', 'top_k', 'min_p', 'max_tokens', 'repetition_penalty',
]);

export const CORE_GROUPS = new Set([
    'temperature', 'dynatemp', 'top_p', 'top_k', 'min_p', 'max_tokens', 'repetition_penalty',
]);

export const INT_PARAMS = new Set([
    'max_tokens', 'top_k', 'repeat_last_n', 'dry_allowed_length',
    'dry_penalty_last_n', 'mirostat', 'seed',
]);

function getActiveSamplers(settings) {
    const raw = settings.active_samplers;
    if (raw === undefined || raw === null) return new Set(DEFAULT_ACTIVE_GROUPS);
    if (raw === '') return new Set();
    let keys = raw.split(',').filter(k => k in SAMPLER_GROUPS);
    // Migration: old format used sampler_* field keys
    if (keys.length === 0) {
        keys = raw.split(',').map(k => k.replace('sampler_', '')).filter(k => k in SAMPLER_GROUPS);
    }
    return keys.length ? new Set(keys) : new Set(DEFAULT_ACTIVE_GROUPS);
}

export function renderSamplerPopover() {
    const pop = el.samplerPopover;
    if (!pop) return;
    pop.innerHTML = '';
    for (const [group, info] of Object.entries(SAMPLER_GROUPS)) {
        const row = document.createElement('label');
        row.className = 'sampler-popover-row';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = state.activeSamplers.has(group);
        cb.addEventListener('change', () => toggleSampler(group, cb.checked));
        const span = document.createElement('span');
        span.textContent = info.label;
        row.appendChild(cb);
        row.appendChild(span);
        if (!CORE_GROUPS.has(group)) {
            const badge = document.createElement('span');
            badge.className = 'sampler-advanced-badge';
            badge.textContent = 'ADV';
            badge.title = 'Advanced sampler — niche or experimental, off by default';
            row.appendChild(badge);
        }
        pop.appendChild(row);
    }
}

export function toggleSampler(group, active) {
    if (active) {
        state.activeSamplers.add(group);
    } else {
        state.activeSamplers.delete(group);
    }
    applySamplerVisibility();
    if (active && !CORE_GROUPS.has(group)) {
        document.querySelector('.sampler-advanced-card')?.setAttribute('open', '');
    }
    // Import saveLLMSettings lazily to avoid circular dependency
    import('./llm-settings.js').then(mod => mod.saveLLMSettings({ active_samplers: [...state.activeSamplers].join(',') }));
}

export function applySamplerVisibility() {
    for (const group of Object.keys(SAMPLER_GROUPS)) {
        const active = state.activeSamplers.has(group);
        // Multi-param groups use a .sampler-group wrapper; single-param use .settings-row directly
        const wrapper = document.querySelector(`.sampler-group[data-sampler="${group}"]`);
        if (wrapper) {
            wrapper.hidden = !active;
        } else {
            const row = document.querySelector(`.settings-row[data-sampler="${group}"]`);
            if (row) row.hidden = !active;
        }
    }
    const activeGroups = [...state.activeSamplers];
    const hasCoreActive = activeGroups.some(group => CORE_GROUPS.has(group));
    const hasAdvancedActive = activeGroups.some(group => !CORE_GROUPS.has(group));
    if (el.samplerCoreEmpty) el.samplerCoreEmpty.hidden = hasCoreActive;
    if (el.samplerAdvancedEmpty) el.samplerAdvancedEmpty.hidden = hasAdvancedActive;
}

export function loadSamplerSettings(settings) {
    for (const [key, elName] of Object.entries(SAMPLER_FIELDS)) {
        if (el[elName]) el[elName].value = settings[key] || SAMPLER_DEFAULTS[key];
    }
    if (el.settingsContextSize) el.settingsContextSize.value = settings.context_max_messages || '0';
    state.activeSamplers = getActiveSamplers(settings);
    renderSamplerPopover();
    applySamplerVisibility();
    const hasAdvancedActive = [...state.activeSamplers].some(group => !CORE_GROUPS.has(group));
    const advanced = document.querySelector('.sampler-advanced-card');
    if (advanced) advanced.open = hasAdvancedActive;
}

const AVG_TOKENS_PER_MSG = 150;

export function updateContextSizeWarning() {
    if (!el.contextSizeWarning) return;
    const limit = parseInt(el.settingsContextSize?.value || '0', 10);
    const ctxLen = state.modelContextLength;
    if (!limit || limit <= 0 || !ctxLen) {
        el.contextSizeWarning.textContent = '';
        el.contextSizeWarning.hidden = true;
        return;
    }
    const estimated = limit * AVG_TOKENS_PER_MSG;
    if (estimated > ctxLen * 0.9) {
        el.contextSizeWarning.textContent =
            `\u26a0 This may exceed the model's context window (~${ctxLen.toLocaleString()} tokens).`;
        el.contextSizeWarning.hidden = false;
    } else {
        el.contextSizeWarning.textContent = '';
        el.contextSizeWarning.hidden = true;
    }
}
