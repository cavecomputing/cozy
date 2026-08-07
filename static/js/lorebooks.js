// ═══════════════════════════════════════════════════════════════════════════
// LOREBOOKS — settings-panel editor for standalone + embedded books
// ═══════════════════════════════════════════════════════════════════════════
import { state, el, icons } from './state.js';
import { API } from './api.js';
import { showToast, updateComposerState, markUnusedVar } from './utils.js';
import { confirmDialog } from './confirm.js';
import { estimateTextTokens } from './tokenizer.js';
import { updateContextMeter, updateContextBoundary } from './context-meter.js';

// Editor state — purely local, swapped wholesale on selection change.
// `kind` is 'standalone' or 'embedded'. `id` is the lorebook id (standalone)
// or the character id (embedded).
let editing = null;
const empty = () => ({
    name: '', description: '', scan_depth: 20, max_entries: 20,
    recursive_scanning: false, extensions: {}, entries: [],
});

const ENTRY_TOOLTIPS = {
    keys: "Comma-separated trigger words. The entry fires when one of these appears in the recent chat (within the book's scan depth).",
    enabled: "Uncheck to disable this entry without deleting it.",
    constant: "Inject this entry every turn, regardless of keywords.",
    case_sensitive: "If on, key matching respects upper/lower case.",
    selective: "Only fire when a primary AND a secondary key are both present in the recent chat.",
    secondary_keys: "Optional second list. Used when 'Require secondary keys' is on.",
    insertion_order: "When multiple entries match, they're inserted in ascending order. Lower numbers come earlier in the prompt.",
    comment: "Notes for yourself — never sent to the model.",
};

