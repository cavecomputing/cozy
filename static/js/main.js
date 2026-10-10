// ═══════════════════════════════════════════════════════════════════════════
// ENTRY POINT — orchestrates all modules
// ═══════════════════════════════════════════════════════════════════════════
import { state, el, llm, icons, initElements } from './state.js';
import { API, prefetchSelection, dropPrefetched } from './api.js';
import {
    autoResize, scrollToBottom, showToast, Flyouts, savePrefs, closeMobileSidebar,
    debounce, updateComposerState, copyText, stopGeneration, MOBILE_SHELL_QUERY,
    withBusy, flashSettingsSavedTick, dismissApiNotice,
} from './utils.js';
import { applyTheme, loadThemeList, renderThemePicker } from './themes.js';
import { loadCharacters, selectCharacter, deleteCharacter, renderCharList, toggleCharMenu, closeCharMenu, toggleArchived, toggleArchivedSection } from './characters.js';
import { selectChat, createNewChat, deleteChat, startChatRename, importChat, handleChatImportFile, renderChats, switchBranch } from './chats.js';
import { startEditing, finishEditing, handleSwipeAction, findStateMsg, drawOlderNearTop } from './messages.js';
import { Modal } from './modal.js';
import { loadPersonas, showPersonaForm, closePersonaForm } from './personas.js';
import { handleSend } from './send.js';
import {
    loadLLMSettings, queueLLMSettingsSave, queueMainApiKeySave,
    flushLLMSettingsSave, applyAdvancedConfigurationVisibility,
    setAdvancedConfigurationVisible,
    browseModels, browseSummaryModels, closeModelMenu, closeSummaryModelMenu,
    selectModelFromMenu, selectSummaryModelFromMenu, testLLMConnection,
    activatePreset, createNewPreset, renamePreset, deletePreset, searchModelsFromInput,
    searchSummaryModelsFromInput, clearModelListCache, clearSummaryModelListCache,
} from './llm-settings.js';
import {
    loadSystemPrompts, selectSystemPrompt, createSystemPrompt, renameSystemPrompt,
    deleteSystemPrompt,
    updateSystemPromptContent, syncActivePromptFromEditors, normalizeVersionEditor,
    previewSystemPrompt, importSystemPrompt, handleSystemPromptImportFile,
    exportSystemPrompt, exportPreviewPayload, switchPromptBuilderMode, initPromptVarsPanel,
    toggleRenderedPrompts, closeRenderedPrompts,
} from './system-prompts.js';
import { loadLorebooks, renderLorebookList, selectLorebook, canLeaveLorebook, newLorebook, saveLorebook, deleteLorebook, addEntry, handleEntriesClick, filterEntries, renderLorebookFlyout, onLorebookSelectChange, importLorebook, handleImportFile, exportLorebook, loadAuthorNote, scheduleAuthorNoteSave, flushAuthorNote, updateAuthorNoteCounter } from './lorebooks.js';
import {
    loadRegexPresets, selectRegexPreset, createRegexPreset, deleteRegexPreset,
    addFilter, handleFilterListClick, handleFilterListInput,
    updateTestPanel, importRegexPreset, handleRegexImportFile, exportRegexPreset,
    flushRegexSave,
} from './regex-filters.js';
import { SAMPLER_FIELDS, updateContextSizeWarning } from './sampler.js';
import { exportChat } from './export.js';
import { initTooltips } from './tooltips.js';
import { saveDraft } from './drafts.js';
import { initSlashCommands, updateSlashCommands, handleSlashKeydown, closeSlashCommands } from './slash-commands.js';
import { updateContextMeter, updateContextViews, initContextMeter, setContextMeterVisible } from './context-meter.js';
import { enhanceSettingsSelects } from './custom-select.js';
import { dialogueStart, matchDialogue } from './rp-dialogue.js';
import { confirmDialog } from './confirm.js';
import { initStorageStats, loadStorageStats } from './storage-stats.js';
import {
    initSummaryHandlers, renderMemorySummaryCard, setSummaryBudgetChangeHandler,
} from './summaries.js';

// Configure markdown renderer — GFM + line-break-to-<br> like most chat apps
marked.use({ breaks: true, gfm: true });

// RP dialogue extension — wrap "quoted speech" in a styled span. Which marks
// count as quotes lives in rp-dialogue.js, so the German and guillemet
// conventions get styled too, not just the English pair.
marked.use({
    extensions: [{
        name: 'rpDialogue',
        level: 'inline',
        start: dialogueStart,
        tokenizer(src) {
            const found = matchDialogue(src);
            if (!found) return;
            const token = { type: 'rpDialogue', ...found, tokens: [] };
            this.lexer.inline(token.text, token.tokens);
            return token;
        },
        renderer(token) {
            // Put back the marks the reply actually used. The job here is to
            // style the speech, not to anglicise its punctuation — swapping
            // German marks for English ones is what the Regex tab is for, and
            // only when the user asks for it.
            return `<span class="rp-dialogue">${token.open}${this.parser.parseInline(token.tokens)}${token.close}</span>`;
        },
    }],
});

// ═══════════════════════════════════════════════════════════════════════════
// PREFS (localStorage)
// ═══════════════════════════════════════════════════════════════════════════
function loadPrefs() {
    try {
        const p = JSON.parse(localStorage.getItem('cozy/prefs') || '{}');
        state.theme            = p.theme             || 'cozy';
        state._savedActiveId   = p.activeCharId     || null;
        state._savedChatId     = p.activeChatId     || null;
        state._savedPersonaId  = p.activePersonaId  || null;
        // Migrate section keys retained in preferences from earlier settings layouts.
        const savedSection     = p.settingsSection  || 'general';
        state.settingsSection  = savedSection === 'sampler' ? 'api'
            : savedSection === 'appearance' ? 'general'
            : savedSection;
    } catch { /* ignore */ }
}

// ═══════════════════════════════════════════════════════════════════════════
// SETTINGS NAV (macOS-style two-pane)
// ═══════════════════════════════════════════════════════════════════════════
const isMobileSettings = () => window.matchMedia(MOBILE_SHELL_QUERY).matches;

function applySettingsSection(key, { drillIntoOnMobile = false } = {}) {
    // The Regex tab is advanced-only: never land on it while hidden.
    if (key === 'regex' && !state.showAdvancedConfiguration) key = 'general';
    state.settingsSection = key;
    for (const sec of el.settingsPane.querySelectorAll('.settings-section')) {
        sec.hidden = sec.dataset.section !== key;
    }
    for (const btn of el.settingsNav.querySelectorAll('.settings-nav-item')) {
        const active = btn.dataset.section === key;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
    }
    if (drillIntoOnMobile && isMobileSettings()) {
        el.settingsShell.classList.add('in-detail');
        el.settingsBackBtn.hidden = false;
    }
    el.settingsPane.scrollTop = 0;
    updateSettingsHeader();
    if (drillIntoOnMobile && isMobileSettings()) {
        document.getElementById('settings-title').focus();
    }
    if (key === 'about' && !el.settingsFlyout.hidden) {
        void loadStorageStats();
    }
    savePrefs();
}

/**
 * Close one `.export-dropdown` menu — the Import/export pairs on the Prompt
 * and Regex tabs and the Backup menu on About are the same three lines of
 * teardown, and Escape needs a fourth caller for whichever one is open.
 */
function closeExportMenu(menu) {
    if (!menu || menu.hidden) return false;
    menu.hidden = true;
    const dropdown = menu.closest('.export-dropdown');
    dropdown?.classList.remove('open');
    dropdown?.querySelector('[aria-haspopup]')?.setAttribute('aria-expanded', 'false');
    return true;
}

/**
 * Peel whichever dropdown is open inside Settings, innermost first. Returns
 * true when one was closed, which is how Escape knows to stop.
 */
function closeOpenSettingsMenu() {
    if (el.modelPickerMenu?.hidden === false) { closeModelMenu(); return true; }
    if (el.summaryModelPickerMenu?.hidden === false) { closeSummaryModelMenu(); return true; }
    const menu = document.querySelector('.export-menu:not([hidden])');
    // Focus is on the trigger already when the menu was opened by click, but
    // not when the user tabbed into an item — and hiding the menu under a
    // focused item would drop focus to the body.
    const trigger = menu?.closest('.export-dropdown')?.querySelector('[aria-haspopup]');
    if (!closeExportMenu(menu)) return false;
    trigger?.focus();
    return true;
}

