import { state, el } from './state.js';
import { autoResize, showToast, Flyouts } from './utils.js';
import { createNewChat, startChatRename } from './chats.js';
import { exportChat } from './export.js';
import { clearDraft } from './drafts.js';
import { regenerateLastAssistantMessage } from './messages.js';
import { jumpToContextBoundary, setContextMeterVisible } from './context-meter.js';
import { matchPresetByName, labelPresets } from './preset-match.js';
import { activatePreset, flushLLMSettingsSave } from './llm-settings.js';
import { selectSystemPrompt } from './system-prompts.js';
import { selectPersona } from './personas.js';
import { selectCharacter } from './characters.js';
import { summarizerConfigured } from './summaries.js';

const COMMANDS = [
    { name: '/retry',  description: 'Regenerate the last assistant message', run: retryLastAssistant },
    { name: '/new',    description: 'Start a new chat for this character', run: newChat },
    { name: '/rename', description: 'Rename the current chat', run: renameCurrentChat },
    { name: '/export', description: 'Export the current chat', run: exportCurrentChat },
    { name: '/jump',   description: 'Jump to where the context window starts', run: jumpToContext },
    { name: '/meter',  description: 'Show or hide the context token meter', run: toggleMeter },
    { name: '/context', description: 'Set max context tokens: /context <tokens>', run: setContextTokens },
    { name: '/auto-summary', description: 'Turn auto summaries on or off: /auto-summary [on|off]', run: setAutoSummary },
    { name: '/prompt', description: 'Switch prompt preset: /prompt <name> [version]', run: switchPromptPreset },
    { name: '/api',    description: 'Switch API preset: /api <name>', run: switchApiPreset },
    { name: '/persona', description: 'Switch persona: /persona <name>', run: switchPersona },
    { name: '/char',   description: 'Switch character: /char <name>', run: switchCharacter },
    { name: '/settings', description: 'Open settings: /settings [page]', run: openSettingsPage },
];

// The commands that take a name, and where the menu finds the names to offer.
const NAME_SOURCES = {
    '/prompt':  { list: () => state.systemPrompts, run: switchPromptPreset, description: 'Switch prompt preset' },
    '/api':     { list: () => state.apiPresets, run: switchApiPreset, description: 'Switch API preset' },
    '/persona': { list: () => state.personas, run: switchPersona, description: 'Switch persona' },
    '/char':    { list: availableCharacters, run: switchCharacter, description: 'Switch character' },
    '/auto-summary': { list: () => [{ name: 'on' }, { name: 'off' }], run: setAutoSummary, description: 'Auto summaries for this chat' },
    '/settings': { list: settingsPages, run: openSettingsPage, description: 'Open settings' },
};

let menuEl = null;
let activeIndex = 0;
let visibleCommands = COMMANDS;

export function initSlashCommands() {
    if (!el.inputWrapper) return;
    menuEl = document.createElement('div');
    menuEl.id = 'slash-command-menu';
    menuEl.className = 'slash-command-menu';
    menuEl.hidden = true;
    menuEl.setAttribute('role', 'listbox');
    menuEl.setAttribute('aria-label', 'Slash commands');
    el.inputWrapper.appendChild(menuEl);
    menuEl.addEventListener('mousedown', e => {
        const item = e.target.closest('.slash-command-item');
        if (!item) return;
        e.preventDefault();
        runCommand(visibleCommands[parseInt(item.dataset.index, 10)]);
    });
    // The click that follows this mousedown would otherwise reach the
    // document-level outside-click handlers and shut a flyout a command just
    // opened — /rename being the one that needs it.
    menuEl.addEventListener('click', e => e.stopPropagation());
}

export function updateSlashCommands() {
    if (!menuEl || !el.userInput) return;
    const value = el.userInput.value;
    if (!value.startsWith('/')) {
        closeSlashCommands();
        return;
    }
    const q = value.trim().toLowerCase();
    visibleCommands = presetSuggestions(value) ?? COMMANDS.filter(cmd => cmd.name.startsWith(q));
    if (visibleCommands.length === 0) {
        closeSlashCommands();
        return;
    }
    activeIndex = Math.min(activeIndex, visibleCommands.length - 1);
    renderMenu();
}

