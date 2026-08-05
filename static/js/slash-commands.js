import { state, el } from './state.js';
import { autoResize, showToast } from './utils.js';
import { createNewChat } from './chats.js';
import { exportChat } from './export.js';
import { clearDraft } from './drafts.js';
import { regenerateLastAssistantMessage } from './messages.js';

const COMMANDS = [
    { name: '/retry',  description: 'Regenerate the last assistant message', run: retryLastAssistant },
    { name: '/new',    description: 'Start a new chat for this character', run: newChat },
    { name: '/export', description: 'Export the current chat', run: exportCurrentChat },
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
}

export function updateSlashCommands() {
    if (!menuEl || !el.userInput) return;
    const value = el.userInput.value;
    if (!value.startsWith('/')) {
        closeSlashCommands();
        return;
    }
    const q = value.trim().toLowerCase();
    visibleCommands = COMMANDS.filter(cmd => cmd.name.startsWith(q));
    if (visibleCommands.length === 0) {
        closeSlashCommands();
        return;
    }
    activeIndex = Math.min(activeIndex, visibleCommands.length - 1);
    renderMenu();
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
    const command = COMMANDS.find(cmd => cmd.name === rawText.trim().split(/\s+/, 1)[0].toLowerCase());
    if (!command) return false;
    runCommand(command);
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

function runCommand(command) {
    if (!command) return;
    resetComposer();
    command.run();
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