// ── Backup and restore (About → Storage) ─────────────────────────────────
// Download is a plain navigation so the browser streams the zip straight to
// disk. Restore replaces the whole data directory, so it asks first and
// reloads afterwards: every list on screen belongs to the database that just
// went away.
function bindBackupHandlers() {
    const closeMenu = () => closeExportMenu(el.backupMenu);
    el.backupBtn?.addEventListener('click', e => {
        e.stopPropagation();
        const willOpen = el.backupMenu?.hidden;
        if (el.backupMenu) el.backupMenu.hidden = !willOpen;
        el.backupDropdown?.classList.toggle('open', willOpen);
        el.backupBtn?.setAttribute('aria-expanded', String(!!willOpen));
    });
    document.addEventListener('click', e => {
        if (!el.backupMenu || el.backupMenu.hidden) return;
        if (!e.target.closest('#about-backup-dropdown')) closeMenu();
    });
    el.backupExport?.addEventListener('click', () => {
        closeMenu();
        showToast('Preparing backup…', 'success', 2000);
        window.location.href = '/api/backup';
    });
    el.backupRestore?.addEventListener('click', () => { closeMenu(); el.backupFile?.click(); });
    el.backupFile?.addEventListener('change', async e => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        const ok = await confirmDialog({
            title: 'Restore from this backup?',
            message: `Everything Cozy has now — chats, characters, personas, settings — `
                + `is deleted and replaced with the contents of ${file.name}. This cannot be undone.`,
            confirmLabel: 'Replace everything',
        });
        if (!ok) return;
        try {
            await API.restoreBackup(file);
            showToast('Backup restored — reloading…', 'success');
            setTimeout(() => window.location.reload(), 900);
        } catch (err) {
            showToast(err.message || 'Restore failed', 'error', 6000);
        }
    });
}

function updateSettingsHeader() {
    const detail = isMobileSettings() && el.settingsShell.classList.contains('in-detail');
    document.getElementById('settings-title').textContent = detail
        ? el.settingsPane.querySelector('.settings-section:not([hidden]) h3').textContent
        : 'Settings';
}

function exitSettingsDetail() {
    const wasDetail = el.settingsShell?.classList.contains('in-detail');
    el.settingsShell?.classList.remove('in-detail');
    // Back from a page slides the list in; opening Settings on it does not.
    el.settingsShell?.classList.toggle('popped', wasDetail && !el.settingsFlyout.hidden);
    if (el.settingsBackBtn) el.settingsBackBtn.hidden = true;
    updateSettingsHeader();
    if (wasDetail && !el.settingsFlyout.hidden) {
        el.settingsNav.querySelector('.settings-nav-item.active')?.focus();
    }
}

function setSamplerPopoverOpen(open) {
    if (!el.samplerPopover || !el.samplerConfigureBtn) return;
    el.samplerPopover.hidden = !open;
    el.samplerConfigureBtn.setAttribute('aria-expanded', String(open));
}

/**
 * Close the innermost thing open inside Settings, if anything is: Esc and Back
 * both peel these before they touch the panel that holds them. Returns true
 * when one went.
 */
function closeInnerLayer() {
    if (settingsSubmodal) {
        closeSettingsSubmodal(settingsSubmodal);
        return true;
    }
    if (!el.settingsFlyout.hidden && el.samplerPopover?.hidden === false) {
        setSamplerPopoverOpen(false);
        el.samplerConfigureBtn.focus();
        return true;
    }
    if (el.promptRenderedFlyout?.hidden === false) {
        closeRenderedPrompts();
        return true;
    }
    // Last layer before the whole panel: a model picker or an import/export
    // menu open inside Settings used to drop straight through to
    // closeAllExcept(), which shut the whole flyout and lost the user's place
    // over a dropdown they only wanted to dismiss.
    return closeOpenSettingsMenu();
}

const panelOpen = () => !el.settingsFlyout.hidden || !el.chatFlyout.hidden
    || el.memoryFlyout?.hidden === false || el.personaDropup.classList.contains('show')
    || !document.getElementById('char-modal').hidden || el.sidebar.classList.contains('mobile-open');

function blurSettingsFlyoutFocus() {
    if (el.settingsFlyout.contains(document.activeElement)) {
        document.activeElement.blur();
    }
}

// Settings sub-modals — the five help panels and the request preview. They open
// at the same spot above the settings flyout, so only one is up at a time, and
// closing the flyout takes it along rather than leaving it over the chat.
let settingsSubmodal = null;
let settingsSubmodalReturnFocus = null;

function openSettingsSubmodal(modal) {
    if (!modal) return;
    // Read the trigger before closing the previous panel: that close restores
    // focus to *its* trigger, which is what we would otherwise record here.
    const trigger = document.activeElement;
    closeSettingsSubmodal(settingsSubmodal);
    settingsSubmodalReturnFocus = trigger;
    settingsSubmodal = modal;
    modal.hidden = false;
    modal.querySelector('button')?.focus();
}

function closeSettingsSubmodal(modal) {
    if (!modal) return;
    modal.hidden = true;
    if (modal === settingsSubmodal) settingsSubmodal = null;
    if (settingsSubmodalReturnFocus && document.contains(settingsSubmodalReturnFocus)) {
        settingsSubmodalReturnFocus.focus();
    }
    settingsSubmodalReturnFocus = null;
}

function closeSettingsFlyout() {
    const restoreFocus = el.settingsFlyout.contains(document.activeElement)
        || settingsSubmodal?.contains(document.activeElement);
    // Ahead of the blur, so focus lands on the trigger and is cleared with the
    // rest of the flyout instead of being left inside a hidden subtree.
    closeSettingsSubmodal(settingsSubmodal);
    blurSettingsFlyoutFocus();
    el.settingsFlyout.hidden = true;
    closeRenderedPrompts();
    setSamplerPopoverOpen(false);
    exitSettingsDetail();
    // An edit made in the last half-second would otherwise die in the debounce.
    void flushRegexSave();
    if (restoreFocus) (isMobileSettings() ? el.mobileMenuBtn : el.settingsBtn)?.focus();
}

// ═══════════════════════════════════════════════════════════════════════════
// SIDEBAR HELPERS
// ═══════════════════════════════════════════════════════════════════════════
function openMobileSidebar() {
    el.sidebar.classList.add('mobile-open');
    el.mobileBackdrop.classList.add('show');
    el.mobileMenuBtn?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('mobile-drawer-open');
    const main = document.getElementById('main-content');
    if (main) {
        main.inert = true;
        main.setAttribute('aria-hidden', 'true');
    }
    el.mobileSidebarClose?.focus();
}

function bindResponsiveShellHandlers() {
    // On mobile, move modals out of sidebar so CSS fixed positioning works
    // (transform on sidebar creates a new containing block that breaks fixed)
    const mobileQuery = window.matchMedia(MOBILE_SHELL_QUERY);
    function handleMobileModals(mq) {
        const sheets = [el.chatFlyout, el.memoryFlyout].filter(Boolean);
        if (mq.matches) {
            document.querySelectorAll('#sidebar .modal-overlay').forEach(m => {
                document.body.appendChild(m);
            });
            // The composer flyouts render as fixed bottom sheets on mobile.
            // Inside #input-wrapper (position:relative + z-index:1) their
            // z-index is trapped below the body-level sheet backdrop, which
            // then paints over them — hoist them to <body> like the modals.
            sheets.forEach(s => document.body.appendChild(s));
        } else {
            // Move them back for desktop flyout positioning
            const sidebar = document.getElementById('sidebar');
            document.querySelectorAll('body > .modal-overlay').forEach(m => {
                sidebar.appendChild(m);
            });
            // Desktop popovers anchor absolutely to #input-wrapper
            sheets.forEach(s => el.inputWrapper?.appendChild(s));
        }
        // The composer placeholder carries the slash hint on desktop only.
        updateComposerState();
    }
    handleMobileModals(mobileQuery);
    mobileQuery.addEventListener('change', handleMobileModals);
}

function bindSheetBackdropHandlers() {
    // On mobile the composer flyouts render as bottom sheets over a dimmed
    // backdrop. Watch the flyouts' hidden attribute so every open/close path
    // (toggle buttons, outside clicks, Escape, chat selection) stays in sync.
    // The backdrop is display:none outside the mobile media query, so the
    // .show class is harmless on desktop.
    const backdrop = document.getElementById('sheet-backdrop');
    if (!backdrop) return;
    const sheets = [el.chatFlyout, el.memoryFlyout].filter(Boolean);
    const sync = () => {
        backdrop.classList.toggle('show', sheets.some(s => !s.hidden));
    };
    const observer = new MutationObserver(sync);
    sheets.forEach(s => observer.observe(s, { attributes: true, attributeFilter: ['hidden'] }));
    backdrop.addEventListener('click', () => Flyouts.closeAllExcept(null));
    sync();
}