/**
 * Name suggestions once a command token and a space are typed, e.g.
 * "/api loc" → the matching API presets. Each entry carries its full name
 * as `args` so picking it runs the switch without re-parsing the partial
 * text. Anything else (bare "/prompt", unknown command) is not ours: null
 * lets the caller fall back to the plain command list.
 */
function presetSuggestions(value) {
    const m = value.match(/^(\/\S+)\s([\s\S]*)$/);
    const key = m?.[1].toLowerCase();
    const source = NAME_SOURCES[key];
    if (!source) return null;
    const partial = m[2].trim().toLowerCase();
    // Labels qualify a repeated name with its version, and a label is itself a
    // valid query — so each edition gets its own row that selects that edition.
    const named = source.list().filter(p => typeof p?.name === 'string');
    return labelPresets(named)
        .filter(label => label.toLowerCase().startsWith(partial))
        .slice(0, 8)
        .map(label => ({
            name: `${key} ${label}`,
            description: source.description,
            args: label,
            run: source.run,
        }));
}

export function closeSlashCommands() {
    if (menuEl) menuEl.hidden = true;
}

export function handleSlashKeydown(e) {
    if (!menuEl || menuEl.hidden) return false;
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIndex = (activeIndex + 1) % visibleCommands.length;
        renderMenu();
        return true;
    }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIndex = (activeIndex - 1 + visibleCommands.length) % visibleCommands.length;
        renderMenu();
        return true;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        runCommand(visibleCommands[activeIndex]);
        return true;
    }
    if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        completeCommand(visibleCommands[activeIndex]);
        return true;
    }
    if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        closeSlashCommands();
        return true;
    }
    return false;
}

export function executeSlashCommand(rawText) {
    const text = rawText.trim();
    const token = text.split(/\s+/, 1)[0].toLowerCase();
    const command = COMMANDS.find(cmd => cmd.name === token);
    if (!command) return false;
    runCommand(command, text.slice(token.length));
    return true;
}

function renderMenu() {
    if (el.chatFlyout) {
        el.chatFlyout.hidden = true;
        el.chatFlyoutBtn?.setAttribute('aria-expanded', 'false');
    }
    if (el.memoryFlyout) {
        el.memoryFlyout.hidden = true;
        el.memoryFlyoutBtn?.setAttribute('aria-expanded', 'false');
    }
    menuEl.innerHTML = '';
    visibleCommands.forEach((cmd, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `slash-command-item${index === activeIndex ? ' active' : ''}`;
        button.dataset.index = String(index);
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', String(index === activeIndex));
        const name = document.createElement('span');
        name.className = 'slash-command-name';
        name.textContent = cmd.name;
        const description = document.createElement('span');
        description.className = 'slash-command-description';
        description.textContent = cmd.description;
        button.append(name, description);
        menuEl.appendChild(button);
    });
    menuEl.hidden = false;
}

function resetComposer() {
    if (!el.userInput) return;
    el.userInput.value = '';
    autoResize(el.userInput);
    clearDraft();
    closeSlashCommands();
}

/**
 * Fill the composer with the highlighted row without running it. A command
 * that takes a name gets its space too, so the names are offered straight away.
 */
function completeCommand(command) {
    el.userInput.value = command.name + (NAME_SOURCES[command.name] ? ' ' : '');
    activeIndex = 0;
    // The input handler re-filters the menu, resizes the box and saves the draft.
    el.userInput.dispatchEvent(new Event('input'));
}

function runCommand(command, args) {
    if (!command) return;
    // Suggestion entries carry their full preset name; plain menu selections
    // take whatever was typed after the command token before resetComposer().
    if (args === undefined) args = command.args ?? (el.userInput?.value || '').slice(command.name.length);
    resetComposer();
    command.run(args);
}

