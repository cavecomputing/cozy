import { state, el, icons } from './state.js';
import { API } from './api.js';
import { saveLLMSettings } from './llm-settings.js';
import { showToast } from './utils.js';
import { confirmDialog } from './confirm.js';
import {
    FLAG_LABELS, combineFilterFlags, filterError, runFilters, splitFilterFlags,
    splitSlashForm,
} from './regex-engine.js';

// ═══════════════════════════════════════════════════════════════════════════
// REGEX OUTPUT FILTERS
//
// A preset is a named, ordered list of find/replace filters run over the
// character's reply once the stream finishes, just before the message is
// saved. See regex-engine.js for the actual matching.
//
// There is no per-filter enable toggle by design: a filter is live when its
// Find pattern compiles, and selecting no preset is how you turn the lot off.
// ═══════════════════════════════════════════════════════════════════════════

const SAVE_DEBOUNCE_MS = 500;
const DEFAULT_TEST_SAMPLE = 'Sie blickte auf und lächelte. „Du bist spät dran," sagte sie leise.';

function activePreset() {
    return state.regexPresets.find(p => p.id === state.activeRegexPresetId) || null;
}

// ── Applied at the save points ────────────────────────────────────────────

/**
 * Rewrite a finished character reply. Called from send.js and messages.js —
 * the only two places a reply is committed.
 */
export function applyOutputFilters(text) {
    return runFilters(text, state.regexFilters);
}

// ── Debounced autosave ────────────────────────────────────────────────────

let saveTimer = null;
let pendingPresetId = null;

/**
 * Queue a write of the active preset's filter list.
 *
 * The target id is captured here rather than read at flush time, so an edit
 * made just before switching presets still lands on the preset it was typed
 * into instead of overwriting the newly selected one.
 */
function queueRegexSave() {
    pendingPresetId = state.activeRegexPresetId;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => { void flushRegexSave(); }, SAVE_DEBOUNCE_MS);
}

/** Persist any queued filter edits now. Safe to call when nothing is pending. */
export async function flushRegexSave() {
    clearTimeout(saveTimer);
    saveTimer = null;
    const id = pendingPresetId;
    pendingPresetId = null;
    if (id == null) return;

    const preset = state.regexPresets.find(p => p.id === id);
    if (!preset) return;  // deleted while the timer was pending
    try {
        await API.updateRegexPreset(id, { filters: preset.filters });
    } catch (e) {
        console.warn('Failed to autosave regex filters:', e);
        showToast('Failed to save filters: ' + e.message);
    }
}

// ── Reading the rows ──────────────────────────────────────────────────────

function readRow(row) {
    const val = sel => row.querySelector(`[data-field="${sel}"]`)?.value ?? '';
    const visibleFlags = FLAG_LABELS
        .map(([flag]) => flag)
        .filter(flag => row.querySelector(`[data-flag="${flag}"]`)?.checked)
        .join('');
    const flags = combineFilterFlags(visibleFlags, row.dataset.extraFlags);
    // Only the name is trimmed — whitespace is meaningful in a pattern, and a
    // replacement of a single space is a perfectly ordinary rule.
    return { name: val('name').trim(), find: val('find'), replace: val('replace'), flags };
}

function readRows() {
    if (!el.regexFilterList) return [];
    return [...el.regexFilterList.querySelectorAll('.regex-filter')].map(readRow);
}

/**
 * Pull the DOM back into state, refresh the error hints and preview, and queue
 * a save. State is updated on every keystroke (not just on save) so a reply
 * arriving mid-edit uses what is actually on screen.
 */
function syncFromRows({ save = true } = {}) {
    const filters = readRows();
    state.regexFilters = filters;
    const preset = activePreset();
    if (preset) preset.filters = filters;
    refreshRowErrors(filters);
    updateTestPanel();
    if (save && preset) queueRegexSave();
}