// A panel dragged toward the edge it came from follows the finger, then either
// carries on out or springs back. The drawer goes left, and can be taken
// anywhere since a vertical touch on it is the list's to scroll (the browser
// takes that one over and cancels ours); a sheet goes down, from its grabber.
// Touch and pen only: a mouse has the buttons.
const DRAG_SLOP_PX = 8;            // before it is a drag at all
const DRAG_CLOSE_PX = 80;          // far enough to mean it
const DRAG_FLICK_PX_PER_MS = 0.5;  // or quick enough to mean it from less

function bindDragToClose(panel, handle, { vertical, close, backdrop, when = () => true }) {
    const toward = vertical ? 1 : -1;   // which way along the axis is out
    const axis = vertical ? 'Y' : 'X';
    let drag = null;                    // the touch being followed

    const settle = away => {
        panel.style.transition = 'transform var(--motion-base) ease-out';
        panel.style.transform = `translate${axis}(${away ? toward * 100 : 0}%)`;
        if (backdrop) {
            backdrop.style.transition = 'opacity var(--motion-base)';
            backdrop.style.opacity = away ? 0 : 1;
        }
        setTimeout(() => {
            if (away) close();
            panel.style.transition = panel.style.transform = '';
            if (backdrop) backdrop.style.transition = backdrop.style.opacity = '';
        }, 180);
    };

    handle.addEventListener('pointerdown', e => {
        if (e.pointerType === 'mouse' || !when()) return;
        drag = { id: e.pointerId, x: e.clientX, y: e.clientY, at: e.timeStamp, dist: 0, live: false };
    });

    handle.addEventListener('pointermove', e => {
        if (e.pointerId !== drag?.id) return;
        const dist = toward * (vertical ? e.clientY - drag.y : e.clientX - drag.x);
        if (!drag.live) {
            if (dist < DRAG_SLOP_PX) return;
            drag.live = true;
            handle.setPointerCapture(e.pointerId);
            panel.style.transition = 'none';
            if (backdrop) backdrop.style.transition = 'none';
        }
        drag.dist = Math.max(0, dist);
        panel.style.transform = `translate${axis}(${toward * drag.dist}px)`;
        if (backdrop) {
            backdrop.style.opacity = 1 - Math.min(1, drag.dist / (vertical ? panel.offsetHeight : panel.offsetWidth));
        }
    });

    const release = e => {
        if (e.pointerId !== drag?.id) return;
        const { live, dist, at } = drag;
        drag = null;
        if (!live) return;
        const flicked = dist > 20 && dist / (e.timeStamp - at) > DRAG_FLICK_PX_PER_MS;
        settle(e.type === 'pointerup' && (dist > DRAG_CLOSE_PX || flicked));
    };
    handle.addEventListener('pointerup', release);
    handle.addEventListener('pointercancel', release);
    handle.addEventListener('lostpointercapture', release);   // closed from under the finger
}

function bindSwipeToClose() {
    bindDragToClose(el.sidebar, el.sidebar, {
        vertical: false,
        close: closeMobileSidebar,
        backdrop: el.mobileBackdrop,
        when: () => el.sidebar.classList.contains('mobile-open'),
    });
    const sheetBackdrop = document.getElementById('sheet-backdrop');
    for (const sheet of [el.chatFlyout, el.memoryFlyout].filter(Boolean)) {
        bindDragToClose(sheet, sheet.querySelector('.sheet-grabber'), {
            vertical: true,
            close: () => Flyouts.closeAllExcept(null),
            backdrop: sheetBackdrop,
        });
    }
}

function bindFlyoutHandlers() {
    // Register flyouts so only one is open at a time
    Flyouts.register('settings', () => {
        closeSettingsFlyout();
    });
    Flyouts.register('chat', () => {
        el.chatFlyout.hidden = true;
        el.chatFlyoutBtn?.setAttribute('aria-expanded', 'false');
    });
    Flyouts.register('memory', () => {
        if (el.memoryFlyout) el.memoryFlyout.hidden = true;
        el.memoryFlyoutBtn?.setAttribute('aria-expanded', 'false');
        flushAuthorNote();
    });
    Flyouts.register('persona', closePersonaDropup);
}

// Hiding the dropup leaves #persona-inline-form with hidden=false, so without
// this the form (and its Save listener, bound to one specific persona) survives
// out of sight and comes back on the next open.
function closePersonaDropup() {
    el.personaDropup.classList.remove('show');
    el.personaDropup.setAttribute('aria-hidden', 'true');
    closePersonaForm();
}

function bindSidebarHandlers() {
    // Mobile sidebar
    el.mobileMenuBtn?.addEventListener('click', openMobileSidebar);
    el.mobileBackdrop?.addEventListener('click', closeMobileSidebar);
    el.mobileSidebarClose?.addEventListener('click', closeMobileSidebar);
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && el.sidebar.classList.contains('mobile-open')) {
            closeMobileSidebar();
        }
    });
}

// On a touch device the system's Back (a swipe in from the screen edge on iOS,
// the back gesture or button on Android) would leave Cozy while a panel sits
// over the chat. While any panel is up Cozy keeps one history entry standing
// for it, so Back spends that entry closing the panel instead; a drilled-down
// Settings page goes back a level at a time. The entry is dropped again when
// the last panel closes some other way, so the history stays as it was.
// Elsewhere there is no entry: the browser's Back leaves, as it always has.
const coarsePointer = window.matchMedia('(pointer: coarse)');
let backEntry = false;        // the current history entry is Cozy's own
let ownBacksPending = 0;      // history.back() calls of Cozy's own yet to land

const confirmOpen = () => !!document.querySelector('.confirm-overlay:not([hidden])');

function syncBackEntry() {
    if (!coarsePointer.matches) return;
    const open = panelOpen() || confirmOpen();
    if (open && !backEntry) {
        history.pushState({ cozyPanel: true }, '');
        backEntry = true;
    } else if (!open && backEntry) {
        backEntry = false;
        ownBacksPending += 1;
        history.back();
    }
}

function goBack() {
    if (confirmOpen()) {
        document.querySelector('.confirm-overlay .confirm-cancel').click();
    } else if (closeInnerLayer()) {
        // a sub-panel or menu inside Settings went
    } else if (!el.settingsFlyout.hidden && el.settingsShell.classList.contains('in-detail')) {
        exitSettingsDetail();
    } else {
        closeMobileSidebar();
        Flyouts.closeAllExcept(null);
    }
}

function bindBackButton() {
    window.addEventListener('popstate', () => {
        if (ownBacksPending) {
            ownBacksPending -= 1;
        } else if (backEntry) {
            backEntry = false;
            goBack();
            syncBackEntry();   // whatever is still open gets a fresh entry
        }
    });
    new MutationObserver(syncBackEntry).observe(document.body, {
        subtree: true, attributes: true, attributeFilter: ['hidden', 'class'],
    });
}

// The meter and the boundary separator share one full context analysis —
// template and lorebook resolution over the whole chat — per refresh. Every
// input that moves the token budget refreshes them, so the recompute is
// debounced: typing "32768" into Context tokens used to re-analyse the
// conversation five times. Persistence behind those fields was already
// debounced; this was the half that wasn't.
//
// The pair moves together so the separator never contradicts the meter's
// tooltip. Two timers because the callers differ in what they refresh: a field
// that cannot move the boundary has no business redrawing it.
const updateContextViewsSoon = debounce(updateContextViews, 150);
const updateContextMeterSoon = debounce(updateContextMeter, 150);