function tip(label, text) {
    const safe = text.replace(/"/g, '&quot;');
    return `<span class="help-tip" tabindex="0" aria-label="${label} help" data-tip="${safe}">?</span>`;
}

// ── Loading ───────────────────────────────────────────────────────────────

export async function loadLorebooks() {
    try {
        state.lorebooks = await API.getLorebooks();
    } catch (e) {
        console.warn('Failed to load lorebooks:', e);
        showToast('Failed to load lorebooks: ' + e.message);
        state.lorebooks = [];
    }
    updateComposerState();
}

/** List items: every standalone book + every character whose card embeds a non-empty book. */
function listEntries() {
    const out = [];
    for (const lb of state.lorebooks) {
        const entries = Array.isArray(lb.book?.entries) ? lb.book.entries : [];
        out.push({
            kind: 'standalone',
            id: lb.id,
            name: lb.name || '(unnamed)',
            badge: 'Global',
            sub: `${lb.entry_count ?? entries.length} entries`,
        });
    }
    for (const c of state.characters) {
        const book = c.character_book || c.data?.character_book;
        const entries = Array.isArray(book?.entries) ? book.entries : [];
        if (entries.length > 0) {
            out.push({
                kind: 'embedded',
                id: c.id,
                name: book.name || c.name,
                badge: 'Embedded',
                sub: `${c.name} character card · ${entries.length} entries`,
            });
        }
    }
    return out;
}

export function renderLorebookList() {
    if (!el.lorebookList) return;
    el.lorebookList.innerHTML = '';
    const entries = listEntries();
    if (entries.length === 0) {
        const li = document.createElement('li');
        li.className = 'lorebook-list-empty';
        li.textContent = 'No lorebooks yet — create one to get started.';
        el.lorebookList.appendChild(li);
        return;
    }
    for (const e of entries) {
        const li = document.createElement('li');
        li.className = 'lorebook-list-item';
        li.dataset.kind = e.kind;
        li.dataset.id = String(e.id);
        if (editing && editing.kind === e.kind && editing.id === e.id) {
            li.classList.add('active');
        }
        const text = document.createElement('div');
        text.className = 'lorebook-list-text';
        const title = document.createElement('div');
        title.className = 'lorebook-list-title';
        const name = document.createElement('div');
        name.className = 'lorebook-list-name';
        name.textContent = e.name;
        const badge = document.createElement('span');
        badge.className = `lorebook-source-badge lorebook-source-badge--${e.kind}`;
        badge.textContent = e.badge;
        title.append(name, badge);
        const sub = document.createElement('div');
        sub.className = 'lorebook-list-sub';
        sub.textContent = e.sub;
        text.append(title, sub);

        const actions = document.createElement('div');
        actions.className = 'lorebook-list-actions';
        actions.innerHTML = `
            <button class="icon-btn lorebook-list-export-btn" title="Export lorebook" aria-label="Export lorebook">${icons.DOWNLOAD}</button>
            <button class="icon-btn lorebook-list-delete-btn" title="Delete lorebook" aria-label="Delete lorebook">${icons.TRASH}</button>
        `;

        li.append(text, actions);
        el.lorebookList.appendChild(li);
    }
}

// ── Editor ─────────────────────────────────────────────────────────────────

function setEditorVisible(visible) {
    if (el.lorebookMeta)     el.lorebookMeta.hidden = !visible;
    if (el.lorebookEntries)  el.lorebookEntries.hidden = !visible;
    if (el.lorebookAddEntry) el.lorebookAddEntry.hidden = !visible;
    if (el.lorebookEmptyMeta) el.lorebookEmptyMeta.hidden = visible;
    if (el.lorebookEmptyEntries) el.lorebookEmptyEntries.hidden = visible;
}

function readEntryRow(row) {
    const get = sel => row.querySelector(sel);
    const csv = v => v ? v.split(',').map(s => s.trim()).filter(Boolean) : [];
    return {
        keys: csv(get('[data-field="keys"]').value),
        secondary_keys: csv(get('[data-field="secondary_keys"]').value),
        content: get('[data-field="content"]').value,
        comment: get('[data-field="comment"]').value || '',
        enabled: get('[data-field="enabled"]').checked,
        constant: get('[data-field="constant"]').checked,
        selective: get('[data-field="selective"]').checked,
        case_sensitive: get('[data-field="case_sensitive"]').checked,
        insertion_order: parseInt(get('[data-field="insertion_order"]').value, 10) || 100,
    };
}

function readEditor() {
    // Preserve V2 spec fields the editor doesn't surface (token_budget,
    // recursive_scanning, extensions, plus per-entry id/priority/position/etc.)
    // by spreading the original book/entry over the form values. Per-entry
    // preservation uses a stable id stored on each row at load time.
    const original = editing?.original || {};
    const origEntries = Array.isArray(original.entries) ? original.entries : [];

    const entries = [];
    el.lorebookEntries.querySelectorAll('.lorebook-entry').forEach(row => {
        const fromForm = readEntryRow(row);
        const origIdx = parseInt(row.dataset.origIndex ?? '-1', 10);
        const orig = origIdx >= 0 ? origEntries[origIdx] : null;
        // Order matters: form values win, original fills in unknowns. Always
        // include `extensions: {}` per the V2 spec.
        entries.push({
            extensions: {},
            ...(orig || {}),
            ...fromForm,
        });
    });
    return {
        ...original,
        name: el.lorebookName.value.trim(),
        description: el.lorebookDescription.value || '',
        scan_depth: parseInt(el.lorebookScanDepth.value, 10) || 20,
        max_entries: parseInt(el.lorebookMaxEntries.value, 10) || 20,
        extensions: original.extensions && typeof original.extensions === 'object' ? original.extensions : {},
        entries,
    };
}

function buildEntryRow(entry, idx, origIndex = -1) {
    const row = document.createElement('div');
    row.className = 'lorebook-entry';
    row.dataset.index = String(idx);
    row.dataset.origIndex = String(origIndex);
    row.innerHTML = `
        <div class="lorebook-entry-header">
            <div class="lorebook-entry-keys-wrap">
                <input type="text" class="form-input lorebook-entry-keys" data-field="keys"
                    placeholder="key1, key2 (comma-separated triggers)">
                ${tip('Keys', ENTRY_TOOLTIPS.keys)}
            </div>
            <button type="button" class="icon-btn lorebook-entry-up" title="Move up" aria-label="Move up">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"></polyline></svg>
            </button>
            <button type="button" class="icon-btn lorebook-entry-down" title="Move down" aria-label="Move down">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </button>
            <button type="button" class="icon-btn lorebook-entry-delete" title="Delete entry" aria-label="Delete entry">${icons.TRASH}</button>
        </div>
        <textarea class="form-textarea lorebook-entry-content" data-field="content"
            rows="3" placeholder="The lore text injected when this entry triggers…"></textarea>
        <div class="lorebook-entry-meta">
            <label class="lorebook-entry-toggle"><input type="checkbox" data-field="enabled"> Enabled ${tip('Enabled', ENTRY_TOOLTIPS.enabled)}</label>
            <label class="lorebook-entry-toggle"><input type="checkbox" data-field="constant"> Always include (ignore keys) ${tip('Always include', ENTRY_TOOLTIPS.constant)}</label>
            <label class="lorebook-entry-toggle"><input type="checkbox" data-field="case_sensitive"> Case-sensitive ${tip('Case-sensitive', ENTRY_TOOLTIPS.case_sensitive)}</label>
            <label class="lorebook-entry-toggle"><input type="checkbox" data-field="selective"> Require secondary keys ${tip('Require secondary keys', ENTRY_TOOLTIPS.selective)}</label>
        </div>
        <div class="lorebook-entry-row">
            <label class="lorebook-entry-field">
                <span>Secondary keys ${tip('Secondary keys', ENTRY_TOOLTIPS.secondary_keys)}</span>
                <input type="text" class="form-input" data-field="secondary_keys" placeholder="optional, comma-separated">
            </label>
            <label class="lorebook-entry-field lorebook-entry-field-narrow">
                <span>Insertion order ${tip('Insertion order', ENTRY_TOOLTIPS.insertion_order)}</span>
                <input type="number" class="form-input" data-field="insertion_order" step="1" min="0" max="9999" value="100">
            </label>
        </div>
        <label class="lorebook-entry-field">
            <span>Comment (optional) ${tip('Comment', ENTRY_TOOLTIPS.comment)}</span>
            <input type="text" class="form-input" data-field="comment" placeholder="Notes about this entry — not sent to the model">
        </label>
    `;

    // Populate values
    row.querySelector('[data-field="keys"]').value = (entry.keys || []).join(', ');
    row.querySelector('[data-field="secondary_keys"]').value = (entry.secondary_keys || []).join(', ');
    row.querySelector('[data-field="content"]').value = entry.content || '';
    row.querySelector('[data-field="comment"]').value = entry.comment || '';
    row.querySelector('[data-field="enabled"]').checked = entry.enabled !== false;
    row.querySelector('[data-field="constant"]').checked = entry.constant === true;
    row.querySelector('[data-field="case_sensitive"]').checked = entry.case_sensitive === true;
    row.querySelector('[data-field="selective"]').checked = entry.selective === true;
    row.querySelector('[data-field="insertion_order"]').value = String(entry.insertion_order ?? 100);
    return row;
}

function renderEntries(entries) {
    el.lorebookEntries.innerHTML = '';
    entries.forEach((e, i) => el.lorebookEntries.appendChild(buildEntryRow(e, i, i)));
    if (entries.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'lorebook-entry-empty';
        empty.textContent = 'No entries yet. Use Add entry to create the first trigger and lore text.';
        el.lorebookEntries.appendChild(empty);
    }
    refreshEntriesCount();
}

function refreshEntriesCount() {
    if (!el.lorebookEntriesCount) return;
    if (!editing) { el.lorebookEntriesCount.textContent = ''; return; }
    const n = el.lorebookEntries?.querySelectorAll('.lorebook-entry').length || 0;
    el.lorebookEntriesCount.textContent = `(${n})`;
}

function fillDestinationOptions() {
    if (!el.lorebookDestination) return;
    const sel = el.lorebookDestination;
    const current = editing
        ? (editing.kind === 'standalone' ? 'standalone' : `embedded:${editing.id}`)
        : 'standalone';
    sel.innerHTML = '';
    const opt = document.createElement('option');
    opt.value = 'standalone';
    opt.textContent = 'Standalone (database)';
    sel.appendChild(opt);
    for (const c of state.characters) {
        if (c.missing) continue;
        const o = document.createElement('option');
        o.value = `embedded:${c.id}`;
        o.textContent = `Embed in ${c.name}`;
        sel.appendChild(o);
    }
    sel.value = current;
}

function loadIntoEditor(book) {
    el.lorebookName.value = book.name || '';
    el.lorebookDescription.value = book.description || '';
    el.lorebookScanDepth.value = String(book.scan_depth ?? 20);
    el.lorebookMaxEntries.value = String(book.max_entries ?? 20);
    renderEntries(Array.isArray(book.entries) ? book.entries : []);
    fillDestinationOptions();
}

export async function selectLorebook(kind, id) {
    if (kind === 'standalone') {
        const full = await API.getLorebook(id).catch(err => {
            showToast('Failed to load lorebook: ' + err.message);
            return null;
        });
        if (!full) return;
        editing = { kind: 'standalone', id, original: full.book };
        loadIntoEditor(full.book || empty());
    } else {
        const char = state.characters.find(c => c.id === id);
        if (!char) return;
        const book = char.character_book || char.data?.character_book || empty();
        editing = { kind: 'embedded', id, original: book };
        loadIntoEditor(book);
    }
    setEditorVisible(true);
    renderLorebookList();
}

function clearEditor() {
    editing = null;
    setEditorVisible(false);
    refreshEntriesCount();
    renderLorebookList();
}

export async function newLorebook() {
    try {
        const created = await API.createLorebook({ name: 'New lorebook' });
        await loadLorebooks();
        await selectLorebook('standalone', created.id);
        // Put focus on the name field so the user can rename immediately
        if (el.lorebookName) {
            el.lorebookName.select();
            el.lorebookName.focus();
        }
        showToast('Lorebook created — rename it above', 'success');
    } catch (e) {
        showToast('Failed to create: ' + e.message);
    }
}

async function saveStandalone(book) {
    const updated = await API.updateLorebook(editing.id, { name: book.name, book });
    await loadLorebooks();
    editing.original = updated.book;
    return updated;
}

async function saveEmbedded(book) {
    // Persist via the character update path, then refresh the in-memory char.
    const updated = await API.updateCharacter(editing.id, { character_book: book });
    const idx = state.characters.findIndex(c => c.id === editing.id);
    if (idx >= 0) state.characters[idx] = updated;
    if (state.activeCharacter?.id === editing.id) state.activeCharacter = updated;
    editing.original = book;
    return updated;
}

export async function saveLorebook() {
    if (!editing) return;
    const book = readEditor();
    if (!book.name) {
        showToast('Name is required');
        return;
    }
    // Destination conversion: if the user changed the dropdown, route
    // accordingly and update `editing` in place.
    const dest = el.lorebookDestination?.value || 'standalone';
    const isStandalone = editing.kind === 'standalone';
    const wantsStandalone = dest === 'standalone';
    const wantsEmbeddedCharId = dest.startsWith('embedded:') ? parseInt(dest.slice(9), 10) : null;

    try {
        if (isStandalone && wantsStandalone) {
            await saveStandalone(book);
        } else if (!isStandalone && wantsEmbeddedCharId === editing.id) {
            await saveEmbedded(book);
        } else if (isStandalone && wantsEmbeddedCharId != null) {
            // Move standalone → embedded in the chosen character, then drop the row.
            await API.embedLorebookInCharacter(editing.id, wantsEmbeddedCharId, true);
            // Re-write entries from the editor (embed-in-character used the *saved* JSON).
            await API.updateCharacter(wantsEmbeddedCharId, { character_book: book });
            const charRefreshed = state.characters.find(c => c.id === wantsEmbeddedCharId);
            if (charRefreshed) charRefreshed.character_book = book;
            await loadLorebooks();
            editing = { kind: 'embedded', id: wantsEmbeddedCharId, original: book };
        } else if (!isStandalone && wantsStandalone) {
            // Move embedded → new standalone DB row, then clear the embedded one.
            const char = state.characters.find(c => c.id === editing.id);
            const created = await API.createLorebook({ name: book.name, book });
            await API.updateCharacter(editing.id, { character_book: null });
            if (char) char.character_book = null;
            await loadLorebooks();
            editing = { kind: 'standalone', id: created.id, original: book };
        } else if (!isStandalone && wantsEmbeddedCharId !== editing.id && wantsEmbeddedCharId != null) {
            // Move from one character's card to another's.
            await API.updateCharacter(wantsEmbeddedCharId, { character_book: book });
            await API.updateCharacter(editing.id, { character_book: null });
            const fromChar = state.characters.find(c => c.id === editing.id);
            const toChar = state.characters.find(c => c.id === wantsEmbeddedCharId);
            if (fromChar) fromChar.character_book = null;
            if (toChar) toChar.character_book = book;
            editing = { kind: 'embedded', id: wantsEmbeddedCharId, original: book };
        }
        showToast('Saved', 'success');
        renderLorebookList();
        renderLorebookFlyout();
        updateComposerState();
        fillDestinationOptions();
        updateContextMeter();
        updateContextBoundary();
    } catch (e) {
        showToast('Save failed: ' + e.message);
    }
}

export async function deleteLorebook(kind, id) {
    // Default to the currently-edited book; explicit args let row buttons
    // act on any list item without selecting it first.
    if (kind == null || id == null) {
        if (!editing) return;
        kind = editing.kind;
        id = editing.id;
    }
    const isEditing = editing && editing.kind === kind && editing.id === id;
    if (kind === 'standalone') {
        const lb = state.lorebooks.find(b => b.id === id);
        const ok = await confirmDialog({
            title: `Delete lorebook "${lb?.name || 'this book'}"?`,
            message: 'This cannot be undone.',
        });
        if (!ok) return;
        try {
            await API.deleteLorebook(id);
            await loadLorebooks();
            if (isEditing) clearEditor();
            else renderLorebookList();
            renderLorebookFlyout();
            updateComposerState();
            updateContextMeter();
            updateContextBoundary();
            showToast('Lorebook deleted', 'success');
        } catch (e) {
            showToast('Delete failed: ' + e.message);
        }
    } else {
        const char = state.characters.find(c => c.id === id);
        const ok = await confirmDialog({
            title: `Remove the embedded lorebook from ${char?.name || 'this character'}?`,
            confirmLabel: 'Remove',
        });
        if (!ok) return;
        try {
            await API.updateCharacter(id, { character_book: null });
            if (char) char.character_book = null;
            if (state.activeCharacter?.id === id) state.activeCharacter.character_book = null;
            if (isEditing) clearEditor();
            else renderLorebookList();
            renderLorebookFlyout();
            updateComposerState();
            updateContextMeter();
            updateContextBoundary();
            showToast('Embedded lorebook removed', 'success');
        } catch (e) {
            showToast('Remove failed: ' + e.message);
        }
    }
}

// ── Entry-row event delegation ────────────────────────────────────────────

export function handleEntriesClick(e) {
    const row = e.target.closest('.lorebook-entry');
    if (!row) return;
    const idx = parseInt(row.dataset.index, 10);
    if (e.target.closest('.lorebook-entry-delete')) {
        row.remove();
        // Re-index remaining rows
        el.lorebookEntries.querySelectorAll('.lorebook-entry').forEach((r, i) => {
            r.dataset.index = String(i);
        });
        if (el.lorebookEntries.querySelectorAll('.lorebook-entry').length === 0) {
            renderEntries([]);
            return;
        }
        refreshEntriesCount();
    } else if (e.target.closest('.lorebook-entry-up') && idx > 0) {
        row.parentNode.insertBefore(row, row.previousElementSibling);
        el.lorebookEntries.querySelectorAll('.lorebook-entry').forEach((r, i) => {
            r.dataset.index = String(i);
        });
    } else if (e.target.closest('.lorebook-entry-down') && row.nextElementSibling) {
        row.parentNode.insertBefore(row.nextElementSibling, row);
        el.lorebookEntries.querySelectorAll('.lorebook-entry').forEach((r, i) => {
            r.dataset.index = String(i);
        });
    }
}

export function addEntry() {
    if (!editing) return;
    el.lorebookEntries.querySelector('.lorebook-entry-empty')?.remove();
    const idx = el.lorebookEntries.querySelectorAll('.lorebook-entry').length;
    el.lorebookEntries.appendChild(buildEntryRow({
        keys: [], content: '', enabled: true, constant: false, insertion_order: 100,
        extensions: {},
    }, idx, -1));
    refreshEntriesCount();
}

// ── Import / export ───────────────────────────────────────────────────────

export function importLorebook() {
    el.lorebookImportFile?.click();
}

export async function handleImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    try {
        const created = await API.importLorebook(file);
        await loadLorebooks();
        await selectLorebook('standalone', created.id);
        showToast('Lorebook imported', 'success');
    } catch (err) {
        showToast('Import failed: ' + err.message);
    }
}