function refreshRowErrors(filters) {
    if (!el.regexFilterList) return;
    el.regexFilterList.querySelectorAll('.regex-filter').forEach((row, i) => {
        const message = filterError(filters[i]);
        const errEl = row.querySelector('.regex-filter-error');
        row.classList.toggle('has-error', Boolean(message));
        if (errEl) {
            errEl.textContent = message;
            errEl.hidden = !message;
        }
    });
}

// ── Rendering ─────────────────────────────────────────────────────────────

function buildFilterRow(filter, idx) {
    const row = document.createElement('div');
    row.className = 'regex-filter';
    row.dataset.index = String(idx);
    row.innerHTML = `
        <div class="regex-filter-header">
            <input type="text" class="form-input regex-filter-name" data-field="name"
                placeholder="Filter name (optional)">
            <button type="button" class="icon-btn regex-filter-up" title="Move up" aria-label="Move up">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"></polyline></svg>
            </button>
            <button type="button" class="icon-btn regex-filter-down" title="Move down" aria-label="Move down">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </button>
            <button type="button" class="icon-btn regex-filter-delete" title="Delete filter" aria-label="Delete filter">${icons.TRASH}</button>
        </div>
        <div class="regex-filter-row">
            <label class="regex-filter-field">
                <span>Find</span>
                <input type="text" class="form-input mono-input" data-field="find" spellcheck="false"
                    autocapitalize="off" autocorrect="off" placeholder="pattern to match">
            </label>
            <label class="regex-filter-field">
                <span>Replace</span>
                <input type="text" class="form-input mono-input" data-field="replace" spellcheck="false"
                    autocapitalize="off" autocorrect="off" placeholder="replacement text">
            </label>
        </div>
        <div class="regex-filter-meta">
            <span class="regex-filter-flags-label">Flags</span>
            ${FLAG_LABELS.map(([flag, gloss]) => `
                <label class="regex-filter-flag">
                    <input type="checkbox" data-flag="${flag}">
                    <code>${flag}</code> <span>${gloss}</span>
                </label>
            `).join('')}
        </div>
        <p class="regex-filter-error" hidden></p>
    `;

    // Populated imperatively so user text is never interpolated into markup.
    row.querySelector('[data-field="name"]').value = filter.name || '';
    row.querySelector('[data-field="find"]').value = filter.find || '';
    row.querySelector('[data-field="replace"]').value = filter.replace || '';
    const splitFlags = splitFilterFlags(filter.flags);
    row.dataset.extraFlags = splitFlags.hidden;
    for (const [flag] of FLAG_LABELS) {
        row.querySelector(`[data-flag="${flag}"]`).checked = splitFlags.visible.includes(flag);
    }
    return row;
}

function renderFilters(filters) {
    if (!el.regexFilterList) return;
    el.regexFilterList.innerHTML = '';
    filters.forEach((f, i) => el.regexFilterList.appendChild(buildFilterRow(f, i)));
    if (filters.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'regex-filter-empty';
        empty.textContent = activePreset()
            ? 'No filters yet. Use Add filter to create the first find and replace rule.'
            : 'Choose a preset above, or create one, to start adding filters.';
        el.regexFilterList.appendChild(empty);
    }
    refreshFilterCount();
    refreshRowErrors(filters);
    updateTestPanel();
}

function refreshFilterCount() {
    if (!el.regexFilterCount) return;
    const n = el.regexFilterList?.querySelectorAll('.regex-filter').length || 0;
    el.regexFilterCount.textContent = n ? `(${n})` : '';
}

function reindexRows() {
    el.regexFilterList?.querySelectorAll('.regex-filter').forEach((r, i) => {
        r.dataset.index = String(i);
    });
}

function setControlsEnabled() {
    const hasPreset = Boolean(activePreset());
    if (el.regexAddFilter) el.regexAddFilter.disabled = !hasPreset;
    if (el.regexPresetDelete) el.regexPresetDelete.disabled = !hasPreset;
    if (el.regexExport) el.regexExport.disabled = !hasPreset;
}

