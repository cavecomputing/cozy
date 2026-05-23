import { state, icons } from './state.js';
import { API } from './api.js';
import { Modal } from './modal.js';
import { applyAvatar, showToast, sanitize } from './utils.js';
import { loadCharacters, selectCharacter, deleteCharacter, renderCharList } from './characters.js';
import { renderLorebookList } from './lorebooks.js';

const DESKTOP_QUERY = '(min-width: 769px)';
const DEFAULT_COLLECTION_ICON = '◇';
const COLLECTION_ICONS = [
    '◇', '★', '✦', '✪', '❖', '✺',
    '🎭', '🚀', '⚔️', '🧪', '🏰', '🌌',
    '🔮', '📖', '🎬', '🎮', '🌸', '🐉',
    '👻', '🌊', '⚡', '🌙', '☀️', '🍵',
    '🎨', '🎤', '🍷', '🗡️', '🪐', '🌹',
    '💎', '🔥', '❄️', '🌿', '🦋', '🐺',
];

const gallery = {
    open: false,
    characters: [],
    collections: [],
    selectedId: null,
    view: 'all',
    query: '',
    dirty: false,
    pendingAvatarFile: null,
    pendingNewIcon: '',
    iconPickerTarget: null,
};

let els = null;
let galleryTags = [];
let galleryAltGreetings = [];

function q(id) {
    return document.getElementById(id);
}

function getEls() {
    if (els) return els;
    els = {
        root: q('character-gallery'),
        openBtn: q('character-gallery-open'),
        collapseBtn: q('gallery-collapse-btn'),
        search: q('gallery-search'),
        addBtn: q('gallery-add-btn'),
        nav: document.querySelector('.gallery-nav'),
        countAll: q('gallery-count-all'),
        countFavorites: q('gallery-count-favorites'),
        countArchived: q('gallery-count-archived'),
        newCollectionBtn: q('gallery-new-collection-btn'),
        newCollectionForm: q('gallery-new-collection-form'),
        newCollectionName: q('gallery-new-collection-name'),
        newCollectionCancel: q('gallery-new-collection-cancel'),
        newCollectionIcon: q('gallery-new-collection-icon'),
        collectionList: q('gallery-collection-list'),
        iconPicker: q('gallery-icon-picker'),
        grid: q('gallery-grid'),
        empty: q('gallery-empty'),
        viewTitle: q('gallery-view-title'),
        viewCount: q('gallery-view-count'),
        inspectorEmpty: q('gallery-inspector-empty'),
        editor: q('gallery-editor'),
        heroBg: q('gallery-hero-bg'),
        avatarInput: q('gallery-avatar-input'),
        heading: q('gallery-editor-heading'),
        subtitle: q('gallery-editor-subtitle'),
        status: q('gallery-editor-status'),
        pinBtn: q('gallery-pin-btn'),
        collections: q('gallery-editor-collections'),
        collectionAdd: q('gallery-collection-add'),
        tabs: document.querySelector('.gallery-tabs'),
        panels: document.querySelectorAll('.gallery-tab-panel'),
        duplicateBtn: q('gallery-duplicate-btn'),
        archiveBtn: q('gallery-archive-btn'),
        deleteBtn: q('gallery-delete-btn'),
        startChatBtn: q('gallery-start-chat-btn'),
        saveBtn: q('gallery-save-btn'),
        tagsWrap: q('gallery-tags-input-wrap'),
        tagsChipList: q('gallery-tags-chip-list'),
        tagsTextInput: q('gallery-tags-text-input'),
        altGreetingsList: q('gallery-alt-greetings-list'),
        addGreetingBtn: q('gallery-add-greeting-btn'),
        exportTrigger: q('gallery-export-card-btn'),
        exportMenu: q('gallery-export-menu'),
        fields: {
            name: q('gallery-field-name'),
            description: q('gallery-field-description'),
            notes: q('gallery-field-notes'),
            personality: q('gallery-field-personality'),
            scenario: q('gallery-field-scenario'),
            system: q('gallery-field-system'),
            post_history: q('gallery-field-post-history'),
            first: q('gallery-field-first'),
            example: q('gallery-field-example'),
            creator: q('gallery-field-creator'),
            version: q('gallery-field-version'),
        },
    };
    return els;
}

