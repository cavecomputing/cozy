// ═══════════════════════════════════════════════════════════════════════════
// ENTRY POINT — orchestrates all modules
// ═══════════════════════════════════════════════════════════════════════════
import { state, el, llm, initElements } from './state.js';
import { API } from './api.js';
import {
    autoResize, scrollToBottom, showToast, Flyouts, savePrefs, closeMobileSidebar,
    debounce, updateComposerState, copyText, stopGeneration,
} from './utils.js';
import { applyTheme, loadThemeList, renderThemePicker } from './themes.js';
import { loadCharacters, selectCharacter, deleteCharacter, renderCharList } from './characters.js';
import { selectChat, createNewChat, deleteChat, startChatRename, importChat, handleChatImportFile, renderChats } from './chats.js';
import { startEditing, finishEditing, handleSwipeAction, findStateMsg } from './messages.js';
import { Modal } from './modal.js';
import { loadPersonas, showPersonaForm } from './personas.js';
import { handleSend } from './send.js';
import {
    loadLLMSettings, saveLLMSettings, queueLLMSettingsSave, queueMainApiKeySave,
    flushLLMSettingsSave,
    browseModels, browseSummaryModels, closeModelMenu, closeSummaryModelMenu,
    selectModelFromMenu, selectSummaryModelFromMenu, testLLMConnection,
    activatePreset, createNewPreset, deletePreset, searchModelsFromInput,
    searchSummaryModelsFromInput, clearModelListCache, clearSummaryModelListCache,
} from './llm-settings.js';
import {
    loadSystemPrompts, selectSystemPrompt, createSystemPrompt, deleteSystemPrompt,
    updateSystemPromptContent, syncActivePromptFromEditors, populateDefaultTemplateHelp,
    previewSystemPrompt, importSystemPrompt, handleSystemPromptImportFile,
    exportSystemPrompt, switchPromptBuilderMode,
} from './system-prompts.js';
import { loadLorebooks, renderLorebookList, selectLorebook, newLorebook, saveLorebook, deleteLorebook, addEntry, handleEntriesClick, renderLorebookFlyout, onLorebookSelectChange, renderLorebookNotice, dismissLorebookNotice, importLorebook, handleImportFile, exportLorebook, loadAuthorNote, scheduleAuthorNoteSave, flushAuthorNote, updateAuthorNoteCounter } from './lorebooks.js';
import { SAMPLER_FIELDS, updateContextSizeWarning } from './sampler.js';
import { exportChat } from './export.js';
import { initTooltips } from './tooltips.js';
import { saveDraft } from './drafts.js';
import { initSlashCommands, updateSlashCommands, handleSlashKeydown, closeSlashCommands } from './slash-commands.js';
import { updateContextMeter, updateContextBoundary } from './context-meter.js';
import { initCharacterGallery } from './character-gallery.js';
import { enhanceSettingsSelects } from './custom-select.js';
import {
    initSummaryHandlers, renderMemorySummaryCard, setSummaryBudgetChangeHandler,
} from './summaries.js';

// Configure markdown renderer — GFM + line-break-to-<br> like most chat apps
marked.use({ breaks: true, gfm: true });

// RP dialogue extension — wrap "quoted speech" in a styled span
const RP_DIALOGUE_QUOTES = ['"', '\u201c'];
const RP_DIALOGUE_PATTERN = /^(?:"([^"\n]+)"|\u201c([^\u201d\n]+)\u201d)/;