// ── Test panel ────────────────────────────────────────────────────────────

/**
 * Show the sample run through every filter, top to bottom. Filters compose, so
 * previewing the whole list is what actually surfaces surprises.
 */
export function updateTestPanel() {
    if (!el.regexTestOutput) return;
    const sample = el.regexTestInput?.value ?? '';
    if (!sample) {
        el.regexTestOutput.textContent = '';
        el.regexTestOutput.classList.remove('is-changed');
        return;
    }
    const result = runFilters(sample, state.regexFilters);
    el.regexTestOutput.textContent = result;
    el.regexTestOutput.classList.toggle('is-changed', result !== sample);
}

// ── Preset load / select / CRUD ───────────────────────────────────────────

export async function loadRegexPresets(existingSettings = null) {
    try {
        state.regexPresets = await API.getRegexPresets();
        const settings = existingSettings || await API.getSettings();
        const activeId = settings.active_regex_preset ? Number(settings.active_regex_preset) : null;
        // Unlike system prompts, never fall back to the first entry: an unset
        // selection is the intended off switch.
        state.activeRegexPresetId = state.regexPresets.some(p => p.id === activeId) ? activeId : null;
        state.regexFilters = activePreset()?.filters || [];

        renderPresetOptions();
        renderFilters(state.regexFilters);
        setControlsEnabled();
        if (el.regexTestInput && !el.regexTestInput.value) {
            el.regexTestInput.value = DEFAULT_TEST_SAMPLE;
            updateTestPanel();
        }
    } catch (e) {
        console.warn('Failed to load regex presets:', e);
    }
}

function renderPresetOptions() {
    const sel = el.regexPresetSelect;
    if (!sel) return;
    sel.innerHTML = '';
    if (state.regexPresets.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.disabled = true;
        opt.selected = true;
        opt.textContent = 'No presets — create one';
        sel.appendChild(opt);
        return;
    }
    // "None" is the off switch, so it stays selectable even once presets exist.
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'None — no filtering';
    none.selected = state.activeRegexPresetId == null;
    sel.appendChild(none);
    state.regexPresets.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        opt.selected = p.id === state.activeRegexPresetId;
        sel.appendChild(opt);
    });
}

export async function selectRegexPreset(id) {
    // Land any pending edit on the preset being left, not the one arriving.
    await flushRegexSave();
    const numeric = id === '' || id == null ? null : Number(id);
    state.activeRegexPresetId = numeric;
    state.regexFilters = activePreset()?.filters || [];
    saveLLMSettings({ active_regex_preset: numeric == null ? '' : String(numeric) });
    renderFilters(state.regexFilters);
    setControlsEnabled();
}

export async function createRegexPreset() {
    const name = prompt('New filter preset name:');
    if (!name || !name.trim()) return;
    try {
        await flushRegexSave();
        const created = await API.createRegexPreset({ name: name.trim(), filters: [] });
        state.regexPresets.push(created);
        state.activeRegexPresetId = created.id;
        state.regexFilters = created.filters || [];
        saveLLMSettings({ active_regex_preset: String(created.id) });
        renderPresetOptions();
        if (el.regexPresetSelect) el.regexPresetSelect.value = created.id;
        renderFilters(state.regexFilters);
        setControlsEnabled();
        showToast('Preset created', 'success');
    } catch (e) {
        showToast('Failed to create preset: ' + e.message);
    }
}

export async function deleteRegexPreset() {
    const preset = activePreset();
    if (!preset) return;
    if (!(await confirmDialog({ title: `Delete preset "${preset.name}"?` }))) return;
    try {
        // Drop any queued write for the preset about to disappear.
        clearTimeout(saveTimer);
        saveTimer = null;
        pendingPresetId = null;
        await API.deleteRegexPreset(preset.id);
        state.regexPresets = state.regexPresets.filter(p => p.id !== preset.id);
        state.activeRegexPresetId = null;
        state.regexFilters = [];
        renderPresetOptions();
        renderFilters([]);
        setControlsEnabled();
        showToast('Preset deleted', 'success');
    } catch (e) {
        showToast('Failed to delete preset: ' + e.message);
    }
}