function isDesktop() {
    return window.matchMedia(DESKTOP_QUERY).matches;
}

function selectedCharacter() {
    return gallery.characters.find(c => c.id === gallery.selectedId) || null;
}

function isArchived(char) {
    return !!char?.archived_at;
}

function replaceCharacter(updated) {
    const idx = gallery.characters.findIndex(c => c.id === updated.id);
    if (idx === -1) gallery.characters.push(updated);
    else gallery.characters[idx] = updated;

    const stateIdx = state.characters.findIndex(c => c.id === updated.id);
    if (isArchived(updated)) {
        if (stateIdx !== -1) state.characters.splice(stateIdx, 1);
    } else if (stateIdx === -1) {
        state.characters.push(updated);
    } else {
        state.characters[stateIdx] = updated;
    }
}

function characterSubtitle(char) {
    const text = (char.description || char.personality || char.scenario || char.creator_notes || '').trim();
    if (!text) return 'No description yet';
    return text.replace(/\s+/g, ' ').slice(0, 70);
}

function collectionCount(collectionId) {
    return gallery.characters.filter(char =>
        !isArchived(char) && (char.collections || []).some(c => c.id === collectionId)
    ).length;
}

function visibleCharacters() {
    let chars = [...gallery.characters];
    if (gallery.view === 'favorites') {
        chars = chars.filter(char => char.pinned && !isArchived(char));
    } else if (gallery.view === 'archived') {
        chars = chars.filter(isArchived);
    } else if (gallery.view.startsWith('collection:')) {
        const collectionId = Number(gallery.view.split(':')[1]);
        chars = chars.filter(char =>
            !isArchived(char) && (char.collections || []).some(c => c.id === collectionId)
        );
    } else {
        chars = chars.filter(char => !isArchived(char));
    }

    const query = gallery.query.trim().toLowerCase();
    if (query) {
        chars = chars.filter(char => {
            const haystack = [
                char.name, char.description, char.personality, char.scenario,
                char.creator_notes, ...(char.tags || []), ...(char.collections || []).map(c => c.name),
            ].join(' ').toLowerCase();
            return haystack.includes(query);
        });
    }

    chars.sort((a, b) => {
        if (a.pinned && !b.pinned) return -1;
        if (!a.pinned && b.pinned) return 1;
        return (a.name || '').localeCompare(b.name || '');
    });
    return chars;
}

function viewTitle() {
    if (gallery.view === 'favorites') return 'Favorites';
    if (gallery.view === 'archived') return 'Archived';
    if (gallery.view.startsWith('collection:')) {
        const id = Number(gallery.view.split(':')[1]);
        return gallery.collections.find(c => c.id === id)?.name || 'Collection';
    }
    return 'All Characters';
}

function setDirty(dirty) {
    gallery.dirty = dirty;
    const { saveBtn, editor } = getEls();
    saveBtn?.classList.toggle('is-dirty', dirty);
    editor?.classList.toggle('is-dirty', dirty);
}

function confirmDiscard() {
    return !gallery.dirty || confirm('Discard unsaved character changes?');
}

function renderCollectionControls() {
    const e = getEls();
    e.collectionList.innerHTML = '';
    e.collectionAdd.innerHTML = '<option value="">+ Add</option>';

    gallery.collections.forEach(collection => {
        const count = collectionCount(collection.id);
        const row = document.createElement('div');
        row.className = `gallery-collection-row${gallery.view === `collection:${collection.id}` ? ' active' : ''}`;
        row.dataset.collectionId = collection.id;

        const iconBtn = document.createElement('button');
        iconBtn.type = 'button';
        iconBtn.className = 'gallery-nav-icon gallery-collection-icon-btn';
        iconBtn.dataset.iconEditCollectionId = collection.id;
        iconBtn.title = 'Change icon';
        iconBtn.setAttribute('aria-label', 'Change icon');
        iconBtn.textContent = collection.icon || DEFAULT_COLLECTION_ICON;

        const selectBtn = document.createElement('button');
        selectBtn.type = 'button';
        selectBtn.className = 'gallery-collection-select';
        selectBtn.dataset.collectionId = collection.id;
        selectBtn.innerHTML = '<span class="gallery-collection-name"></span><strong></strong>';
        selectBtn.querySelector('.gallery-collection-name').textContent = collection.name;
        selectBtn.querySelector('strong').textContent = count;

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'gallery-collection-delete';
        deleteBtn.dataset.deleteCollectionId = collection.id;
        deleteBtn.title = `Delete ${collection.name}`;
        deleteBtn.setAttribute('aria-label', `Delete ${collection.name}`);
        deleteBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

        row.append(iconBtn, selectBtn, deleteBtn);
        e.collectionList.appendChild(row);

        const addOption = document.createElement('option');
        addOption.value = String(collection.id);
        addOption.textContent = `${collection.icon || DEFAULT_COLLECTION_ICON} ${collection.name}`;
        e.collectionAdd.appendChild(addOption);
    });
}