marked.use({
    extensions: [{
        name: 'rpDialogue',
        level: 'inline',
        start(src) {
            const starts = RP_DIALOGUE_QUOTES
                .map(ch => src.indexOf(ch))
                .filter(idx => idx !== -1);
            return starts.length ? Math.min(...starts) : undefined;
        },
        tokenizer(src) {
            const match = RP_DIALOGUE_PATTERN.exec(src);
            if (match) {
                const token = { type: 'rpDialogue', raw: match[0], text: match[1] || match[2], tokens: [] };
                this.lexer.inline(token.text, token.tokens);
                return token;
            }
        },
        renderer(token) {
            return `<span class="rp-dialogue">"${this.parser.parseInline(token.tokens)}"</span>`;
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
const MOBILE_SHELL_QUERY = '(max-width: 768px)';
const isMobileSettings = () => window.matchMedia(MOBILE_SHELL_QUERY).matches;

function applySettingsSection(key, { drillIntoOnMobile = false } = {}) {
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
    savePrefs();
}

function exitSettingsDetail() {
    el.settingsShell?.classList.remove('in-detail');
    if (el.settingsBackBtn) el.settingsBackBtn.hidden = true;
}

function setSamplerPopoverOpen(open) {
    if (!el.samplerPopover || !el.samplerConfigureBtn) return;
    el.samplerPopover.hidden = !open;
    el.samplerConfigureBtn.setAttribute('aria-expanded', String(open));
}

function blurSettingsFlyoutFocus() {
    if (el.settingsFlyout.contains(document.activeElement)) {
        document.activeElement.blur();
    }
}

function closeSettingsFlyout() {
    blurSettingsFlyoutFocus();
    el.settingsFlyout.hidden = true;
    setSamplerPopoverOpen(false);
    exitSettingsDetail();
}

let settingsSubmodalReturnFocus = null;
function rememberSettingsSubmodalTrigger() {
    settingsSubmodalReturnFocus = document.activeElement;
}
function closeSettingsSubmodal(modal) {
    if (!modal) return;
    modal.hidden = true;
    if (settingsSubmodalReturnFocus && document.contains(settingsSubmodalReturnFocus)) {
        settingsSubmodalReturnFocus.focus();
    }
    settingsSubmodalReturnFocus = null;
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
    }
    handleMobileModals(mobileQuery);
    mobileQuery.addEventListener('change', handleMobileModals);

    // Keep flyout bottom edge aligned with chat scroll area.
    // Containing block is sidebar's padding box (inside border), so subtract
    // both the bottom margin and bottom border of the sidebar.
    const sidebarStyle = getComputedStyle(el.sidebar);
    const sidebarBottomOffset = parseFloat(sidebarStyle.marginBottom)
                              + parseFloat(sidebarStyle.borderBottomWidth);
    function updateModalBottom() {
        const offset = el.inputContainer.offsetHeight - sidebarBottomOffset;
        document.documentElement.style.setProperty('--modal-bottom-offset', `${offset}px`);
    }
    new ResizeObserver(updateModalBottom).observe(el.inputContainer);
    updateModalBottom();
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
    Flyouts.register('persona', () => {
        el.personaDropup.classList.remove('show');
        el.personaDropup.setAttribute('aria-hidden', 'true');
    });
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

function bindSettingsHandlers() {
    const openSettings = async e => {
        e.stopPropagation();
        const isOpen = !el.settingsFlyout.hidden;
        if (isOpen) blurSettingsFlyoutFocus();
        Flyouts.closeAllExcept('settings');
        if (!isOpen && isMobileSettings()) {
            closeMobileSidebar({ restoreFocus: false, immediate: true });
        }
        el.settingsFlyout.hidden = isOpen;
        if (isOpen) setSamplerPopoverOpen(false);
        if (!isOpen) {
            // On desktop: restore the saved section. On mobile: show the list view first
            // (saved section stays "active" in the nav so reopening from the list is one tap away).
            applySettingsSection(state.settingsSection);
            exitSettingsDetail();
            renderThemePicker();
            const s = await loadLLMSettings();
            await loadSystemPrompts(s);
        }
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

    // Close settings on outside click (matches chat / lorebook flyout behavior).
    // Skip the toggle button (it handles open/close itself) and the
    // stacked sub-modals that float above the settings flyout.
    document.addEventListener('click', e => {
        if (el.settingsFlyout.hidden) return;
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
        if (el.settingsFlyout.contains(e.target) || path.includes(el.settingsFlyout)) return;
        if (e.target.closest('#settings-btn')) return;
        if (e.target.closest('#prompt-help-modal, #prompt-preview-modal, #sampler-help-modal')) return;
        closeSettingsFlyout();
    });

    el.settingsThemeSelect?.addEventListener('change', () => {
        applyTheme(el.settingsThemeSelect.value);
        savePrefs();
    });
    el.settingsContextMeterToggle?.addEventListener('change', () => {
        state.showContextTokenMeter = el.settingsContextMeterToggle.checked;
        saveLLMSettings({ show_context_token_meter: state.showContextTokenMeter ? '1' : '0' });
        updateContextMeter();
    });
    el.settingsGalleryBtnToggle?.addEventListener('change', () => {
        state.showGalleryButton = el.settingsGalleryBtnToggle.checked;
        if (el.galleryOpenBtn) el.galleryOpenBtn.hidden = !state.showGalleryButton;
        saveLLMSettings({ show_gallery_button: state.showGalleryButton ? '1' : '0' });
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
        updateContextMeter();
    });
    el.summaryCapInput?.addEventListener('blur', flushLLMSettingsSave);
    el.summaryIntervalInput?.addEventListener('input', () => {
        state.summaryTriggerInterval = el.summaryIntervalInput.value || '10';
        queueLLMSettingsSave({ summary_trigger_interval: state.summaryTriggerInterval });
    });
    el.summaryIntervalInput?.addEventListener('blur', flushLLMSettingsSave);
    el.summaryCompressInput?.addEventListener('input', () => {
        state.summaryCompressBatch = el.summaryCompressInput.value || '3';
        queueLLMSettingsSave({ summary_compress_batch: state.summaryCompressBatch });
    });
    el.summaryCompressInput?.addEventListener('blur', flushLLMSettingsSave);

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
            updateContextMeter();
            updateContextBoundary();
        });
    });
    el.presetNew?.addEventListener('click', createNewPreset);
    el.presetDelete?.addEventListener('click', deletePreset);

    // System prompt settings
    el.promptBuilderTabs?.addEventListener('click', e => {
        const btn = e.target.closest('[data-prompt-builder-tab]');
        if (btn) switchPromptBuilderMode(btn.dataset.promptBuilderTab);
    });
    el.syspromptSelect?.addEventListener('change', () => {
        selectSystemPrompt(el.syspromptSelect.value).then(() => {
            updateContextMeter();
            updateContextBoundary();
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
        updateContextMeter();
        updateContextBoundary();
        saveSystemPromptDebounced();
    };
    el.syspromptContent?.addEventListener('input', handleSystemPromptInput);
    el.syspromptContent?.addEventListener('blur', persistSystemPrompt);
    el.postHistoryContent?.addEventListener('input', handleSystemPromptInput);
    el.postHistoryContent?.addEventListener('blur', persistSystemPrompt);
    el.syspromptNew?.addEventListener('click', createSystemPrompt);
    el.syspromptDelete?.addEventListener('click', deleteSystemPrompt);
    el.syspromptPreview?.addEventListener('click', () => {
        rememberSettingsSubmodalTrigger();
        previewSystemPrompt();
    });
    el.syspromptHelp?.addEventListener('click', () => {
        rememberSettingsSubmodalTrigger();
        populateDefaultTemplateHelp();
        if (el.promptHelpModal) el.promptHelpModal.hidden = false;
    });
    el.promptHelpModal?.addEventListener('click', e => {
        const copyBtn = e.target.closest('.prompt-help-copy');
        if (!copyBtn) return;
        const which = copyBtn.dataset.copyDefault === 'post-history'
            ? 'prompt-help-default-post-history' : 'prompt-help-default-system';
        const text = document.getElementById(which)?.textContent || '';
        copyText(text)
            .then(ok => ok
                ? showToast('Copied default template', 'success', 2000)
                : showToast('Could not copy template'));
    });
    // Import / export dropdown
    const closeSyspromptIoMenu = () => {
        if (el.syspromptIoMenu) el.syspromptIoMenu.hidden = true;
        el.syspromptIoDropdown?.classList.remove('open');
        el.syspromptIoBtn?.setAttribute('aria-expanded', 'false');
    };
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
    el.promptPreviewClose?.addEventListener('click', () => {
        closeSettingsSubmodal(el.promptPreviewModal);
    });
    el.promptPreviewModal?.addEventListener('click', e => {
        if (e.target === el.promptPreviewModal) closeSettingsSubmodal(el.promptPreviewModal);
    });
    el.promptHelpClose?.addEventListener('click', () => {
        closeSettingsSubmodal(el.promptHelpModal);
    });
    el.promptHelpModal?.addEventListener('click', e => {
        if (e.target === el.promptHelpModal) closeSettingsSubmodal(el.promptHelpModal);
    });
    el.samplerHelpBtn?.addEventListener('click', () => {
        rememberSettingsSubmodalTrigger();
        if (el.samplerHelpModal) el.samplerHelpModal.hidden = false;
    });
    el.samplerHelpClose?.addEventListener('click', () => {
        closeSettingsSubmodal(el.samplerHelpModal);
    });
    el.samplerHelpModal?.addEventListener('click', e => {
        if (e.target === el.samplerHelpModal) closeSettingsSubmodal(el.samplerHelpModal);
    });

    // Sampler configure popover
    el.samplerConfigureBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        setSamplerPopoverOpen(el.samplerPopover.hidden);
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
                updateContextMeter();
                updateContextBoundary();
            }
        });
        el[elName]?.addEventListener('blur', flushLLMSettingsSave);
    }

    // Extra request params — autosave while typing.
    el.extraParams?.addEventListener('input', () => {
        state.extraRequestParams = el.extraParams.value;
        queueLLMSettingsSave({ extra_request_params: el.extraParams.value });
    });
    el.extraParams?.addEventListener('blur', flushLLMSettingsSave);

    // Context budget — autosave while editing and update warning + meter.
    const handleContextTokenInput = () => {
        state.contextMaxTokens = el.settingsContextTokens.value || '0';
        queueLLMSettingsSave({ context_max_tokens: state.contextMaxTokens });
        updateContextSizeWarning();
        updateContextMeter();
        updateContextBoundary();
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
        updateContextMeter();
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

    // Edits open the slide-out modal on all screen sizes; the gallery
    // inspector is reachable only via its own open button.
    const editCharacter = char => openCharacterModal(char);

    // New character — sidebar header "+", empty-state CTA, and the mobile header "+"
    el.newCharBtn?.addEventListener('click', () => openCharacterModal());
    el.emptyNewCharBtn?.addEventListener('click', () => openCharacterModal());
    el.mobileNewCharBtn?.addEventListener('click', () => openCharacterModal());

    // Character list — select / edit / delete / pin
    el.charList.addEventListener('click', e => {
        if (e.target.closest('.char-list-create-btn')) {
            openCharacterModal();
            return;
        }
        const pinBtn    = e.target.closest('.char-pin-btn');
        const editBtn   = e.target.closest('.char-edit-btn');
        const deleteBtn = e.target.closest('.char-delete-btn');
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
                        // Mirror server sort: pinned first (most recent pin at top),
                        // then unpinned by created_at ASC
                        state.characters.sort((a, b) => {
                            if (a.pinned && !b.pinned) return -1;
                            if (!a.pinned && b.pinned) return 1;
                            if (a.pinned && b.pinned) {
                                return (b.pinned_at || '').localeCompare(a.pinned_at || '');
                            }
                            return (a.created_at || '').localeCompare(b.created_at || '');
                        });
                        renderCharList();
                    })
                    .catch(err => showToast('Could not pin character: ' + err.message, 'error'));
            }
        } else if (editBtn) {
            e.stopPropagation();
            if (char) editCharacter(char);
        } else if (deleteBtn) {
            e.stopPropagation();
            deleteCharacter(id, char?.name);
        } else if (selectBtn) {
            selectCharacter(id);
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
        if (e.key !== 'Escape') return;
        if (llm.abortController) {
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

    // Inline notice — dismiss
    el.lorebookNoticeDismiss?.addEventListener('click', dismissLorebookNotice);

    // "No API configured" notice — deep-link to the API settings section
    el.apiNoticeSettings?.addEventListener('click', e => {
        e.stopPropagation();
        if (el.apiNotice) el.apiNotice.hidden = true;
        if (el.settingsFlyout?.hidden !== false) el.settingsBtn?.click();
        applySettingsSection('api', { drillIntoOnMobile: true });
    });
    el.apiNoticeDismiss?.addEventListener('click', () => {
        if (el.apiNotice) el.apiNotice.hidden = true;
    });

    // Lorebook list (settings panel) — select / export / delete per row
    el.lorebookList?.addEventListener('click', e => {
        const item = e.target.closest('.lorebook-list-item');
        if (!item) return;
        e.stopPropagation();
        const kind = item.dataset.kind;
        const id = parseInt(item.dataset.id, 10);
        if (e.target.closest('.lorebook-list-export-btn')) {
            exportLorebook(kind, id);
        } else if (e.target.closest('.lorebook-list-delete-btn')) {
            deleteLorebook(kind, id);
        } else {
            selectLorebook(kind, id);
        }
    });
    el.lorebookNew?.addEventListener('click', newLorebook);
    el.lorebookSave?.addEventListener('click', saveLorebook);
    el.lorebookAddEntry?.addEventListener('click', addEntry);
    el.lorebookImport?.addEventListener('click', importLorebook);
    el.lorebookImportFile?.addEventListener('change', handleImportFile);
    el.lorebookEntries?.addEventListener('click', handleEntriesClick);

    // Scan-depth override — debounced save (mirrors sampler/context fields)
    el.lorebookScanOverride?.addEventListener('change', () => {
        const v = parseInt(el.lorebookScanOverride.value, 10) || 0;
        state.lorebookScanDepthOverride = v;
        saveLLMSettings({ lorebook_scan_depth_override: String(v) });
    });

    // Always-inject-all toggle
    el.lorebookAlwaysInjectAll?.addEventListener('change', () => {
        const on = !!el.lorebookAlwaysInjectAll.checked;
        state.lorebookAlwaysInjectAll = on;
        saveLLMSettings({ lorebook_always_inject_all: on ? '1' : '0' });
    });
}

function bindMessageHandlers() {
    el.chatHistory.addEventListener('click', async e => {
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
            copyText(msgEl.dataset.rawText || '')
                .then(ok => ok
                    ? showToast('Copied message', 'success', 2000)
                    : showToast('Could not copy message'));
        } else if (e.target.closest('.fork-msg-btn')) {
            if (!state.activeChat || !msgEl.dataset.msgId) return;
            try {
                const newChat = await API.forkChat(state.activeChat.id, parseInt(msgEl.dataset.msgId));
                state.chats.push(newChat);
                renderChats();
                await selectChat(newChat);
                showToast('Chat forked', 'success', 2000);
            } catch (err) {
                showToast('Could not fork chat: ' + err.message, 'error');
            }
        } else if (e.target.closest('.swipe-prev') || e.target.closest('.swipe-next')) {
            const isPrev = !!e.target.closest('.swipe-prev');
            await handleSwipeAction(msgEl, isPrev);
        }
    });
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
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    // The meter runs the full context analysis (template + lorebook
    // resolution); per-keystroke that adds up on long chats and slower
    // phones, and the draft segment does not need letter-level latency.
    const updateContextMeterAfterTyping = debounce(updateContextMeter, 150);
    el.userInput.addEventListener('input', () => {
        autoResize(el.userInput);
        saveDraftDebounced();
        updateSlashCommands();
        updateContextMeterAfterTyping();
    });
    autoResize(el.userInput);
}

function bindPersonaHandlers() {
    // Persona dropup
    el.userProfile.addEventListener('click', e => {
        e.stopPropagation();
        const isOpen = el.personaDropup.classList.contains('show');
        Flyouts.closeAllExcept('persona');
        el.personaDropup.classList.toggle('show', !isOpen);
        el.personaDropup.setAttribute('aria-hidden', String(isOpen));
    });
    document.addEventListener('click', e => {
        if (!el.personaDropup.contains(e.target) && e.target !== el.userProfile) {
            el.personaDropup.classList.remove('show');
            el.personaDropup.setAttribute('aria-hidden', 'true');
        }
    });

    // (settings button listener registered above)

    // Persona create button
    document.getElementById('persona-create-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        showPersonaForm();
    });
}

