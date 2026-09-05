import { state, el } from './state.js';
import { autoResize, showToast, Flyouts } from './utils.js';
import { createNewChat, startChatRename, importChat } from './chats.js';
import { exportChat } from './export.js';
import { clearDraft } from './drafts.js';
import { regenerateLastAssistantMessage, handleSwipeAction } from './messages.js';
import { jumpToContextBoundary, setContextMeterVisible } from './context-meter.js';
import { matchPresetByName } from './preset-match.js';
import { activatePreset } from './llm-settings.js';
import { selectSystemPrompt } from './system-prompts.js';

const COMMANDS = [
    { name: '/retry',  description: 'Regenerate the last assistant message', run: retryLastAssistant },
    { name: '/prev',   description: 'Show the previous swipe', run: () => swipeLast(true) },
    { name: '/next',   description: 'Next swipe, or generate a new one', run: () => swipeLast(false) },
    { name: '/new',    description: 'Start a new chat for this character', run: newChat },
    { name: '/rename', description: 'Rename the current chat', run: renameCurrentChat },
    { name: '/import', description: 'Import a chat from a file', run: importCurrentChat },
    { name: '/export', description: 'Export the current chat', run: exportCurrentChat },
    { name: '/jump',   description: 'Jump to where the context window starts', run: jumpToContext },
    { name: '/meter',  description: 'Show or hide the context token meter', run: toggleMeter },
    { name: '/prompt', description: 'Switch prompt preset: /prompt <name>', run: switchPromptPreset },
    { name: '/api',    description: 'Switch API preset: /api <name>', run: switchApiPreset },
];

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
 * Preset-name suggestions once a command token and a space are typed, e.g.
 * "/api loc" → the matching API presets. Each entry carries its full name
 * as `args` so picking it runs the switch without re-parsing the partial
 * text. Anything else (bare "/prompt", unknown command) is not ours: null
 * lets the caller fall back to the plain command list.
 */
function presetSuggestions(value) {
    const m = value.match(/^\/(prompt|api)\s([\s\S]*)$/i);
    if (!m) return null;
    const key = `/${m[1].toLowerCase()}`;
    const partial = m[2].trim().toLowerCase();
    const source = key === '/prompt' ? state.systemPrompts : state.apiPresets;
    const run = key === '/prompt' ? switchPromptPreset : switchApiPreset;
    const kind = key === '/prompt' ? 'Prompt' : 'API';
    return source
        .filter(p => typeof p?.name === 'string' && p.name.toLowerCase().startsWith(partial))
        .slice(0, 8)
        .map(p => ({
            name: `${key} ${p.name}`,
            description: `Switch ${kind.toLowerCase()} preset`,
            args: p.name,
            run,
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

/**
 * Step the last character message through its swipes, greetings included —
 * cycling alternate greetings is the half the message arrows offer that
 * /retry (which always lands on a fresh generation) cannot reach.
 */
function swipeLast(isPrev) {
    if (!state.activeChat) return showToast('Select a chat first');
    const last = [...state.messages].reverse().find(m => m.role === 'character');
    const msgEl = last?.id
        ? el.chatHistory.querySelector(`.message.character[data-msg-id="${last.id}"]`)
        : null;
    if (!msgEl) return showToast('No assistant message to swipe yet');

    const swipes = JSON.parse(msgEl.dataset.swipes || '[]');
    const idx = parseInt(msgEl.dataset.activeSwipeIndex || '0', 10);
    // handleSwipeAction() no-ops at either end; from the composer there is no
    // greyed-out arrow to explain why nothing moved.
    if (isPrev && idx <= 0) return showToast('Already at the first swipe');
    if (!isPrev && idx >= swipes.length - 1 && msgEl.dataset.isGreeting === 'true') {
        return showToast('No more greetings');
    }
    handleSwipeAction(msgEl, isPrev);
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

function importCurrentChat() {
    if (!state.activeCharacter) return showToast('Select a character first');
    importChat();
}

function jumpToContext() {
    if (!jumpToContextBoundary()) showToast('The whole chat fits in the context window');
}

function toggleMeter() {
    const visible = !state.showContextTokenMeter;
    setContextMeterVisible(visible);
    showToast(visible ? 'Context token meter shown' : 'Context token meter hidden');
}

/** Keep the not-found toast readable no matter how many presets exist. */
function candidateNames(candidates) {
    return candidates.length > 6 ? `${candidates.slice(0, 6).join(', ')}, …` : candidates.join(', ');
}

async function switchPromptPreset(args) {
    const { preset, error, candidates } = matchPresetByName(state.systemPrompts, args);
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
    showToast(`Prompt preset: ${preset.name}`, 'success');
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