function renderRail() {
    const e = getEls();
    e.countAll.textContent = gallery.characters.filter(c => !isArchived(c)).length;
    e.countFavorites.textContent = gallery.characters.filter(c => c.pinned && !isArchived(c)).length;
    e.countArchived.textContent = gallery.characters.filter(isArchived).length;

    document.querySelectorAll('.gallery-nav .gallery-nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === gallery.view);
    });
    renderCollectionControls();
}

function renderGrid() {
    const e = getEls();
    const chars = visibleCharacters();
    e.grid.innerHTML = '';
    e.viewTitle.textContent = viewTitle();
    e.viewCount.textContent = `${chars.length} character${chars.length === 1 ? '' : 's'}`;
    e.empty.hidden = chars.length !== 0;

    chars.forEach(char => {
        const card = document.createElement('div');
        card.setAttribute('role', 'button');
        card.tabIndex = 0;
        card.className = `gallery-card${char.id === gallery.selectedId ? ' selected' : ''}${isArchived(char) ? ' archived' : ''}`;
        card.dataset.charId = char.id;

        const avatarWrap = document.createElement('div');
        avatarWrap.className = 'gallery-card-avatar-wrap';
        const avatar = document.createElement('div');
        avatar.className = 'gallery-card-avatar avatar';
        applyAvatar(avatar, char);
        avatarWrap.appendChild(avatar);

        const star = document.createElement('button');
        star.type = 'button';
        star.className = `gallery-card-star${char.pinned ? ' pinned' : ''}`;
        star.dataset.pinCharId = char.id;
        star.title = char.pinned ? 'Unfavorite' : 'Favorite';
        star.setAttribute('aria-label', char.pinned ? 'Unfavorite character' : 'Favorite character');
        star.setAttribute('aria-pressed', char.pinned ? 'true' : 'false');
        star.innerHTML = char.pinned ? icons.STAR_FILLED : icons.STAR;
        avatarWrap.appendChild(star);

        if (isArchived(char)) {
            const badge = document.createElement('span');
            badge.className = 'gallery-card-archive-badge';
            badge.textContent = 'Archived';
            avatarWrap.appendChild(badge);
        }

        const body = document.createElement('div');
        body.className = 'gallery-card-body';
        const name = document.createElement('strong');
        name.textContent = char.name || 'Unnamed';
        const subtitle = document.createElement('span');
        subtitle.className = 'gallery-card-subtitle';
        subtitle.textContent = characterSubtitle(char);
        body.append(name, subtitle);

        const meta = document.createElement('div');
        meta.className = 'gallery-card-meta';
        const firstCollection = (char.collections || [])[0];
        if (firstCollection) {
            const chip = document.createElement('span');
            chip.className = 'gallery-card-chip';
            chip.innerHTML = `<span class="gallery-card-chip-icon"></span><span class="gallery-card-chip-name"></span>`;
            chip.querySelector('.gallery-card-chip-icon').textContent = firstCollection.icon || DEFAULT_COLLECTION_ICON;
            chip.querySelector('.gallery-card-chip-name').textContent = firstCollection.name;
            meta.appendChild(chip);
        } else {
            meta.appendChild(document.createElement('span'));
        }
        const dot = document.createElement('span');
        dot.className = `gallery-card-dot${isArchived(char) ? ' archived' : ''}`;
        dot.title = isArchived(char) ? 'Archived' : 'Active';
        meta.appendChild(dot);

        card.append(avatarWrap, body, meta);
        e.grid.appendChild(card);
    });
}