function retryLastAssistant() {
    regenerateLastAssistantMessage();
}

async function newChat() {
    if (!state.activeCharacter) return showToast('Select a character first');
    await createNewChat(true, false);
}

function exportCurrentChat() {
    if (!state.activeChat) return showToast('Select a chat first');
    exportChat(state.activeChat.id);
}

function renameCurrentChat() {
    if (!state.activeChat) return showToast('Select a chat first');
    // The rename input is an inline swap inside the chat list, so the flyout
    // holding that list has to be open for it to be visible at all.
    Flyouts.closeAllExcept('chat');
    el.chatFlyout.hidden = false;
    el.chatFlyoutBtn?.setAttribute('aria-expanded', 'true');
    const li = el.flyoutChatList?.querySelector(`.chat-item[data-chat-id="${state.activeChat.id}"]`);
    if (li) startChatRename(li, state.activeChat);
}

function jumpToContext() {
    if (!jumpToContextBoundary()) showToast('The whole chat fits in the context window');
}

function toggleMeter() {
    const visible = !state.showContextTokenMeter;
    setContextMeterVisible(visible);
    showToast(visible ? 'Context token meter shown' : 'Context token meter hidden');
}

function contextLabel(tokens) {
    return tokens > 0 ? `${tokens.toLocaleString()} tokens` : 'unlimited';
}

/**
 * Write the number into Settings → API's Max context tokens and let that
 * field's own input handler save it to the active API preset and refresh the
 * warning and meter. "16k" is accepted as shorthand for 16384.
 */
async function setContextTokens(args) {
    const typed = String(args).trim();
    const modelWindow = state.modelContextLength;
    if (!typed) {
        const current = parseInt(el.settingsContextTokens?.value || state.contextMaxTokens, 10) || 0;
        const model = modelWindow ? ` The model's window is ~${modelWindow.toLocaleString()}.` : '';
        return showToast(`Max context: ${contextLabel(current)}.${model}`);
    }
    const m = typed.match(/^(\d+)(k?)$/i);
    if (!m) return showToast('Give a number of tokens, such as /context 16384 or /context 16k');
    const tokens = parseInt(m[1], 10) * (m[2] ? 1024 : 1);
    el.settingsContextTokens.value = String(tokens);
    el.settingsContextTokens.dispatchEvent(new Event('input'));
    // A failed save has already toasted.
    if (!(await flushLLMSettingsSave()).ok) return;
    if (modelWindow && tokens > modelWindow * 0.9) {
        return showToast(`Max context: ${contextLabel(tokens)}. This may exceed the model's window (~${modelWindow.toLocaleString()}).`);
    }
    showToast(`Max context: ${contextLabel(tokens)}`, 'success');
}

/**
 * Flip the memory flyout's per-chat toggle and let its change handler do the
 * rest, backfill included. With no argument it toggles, as /meter does.
 */
function setAutoSummary(args) {
    const chat = state.activeChat;
    if (!chat) return showToast('Select a chat first');
    const word = String(args).trim().toLowerCase();
    if (word && word !== 'on' && word !== 'off') return showToast('Use /auto-summary on or /auto-summary off');
    const on = word ? word === 'on' : !chat.summary_enabled;
    if (on === !!chat.summary_enabled) return showToast(`Auto summaries are already ${word} for this chat`);
    el.summaryToggle.checked = on;
    el.summaryToggle.dispatchEvent(new Event('change'));
    if (on && !summarizerConfigured()) {
        return showToast('Auto summaries on for this chat, but no summarizer is set up yet: see Settings → Auto Summaries');
    }
    showToast(`Auto summaries ${on ? 'on' : 'off'} for this chat`, 'success');
}

/** Keep the not-found toast readable no matter how many presets exist. */
function candidateNames(candidates) {
    return candidates.length > 6 ? `${candidates.slice(0, 6).join(', ')}, …` : candidates.join(', ');
}