export function exportLorebook(kind, id) {
    if (kind == null || id == null) {
        if (!editing) return;
        kind = editing.kind;
        id = editing.id;
    }
    if (kind === 'standalone') {
        // Server emits the file with proper Content-Disposition.
        window.location.href = API.exportLorebookUrl(id);
    } else {
        window.location.href = API.exportCharacterLorebookUrl(id);
    }
}

// ── Per-chat selection flyout (next to the composer) ──────────────────────

/** Build the dropdown options for the active-lorebook flyout. */
export function renderLorebookFlyout() {
    const sel = el.lorebookFlyoutSelect;
    if (!sel) return;
    sel.innerHTML = '';
    const chat = state.activeChat;
    const char = state.activeCharacter;

    // Each option encodes its selection in `value`: "none", "embedded",
    // or "standalone:<id>". `setActiveLorebook` decodes it on change.
    const option = (value, label, isActive) => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = label;
        if (isActive) opt.selected = true;
        sel.appendChild(opt);
    };

    const noneActive = chat && !chat.active_lorebook_embedded && chat.active_lorebook_id == null;
    option('none', 'None', !!noneActive);

    const embedded = char?.character_book || char?.data?.character_book;
    if (embedded && Array.isArray(embedded.entries) && embedded.entries.length > 0) {
        const label = `${char.name}'s lorebook (${embedded.entries.length} entries)`;
        option('embedded', label, chat?.active_lorebook_embedded === true);
    }

    for (const lb of state.lorebooks) {
        const label = `${lb.name} · ${lb.entry_count} entries`;
        option(`standalone:${lb.id}`, label, chat?.active_lorebook_id === lb.id);
    }

    sel.disabled = !chat;
    markUnusedVar(el.lorebookMarker, 'lorebook');
}