function switchTab(tab) {
    const e = getEls();
    e.tabs.querySelectorAll('.gallery-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    e.panels.forEach(panel => {
        const active = panel.dataset.panel === tab;
        panel.hidden = !active;
        panel.classList.toggle('active', active);
    });
}

function fillEditor(char) {
    const e = getEls();
    if (!char) {
        e.inspectorEmpty.hidden = false;
        e.editor.hidden = true;
        return;
    }
    e.inspectorEmpty.hidden = true;
    e.editor.hidden = false;
    applyAvatar(e.heroBg, char);
    e.heading.textContent = char.name || 'Unnamed';
    e.subtitle.textContent = characterSubtitle(char);
    e.status.textContent = isArchived(char) ? 'Archived' : 'Active';
    e.status.classList.toggle('archived', isArchived(char));
    e.pinBtn.innerHTML = char.pinned ? icons.STAR_FILLED : icons.STAR;
    e.pinBtn.classList.toggle('pinned', !!char.pinned);
    e.archiveBtn.querySelector('.archive-btn-text').textContent = isArchived(char) ? 'Unarchive' : 'Archive';
    e.fields.name.value = char.name || '';
    galleryTags = Array.isArray(char.tags) ? [...char.tags] : [];
    renderGalleryTags();
    galleryAltGreetings = Array.isArray(char.alternate_greetings) ? [...char.alternate_greetings] : [];
    renderGalleryAltGreetings();
    e.fields.post_history.value = char.post_history_instructions || '';
    e.fields.description.value = char.description || '';
    e.fields.notes.value = char.creator_notes || '';
    e.fields.personality.value = char.personality || '';
    e.fields.scenario.value = char.scenario || '';
    e.fields.system.value = char.system_prompt || '';
    e.fields.first.value = char.first_mes || '';
    e.fields.example.value = char.mes_example || '';
    e.fields.creator.value = char.creator || '';
    e.fields.version.value = char.character_version || '';
    gallery.pendingAvatarFile = null;
    e.avatarInput.value = '';
    e.exportMenu.hidden = true;
    e.exportTrigger.setAttribute('aria-expanded', 'false');
    setDirty(false);
    switchTab('basic');
    renderEditorCollections(char);
}

function renderEditorCollections(char = selectedCharacter()) {
    const e = getEls();
    e.collections.innerHTML = '';
    const assignedIds = new Set((char?.collections || []).map(c => c.id));
    (char?.collections || []).forEach(collection => {
        const chip = document.createElement('span');
        chip.className = 'gallery-chip';
        chip.innerHTML = '<span class="gallery-chip-icon"></span><span class="gallery-chip-name"></span>';
        chip.querySelector('.gallery-chip-icon').textContent = collection.icon || DEFAULT_COLLECTION_ICON;
        chip.querySelector('.gallery-chip-name').textContent = collection.name;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.title = `Remove ${collection.name}`;
        remove.setAttribute('aria-label', `Remove ${collection.name}`);
        remove.textContent = '×';
        remove.dataset.removeCollectionId = collection.id;
        chip.appendChild(remove);
        e.collections.appendChild(chip);
    });
    Array.from(e.collectionAdd.options).forEach(option => {
        option.disabled = option.value && assignedIds.has(Number(option.value));
    });
    e.collectionAdd.value = '';
}

function renderGalleryTags() {
    const e = getEls();
    e.tagsChipList.innerHTML = '';
    galleryTags.forEach((tag, idx) => {
        const chip = document.createElement('span');
        chip.className = 'tag-chip';
        chip.innerHTML = `${sanitize(tag)}<button class="tag-chip-remove" title="Remove tag" aria-label="Remove tag">\u00d7</button>`;
        chip.querySelector('.tag-chip-remove').addEventListener('click', () => {
            galleryTags.splice(idx, 1);
            renderGalleryTags();
            setDirty(true);
        });
        e.tagsChipList.appendChild(chip);
    });
}