// ── Filter row events ─────────────────────────────────────────────────────

export function addFilter() {
    if (!activePreset() || !el.regexFilterList) return;
    el.regexFilterList.querySelector('.regex-filter-empty')?.remove();
    const idx = el.regexFilterList.querySelectorAll('.regex-filter').length;
    // `g` on by default: without it only the first match is replaced, which for
    // text correction reads as "it only fixed the first one".
    el.regexFilterList.appendChild(buildFilterRow({ name: '', find: '', replace: '', flags: 'g' }, idx));
    refreshFilterCount();
    syncFromRows();
    el.regexFilterList.lastElementChild?.querySelector('[data-field="name"]')?.focus();
}

export function handleFilterListClick(e) {
    const row = e.target.closest('.regex-filter');
    if (!row) return;
    const idx = parseInt(row.dataset.index, 10);
    if (e.target.closest('.regex-filter-delete')) {
        row.remove();
        reindexRows();
        if (!el.regexFilterList.querySelector('.regex-filter')) {
            const filters = [];
            state.regexFilters = filters;
            const preset = activePreset();
            if (preset) preset.filters = filters;
            renderFilters(filters);
            queueRegexSave();
            return;
        }
        refreshFilterCount();
        syncFromRows();
    } else if (e.target.closest('.regex-filter-up') && idx > 0) {
        row.parentNode.insertBefore(row, row.previousElementSibling);
        reindexRows();
        syncFromRows();
    } else if (e.target.closest('.regex-filter-down') && row.nextElementSibling) {
        row.parentNode.insertBefore(row.nextElementSibling, row);
        reindexRows();
        syncFromRows();
    }
}

export function handleFilterListInput(e) {
    const field = e.target.closest('[data-field], [data-flag]');
    if (!field) return;
    // A pasted `/pattern/gi` is split into the pattern plus its flag boxes, so
    // scripts copied from SillyTavern work without hand-translation.
    if (field.dataset.field === 'find') {
        const parsed = splitSlashForm(field.value);
        if (parsed) {
            const row = field.closest('.regex-filter');
            field.value = parsed.find;
            const splitFlags = splitFilterFlags(parsed.flags);
            row.dataset.extraFlags = splitFlags.hidden;
            for (const [flag] of FLAG_LABELS) {
                const box = row.querySelector(`[data-flag="${flag}"]`);
                if (box) box.checked = splitFlags.visible.includes(flag);
            }
        }
    }
    syncFromRows();
}

// ── Import / export ───────────────────────────────────────────────────────

export function importRegexPreset() {
    el.regexImportFile?.click();
}

export async function handleRegexImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    try {
        await flushRegexSave();
        const created = await API.importRegexPreset(file);
        state.regexPresets.push(created);
        state.activeRegexPresetId = created.id;
        state.regexFilters = created.filters || [];
        saveLLMSettings({ active_regex_preset: String(created.id) });
        renderPresetOptions();
        if (el.regexPresetSelect) el.regexPresetSelect.value = created.id;
        renderFilters(state.regexFilters);
        setControlsEnabled();
        showToast('Preset imported', 'success');
        // A SillyTavern script aimed at user input would silently start
        // rewriting replies here — say so rather than letting it surprise them.
        for (const warning of created.warnings || []) showToast(warning);
    } catch (err) {
        showToast('Import failed: ' + err.message);
    }
}

export async function exportRegexPreset() {
    const preset = activePreset();
    if (!preset) {
        showToast('No preset selected');
        return;
    }
    // Land on-screen edits first so the download matches what is displayed.
    await flushRegexSave();
    window.location.href = `/api/regex-presets/${preset.id}/export`;
}