/** Decode a dropdown option value and persist the selection. */
export function onLorebookSelectChange() {
    const value = el.lorebookFlyoutSelect?.value || 'none';
    if (value === 'embedded') setActiveLorebook({ kind: 'embedded' });
    else if (value.startsWith('standalone:')) {
        setActiveLorebook({ kind: 'standalone', id: Number(value.slice('standalone:'.length)) });
    } else {
        setActiveLorebook({ kind: 'none' });
    }
}

async function setActiveLorebook(sel) {
    const chat = state.activeChat;
    if (!chat) return;
    const fields = { active_lorebook_id: null, active_lorebook_embedded: false };
    if (sel.kind === 'embedded') fields.active_lorebook_embedded = true;
    else if (sel.kind === 'standalone') fields.active_lorebook_id = sel.id;
    try {
        const updated = await API.updateChat(chat.id, fields);
        state.activeChat = updated;
        const idx = state.chats.findIndex(c => c.id === chat.id);
        if (idx >= 0) state.chats[idx] = updated;
        renderLorebookFlyout();
        renderLorebookNotice();
        updateComposerState();
        updateContextMeter();
        updateContextBoundary();
    } catch (e) {
        showToast('Failed to set lorebook: ' + e.message);
    }
}

// ── Per-chat Author's Note (lives in the Memory flyout) ───────────────────
let authorNoteTimer = null;