function bindSettingsHandlers() {
    const openSettings = async e => {
        e.stopPropagation();
        const isOpen = !el.settingsFlyout.hidden;
        Flyouts.closeAllExcept('settings');
        if (isOpen) {
            closeSettingsFlyout();
            return;
        }
        if (isMobileSettings()) {
            closeMobileSidebar({ restoreFocus: false, immediate: true });
        }
        el.settingsFlyout.hidden = false;
        dismissApiNotice();
        // Nothing can chat until an endpoint is set, so that is the page to open on.
        if (!el.apiEndpoint?.value) state.settingsSection = 'api';
        // On desktop: restore the saved section. On mobile: show the list view first
        // (saved section stays "active" in the nav so reopening from the list is one tap away).
        const requestedSection = state.settingsSection;
        applySettingsSection(state.settingsSection);
        exitSettingsDetail();
        el.settingsNav.querySelector('.settings-nav-item.active')?.focus();
        renderThemePicker();
        const s = await loadLLMSettings();
        await loadSystemPrompts(s);
        applyAdvancedConfigurationVisibility();
        // A first open may have bounced off the hidden Regex tab before
        // settings loaded; restore it for advanced users.
        if (requestedSection === 'regex' && state.showAdvancedConfiguration) {
            applySettingsSection('regex');
        }
        await loadRegexPresets(s);
    };
    el.settingsBtn?.addEventListener('click', openSettings);
    el.settingsCloseBtn?.addEventListener('click', () => {
        closeSettingsFlyout();
    });

    // Settings nav (section switcher) + mobile back button
    el.settingsNav?.addEventListener('click', e => {
        const btn = e.target.closest('.settings-nav-item');
        if (!btn) return;
        applySettingsSection(btn.dataset.section, { drillIntoOnMobile: true });
    });
    el.settingsBackBtn?.addEventListener('click', exitSettingsDetail);

    // Safari's keyboard shrinks the visual viewport without changing 100dvh.
    // Leave pinch zoom to the browser; only follow the keyboard-sized viewport.
    const fitSettingsViewport = () => {
        const viewport = window.visualViewport;
        const fit = isMobileSettings() && viewport?.scale === 1;
        document.documentElement.style.setProperty('--settings-viewport-height', fit ? `${viewport.height}px` : '100dvh');
        document.documentElement.style.setProperty('--settings-viewport-top', fit ? `${viewport.offsetTop}px` : '0px');
    };
    window.visualViewport?.addEventListener('resize', () => {
        fitSettingsViewport();
        const focused = document.activeElement;
        if (isMobileSettings() && window.visualViewport.scale === 1
            && el.settingsFlyout.contains(focused) && focused.matches('input, textarea')) {
            requestAnimationFrame(() => focused.scrollIntoView({ block: 'center' }));
        }
    });
    window.visualViewport?.addEventListener('scroll', fitSettingsViewport);
    fitSettingsViewport();

    window.matchMedia(MOBILE_SHELL_QUERY).addEventListener('change', () => {
        exitSettingsDetail();
    });

    document.getElementById('prompt-vars-toggle').addEventListener('click', e => {
        const button = e.currentTarget;
        button.setAttribute('aria-expanded', String(button.getAttribute('aria-expanded') !== 'true'));
    });

    // Close settings on outside click (matches chat / lorebook flyout behavior).
    // Skip the toggle button (it handles open/close itself) and the
    // stacked sub-modals that float above the settings flyout.
    document.addEventListener('click', e => {
        if (el.settingsFlyout.hidden) return;
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
        if (el.settingsFlyout.contains(e.target) || path.includes(el.settingsFlyout)) return;
        if (e.target.closest('#settings-btn')) return;
        if (e.target.closest('.settings-submodal')) return;
        closeSettingsFlyout();
    });

    el.settingsThemeSelect?.addEventListener('change', () => {
        applyTheme(el.settingsThemeSelect.value);
        savePrefs();
        // The only General control that persists to localStorage rather than
        // the settings row, so it needs its own tick.
        flashSettingsSavedTick();
    });
    el.settingsContextMeterToggle?.addEventListener('change', () => {
        setContextMeterVisible(el.settingsContextMeterToggle.checked);
    });
    el.settingsAdvancedToggle?.addEventListener('change', () => {
        setAdvancedConfigurationVisible(el.settingsAdvancedToggle.checked);
    });
    // Auto Summaries config — autosave while typing, flush on blur.
    el.summaryEndpoint?.addEventListener('input', () => {
        state.summaryApiEndpoint = el.summaryEndpoint.value;
        clearSummaryModelListCache();
        queueLLMSettingsSave({ summary_api_endpoint: el.summaryEndpoint.value });
        renderMemorySummaryCard();
    });
    el.summaryEndpoint?.addEventListener('blur', flushLLMSettingsSave);
    el.summaryKey?.addEventListener('input', () => {
        state.summaryApiKeySet = !!el.summaryKey.value;
        clearSummaryModelListCache();
        queueLLMSettingsSave({ summary_api_key: el.summaryKey.value });
    });
    el.summaryKey?.addEventListener('blur', flushLLMSettingsSave);
    el.summaryModel?.addEventListener('input', () => {
        state.summaryApiModel = el.summaryModel.value;
        searchSummaryModelsFromInput();
        queueLLMSettingsSave({ summary_api_model: el.summaryModel.value });
        renderMemorySummaryCard();
    });
    el.summaryModel?.addEventListener('blur', flushLLMSettingsSave);
    el.summaryRefreshModels?.addEventListener('click', browseSummaryModels);
    el.summaryModelPickerMenu?.addEventListener('click', e => {
        const btn = e.target.closest('.model-picker-item');
        if (btn) selectSummaryModelFromMenu(btn.dataset.model);
    });
    el.summaryCapInput?.addEventListener('input', () => {
        state.summaryCapPct = el.summaryCapInput.value || '10';
        queueLLMSettingsSave({ summary_cap_pct: state.summaryCapPct });
        renderMemorySummaryCard();
        updateContextMeterSoon();
    });
    el.summaryCapInput?.addEventListener('blur', flushLLMSettingsSave);

    // LLM API settings — autosave while typing, then flush on blur.
    el.apiEndpoint?.addEventListener('input', () => {
        clearModelListCache();
        queueLLMSettingsSave({ api_endpoint: el.apiEndpoint.value });
        renderMemorySummaryCard();
    });
    el.apiEndpoint?.addEventListener('blur', flushLLMSettingsSave);
    el.apiKey?.addEventListener('input', () => {
        queueMainApiKeySave(el.apiKey.value);
    });
    el.apiKey?.addEventListener('blur', flushLLMSettingsSave);
    el.refreshModels?.addEventListener('click', browseModels);
    el.modelPickerMenu?.addEventListener('click', e => {
        const btn = e.target.closest('.model-picker-item');
        if (btn) selectModelFromMenu(btn.dataset.model);
    });
    document.addEventListener('click', e => {
        if (el.modelPickerMenu && !el.modelPickerMenu.hidden
            && !el.modelPickerMenu.contains(e.target)
            && !el.refreshModels?.contains(e.target)
            && !el.apiModel?.contains(e.target)) {
            closeModelMenu();
        }
        if (el.summaryModelPickerMenu && !el.summaryModelPickerMenu.hidden
            && !el.summaryModelPickerMenu.contains(e.target)
            && !el.summaryRefreshModels?.contains(e.target)
            && !el.summaryModel?.contains(e.target)) {
            closeSummaryModelMenu();
        }
    });
    el.testApi?.addEventListener('click', testLLMConnection);

    // API presets
    el.apiPreset?.addEventListener('change', () => {
        activatePreset(el.apiPreset.value).then(() => {
            updateContextViews();
        });
    });
    el.presetNew?.addEventListener('click', createNewPreset);
    el.presetRename?.addEventListener('click', renamePreset);
    el.presetDelete?.addEventListener('click', deletePreset);

    // System prompt settings
    el.promptBuilderTabs?.addEventListener('click', e => {
        const btn = e.target.closest('[data-prompt-builder-tab]');
        if (btn) switchPromptBuilderMode(btn.dataset.promptBuilderTab);
    });
    el.syspromptSelect?.addEventListener('change', () => {
        selectSystemPrompt(el.syspromptSelect.value).then(() => {
            updateContextViews();
        });
    });
    // Prompt editors — debounced autosave while typing, flush on blur.
    const persistSystemPrompt = async () => {
        await updateSystemPromptContent();
    };
    const saveSystemPromptDebounced = debounce(persistSystemPrompt, 500);
    const handleSystemPromptInput = () => {
        // Context analysis reads the active prompt from state. Keep that draft
        // in sync immediately; persistence can remain debounced.
        syncActivePromptFromEditors();
        updateContextViewsSoon();
        saveSystemPromptDebounced();
    };
    el.syspromptContent?.addEventListener('input', handleSystemPromptInput);
    el.syspromptContent?.addEventListener('blur', persistSystemPrompt);
    // Description and version edits reuse the same debounced autosave, without
    // re-running the context meter (neither affects the request).
    const handlePromptMetaInput = () => {
        syncActivePromptFromEditors();
        saveSystemPromptDebounced();
    };
    el.syspromptDescription?.addEventListener('input', handlePromptMetaInput);
    el.syspromptDescription?.addEventListener('blur', persistSystemPrompt);
    el.syspromptVersion?.addEventListener('input', handlePromptMetaInput);
    el.syspromptVersion?.addEventListener('blur', async () => {
        await persistSystemPrompt();
        normalizeVersionEditor();
    });
    el.postHistoryContent?.addEventListener('input', handleSystemPromptInput);
    el.postHistoryContent?.addEventListener('blur', persistSystemPrompt);
    el.syspromptNew?.addEventListener('click', createSystemPrompt);
    el.syspromptRename?.addEventListener('click', renameSystemPrompt);
    el.syspromptDelete?.addEventListener('click', deleteSystemPrompt);
    el.promptEnableAdvanced?.addEventListener('click', () => {
        setAdvancedConfigurationVisible(true);
    });
    el.syspromptPreview?.addEventListener('click', () => {
        previewSystemPrompt();
        openSettingsSubmodal(el.promptPreviewModal);
    });
    el.promptRenderedBtn?.addEventListener('click', e => {
        // The outside-click handler below would otherwise close it right away.
        e.stopPropagation();
        toggleRenderedPrompts();
    });
    el.promptRenderedClose?.addEventListener('click', closeRenderedPrompts);
    document.addEventListener('click', e => {
        if (el.promptRenderedFlyout?.hidden !== false) return;
        if (!e.target.closest('#prompt-rendered-flyout')) closeRenderedPrompts();
    });
    // Import / export dropdown
    const closeSyspromptIoMenu = () => closeExportMenu(el.syspromptIoMenu);
    el.syspromptIoBtn?.addEventListener('click', e => {
        e.stopPropagation();
        const willOpen = el.syspromptIoMenu?.hidden;
        if (el.syspromptIoMenu) el.syspromptIoMenu.hidden = !willOpen;
        el.syspromptIoDropdown?.classList.toggle('open', willOpen);
        el.syspromptIoBtn?.setAttribute('aria-expanded', String(!!willOpen));
    });
    document.addEventListener('click', e => {
        if (!el.syspromptIoMenu || el.syspromptIoMenu.hidden) return;
        if (!e.target.closest('#sysprompt-io-dropdown')) closeSyspromptIoMenu();
    });
    el.syspromptImport?.addEventListener('click', () => { closeSyspromptIoMenu(); importSystemPrompt(); });
    el.syspromptImportFile?.addEventListener('change', handleSystemPromptImportFile);
    el.syspromptExport?.addEventListener('click', () => { closeSyspromptIoMenu(); exportSystemPrompt(); });
    el.promptPreviewExport?.addEventListener('click', exportPreviewPayload);
    // Regex output filters
    el.regexPresetSelect?.addEventListener('change', () => {
        void selectRegexPreset(el.regexPresetSelect.value);
    });
    el.regexPresetNew?.addEventListener('click', createRegexPreset);
    el.regexPresetDelete?.addEventListener('click', deleteRegexPreset);
    el.regexAddFilter?.addEventListener('click', addFilter);
    el.regexFilterList?.addEventListener('click', handleFilterListClick);
    el.regexFilterList?.addEventListener('input', handleFilterListInput);
    // Flag checkboxes fire `change`, not `input`, in some engines.
    el.regexFilterList?.addEventListener('change', handleFilterListInput);
    el.regexTestInput?.addEventListener('input', debounce(updateTestPanel, 200));
    const closeRegexIoMenu = () => closeExportMenu(el.regexIoMenu);
    el.regexIoBtn?.addEventListener('click', e => {
        e.stopPropagation();
        const willOpen = el.regexIoMenu?.hidden;
        if (el.regexIoMenu) el.regexIoMenu.hidden = !willOpen;
        el.regexIoDropdown?.classList.toggle('open', willOpen);
        el.regexIoBtn?.setAttribute('aria-expanded', String(!!willOpen));
    });
    document.addEventListener('click', e => {
        if (!el.regexIoMenu || el.regexIoMenu.hidden) return;
        if (!e.target.closest('#regex-io-dropdown')) closeRegexIoMenu();
    });
    el.regexImport?.addEventListener('click', () => { closeRegexIoMenu(); importRegexPreset(); });
    el.regexImportFile?.addEventListener('change', handleRegexImportFile);
    el.regexExport?.addEventListener('click', () => { closeRegexIoMenu(); void exportRegexPreset(); });
    // Every help panel opens and closes the same way, so the markup carries the
    // wiring: data-help names the panel to open, data-submodal-close is the
    // header X. The request preview shares the close half.
    document.addEventListener('click', e => {
        const opener = e.target.closest('[data-help]');
        if (opener) {
            openSettingsSubmodal(document.getElementById(opener.dataset.help));
            return;
        }
        if (e.target.closest('[data-submodal-close]')) {
            closeSettingsSubmodal(e.target.closest('.settings-submodal'));
        }
    });

    // Sampler configure popover
    el.samplerConfigureBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        setSamplerPopoverOpen(el.samplerPopover.hidden);
    });
    // On a phone the list fills the screen, leaving little outside it to tap,
    // so it has a Done button there (style.css hides it elsewhere).
    el.samplerPopover?.addEventListener('click', e => {
        if (!e.target.closest('.sampler-done-btn')) return;
        setSamplerPopoverOpen(false);
        el.samplerConfigureBtn.focus();
    });
    document.addEventListener('click', (e) => {
        if (el.samplerPopover && !el.samplerPopover.hidden
            && !el.samplerPopover.contains(e.target)
            && !el.samplerConfigureBtn.contains(e.target)) {
            setSamplerPopoverOpen(false);
        }
    });

    // Sampler settings — autosave while editing, then flush on blur.
    for (const [key, elName] of Object.entries(SAMPLER_FIELDS)) {
        el[elName]?.addEventListener('input', () => {
            queueLLMSettingsSave({ [key]: el[elName].value });
            if (key === 'sampler_max_tokens') {
                updateContextViewsSoon();
            }
        });
        el[elName]?.addEventListener('blur', flushLLMSettingsSave);
    }

    // Extra request params — autosave while typing. A max_tokens here overrides
    // the sampler field, so the reserved-response slice has to follow it.
    el.extraParams?.addEventListener('input', () => {
        state.extraRequestParams = el.extraParams.value;
        queueLLMSettingsSave({ extra_request_params: el.extraParams.value });
        updateContextViewsSoon();
    });
    el.extraParams?.addEventListener('blur', flushLLMSettingsSave);

    // Context budget — autosave while editing and update warning + meter.
    const handleContextTokenInput = () => {
        state.contextMaxTokens = el.settingsContextTokens.value || '0';
        queueLLMSettingsSave({ context_max_tokens: state.contextMaxTokens });
        updateContextSizeWarning();
        updateContextViewsSoon();
    };
    el.settingsContextTokens?.addEventListener('input', handleContextTokenInput);
    // Some embedded/mobile number controls only dispatch change on commit.
    el.settingsContextTokens?.addEventListener('change', handleContextTokenInput);
    el.settingsContextTokens?.addEventListener('blur', flushLLMSettingsSave);

    // Model input — search suggestions and autosave while typing.
    el.apiModel?.addEventListener('input', () => {
        state.apiModel = el.apiModel.value;
        state.modelContextLength = state.modelDetails[el.apiModel.value] ?? null;
        updateContextSizeWarning();
        updateContextMeterSoon();
        searchModelsFromInput();
        queueLLMSettingsSave({ api_model: el.apiModel.value });
        renderMemorySummaryCard();
    });
    el.apiModel?.addEventListener('blur', flushLLMSettingsSave);
}