function bindScrollHandlers() {
    // Scroll-to-bottom button
    el.scrollToBottomBtn?.addEventListener('click', () => {
        scrollToBottom();
    });

    // Auto-scroll detection on chat scroll area
    el.chatHistory.addEventListener('scroll', () => {
        const atBottom =
            el.chatHistory.scrollHeight - el.chatHistory.scrollTop - el.chatHistory.clientHeight < 60;
        state.autoScroll = atBottom;
        el.scrollToBottomBtn?.classList.toggle('visible', !atBottom);
    });
}

// ═══════════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════════
async function init() {
    initElements();
    setSummaryBudgetChangeHandler(() => {
        updateContextMeter();
        updateContextBoundary();
    });
    initTooltips();
    loadPrefs();
    applyTheme(state.theme);
    bindResponsiveShellHandlers();

    // The loading screen stays up until all of this settles, so every serial
    // await here is a full round trip added to perceived boot time — cheap on
    // localhost, not cheap behind a remote server. These four are independent,
    // so they go out together; system prompts still follow settings so they can
    // reuse that response instead of refetching it.
    await Promise.all([
        loadThemeList(),
        loadPersonas(),
        loadLLMSettings().then(settings => loadSystemPrompts(settings)),
        loadLorebooks(),
    ]);
    // Deliberately not in the group above: loadCharacters() cascades into
    // selectChat(), which ends by running the context analysis in
    // context-analysis.js — that reads state.systemPrompts and state.lorebooks,
    // and feeds updateContextBoundary() and the summary trigger. Neither is
    // recomputed after this point, so both must see fully loaded state.
    await loadCharacters();
    updateComposerState();
    renderLorebookList();
    renderLorebookFlyout();
    renderLorebookNotice();
    updateContextMeter();

    bindFlyoutHandlers();
    bindSheetBackdropHandlers();
    bindSidebarHandlers();
    bindSettingsHandlers();
    bindCharacterHandlers();
    bindChatHandlers();
    bindMemoryHandlers();
    initSummaryHandlers();
    bindMessageHandlers();
    bindComposerHandlers();
    bindPersonaHandlers();
    bindScrollHandlers();
    initCharacterGallery();
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
        loader.addEventListener('transitionend', () => loader.remove());
    }
});