/** Populate the Author's Note textarea from the active chat. */
export function loadAuthorNote() {
    if (!el.authorNoteInput) return;
    const chat = state.activeChat;
    el.authorNoteInput.value = chat?.author_note || '';
    el.authorNoteInput.disabled = !chat;
    updateAuthorNoteCounter();
    markUnusedVar(el.authorNoteMarker, 'author_note');
}

/** Refresh the "≈ N tokens" counter under the Author's Note box. */
export function updateAuthorNoteCounter() {
    if (!el.authorNoteCounter) return;
    const used = estimateTextTokens(el.authorNoteInput?.value || '');
    el.authorNoteCounter.textContent = `≈ ${used.toLocaleString()} tokens`;
}

/** Persist the Author's Note for the active chat (mirrors setActiveLorebook). */
async function saveAuthorNote() {
    const chat = state.activeChat;
    if (!chat) return;
    const value = el.authorNoteInput?.value || '';
    if (value === (chat.author_note || '')) return;  // no-op when unchanged
    try {
        const updated = await API.updateChat(chat.id, { author_note: value });
        state.activeChat = updated;
        const idx = state.chats.findIndex(c => c.id === chat.id);
        if (idx >= 0) state.chats[idx] = updated;
        updateContextMeter();
        updateContextBoundary();
    } catch (e) {
        showToast('Failed to save note: ' + e.message);
    }
}