function bindCharacterHandlers() {
    const openCharacterModal = char => {
        closeMobileSidebar({ restoreFocus: false, immediate: true });
        Modal.open(char);
    };

    // New character — sidebar header "+", empty-state CTA, and the mobile header "+"
    el.newCharBtn?.addEventListener('click', () => openCharacterModal());
    el.emptyNewCharBtn?.addEventListener('click', () => openCharacterModal());
    el.mobileNewCharBtn?.addEventListener('click', () => openCharacterModal());

    // Character list — select / pin / row menu / Archived section
    el.charList.addEventListener('click', e => {
        if (e.target.closest('.char-list-create-btn')) {
            openCharacterModal();
            return;
        }
        if (e.target.closest('.char-archived-btn')) {
            toggleArchivedSection();
            return;
        }
        const pinBtn    = e.target.closest('.char-pin-btn');
        const menuBtn   = e.target.closest('.char-menu-btn');
        const selectBtn = e.target.closest('.char-select-btn');
        const item      = e.target.closest('.char-item');
        if (!item) return;
        const id   = parseInt(item.dataset.charId, 10);
        const char = state.characters.find(c => c.id === id);
        if (pinBtn) {
            e.stopPropagation();
            if (char) {
                API.toggleCharacterPin(id)
                    .then(updated => {
                        // Replace the character in state and re-render so order updates
                        const idx = state.characters.findIndex(c => c.id === id);
                        if (idx !== -1) state.characters[idx] = updated;
                        renderCharList();
                    })
                    .catch(err => showToast('Could not pin character: ' + err.message, 'error'));
            }
        } else if (menuBtn) {
            e.stopPropagation();
            // A click with no detail came from Enter or Space, not a pointer.
            if (char) toggleCharMenu(menuBtn, char, e.detail === 0);
        } else if (selectBtn) {
            selectCharacter(id);
        }
    });

    // Row menu — acts on the row it was opened from
    el.charRowMenu.addEventListener('click', e => {
        const action = e.target.closest('[data-action]')?.dataset.action;
        if (!action) return;
        e.stopPropagation();
        const id   = parseInt(el.charRowMenu.dataset.charId, 10);
        const char = state.characters.find(c => c.id === id);
        closeCharMenu();
        if (action === 'edit') {
            if (char) openCharacterModal(char);
        } else if (action === 'archive') {
            toggleArchived(id);
        } else {
            deleteCharacter(id, char?.name);
        }
    });
    // Any click outside the menu and the ⋯ buttons closes it. Capture phase,
    // because the star and the ⋯ stop their clicks from bubbling.
    document.addEventListener('click', e => {
        if (!e.target.closest('#char-row-menu, .char-menu-btn')) closeCharMenu();
    }, true);
    // The menu is placed once, so it can't follow the rows as the list scrolls.
    el.charList.parentElement.addEventListener('scroll', closeCharMenu, { passive: true });
    // Keys while the menu is open. Escape closes it and stops there, instead of
    // reaching the document handlers that close the mobile sidebar and every
    // open flyout. The arrows walk the items, from the ⋯ as well, wrapping at
    // either end. Tab leaves for wherever it would have gone from the ⋯.
    // Focus goes back to the ⋯ before the menu closes: an unhovered row shows
    // its ⋯ only while the menu is open or focus is in the row, and focusing
    // a hidden button quietly drops focus out of the list.
    el.sidebar.addEventListener('keydown', e => {
        if (el.charRowMenu.hidden) return;
        const trigger = el.charList.querySelector('.char-menu-btn[aria-expanded="true"]');
        const items = [...el.charRowMenu.querySelectorAll('li:not([hidden]) > button')];
        const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            trigger?.focus();
            closeCharMenu();
        } else if (step) {
            e.preventDefault();
            const at = items.indexOf(document.activeElement);
            const next = at === -1 ? (step > 0 ? 0 : items.length - 1) : (at + step + items.length) % items.length;
            items[next].focus();
        } else if (e.key === 'Tab' && el.charRowMenu.contains(document.activeElement)) {
            trigger?.focus();
            closeCharMenu();
        }
    });
}

