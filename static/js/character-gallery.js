import { state, icons } from './state.js';
import { API } from './api.js';
import { Modal } from './modal.js';
import { applyAvatar, showToast } from './utils.js';
import { loadCharacters, selectCharacter, deleteCharacter, renderCharList } from './characters.js';
import { renderLorebookList } from './lorebooks.js';

const DESKTOP_QUERY = '(min-width: 769px)';

const gallery = {
    open: false,
    characters: [],
    collections: [],
    selectedId: null,
    view: 'all',
    query: '',
    sort: 'name-asc',
    collectionFilter: 'all',
    dirty: false,
    pendingAvatarFile: null,
};

let els = null;

function q(id) {
    return document.getElementById(id);
}

function getEls() {
    if (els) return els;
    els = {
        root: q('character-gallery'),
        openBtn: q('character-gallery-open'),
        closeBtn: q('gallery-close-btn'),
        collapseBtn: q('gallery-collapse-btn'),
        search: q('gallery-search'),
        sort: q('gallery-sort'),
        collectionFilter: q('gallery-collection-filter'),
        addBtn: q('gallery-add-btn'),
        importFile: q('gallery-import-file'),
        nav: document.querySelector('.gallery-nav'),
        countAll: q('gallery-count-all'),
        countFavorites: q('gallery-count-favorites'),
        countArchived: q('gallery-count-archived'),
        footerCount: q('gallery-footer-count'),
        newCollectionBtn: q('gallery-new-collection-btn'),
        newCollectionForm: q('gallery-new-collection-form'),
        newCollectionName: q('gallery-new-collection-name'),
        newCollectionCancel: q('gallery-new-collection-cancel'),
        collectionList: q('gallery-collection-list'),
        grid: q('gallery-grid'),
        empty: q('gallery-empty'),
        viewTitle: q('gallery-view-title'),
        viewCount: q('gallery-view-count'),
        inspectorEmpty: q('gallery-inspector-empty'),
        editor: q('gallery-editor'),
        avatar: q('gallery-editor-avatar'),
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
        saveBtn: q('gallery-save-btn'),
        fields: {
            name: q('gallery-field-name'),
            tags: q('gallery-field-tags'),
            description: q('gallery-field-description'),
            notes: q('gallery-field-notes'),
            personality: q('gallery-field-personality'),
            scenario: q('gallery-field-scenario'),
            system: q('gallery-field-system'),
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

    if (gallery.collectionFilter !== 'all') {
        const collectionId = Number(gallery.collectionFilter);
        chars = chars.filter(char => (char.collections || []).some(c => c.id === collectionId));
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
        if (gallery.sort === 'name-desc') return (b.name || '').localeCompare(a.name || '');
        if (gallery.sort === 'newest') return (b.created_at || '').localeCompare(a.created_at || '');
        if (gallery.sort === 'favorites') {
            if (a.pinned && !b.pinned) return -1;
            if (!a.pinned && b.pinned) return 1;
        }
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
    e.collectionFilter.innerHTML = '<option value="all">Collection: All</option>';
    e.collectionAdd.innerHTML = '<option value="">Add to collection...</option>';

    gallery.collections.forEach(collection => {
        const count = collectionCount(collection.id);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `gallery-nav-item${gallery.view === `collection:${collection.id}` ? ' active' : ''}`;
        btn.dataset.collectionId = collection.id;
        btn.innerHTML = `
            <span class="gallery-nav-icon">◇</span>
            <span></span>
            <strong>${count}</strong>
        `;
        btn.querySelector('span:nth-of-type(2)').textContent = collection.name;
        e.collectionList.appendChild(btn);

        const filterOption = document.createElement('option');
        filterOption.value = String(collection.id);
        filterOption.textContent = `Collection: ${collection.name}`;
        e.collectionFilter.appendChild(filterOption);

        const addOption = document.createElement('option');
        addOption.value = String(collection.id);
        addOption.textContent = collection.name;
        e.collectionAdd.appendChild(addOption);
    });
    e.collectionFilter.value = gallery.collectionFilter;
}

function renderRail() {
    const e = getEls();
    e.countAll.textContent = gallery.characters.filter(c => !isArchived(c)).length;
    e.countFavorites.textContent = gallery.characters.filter(c => c.pinned && !isArchived(c)).length;
    e.countArchived.textContent = gallery.characters.filter(isArchived).length;
    e.footerCount.textContent = `${gallery.characters.length} character${gallery.characters.length === 1 ? '' : 's'}`;

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
        const card = document.createElement('button');
        card.type = 'button';
        card.className = `gallery-card${char.id === gallery.selectedId ? ' selected' : ''}${isArchived(char) ? ' archived' : ''}`;
        card.dataset.charId = char.id;

        const avatar = document.createElement('div');
        avatar.className = 'gallery-card-avatar avatar';
        applyAvatar(avatar, char);

        const star = document.createElement('span');
        star.className = `gallery-card-star${char.pinned ? ' pinned' : ''}`;
        star.innerHTML = char.pinned ? icons.STAR_FILLED : icons.STAR;

        const name = document.createElement('strong');
        name.textContent = char.name || 'Unnamed';

        const subtitle = document.createElement('span');
        subtitle.textContent = characterSubtitle(char);

        const meta = document.createElement('div');
        meta.className = 'gallery-card-meta';
        const firstCollection = (char.collections || [])[0];
        if (firstCollection) {
            const chip = document.createElement('span');
            chip.className = 'gallery-card-chip';
            chip.textContent = firstCollection.name;
            meta.appendChild(chip);
        }
        const dot = document.createElement('span');
        dot.className = `gallery-card-dot${isArchived(char) ? ' archived' : ''}`;
        meta.appendChild(dot);

        card.append(avatar, star, name, subtitle, meta);
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
    applyAvatar(e.avatar, char);
    e.heading.textContent = char.name || 'Unnamed';
    e.subtitle.textContent = characterSubtitle(char);
    e.status.textContent = isArchived(char) ? 'Archived' : 'Active';
    e.status.classList.toggle('archived', isArchived(char));
    e.pinBtn.innerHTML = char.pinned ? icons.STAR_FILLED : icons.STAR;
    e.pinBtn.classList.toggle('pinned', !!char.pinned);
    e.archiveBtn.textContent = isArchived(char) ? 'Unarchive' : 'Archive';
    e.fields.name.value = char.name || '';
    e.fields.tags.value = Array.isArray(char.tags) ? char.tags.join(', ') : '';
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
    setDirty(false);
    switchTab('profile');
    renderEditorCollections(char);
}

function renderEditorCollections(char = selectedCharacter()) {
    const e = getEls();
    e.collections.innerHTML = '';
    const assignedIds = new Set((char?.collections || []).map(c => c.id));
    (char?.collections || []).forEach(collection => {
        const chip = document.createElement('span');
        chip.className = 'gallery-chip';
        chip.textContent = collection.name;
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

async function openGallery() {
    if (!isDesktop()) return;
    const e = getEls();
    e.root.hidden = false;
    gallery.open = true;
    await refreshData({ keepSelection: true });
    e.search.focus();
}

function closeGallery() {
    if (!confirmDiscard()) return;
    const e = getEls();
    e.root.hidden = true;
    gallery.open = false;
}

function collectEditorData() {
    const { fields } = getEls();
    return {
        name: fields.name.value.trim(),
        tags: fields.tags.value.split(',').map(t => t.trim()).filter(Boolean),
        description: fields.description.value,
        creator_notes: fields.notes.value,
        personality: fields.personality.value,
        scenario: fields.scenario.value,
        system_prompt: fields.system.value,
        first_mes: fields.first.value,
        mes_example: fields.example.value,
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
        e.saveBtn.textContent = 'Save Changes';
    }
}

async function togglePin() {
    const char = selectedCharacter();
    if (!char) return;
    try {
        const updated = await API.toggleCharacterPin(char.id);
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
        if (state.activeCharacter?.id === updated.id && archived) {
            const next = state.characters.find(c => !c.missing);
            if (next) await selectCharacter(next.id);
        }
        await refreshData({ keepSelection: true });
        showToast(archived ? 'Character archived' : 'Character unarchived', 'success');
    } catch (err) {
        showToast('Could not update archive: ' + err.message, 'error');
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

async function addCollection(name) {
    try {
        const collection = await API.createCharacterCollection(name);
        gallery.collections.push(collection);
        gallery.view = `collection:${collection.id}`;
        getEls().newCollectionForm.hidden = true;
        await refreshData({ keepSelection: true });
        showToast('Collection created', 'success');
    } catch (err) {
        showToast('Could not create collection: ' + err.message, 'error');
    }
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

async function importFromGallery(file) {
    if (!file) return;
    try {
        const char = await API.importCard(file);
        gallery.selectedId = char.id;
        state.characters.push(char);
        renderCharList();
        renderLorebookList();
        await selectCharacter(char.id);
        await refreshData({ keepSelection: true });
        showToast('Character imported', 'success');
    } catch (err) {
        showToast('Import failed: ' + err.message, 'error');
    } finally {
        getEls().importFile.value = '';
    }
}

function bindEvents() {
    const e = getEls();
    e.openBtn?.addEventListener('click', openGallery);
    e.closeBtn?.addEventListener('click', closeGallery);
    e.collapseBtn?.addEventListener('click', closeGallery);

    e.search.addEventListener('input', () => {
        gallery.query = e.search.value;
        renderGrid();
    });
    e.sort.addEventListener('change', () => {
        gallery.sort = e.sort.value;
        renderGrid();
    });
    e.collectionFilter.addEventListener('change', () => {
        gallery.collectionFilter = e.collectionFilter.value;
        renderGrid();
    });
    e.addBtn.addEventListener('click', () => Modal.open());
    e.importFile.addEventListener('change', () => importFromGallery(e.importFile.files[0]));

    e.nav.addEventListener('click', event => {
        const btn = event.target.closest('.gallery-nav-item');
        if (!btn || !confirmDiscard()) return;
        gallery.view = btn.dataset.view;
        gallery.collectionFilter = 'all';
        const visible = visibleCharacters();
        gallery.selectedId = visible[0]?.id || null;
        render();
    });
    e.collectionList.addEventListener('click', event => {
        const btn = event.target.closest('.gallery-nav-item');
        if (!btn || !confirmDiscard()) return;
        gallery.view = `collection:${btn.dataset.collectionId}`;
        gallery.collectionFilter = 'all';
        const visible = visibleCharacters();
        gallery.selectedId = visible[0]?.id || null;
        render();
    });

    e.newCollectionBtn.addEventListener('click', () => {
        e.newCollectionForm.hidden = false;
        e.newCollectionName.value = '';
        e.newCollectionName.focus();
    });
    e.newCollectionCancel.addEventListener('click', () => {
        e.newCollectionForm.hidden = true;
    });
    e.newCollectionForm.addEventListener('submit', event => {
        event.preventDefault();
        const name = e.newCollectionName.value.trim();
        if (name) addCollection(name);
    });

    e.grid.addEventListener('click', event => {
        const card = event.target.closest('.gallery-card');
        if (!card || !confirmDiscard()) return;
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
            e.avatar.style.backgroundImage = `url('${event.target.result}')`;
            e.avatar.dataset.hasImage = 'true';
            e.avatar.textContent = '';
        };
        reader.readAsDataURL(file);
        setDirty(true);
    });
    e.pinBtn.addEventListener('click', togglePin);
    e.collectionAdd.addEventListener('change', () => {
        const value = Number(e.collectionAdd.value);
        if (value) addSelectedToCollection(value);
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
    e.deleteBtn.addEventListener('click', async () => {
        const char = selectedCharacter();
        if (!char) return;
        await deleteCharacter(char.id, char.name);
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