/** Debounced autosave while typing. */
export function scheduleAuthorNoteSave() {
    clearTimeout(authorNoteTimer);
    authorNoteTimer = setTimeout(saveAuthorNote, 500);
}

/** Immediate flush — used on blur / when the flyout closes. */
export function flushAuthorNote() {
    clearTimeout(authorNoteTimer);
    saveAuthorNote();
}

// ── Inline notice above the composer ──────────────────────────────────────

const NOTICE_AUTO_DISMISS_MS = 5000;
let noticeTimer = null;

export function renderLorebookNotice() {
    if (!el.lorebookNotice) return;
    if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null; }

    const chat = state.activeChat;
    const char = state.activeCharacter;
    const embedded = char?.character_book || char?.data?.character_book;
    const hasEmbedded = embedded && Array.isArray(embedded.entries) && embedded.entries.length > 0;
    const show = chat
        && hasEmbedded
        && chat.active_lorebook_embedded === true
        && !chat.lorebook_notice_dismissed;
    el.lorebookNotice.hidden = !show;
    if (show && el.lorebookNoticeText) {
        el.lorebookNoticeText.textContent =
            `${char.name} has a lorebook — it's enabled by default for this chat.`;
        const noticeChatId = chat.id;
        noticeTimer = setTimeout(() => {
            noticeTimer = null;
            // Only dismiss if we're still on the same chat that triggered it.
            if (state.activeChat?.id === noticeChatId) dismissLorebookNotice();
        }, NOTICE_AUTO_DISMISS_MS);
    }
}

export async function dismissLorebookNotice() {
    if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null; }
    const chat = state.activeChat;
    if (!chat) return;
    el.lorebookNotice.hidden = true;
    try {
        const updated = await API.updateChat(chat.id, { lorebook_notice_dismissed: true });
        state.activeChat = updated;
        const idx = state.chats.findIndex(c => c.id === chat.id);
        if (idx >= 0) state.chats[idx] = updated;
    } catch (e) {
        // Non-critical — the user just won't see it again until the server confirms.
        console.warn('Failed to persist notice dismissal:', e);
    }
}