async function switchPromptPreset(args) {
    const { preset, label, error, candidates } = matchPresetByName(state.systemPrompts, args);
    if (!preset) {
        if (candidates.length === 0) return showToast('No prompt presets yet — create one in Settings → Prompt');
        const active = state.systemPrompts.find(p => p.id === state.activeSystemPromptId);
        if (error === 'missing') {
            return showToast(`Current prompt: ${active?.name || 'none'}. Available: ${candidateNames(candidates)}`);
        }
        return showToast(`No prompt preset matches "${String(args).trim()}". Available: ${candidateNames(candidates)}`);
    }
    await selectSystemPrompt(preset.id);
    if (el.syspromptSelect) el.syspromptSelect.value = String(preset.id);
    showToast(`Prompt preset: ${label}`, 'success');
}

async function switchApiPreset(args) {
    const { preset, error, candidates } = matchPresetByName(state.apiPresets, args);
    if (!preset) {
        if (candidates.length === 0) return showToast('No API presets yet — create one in Settings → API');
        const active = state.apiPresets.find(p => String(p.id) === String(state.activePresetId));
        if (error === 'missing') {
            return showToast(`Current API preset: ${active?.name || 'none'}. Available: ${candidateNames(candidates)}`);
        }
        return showToast(`No API preset matches "${String(args).trim()}". Available: ${candidateNames(candidates)}`);
    }
    await activatePreset(String(preset.id));
    // activatePreset toasts on failure, so confirm only a real switch.
    if (String(state.activePresetId) === String(preset.id)) {
        showToast(`API preset: ${preset.name}`, 'success');
    }
}

async function switchPersona(args) {
    const { preset, error, candidates } = matchPresetByName(state.personas, args);
    if (!preset) {
        if (candidates.length === 0) return showToast('No personas yet — create one from the persona menu');
        if (error === 'missing') {
            return showToast(`Current persona: ${state.activePersona?.name || 'none'}. Available: ${candidateNames(candidates)}`);
        }
        return showToast(`No persona matches "${String(args).trim()}". Available: ${candidateNames(candidates)}`);
    }
    selectPersona(preset);
    showToast(`Persona: ${preset.name}`, 'success');
}

/** Cards whose PNG has gone missing can't be opened, so they are never offered. */
function availableCharacters() {
    return state.characters.filter(c => !c.missing);
}

async function switchCharacter(args) {
    const { preset, error, candidates } = matchPresetByName(availableCharacters(), args);
    if (!preset) {
        if (candidates.length === 0) return showToast('No characters yet — create or import one first');
        if (error === 'missing') {
            return showToast(`Current character: ${state.activeCharacter?.name || 'none'}. Available: ${candidateNames(candidates)}`);
        }
        return showToast(`No character matches "${String(args).trim()}". Available: ${candidateNames(candidates)}`);
    }
    // Re-selecting reloads the chat and would cut off a reply mid-stream.
    if (preset.id === state.activeCharacter?.id) return showToast(`Already chatting with ${preset.name}`);
    await selectCharacter(preset.id);
}

/** The settings nav's own pages. */
function settingsPages() {
    return [...el.settingsNav.querySelectorAll('.settings-nav-item')]
        .map(button => ({ name: button.querySelector('span').textContent, button }));
}

/**
 * Open Settings, then click the named page's nav item so it opens exactly as
 * a tap would, drilling into the page on phones.
 */
function openSettingsPage(args) {
    const typed = String(args).trim();
    const { preset: page, candidates } = matchPresetByName(settingsPages(), typed);
    if (typed && !page) return showToast(`No settings page matches "${typed}". Pages: ${candidates.join(', ')}`);
    // Opening shows the saved page first, so make this the saved page rather
    // than let the other one flash past.
    if (page) state.settingsSection = page.button.dataset.section;
    if (el.settingsFlyout?.hidden !== false) el.settingsBtn?.click();
    page?.button.click();
}