function bindChatHandlers() {
    // Chat flyout — toggle open/close
    el.chatFlyoutBtn.addEventListener('click', e => {
        e.stopPropagation();
        closeSlashCommands();
        const isOpen = !el.chatFlyout.hidden;
        Flyouts.closeAllExcept('chat');
        // Drawn again so each row's "2 hours ago" is as of now.
        if (!isOpen) renderChats();
        el.chatFlyout.hidden = isOpen;
        el.chatFlyoutBtn.setAttribute('aria-expanded', String(!isOpen));
    });

    // Close flyout on outside click
    document.addEventListener('click', e => {
        if (!el.chatFlyout.hidden &&
            !el.chatFlyout.contains(e.target) &&
            e.target !== el.chatFlyoutBtn) {
            el.chatFlyout.hidden = true;
            el.chatFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    });
    document.addEventListener('keydown', e => {
        // An edit or a rename that took Esc for itself has had it.
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        if (closeInnerLayer()) {
            e.preventDefault();
            return;
        }
        // A panel over the chat goes before a running reply does: pressed to
        // close Settings, Esc used to cut the reply short and leave it open.
        if (llm.abortController && !panelOpen()) {
            e.preventDefault();
            e.stopPropagation();
            stopGeneration();
            return;
        }
        Flyouts.closeAllExcept(null);
    });

    // Chat list — select / rename / delete (in flyout)
    el.flyoutChatList.addEventListener('click', e => {
        // Ignore clicks inside an active rename input
        if (e.target.classList.contains('chat-rename-input')) return;

        const exportBtn = e.target.closest('.chat-export-btn');
        const renameBtn = e.target.closest('.chat-rename-btn');
        const deleteBtn = e.target.closest('.chat-delete-btn');
        const selectBtn = e.target.closest('.chat-select-btn');
        const item      = e.target.closest('.chat-item');
        if (!item) return;
        const chatId = parseInt(item.dataset.chatId, 10);
        const chat   = state.chats.find(c => c.id === chatId);

        if (exportBtn) {
            e.stopPropagation();
            exportChat(chatId);
        } else if (renameBtn) {
            e.stopPropagation();
            if (chat) startChatRename(item, chat);
        } else if (deleteBtn) {
            e.stopPropagation();
            deleteChat(chatId);
        } else if (selectBtn && chat) {
            selectChat(chat);
            el.chatFlyout.hidden = true;
            el.chatFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    });

    // New chat button (in flyout)
    el.flyoutNewChatBtn.addEventListener('click', () => createNewChat(true, false));
    el.flyoutImportChatBtn?.addEventListener('click', importChat);
    el.flyoutImportChatFile?.addEventListener('change', handleChatImportFile);
}

function bindMemoryHandlers() {
    // Memory flyout — Author's Note + active lorebook. Toggle, render fresh on
    // each open, close on outside click.
    el.memoryFlyoutBtn?.addEventListener('click', e => {
        e.stopPropagation();
        closeSlashCommands();
        const isOpen = !el.memoryFlyout.hidden;
        Flyouts.closeAllExcept('memory');
        if (!isOpen) {
            renderLorebookFlyout();
            loadAuthorNote();
            renderMemorySummaryCard();
        } else {
            flushAuthorNote();
        }
        el.memoryFlyout.hidden = isOpen;
        el.memoryFlyoutBtn.setAttribute('aria-expanded', String(!isOpen));
    });
    document.addEventListener('click', e => {
        if (el.memoryFlyout && !el.memoryFlyout.hidden
            && !el.memoryFlyout.contains(e.target)
            && e.target !== el.memoryFlyoutBtn
            && !el.memoryFlyoutBtn?.contains(e.target)) {
            el.memoryFlyout.hidden = true;
            el.memoryFlyoutBtn.setAttribute('aria-expanded', 'false');
            flushAuthorNote();
        }
    });
    // Author's Note — debounced autosave while typing, flush on blur.
    el.authorNoteInput?.addEventListener('input', () => {
        scheduleAuthorNoteSave();
        updateAuthorNoteCounter();
    });
    el.authorNoteInput?.addEventListener('blur', flushAuthorNote);
    el.lorebookFlyoutSelect?.addEventListener('change', onLorebookSelectChange);
    el.lorebookManageBtn?.addEventListener('click', e => {
        e.stopPropagation();
        el.memoryFlyout.hidden = true;
        // Open settings on the lorebooks tab
        if (el.settingsFlyout?.hidden !== false) el.settingsBtn?.click();
        applySettingsSection('lorebooks', { drillIntoOnMobile: true });
    });
    // Summary config hint — deep-link to the Auto Summaries settings tab
    el.summaryConfigHint?.querySelector('#summary-open-settings')?.addEventListener('click', e => {
        e.stopPropagation();
        el.memoryFlyout.hidden = true;
        if (el.settingsFlyout?.hidden !== false) el.settingsBtn?.click();
        applySettingsSection('summaries', { drillIntoOnMobile: true });
    });

    // The picker shares the preset dropdown's keyboard and touch behavior.
    el.lorebookList?.addEventListener('change', async () => {
        const [kind, id] = el.lorebookList.value.split(':');
        if (!kind || !id) return;
        // Ask only once the picker has finished moving focus, or the dialog
        // opens with focus behind it (or, after a Tab, on Discard).
        await new Promise(resolve => setTimeout(resolve));
        if (await canLeaveLorebook()) selectLorebook(kind, Number(id));
        else renderLorebookList();  // puts the picker back on the open book
    });
    document.getElementById('settings-lorebook-export').addEventListener('click', () => exportLorebook());
    document.getElementById('settings-lorebook-delete').addEventListener('click', () => deleteLorebook());
    el.lorebookNew?.addEventListener('click', newLorebook);
    // saveLorebook handles its own errors, so withBusy's restore always runs.
    el.lorebookSave?.addEventListener('click',
        () => withBusy(el.lorebookSave, 'Saving\u2026', saveLorebook));
    el.lorebookAddEntry?.addEventListener('click', addEntry);
    el.lorebookImport?.addEventListener('click', importLorebook);
    el.lorebookImportFile?.addEventListener('change', handleImportFile);
    el.lorebookEntries?.addEventListener('click', handleEntriesClick);
    el.lorebookEntrySearch?.addEventListener('input', filterEntries);
}

/**
 * Confirm a copy on the button itself. A toast in the far corner pulls the eye
 * away from where the click happened, for something the user already expected
 * to work — the tick answers in place, and only a failure is worth a toast.
 */
function flashCopied(btn) {
    clearTimeout(btn._copiedTimer);
    btn.innerHTML = icons.SAVE;
    btn.classList.add('copied');
    btn._copiedTimer = setTimeout(() => {
        btn.innerHTML = icons.COPY;
        btn.classList.remove('copied');
    }, 1600);
}

function bindMessageHandlers() {
    bindHeldActions();
    el.chatHistory.addEventListener('click', async e => {
        // A held bar's buttons put it away, bar the swipe arrows and Copy, whose
        // tick shows on the bar; Edit's own Save and Cancel then show in the
        // message's row (see bindHeldActions).
        if (e.target.closest('.msg-actions button:not(.swipe-btn, .copy-msg-btn)')) closeHeldActions();
        const avatar = e.target.closest('.message-container .avatar[data-has-image="true"]');
        if (avatar) {
            if (avatar.classList.contains('avatar-expanded')) {
                avatar.classList.remove('avatar-expanded');
                avatar.style.width = '';
                avatar.style.height = '';
                if (avatar.dataset.thumbSrc) {
                    avatar.style.backgroundImage = `url('${avatar.dataset.thumbSrc}')`;
                }
            } else {
                // The displayed image is a small square thumbnail, so expand
                // against the large one — it carries the card's real aspect
                // ratio and enough detail for a 300px box.
                const large = avatar.dataset.largeSrc;
                if (!large) return;
                const img = new Image();
                img.onload = () => {
                    const maxDim = 300;
                    let w = img.naturalWidth, h = img.naturalHeight;
                    if (w >= h) { h = Math.round(maxDim * (h / w)); w = maxDim; }
                    else        { w = Math.round(maxDim * (w / h)); h = maxDim; }
                    avatar.style.width = w + 'px';
                    avatar.style.height = h + 'px';
                    avatar.style.backgroundImage = `url('${large}')`;
                    avatar.classList.add('avatar-expanded');
                };
                img.src = large;
            }
            return;
        }

        let msgEl = e.target.closest('.message');
        if (!msgEl) {
            const wrapper = e.target.closest('.message-wrapper');
            if (wrapper) msgEl = wrapper.querySelector('.message');
        }
        if (!msgEl) return;
        const isEditing = msgEl.classList.contains('editing');

        if (e.target.closest('.edit-msg-btn')) {
            startEditing(msgEl);
        } else if (e.target.closest('.save-msg-btn')) {
            finishEditing(true);
        } else if (e.target.closest('.cancel-msg-btn')) {
            finishEditing(false);
        } else if (e.target.closest('.delete-msg-btn')) {
            if (!isEditing) {
                const swipes = msgEl.dataset.swipes ? JSON.parse(msgEl.dataset.swipes) : [];
                // The trash icon sits in the hover bar next to swipe/edit, so a
                // stray tap used to nuke a message outright — always confirm.
                const ok = await confirmDialog({
                    title: 'Delete this message?',
                    message: swipes.length > 1
                        ? `All ${swipes.length} swipes of it will be deleted. This cannot be undone.`
                        : 'This cannot be undone.',
                });
                // A re-render (chat switch, reload) during the dialog would
                // leave msgEl orphaned and state.messages pointing elsewhere.
                if (!ok || !document.contains(msgEl)) return;
                const stateMsg = findStateMsg(swipes, msgEl);
                if (stateMsg) {
                    const stateIdx = state.messages.indexOf(stateMsg);
                    if (stateIdx !== -1) state.messages.splice(stateIdx, 1);
                    if (stateMsg.id) {
                        API.deleteMessage(stateMsg.id).catch(err => {
                            console.error('Message delete failed:', err);
                            showToast('Failed to delete message: ' + err.message);
                        });
                    }
                }
                msgEl.closest('.message-container').remove();
            }
        } else if (e.target.closest('.copy-msg-btn')) {
            const copyBtn = e.target.closest('.copy-msg-btn');
            copyText(msgEl.dataset.rawText || '')
                .then(ok => ok
                    ? flashCopied(copyBtn)
                    : showToast('Could not copy message'));
        } else if (e.target.closest('.fork-msg-btn')) {
            if (!state.activeChat || !msgEl.dataset.msgId) return;
            // Disabled for the duration: two fast clicks used to fork twice.
            await withBusy(e.target.closest('.fork-msg-btn'), null, async () => {
                try {
                    const newChat = await API.forkChat(state.activeChat.id, parseInt(msgEl.dataset.msgId));
                    state.chats.push(newChat);
                    renderChats();
                    await selectChat(newChat);
                    showToast('Chat forked', 'success', 2000);
                } catch (err) {
                    showToast('Could not fork chat: ' + err.message, 'error');
                }
            });
        } else if (e.target.closest('.swipe-prev') || e.target.closest('.swipe-next')) {
            const isPrev = !!e.target.closest('.swipe-prev');
            await handleSwipeAction(msgEl, isPrev);
        } else if (e.target.closest('.branch-pill')) {
            await withBusy(e.target.closest('.branch-pill'), null, () => switchBranch(msgEl));
        }
    });
}

// On a touch phone a message's buttons stay hidden until it is pressed and
// held (the CSS in style.css's phone block decides which rows hide). The bar
// opens just above the finger, or below it when the finger is too near the top
// of the transcript for the bar to fit above.
const HOLD_MS = 450;
const heldQuery = window.matchMedia('(max-width: 768px) and (hover: none), (max-height: 500px) and (hover: none)');
let heldContainer = null;

function closeHeldActions() {
    heldContainer?.classList.remove('actions-open');
    heldContainer = null;
}

function bindHeldActions() {
    let timer = 0;
    let start = null;
    document.addEventListener('touchstart', e => {
        clearTimeout(timer);
        if (heldContainer && !heldContainer.querySelector('.msg-actions').contains(e.target)) closeHeldActions();
        const container = e.target.closest('#chat-scroll .message-container');
        const bar = container?.querySelector('.msg-actions');
        if (!heldQuery.matches || !bar || getComputedStyle(bar).display !== 'none'
            || e.target.closest('button, a, .avatar, [contenteditable="true"], [contenteditable="plaintext-only"]')) return;
        const { clientX, clientY } = e.touches[0];
        start = { clientX, clientY };
        timer = setTimeout(() => {
            container.classList.add('actions-open');
            heldContainer = container;
            const message = container.querySelector('.message');
            const fingerY = start.clientY - message.getBoundingClientRect().top;
            const room = start.clientY - el.chatHistory.getBoundingClientRect().top;
            const above = room > bar.offsetHeight + 24;
            bar.style.top = `${above ? fingerY - bar.offsetHeight - 16 : fingerY + 24}px`;
        }, HOLD_MS);
    }, { passive: true });
    document.addEventListener('touchmove', e => {
        const t = e.touches[0];
        if (start && Math.hypot(t.clientX - start.clientX, t.clientY - start.clientY) > 10) clearTimeout(timer);
    }, { passive: true });
    document.addEventListener('touchend', () => clearTimeout(timer), { passive: true });
    document.addEventListener('touchcancel', () => clearTimeout(timer), { passive: true });
    el.chatHistory.addEventListener('scroll', closeHeldActions, { passive: true });
}

function bindComposerHandlers() {
    const saveDraftDebounced = debounce(saveDraft, 250);
    initSlashCommands();

    // Send / Stop
    el.sendBtn.addEventListener('click', () => {
        if (llm.abortController) stopGeneration();
        else handleSend();
    });
    el.userInput.addEventListener('keydown', e => {
        if (handleSlashKeydown(e)) return;
        // Discord's and SillyTavern's keys for an empty box: Up edits your last
        // message, Left and Right swipe the latest reply. Each clicks the
        // message's own button, so its guards and states still apply.
        const button = { ArrowUp: '.edit-msg-btn', ArrowLeft: '.swipe-prev', ArrowRight: '.swipe-next' }[e.key];
        if (button && !el.userInput.value && !llm.generationActive
            && !(e.shiftKey || e.altKey || e.ctrlKey || e.metaKey)) {
            const rows = el.chatHistory.querySelectorAll(
                e.key === 'ArrowUp' ? '.message-container.user' : '.message-container');
            const row = rows[rows.length - 1];
            const target = row?.querySelector(button);
            if (target && !target.disabled) {
                e.preventDefault();
                target.click();
                // A long reply can push the message Up opens out of sight. One
                // already in full view stays put; one cut off by either fade
                // lands with its top just under the top fade. Left and Right
                // change the newest reply, so they show the bottom of the chat.
                if (e.key === 'ArrowUp') {
                    const scroller = el.chatHistory;
                    const { paddingTop, paddingBottom } = getComputedStyle(scroller);
                    const band = scroller.clientHeight - parseFloat(paddingTop) - parseFloat(paddingBottom);
                    const rect = row.getBoundingClientRect();
                    const top = rect.top - scroller.getBoundingClientRect().top - parseFloat(paddingTop);
                    if (top < 0 || top + Math.min(rect.height, band) > band) scroller.scrollTop += top;
                } else {
                    scrollToBottom();
                }
                return;
            }
        }
        // On touch shells Enter is the on-screen keyboard's line-break key, not
        // a submit shortcut, so leave it alone there; desktop keeps Enter-to-send.
        if (e.key === 'Enter' && !e.shiftKey && !window.matchMedia('(pointer: coarse)').matches) {
            e.preventDefault();
            handleSend();
        }
    });
    // The draft counts toward the window while the meter shows, so typing moves
    // both views. It does not need letter-level latency, hence the shared
    // debounce. Hidden, the meter leaves the draft out of both, and typing
    // skips an analysis that grows with the length of the chat.
    el.userInput.addEventListener('input', () => {
        autoResize(el.userInput);
        saveDraftDebounced();
        updateSlashCommands();
        if (state.showContextTokenMeter) updateContextViewsSoon();
    });
    autoResize(el.userInput);
}

function bindPersonaHandlers() {
    // Persona dropup
    el.userProfile.addEventListener('click', e => {
        e.stopPropagation();
        const isOpen = el.personaDropup.classList.contains('show');
        Flyouts.closeAllExcept('persona');
        if (isOpen) closePersonaDropup();
        else {
            el.personaDropup.classList.add('show');
            el.personaDropup.setAttribute('aria-hidden', 'false');
        }
    });
    document.addEventListener('click', e => {
        if (!el.personaDropup.contains(e.target) && e.target !== el.userProfile) {
            closePersonaDropup();
        }
    });

    el.personaCreateBtn?.addEventListener('click', e => {
        e.stopPropagation();
        showPersonaForm();
    });
}

// Older pages wait for the scrolling to pause. Drawing one has to move
// scrollTop to hold the view still, and on iOS a scrollTop write mid-fling
// stops the momentum dead.
const drawOlderSoon = debounce(drawOlderNearTop, 100);

function bindScrollHandlers() {
    // Scroll-to-bottom button
    el.scrollToBottomBtn?.addEventListener('click', () => {
        scrollToBottom();
    });

    // A streaming reply calls maybeScrollToBottom() on every token, and the
    // native 'scroll' event from a user's wheel/touch gesture is async — a
    // token can land in that gap and snap the view back down before the event
    // ever fires, which reads as the scroll being stuck (same race noted on
    // jumpToContextBoundary's autoScroll reset). Reading the gesture directly
    // lets an upward scroll win synchronously, before the next token can.
    const breakAutoScroll = () => { state.autoScroll = false; };
    el.chatHistory.addEventListener('wheel', e => {
        if (e.deltaY < 0 && el.chatHistory.scrollHeight > el.chatHistory.clientHeight) breakAutoScroll();
    }, { passive: true });
    let touchStartY = null;
    el.chatHistory.addEventListener('touchstart', e => {
        touchStartY = e.touches[0]?.clientY ?? null;
    }, { passive: true });
    el.chatHistory.addEventListener('touchmove', e => {
        const y = e.touches[0]?.clientY;
        if (touchStartY != null && y != null && y > touchStartY
            && el.chatHistory.scrollHeight > el.chatHistory.clientHeight) breakAutoScroll();
    }, { passive: true });

    // Reaching the bottom starts the follow and moving up ends it. Nothing
    // else does: a reply that grows past the fold between a draw and this
    // event, or a browser that settles its own scroll late, leaves the view
    // short of the bottom without the reader having asked to stop. A move up
    // that ends flush with the bottom is the view clamping to content that
    // shrank, not the reader.
    let lastScrollTop = el.chatHistory.scrollTop;
    el.chatHistory.addEventListener('scroll', () => {
        const { scrollTop, scrollHeight, clientHeight } = el.chatHistory;
        const fromBottom = scrollHeight - scrollTop - clientHeight;
        const atBottom = fromBottom < 60;
        if (scrollTop < lastScrollTop && fromBottom > 1) state.autoScroll = false;
        else if (atBottom) state.autoScroll = true;
        lastScrollTop = scrollTop;
        el.scrollToBottomBtn?.classList.toggle('visible', !atBottom);
        drawOlderSoon();
    });
}

// ═══════════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════════
async function init() {
    initElements();
    // Enter and Esc also confirm and cancel Japanese, Chinese or Korean input
    // while a word is being composed. Those belong to the input method, so no
    // handler may send, save or close on them. Safari reports the confirming
    // Enter with isComposing already false, but keyCode 229.
    window.addEventListener('keydown', e => {
        if ((e.key === 'Enter' || e.key === 'Escape') && (e.isComposing || e.keyCode === 229)) e.stopPropagation();
    }, true);
    // A link in a reply would take Cozy's own tab, and the chat with it, so
    // every rendered link opens in a new one.
    DOMPurify.addHook('afterSanitizeAttributes', node => {
        if (node.tagName === 'A' && node.hasAttribute('href')) {
            node.setAttribute('target', '_blank');
            node.setAttribute('rel', 'noopener noreferrer');
        }
    });
    setSummaryBudgetChangeHandler(updateContextViews);
    initTooltips();
    initContextMeter();
    // Lift the pinned settings header off the cards once the pane scrolls.
    el.settingsPane?.addEventListener('scroll', () => {
        el.settingsPane.classList.toggle('has-scrolled', el.settingsPane.scrollTop > 0);
    }, { passive: true });
    initPromptVarsPanel();
    loadPrefs();
    // Only the responses come early: loadCharacters() still waits for the
    // group below, as the comment there requires.
    prefetchSelection(state._savedActiveId, state._savedChatId);
    applyTheme(state.theme);
    bindResponsiveShellHandlers();

    // The loading screen stays up until all of this settles, so every serial
    // await here is a full round trip added to perceived boot time — cheap on
    // localhost, not cheap behind a remote server. These four are independent,
    // so they go out together; system prompts and regex presets still follow
    // settings so they can reuse that response instead of refetching it.
    await Promise.all([
        loadThemeList(),
        loadPersonas(),
        loadLLMSettings().then(settings => Promise.all([
            loadSystemPrompts(settings),
            // Filters have to be live from the first reply, not just once the
            // settings flyout has been opened.
            loadRegexPresets(settings),
        ]).then(() => applyAdvancedConfigurationVisibility())),
        loadLorebooks(),
    ]);
    // Deliberately not in the group above: loadCharacters() cascades into
    // selectChat(), which ends by running the context analysis in
    // context-analysis.js — that reads state.systemPrompts and state.lorebooks,
    // and feeds updateContextBoundary() and the summary trigger. Neither is
    // recomputed after this point, so both must see fully loaded state.
    await loadCharacters();
    dropPrefetched();
    updateComposerState();
    renderLorebookList();
    renderLorebookFlyout();
    updateContextMeter();

    bindFlyoutHandlers();
    bindSheetBackdropHandlers();
    bindSidebarHandlers();
    bindSwipeToClose();
    bindBackButton();
    bindSettingsHandlers();
    initStorageStats();
    bindBackupHandlers();
    bindCharacterHandlers();
    bindChatHandlers();
    bindMemoryHandlers();
    initSummaryHandlers();
    bindMessageHandlers();
    bindComposerHandlers();
    bindPersonaHandlers();
    bindScrollHandlers();
    enhanceSettingsSelects();
}

init().then(() => {
    // Wait for all avatar images currently in the DOM to finish loading
    const imgs = document.querySelectorAll('[data-has-image="true"]');
    const imgPromises = Array.from(imgs).map(imgEl => {
        const bg = imgEl.style.backgroundImage;
        const urlMatch = bg && bg.match(/url\(['"]?([^'"]+)['"]?\)/);
        if (!urlMatch) return Promise.resolve();
        return new Promise(resolve => {
            const img = new Image();
            img.onload = resolve;
            img.onerror = resolve; // don't block on broken images
            img.src = urlMatch[1];
        });
    });
    // Also wait for web fonts
    const fontReady = document.fonts ? document.fonts.ready : Promise.resolve();
    return Promise.all([fontReady, ...imgPromises]);
}).then(() => {
    const loader = document.getElementById('loading-screen');
    if (loader) {
        loader.classList.add('fade-out');
        // transitionend on its own is not safe for a full-screen overlay: under
        // prefers-reduced-motion the fade collapses to a near-zero duration, and
        // a browser that rounds that away fires no event and leaves the loading
        // screen covering the app forever. Drop it on a timer as well.
        const drop = () => loader.remove();
        loader.addEventListener('transitionend', drop, { once: true });
        setTimeout(drop, 600);
    }
});