function renderGalleryAltGreetings() {
    const e = getEls();
    e.altGreetingsList.innerHTML = '';
    galleryAltGreetings.forEach((text, idx) => {
        const row = document.createElement('div');
        row.className = 'alt-greeting-item';
        const ta = document.createElement('textarea');
        ta.className = 'form-textarea';
        ta.rows = 3;
        ta.value = text;
        ta.placeholder = 'Alternate greeting text\u2026';
        ta.addEventListener('input', () => { galleryAltGreetings[idx] = ta.value; setDirty(true); });
        const rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'icon-btn remove-greeting-btn';
        rm.title = 'Remove greeting';
        rm.innerHTML = icons.TRASH;
        rm.addEventListener('click', () => { galleryAltGreetings.splice(idx, 1); renderGalleryAltGreetings(); setDirty(true); });
        row.append(ta, rm);
        e.altGreetingsList.appendChild(row);
    });
}

function render() {
    renderRail();
    renderGrid();
    fillEditor(selectedCharacter());
}

async function refreshData({ keepSelection = true } = {}) {
    const [characters, collections] = await Promise.all([
        API.getCharacters({ includeArchived: true }),
        API.getCharacterCollections(),
    ]);
    gallery.characters = characters;
    gallery.collections = collections;
    if (!keepSelection || !gallery.characters.some(c => c.id === gallery.selectedId)) {
        const visible = visibleCharacters();
        gallery.selectedId = visible[0]?.id || gallery.characters.find(c => !isArchived(c))?.id || gallery.characters[0]?.id || null;
    }
    render();
}

let charModalOriginalParent = null;
const charModalEl = () => document.getElementById('char-modal');

function lockModalAboveGallery() {
    const modal = charModalEl();
    if (!modal || charModalOriginalParent) return;
    charModalOriginalParent = modal.parentElement;
    document.body.appendChild(modal);
}

function restoreModalParent() {
    const modal = charModalEl();
    if (!modal || !charModalOriginalParent) return;
    charModalOriginalParent.appendChild(modal);
    charModalOriginalParent = null;
}

async function openGallery() {
    if (!isDesktop()) return;
    const e = getEls();
    e.root.hidden = false;
    gallery.open = true;
    document.body.classList.add('gallery-open');
    lockModalAboveGallery();
    await refreshData({ keepSelection: true });
    e.search.focus();
}

function closeGallery() {
    if (!confirmDiscard()) return;
    const e = getEls();
    e.root.hidden = true;
    gallery.open = false;
    document.body.classList.remove('gallery-open');
    restoreModalParent();
}

function collectEditorData() {
    const { fields } = getEls();
    return {
        name: fields.name.value.trim(),
        tags: [...galleryTags],
        description: fields.description.value,
        creator_notes: fields.notes.value,
        personality: fields.personality.value,
        scenario: fields.scenario.value,
        system_prompt: fields.system.value,
        post_history_instructions: fields.post_history.value,
        first_mes: fields.first.value,
        mes_example: fields.example.value,
        alternate_greetings: [...galleryAltGreetings],
        creator: fields.creator.value,
        character_version: fields.version.value,
    };
}

async function saveSelected() {
    const char = selectedCharacter();
    if (!char) return;
    const data = collectEditorData();
    if (!data.name) {
        getEls().fields.name.focus();
        showToast('Character name is required', 'error');
        return;
    }
    const e = getEls();
    e.saveBtn.disabled = true;
    e.saveBtn.textContent = 'Saving...';
    try {
        let updated = await API.updateCharacter(char.id, data);
        if (gallery.pendingAvatarFile) {
            updated = await API.uploadAvatar(char.id, gallery.pendingAvatarFile);
        }
        replaceCharacter(updated);
        if (state.activeCharacter?.id === updated.id) {
            state.activeCharacter = updated;
            document.getElementById('current-char-name').textContent = updated.name;
        }
        renderCharList();
        renderLorebookList();
        await refreshData({ keepSelection: true });
        showToast('Character saved', 'success');
    } catch (err) {
        showToast('Could not save character: ' + err.message, 'error');
    } finally {
        e.saveBtn.disabled = false;
        e.saveBtn.textContent = 'Save';
    }
}

async function togglePin() {
    const char = selectedCharacter();
    if (!char) return;
    await togglePinForCharId(char.id);
}

