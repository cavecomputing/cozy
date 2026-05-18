import { state, el, llm } from './state.js';

// ═══════════════════════════════════════════════════════════════════════════
// UTILITY
// ═══════════════════════════════════════════════════════════════════════════
export function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = (textarea.scrollHeight + 2) + 'px';
}

export function sanitize(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

export function getInitials(name) {
    return (name || '?').trim().substring(0, 2).toUpperCase();
}

export function downloadUrl(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

export function sanitizeFilename(name) {
    return (name || '').replace(/[\\/:*?"<>|]/g, '_');
}

/** Syslog-style local timestamp: "Mar 22 20:06:42" */
export function syslogStamp() {
    const now = new Date();
    const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mon = MON[now.getMonth()];
    const day = String(now.getDate()).padStart(2, ' ');
    const hh  = String(now.getHours()).padStart(2, '0');
    const mm  = String(now.getMinutes()).padStart(2, '0');
    const ss  = String(now.getSeconds()).padStart(2, '0');
    return `${mon} ${day} ${hh}:${mm}:${ss}`;
}

/**
 * Resolve SillyTavern-style template variables in a string.
 * Supports conditional blocks: {{#var}}…{{/var}} drops out when var is empty.
 * @param {string} template — the raw template with {{var}} placeholders
 * @param {object} context  — { user, char, personality, scenario, description,
 *                              persona, mesExamples, lorebook, system_prompt,
 *                              idle_duration }
 * @returns {string} the resolved string
 */
export function resolveTemplateVariables(template, context) {
    if (!template) return '';
    template = template.replace(
        /\{\{#(\w+)\}\}([\s\S]*?)\{\{\/\1\}\}/g,
        (_, key, body) => {
            const v = context[key];
            if (v == null || String(v).trim() === '') return '';
            return body.replace(/^\n/, '').replace(/\n$/, '');
        }
    );
    return template
        .replace(/\{\{user\}\}/gi,           context.user || '')
        .replace(/\{\{char\}\}/gi,           context.char || '')
        .replace(/\{\{personality\}\}/gi,    context.personality || '')
        .replace(/\{\{scenario\}\}/gi,       context.scenario || '')
        .replace(/\{\{description\}\}/gi,    context.description || '')
        .replace(/\{\{persona\}\}/gi,        context.persona || '')
        .replace(/\{\{mesExamples\}\}/gi,    context.mesExamples || '')
        .replace(/\{\{lorebook\}\}/gi,       context.lorebook || '')
        .replace(/\{\{system_prompt\}\}/gi,  context.system_prompt || '')
        .replace(/\{\{time\}\}/gi,           new Date().toLocaleTimeString())
        .replace(/\{\{date\}\}/gi,           new Date().toLocaleDateString())
        .replace(/\{\{idle_duration\}\}/gi,  context.idle_duration || '0')
        .replace(/\{\{random:([^}]+)\}\}/gi, (_, choices) => {
            const arr = choices.split(',').map(s => s.trim());
            return arr[Math.floor(Math.random() * arr.length)] || '';
        })
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

// ═══════════════════════════════════════════════════════════════════════════
// TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════════════════
export function showToast(message, type = 'error', duration = 5000) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', 'alert');
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => { toast.remove(); }, duration);
}

export function scrollToBottom() {
    el.chatHistory.scrollTop = el.chatHistory.scrollHeight;
    state.autoScroll = true;
}

export function maybeScrollToBottom() {
    if (state.autoScroll) scrollToBottom();
}

export function setSendButtonMode(mode) {
    const isStop = mode === 'stop';
    el.sendBtn.title = isStop ? 'Stop generation' : 'Send message';
    el.sendBtn.setAttribute('aria-label', el.sendBtn.title);
    el.sendBtn.classList.toggle('stop-mode', isStop);
}

export function updateComposerState() {
    if (!el.userInput || !el.sendBtn) return;
    const hasChat = !!state.activeCharacter && !!state.activeChat;
    const hasCharacter = !!state.activeCharacter;
    el.inputContainer?.classList.toggle('composer-no-character', !hasCharacter);
    el.inputContainer?.classList.toggle('composer-no-chat', hasCharacter && !hasChat);
    el.userInput.disabled = !hasChat;
    if (hasChat) {
        const name = state.activeCharacter?.name || 'this character';
        el.userInput.placeholder = llm.abortController
            ? 'Generating response...'
            : `Message ${name}...`;
    } else if (state.activeCharacter) {
        el.userInput.placeholder = 'Create or select a chat to start messaging';
    } else {
        el.userInput.placeholder = state.characters.length === 0
            ? 'Create a character to start chatting'
            : 'Select a character to start chatting';
    }
    if (!llm.abortController) el.sendBtn.disabled = !hasChat;

    if (el.chatFlyoutBtn) {
        el.chatFlyoutBtn.disabled = !hasChat;
        el.chatFlyoutBtn.title = hasChat ? 'Chats' : 'Select a character to manage chats';
        el.chatFlyoutBtn.setAttribute('aria-label', hasChat ? 'Open chats' : 'Select a character to manage chats');
        if (!hasChat && el.chatFlyout) {
            el.chatFlyout.hidden = true;
            el.chatFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    }

    if (el.lorebookFlyoutBtn) {
        el.lorebookFlyoutBtn.disabled = !hasChat;
        el.lorebookFlyoutBtn.title = hasChat ? 'Lorebook for this chat' : (hasCharacter ? 'Create or select a chat to use lorebooks' : 'Select a character to use lorebooks');
        el.lorebookFlyoutBtn.setAttribute('aria-label', el.lorebookFlyoutBtn.title);
        if (!hasChat && el.lorebookFlyout) {
            el.lorebookFlyout.hidden = true;
            el.lorebookFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    }
}

export function showEmptyState(title, text, showCreate = false) {
    if (!el.emptyState) return;
    if (el.emptyStateTitle) el.emptyStateTitle.textContent = title;
    if (el.emptyStateText) el.emptyStateText.textContent = text;
    if (el.emptyNewCharBtn) el.emptyNewCharBtn.hidden = !showCreate;
    el.emptyState.hidden = false;
}

export function hideEmptyState() {
    if (el.emptyState) el.emptyState.hidden = true;
}

/** Set avatar element — background-image if URL, else initials. */
export function applyAvatar(avatarEl, obj, fallbackName = '?') {
    if (obj && obj.avatar_url) {
        avatarEl.style.backgroundImage = `url('${obj.avatar_url}')`;
        avatarEl.dataset.hasImage = 'true';
        avatarEl.textContent = '';
    } else {
        avatarEl.style.backgroundImage = '';
        avatarEl.dataset.hasImage = 'false';
        avatarEl.textContent = getInitials((obj && obj.name) || fallbackName);
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// FLYOUT MANAGER — register flyouts so only one can be open at a time
// ═══════════════════════════════════════════════════════════════════════════
export const Flyouts = (() => {
    const registry = {};  // name → { close }

    function register(name, closeFn) {
        registry[name] = { close: closeFn };
    }

    function closeAllExcept(name) {
        for (const [key, entry] of Object.entries(registry)) {
            if (key !== name) entry.close();
        }
    }

    return { register, closeAllExcept };
})();

// ═══════════════════════════════════════════════════════════════════════════
// PREFS PERSISTENCE
// ═══════════════════════════════════════════════════════════════════════════
export function savePrefs() {
    localStorage.setItem('cozy/prefs', JSON.stringify({
        sidebarCollapsed: state.sidebarCollapsed,
        theme:            state.theme,
        activeCharId:     state.activeCharacter?.id ?? null,
        activeChatId:     state.activeChat?.id ?? null,
        activePersonaId:  state.activePersona?.id ?? null,
        settingsSection:  state.settingsSection,
    }));
}

export function closeMobileSidebar({ restoreFocus = true, immediate = false } = {}) {
    const wasOpen = el.sidebar.classList.contains('mobile-open');
    if (immediate) {
        el.sidebar.classList.add('mobile-closing-immediate');
    }
    el.sidebar.classList.remove('mobile-open');
    el.mobileBackdrop.classList.remove('show');
    el.mobileMenuBtn?.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('mobile-drawer-open');
    const main = document.getElementById('main-content');
    if (main) {
        main.inert = false;
        main.removeAttribute('aria-hidden');
    }
    if (wasOpen && restoreFocus) el.mobileMenuBtn?.focus();
    if (immediate) {
        requestAnimationFrame(() => {
            el.sidebar.classList.remove('mobile-closing-immediate');
        });
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// DEBOUNCE
// ═══════════════════════════════════════════════════════════════════════════
export function debounce(fn, ms) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}
