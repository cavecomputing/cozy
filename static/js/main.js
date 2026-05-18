// ═══════════════════════════════════════════════════════════════════════════
// ENTRY POINT — orchestrates all modules
// ═══════════════════════════════════════════════════════════════════════════
import { state, el, llm, initElements } from './state.js';
import { API } from './api.js';
import {
    autoResize, scrollToBottom, showToast, Flyouts, savePrefs, closeMobileSidebar,
    debounce, updateComposerState,
} from './utils.js';
import { applyTheme, loadThemeList, renderThemePicker } from './themes.js';
import { loadCharacters, selectCharacter, deleteCharacter, renderCharList } from './characters.js';
import { selectChat, createNewChat, deleteChat, startChatRename, importChat, handleChatImportFile } from './chats.js';
import { startEditing, finishEditing, handleSwipeAction, findStateMsg } from './messages.js';
import { Modal } from './modal.js';
import { loadPersonas, showPersonaForm } from './personas.js';
import { handleSend } from './send.js';
import { loadLLMSettings, saveLLMSettings, browseModels, closeModelMenu, selectModelFromMenu, testLLMConnection, activatePreset, createNewPreset, saveActivePreset, deletePreset, searchModelsFromInput, clearModelListCache } from './llm-settings.js';
import { loadSystemPrompts, selectSystemPrompt, createSystemPrompt, deleteSystemPrompt, updateSystemPromptContent, saveActiveSystemPrompt, resetSystemPromptToDefault, previewSystemPrompt, importSystemPrompt, handleSystemPromptImportFile, exportSystemPrompt } from './system-prompts.js';
import { loadLorebooks, renderLorebookList, selectLorebook, newLorebook, saveLorebook, deleteLorebook, addEntry, handleEntriesClick, renderLorebookFlyout, renderLorebookNotice, dismissLorebookNotice, importLorebook, handleImportFile, exportLorebook } from './lorebooks.js';
import { SAMPLER_FIELDS, updateContextSizeWarning } from './sampler.js';
import { exportChat } from './export.js';
import { initTooltips } from './tooltips.js';
import { saveDraft } from './drafts.js';
import { initSlashCommands, updateSlashCommands, handleSlashKeydown, closeSlashCommands } from './slash-commands.js';
import { updateContextMeter } from './context-meter.js';

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
        state.sidebarCollapsed = p.sidebarCollapsed || false;
        state.theme            = p.theme             || 'cozy';
        state._savedActiveId   = p.activeCharId     || null;
        state._savedChatId     = p.activeChatId     || null;
        state._savedPersonaId  = p.activePersonaId  || null;
        state.settingsSection  = p.settingsSection  || 'appearance';
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
function toggleSidebar() {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    el.sidebar.classList.toggle('collapsed', state.sidebarCollapsed);
    savePrefs();
}

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
        if (mq.matches) {
            document.querySelectorAll('#sidebar .modal-overlay').forEach(m => {
                document.body.appendChild(m);
            });
        } else {
            // Move them back into the sidebar for desktop flyout positioning
            const sidebar = document.getElementById('sidebar');
            document.querySelectorAll('body > .modal-overlay').forEach(m => {
                sidebar.appendChild(m);
            });
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

function bindFlyoutHandlers() {
    // Register flyouts so only one is open at a time
    Flyouts.register('settings', () => {
        closeSettingsFlyout();
    });
    Flyouts.register('chat', () => {
        el.chatFlyout.hidden = true;
        el.chatFlyoutBtn?.setAttribute('aria-expanded', 'false');
    });
    Flyouts.register('lorebook', () => {
        if (el.lorebookFlyout) el.lorebookFlyout.hidden = true;
        el.lorebookFlyoutBtn?.setAttribute('aria-expanded', 'false');
    });
    Flyouts.register('persona', () => {
        el.personaDropup.classList.remove('show');
        el.personaDropup.setAttribute('aria-hidden', 'true');
    });
}

function bindSidebarHandlers() {
    // Sidebar toggle
    el.sidebarToggle.addEventListener('click', toggleSidebar);

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
    // Settings flyout (collapsed gear icon delegates to same handler)
    const collapsedSettingsBtn = document.getElementById('collapsed-settings-btn');
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
    collapsedSettingsBtn?.addEventListener('click', openSettings);
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
    // Skip the toggle buttons (they handle open/close themselves) and the
    // stacked sub-modals that float above the settings flyout.
    document.addEventListener('click', e => {
        if (el.settingsFlyout.hidden) return;
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
        if (el.settingsFlyout.contains(e.target) || path.includes(el.settingsFlyout)) return;
        if (e.target.closest('#settings-btn, #collapsed-settings-btn')) return;
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

    // LLM API settings — save on blur
    el.apiEndpoint?.addEventListener('change', () => {
        state.apiEndpoint = el.apiEndpoint.value;
        clearModelListCache();
        saveLLMSettings({api_endpoint: el.apiEndpoint.value});
    });
    el.apiKey?.addEventListener('change', () => {
        const v = el.apiKey.value;
        // Skip masked API keys: bullet-prefixed ("\u2022\u2022…") or containing ellipsis mask ("\u2026")
        if (v && !v.startsWith('\u2022\u2022') && !v.includes('\u2026')) {
            state.apiKeySet = true;
            clearModelListCache();
            saveLLMSettings({api_key: v});
        }
    });
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
    });
    el.testApi?.addEventListener('click', testLLMConnection);

    // API presets
    el.apiPreset?.addEventListener('change', () => activatePreset(el.apiPreset.value));
    el.presetNew?.addEventListener('click', createNewPreset);
    el.presetSave?.addEventListener('click', saveActivePreset);
    el.presetDelete?.addEventListener('click', deletePreset);

    // System prompt settings
    el.syspromptSelect?.addEventListener('change', () => {
        selectSystemPrompt(el.syspromptSelect.value);
    });
    el.syspromptContent?.addEventListener('change', updateSystemPromptContent);
    el.syspromptNew?.addEventListener('click', createSystemPrompt);
    el.syspromptSave?.addEventListener('click', saveActiveSystemPrompt);
    el.syspromptDelete?.addEventListener('click', deleteSystemPrompt);
    el.syspromptPreview?.addEventListener('click', () => {
        rememberSettingsSubmodalTrigger();
        previewSystemPrompt();
    });
    el.syspromptReset?.addEventListener('click', resetSystemPromptToDefault);
    el.syspromptHelp?.addEventListener('click', () => {
        rememberSettingsSubmodalTrigger();
        if (el.promptHelpModal) el.promptHelpModal.hidden = false;
    });
    el.syspromptImport?.addEventListener('click', importSystemPrompt);
    el.syspromptImportFile?.addEventListener('change', handleSystemPromptImportFile);
    el.syspromptExport?.addEventListener('click', exportSystemPrompt);
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

    // Batched settings save — merges rapid changes into a single PUT
    let pendingSettings = {};
    const flushSettings = debounce(() => {
        if (Object.keys(pendingSettings).length === 0) return;
        saveLLMSettings(pendingSettings);
        pendingSettings = {};
    }, 300);
    function queueSettingsSave(fields) {
        Object.assign(pendingSettings, fields);
        flushSettings();
    }

    // Sampler settings — save on change (debounced)
    for (const [key, elName] of Object.entries(SAMPLER_FIELDS)) {
        el[elName]?.addEventListener('change', () => {
            queueSettingsSave({ [key]: el[elName].value });
        });
    }

    // Context budget — save on change and update warning + meter
    el.settingsContextTokens?.addEventListener('change', () => {
        state.contextMaxTokens = el.settingsContextTokens.value || '0';
        queueSettingsSave({ context_max_tokens: state.contextMaxTokens });
        updateContextSizeWarning();
        updateContextMeter();
    });

    // Model input — type to search suggestions, save on committed change.
    el.apiModel?.addEventListener('input', () => {
        state.apiModel = el.apiModel.value;
        state.modelContextLength = state.modelDetails[el.apiModel.value] ?? null;
        updateContextSizeWarning();
        updateContextMeter();
        searchModelsFromInput();
    });
    el.apiModel?.addEventListener('change', () => {
        state.apiModel = el.apiModel.value;
        state.modelContextLength = state.modelDetails[el.apiModel.value] ?? null;
        saveLLMSettings({ api_model: el.apiModel.value });
        updateContextSizeWarning();
        updateContextMeter();
    });

    // Thinking settings — save on change
    el.sendThinking?.addEventListener('change', () => {
        saveLLMSettings({ send_thinking: el.sendThinking.checked ? '1' : '0' });
        updateContextMeter();
    });
}

function bindCharacterHandlers() {
    const openCharacterModal = char => {
        closeMobileSidebar({ restoreFocus: false, immediate: true });
        Modal.open(char);
    };

    // New character
    el.newCharBtn.addEventListener('click', () => openCharacterModal());
    el.collapsedNewCharBtn?.addEventListener('click', () => openCharacterModal());
    el.emptyNewCharBtn?.addEventListener('click', () => openCharacterModal());

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
            if (char) openCharacterModal(char);
        } else if (deleteBtn) {
            e.stopPropagation();
            deleteCharacter(id);
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
            llm.abortController.abort();
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

function bindLorebookHandlers() {
    // Lorebook flyout — toggle, render fresh on each open, close on outside click
    el.lorebookFlyoutBtn?.addEventListener('click', e => {
        e.stopPropagation();
        closeSlashCommands();
        const isOpen = !el.lorebookFlyout.hidden;
        Flyouts.closeAllExcept('lorebook');
        if (!isOpen) renderLorebookFlyout();
        el.lorebookFlyout.hidden = isOpen;
        el.lorebookFlyoutBtn.setAttribute('aria-expanded', String(!isOpen));
    });
    document.addEventListener('click', e => {
        if (el.lorebookFlyout && !el.lorebookFlyout.hidden
            && !el.lorebookFlyout.contains(e.target)
            && e.target !== el.lorebookFlyoutBtn
            && !el.lorebookFlyoutBtn?.contains(e.target)) {
            el.lorebookFlyout.hidden = true;
            el.lorebookFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    });
    el.lorebookManageBtn?.addEventListener('click', e => {
        e.stopPropagation();
        el.lorebookFlyout.hidden = true;
        // Open settings on the lorebooks tab
        if (el.settingsFlyout?.hidden !== false) el.settingsBtn?.click();
        applySettingsSection('lorebooks', { drillIntoOnMobile: true });
    });

    // Inline notice — dismiss
    el.lorebookNoticeDismiss?.addEventListener('click', dismissLorebookNotice);

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
            } else {
                const bg = avatar.style.backgroundImage;
                const match = bg.match(/url\(['"]?([^'"()]+)['"]?\)/);
                if (!match) return;
                const img = new Image();
                img.onload = () => {
                    const maxDim = 300;
                    let w = img.naturalWidth, h = img.naturalHeight;
                    if (w >= h) { h = Math.round(maxDim * (h / w)); w = maxDim; }
                    else        { w = Math.round(maxDim * (w / h)); h = maxDim; }
                    avatar.style.width = w + 'px';
                    avatar.style.height = h + 'px';
                    avatar.classList.add('avatar-expanded');
                };
                img.src = match[1];
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
            navigator.clipboard.writeText(msgEl.dataset.rawText || '')
                .then(() => showToast('Copied message', 'success', 2000))
                .catch(() => showToast('Could not copy message'));
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
        if (llm.abortController) llm.abortController.abort();
        else handleSend();
    });
    el.userInput.addEventListener('keydown', e => {
        if (handleSlashKeydown(e)) return;
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    el.userInput.addEventListener('input', () => {
        autoResize(el.userInput);
        saveDraftDebounced();
        updateSlashCommands();
        updateContextMeter();
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
    initTooltips();
    loadPrefs();
    if (state.sidebarCollapsed) el.sidebar.classList.add('collapsed');
    applyTheme(state.theme);
    await loadThemeList();
    bindResponsiveShellHandlers();

    const [, settings] = await Promise.all([loadPersonas(), loadLLMSettings()]);
    await loadSystemPrompts(settings);
    await loadLorebooks();
    await loadCharacters();
    updateComposerState();
    renderLorebookList();
    renderLorebookFlyout();
    renderLorebookNotice();
    updateContextMeter();

    bindFlyoutHandlers();
    bindSidebarHandlers();
    bindSettingsHandlers();
    bindCharacterHandlers();
    bindChatHandlers();
    bindLorebookHandlers();
    bindMessageHandlers();
    bindComposerHandlers();
    bindPersonaHandlers();
    bindScrollHandlers();
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