async function togglePinForCharId(charId) {
    const char = gallery.characters.find(c => c.id === charId);
    if (!char) return;
    try {
        const updated = await API.toggleCharacterPin(charId);
        replaceCharacter(updated);
        renderCharList();
        render();
    } catch (err) {
        showToast('Could not favorite character: ' + err.message, 'error');
    }
}

async function setArchived(archived) {
    const char = selectedCharacter();
    if (!char) return;
    try {
        const updated = await API.archiveCharacter(char.id, archived);
        replaceCharacter(updated);
        await loadCharacters();
        await refreshData({ keepSelection: true });
        showToast(archived ? 'Character archived' : 'Character unarchived', 'success');
    } catch (err) {
        showToast('Could not update archive: ' + err.message, 'error');
    }
}

async function startChatWithSelected() {
    const char = selectedCharacter();
    if (!char) return;
    if (isArchived(char)) {
        showToast('Unarchive this character before starting a chat', 'error');
        return;
    }
    if (!confirmDiscard()) return;
    try {
        await selectCharacter(char.id);
        getEls().root.hidden = true;
        gallery.open = false;
        document.body.classList.remove('gallery-open');
        restoreModalParent();
    } catch (err) {
        showToast('Could not open chat: ' + err.message, 'error');
    }
}

async function duplicateSelected() {
    const char = selectedCharacter();
    if (!char) return;
    try {
        const duplicated = await API.duplicateCharacter(char.id);
        gallery.selectedId = duplicated.id;
        await loadCharacters();
        await refreshData({ keepSelection: true });
        showToast('Character duplicated', 'success');
    } catch (err) {
        showToast('Could not duplicate character: ' + err.message, 'error');
    }
}

async function addCollection(name, icon = '') {
    try {
        const collection = await API.createCharacterCollection(name, icon);
        gallery.collections.push(collection);
        gallery.view = `collection:${collection.id}`;
        getEls().newCollectionForm.hidden = true;
        gallery.pendingNewIcon = '';
        await refreshData({ keepSelection: true });
        showToast('Collection created', 'success');
    } catch (err) {
        showToast('Could not create collection: ' + err.message, 'error');
    }
}

async function deleteCollectionById(collectionId) {
    const collection = gallery.collections.find(c => c.id === collectionId);
    if (!collection) return;
    if (!confirm(`Delete collection "${collection.name}"? Characters are not deleted.`)) return;
    try {
        await API.deleteCharacterCollection(collectionId);
        if (gallery.view === `collection:${collectionId}`) gallery.view = 'all';
        await refreshData({ keepSelection: true });
        showToast('Collection deleted', 'success');
    } catch (err) {
        showToast('Could not delete collection: ' + err.message, 'error');
    }
}

async function setCollectionIcon(collectionId, icon) {
    try {
        const updated = await API.updateCharacterCollection(collectionId, { icon });
        const idx = gallery.collections.findIndex(c => c.id === collectionId);
        if (idx !== -1) gallery.collections[idx] = { ...gallery.collections[idx], ...updated };
        await refreshData({ keepSelection: true });
    } catch (err) {
        showToast('Could not update icon: ' + err.message, 'error');
    }
}

function openIconPicker(target, anchorEl) {
    const e = getEls();
    gallery.iconPickerTarget = target;
    e.iconPicker.innerHTML = '';
    COLLECTION_ICONS.forEach(icon => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'gallery-icon-choice';
        btn.dataset.icon = icon;
        btn.textContent = icon;
        e.iconPicker.appendChild(btn);
    });
    const rect = anchorEl.getBoundingClientRect();
    e.iconPicker.style.top = `${rect.bottom + 6}px`;
    e.iconPicker.style.left = `${rect.left}px`;
    e.iconPicker.hidden = false;
}

function closeIconPicker() {
    const e = getEls();
    e.iconPicker.hidden = true;
    gallery.iconPickerTarget = null;
}

function pickIcon(icon) {
    const target = gallery.iconPickerTarget;
    if (!target) return;
    if (target.type === 'new') {
        gallery.pendingNewIcon = icon;
        getEls().newCollectionIcon.textContent = icon;
    } else if (target.type === 'existing') {
        setCollectionIcon(target.collectionId, icon);
    }
    closeIconPicker();
}

async function addSelectedToCollection(collectionId) {
    const char = selectedCharacter();
    if (!char || !collectionId) return;
    try {
        const updated = await API.addCharacterToCollection(collectionId, char.id);
        replaceCharacter(updated);
        await refreshData({ keepSelection: true });
    } catch (err) {
        showToast('Could not add to collection: ' + err.message, 'error');
    }
}

async function removeSelectedFromCollection(collectionId) {
    const char = selectedCharacter();
    if (!char) return;
    try {
        const updated = await API.removeCharacterFromCollection(collectionId, char.id);
        replaceCharacter(updated);
        await refreshData({ keepSelection: true });
    } catch (err) {
        showToast('Could not remove from collection: ' + err.message, 'error');
    }
}

function bindEvents() {
    const e = getEls();
    e.openBtn?.addEventListener('click', openGallery);
    e.collapseBtn?.addEventListener('click', closeGallery);

    e.search.addEventListener('input', () => {
        gallery.query = e.search.value;
        renderGrid();
    });
    e.addBtn.addEventListener('click', () => Modal.open());

    e.nav.addEventListener('click', event => {
        const btn = event.target.closest('.gallery-nav-item');
        if (!btn || !confirmDiscard()) return;
        gallery.view = btn.dataset.view;
        const visible = visibleCharacters();
        gallery.selectedId = visible[0]?.id || null;
        render();
    });
    e.collectionList.addEventListener('click', event => {
        const deleteBtn = event.target.closest('[data-delete-collection-id]');
        if (deleteBtn) {
            deleteCollectionById(Number(deleteBtn.dataset.deleteCollectionId));
            return;
        }
        const iconBtn = event.target.closest('[data-icon-edit-collection-id]');
        if (iconBtn) {
            openIconPicker({ type: 'existing', collectionId: Number(iconBtn.dataset.iconEditCollectionId) }, iconBtn);
            return;
        }
        const selectBtn = event.target.closest('.gallery-collection-select');
        if (!selectBtn || !confirmDiscard()) return;
        gallery.view = `collection:${selectBtn.dataset.collectionId}`;
        const visible = visibleCharacters();
        gallery.selectedId = visible[0]?.id || null;
        render();
    });

    e.newCollectionBtn.addEventListener('click', () => {
        e.newCollectionForm.hidden = false;
        e.newCollectionName.value = '';
        gallery.pendingNewIcon = '';
        e.newCollectionIcon.textContent = DEFAULT_COLLECTION_ICON;
        e.newCollectionName.focus();
    });
    e.newCollectionCancel.addEventListener('click', () => {
        e.newCollectionForm.hidden = true;
        closeIconPicker();
    });
    e.newCollectionIcon.addEventListener('click', () => {
        openIconPicker({ type: 'new' }, e.newCollectionIcon);
    });
    e.newCollectionForm.addEventListener('submit', event => {
        event.preventDefault();
        const name = e.newCollectionName.value.trim();
        if (name) addCollection(name, gallery.pendingNewIcon);
    });
    e.iconPicker.addEventListener('click', event => {
        const btn = event.target.closest('.gallery-icon-choice');
        if (btn) pickIcon(btn.dataset.icon);
    });
    document.addEventListener('click', event => {
        if (gallery.iconPickerTarget && !e.iconPicker.hidden) {
            if (!event.target.closest('.gallery-icon-picker') &&
                !event.target.closest('[data-icon-edit-collection-id]') &&
                event.target.id !== 'gallery-new-collection-icon') {
                closeIconPicker();
            }
        }
        if (!e.exportMenu.hidden) {
            if (!event.target.closest('#gallery-export-card-btn') && !event.target.closest('#gallery-export-menu')) {
                e.exportMenu.hidden = true;
                e.exportTrigger.setAttribute('aria-expanded', 'false');
            }
        }
    });

    e.grid.addEventListener('click', event => {
        const starBtn = event.target.closest('[data-pin-char-id]');
        if (starBtn) {
            event.stopPropagation();
            togglePinForCharId(Number(starBtn.dataset.pinCharId));
            return;
        }
        const card = event.target.closest('.gallery-card');
        if (!card || !confirmDiscard()) return;
        gallery.selectedId = Number(card.dataset.charId);
        render();
    });
    e.grid.addEventListener('keydown', event => {
        const card = event.target.closest('.gallery-card');
        if (!card || event.target !== card) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        if (!confirmDiscard()) return;
        gallery.selectedId = Number(card.dataset.charId);
        render();
    });

    e.tabs.addEventListener('click', event => {
        const tab = event.target.closest('.gallery-tab');
        if (tab) switchTab(tab.dataset.tab);
    });
    e.editor.addEventListener('input', event => {
        if (event.target.matches('input, textarea')) setDirty(true);
    });
    e.avatarInput.addEventListener('change', () => {
        const file = e.avatarInput.files[0];
        if (!file) return;
        gallery.pendingAvatarFile = file;
        const reader = new FileReader();
        reader.onload = event => {
            e.heroBg.style.backgroundImage = `url('${event.target.result}')`;
            e.heroBg.dataset.hasImage = 'true';
        };
        reader.readAsDataURL(file);
        setDirty(true);
    });
    e.pinBtn.addEventListener('click', togglePin);
    e.collectionAdd.addEventListener('change', () => {
        const value = Number(e.collectionAdd.value);
        if (value) addSelectedToCollection(value);
    });
    e.tagsTextInput.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault();
            const val = e.tagsTextInput.value.trim().replace(/,/g, '');
            if (val && !galleryTags.includes(val)) { galleryTags.push(val); renderGalleryTags(); setDirty(true); }
            e.tagsTextInput.value = '';
        } else if (event.key === 'Backspace' && e.tagsTextInput.value === '' && galleryTags.length) {
            galleryTags.pop(); renderGalleryTags(); setDirty(true);
        }
    });
    e.tagsWrap.addEventListener('click', () => e.tagsTextInput.focus());
    e.addGreetingBtn.addEventListener('click', () => {
        galleryAltGreetings.push('');
        renderGalleryAltGreetings();
        setDirty(true);
        const tas = e.altGreetingsList.querySelectorAll('textarea');
        if (tas.length) tas[tas.length - 1].focus();
    });
    e.exportTrigger.addEventListener('click', event => {
        event.stopPropagation();
        const opening = e.exportMenu.hidden;
        e.exportMenu.hidden = !opening;
        e.exportTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
        if (opening) {
            const rect = e.exportTrigger.getBoundingClientRect();
            e.exportMenu.style.top = `${rect.bottom + 6}px`;
            e.exportMenu.style.left = `${rect.left}px`;
        }
    });
    e.exportMenu.addEventListener('click', event => {
        const btn = event.target.closest('[data-fmt]');
        if (!btn) return;
        const char = selectedCharacter();
        if (!char) return;
        API.exportCard(char.id, e.fields.name.value.trim(), btn.dataset.fmt);
        e.exportMenu.hidden = true;
        e.exportTrigger.setAttribute('aria-expanded', 'false');
    });
    e.collections.addEventListener('click', event => {
        const btn = event.target.closest('[data-remove-collection-id]');
        if (btn) removeSelectedFromCollection(Number(btn.dataset.removeCollectionId));
    });
    e.editor.addEventListener('submit', event => {
        event.preventDefault();
        saveSelected();
    });
    e.duplicateBtn.addEventListener('click', duplicateSelected);
    e.archiveBtn.addEventListener('click', () => {
        const char = selectedCharacter();
        if (char) setArchived(!isArchived(char));
    });
    e.startChatBtn.addEventListener('click', startChatWithSelected);
    e.deleteBtn.addEventListener('click', async () => {
        const char = selectedCharacter();
        if (!char) return;
        const deleted = await deleteCharacter(char.id, char.name);
        if (!deleted) return;
        gallery.characters = gallery.characters.filter(c => c.id !== char.id);
        gallery.selectedId = visibleCharacters()[0]?.id || null;
        render();
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && gallery.open) closeGallery();
    });
    document.addEventListener('cozy:characters-changed', async () => {
        if (gallery.open) await refreshData({ keepSelection: true });
    });
    window.matchMedia(DESKTOP_QUERY).addEventListener('change', event => {
        if (!event.matches && gallery.open) closeGallery();
    });
}

export function initCharacterGallery() {
    if (!getEls().root) return;
    bindEvents();
}
