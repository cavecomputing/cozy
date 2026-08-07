import { state, el, llm, SEND_SVG, STOP_SVG } from './state.js';

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

/**
 * Matches the chatStamp() default name given to new chats. The second branch is
 * the retired syslog-style stamp ("May 18 16:54:17"), still worn by chats made
 * before the switch.
 */
export const DEFAULT_CHAT_NAME_RE =
    /^(?:\d{4}-\d{2}-\d{2}:\d{2}-\d{2}-\d{2}|[A-Z][a-z]{2} {1,2}\d{1,2} \d{2}:\d{2}:\d{2})$/;

/** User-facing chat name: the stored name, with a fallback for unnamed chats. */
export function displayChatName(chat) {
    const name = (chat?.name || '').trim();
    if (!name || name === 'New Chat') return 'New chat';
    return name;
}

/** Local timestamp used to name new chats: "2026-03-22:20-06-42" */
export function chatStamp() {
    const now = new Date();
    const yyyy = String(now.getFullYear());
    const MM   = String(now.getMonth() + 1).padStart(2, '0');
    const dd   = String(now.getDate()).padStart(2, '0');
    const hh   = String(now.getHours()).padStart(2, '0');
    const mm   = String(now.getMinutes()).padStart(2, '0');
    const ss   = String(now.getSeconds()).padStart(2, '0');
    return `${yyyy}-${MM}-${dd}:${hh}-${mm}-${ss}`;
}

/**
 * Resolve SillyTavern-style template variables in a string.
 * Supports conditional blocks: {{#var}}…{{/var}} drops out when var is empty.
 * @param {string} template — the raw template with {{var}} placeholders
 * @param {object} context  — { user, char, personality, scenario, description,
 *                              persona, mesExamples, lorebook, author_note, summary,
 *                              system_prompt, post_history_instructions,
 *                              user_message, idle_duration }
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
        .replace(/\{\{author_note\}\}/gi,    context.author_note || '')
        .replace(/\{\{summary\}\}/gi,        context.summary || '')
        .replace(/\{\{system_prompt\}\}/gi,  context.system_prompt || '')
        .replace(/\{\{post_history_instructions\}\}/gi,
                                                context.post_history_instructions || '')
        .replace(/\{\{user_message\}\}/gi,   context.user_message || '')
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
export function showToast(message, type = 'error', duration = 5000, action = null) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', 'alert');
    toast.textContent = message;
    if (action) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'toast-action';
        btn.textContent = action.label;
        btn.addEventListener('click', () => { toast.remove(); action.onClick(); });
        toast.appendChild(btn);
    }
    container.appendChild(toast);
    setTimeout(() => { toast.remove(); }, duration);
}

// Inline "no API configured" notice above the composer. Shown when a send is
// attempted without a model; cleared on dismiss or once a model is set.
export function showApiNotice() {
    if (el.apiNotice) el.apiNotice.hidden = false;
}

export function hideApiNotice() {
    if (el.apiNotice) el.apiNotice.hidden = true;
}

export function scrollToBottom() {
    el.chatHistory.scrollTop = el.chatHistory.scrollHeight;
    state.autoScroll = true;
}

export function maybeScrollToBottom() {
    if (state.autoScroll) scrollToBottom();
}

/**
 * Cancel the in-flight generation at the user's request. Unlike a bare
 * `abort()` (see selectChat), this keeps whatever has streamed so far — the
 * generation's catch block reads `stopRequested` to decide.
 */
export function stopGeneration() {
    if (!llm.abortController) return;
    llm.stopRequested = true;
    llm.abortController.abort();
}

/** Claim the single generation slot before an async preflight can yield. */
export function beginGeneration() {
    if (llm.generationActive) return false;
    llm.generationActive = true;
    return true;
}

/** Release the generation slot on every success, failure and abort path. */
export function endGeneration() {
    llm.generationActive = false;
}

export function setSendButtonMode(mode) {
    const isStop = mode === 'stop';
    el.sendBtn.innerHTML = isStop ? STOP_SVG : SEND_SVG;
    el.sendBtn.title = isStop ? 'Stop generation' : 'Send message';
    el.sendBtn.setAttribute('aria-label', el.sendBtn.title);
    el.sendBtn.classList.toggle('stop-mode', isStop);
}

function activeChatLabel() {
    return displayChatName(state.activeChat);
}

function activeLorebookLabel() {
    const chat = state.activeChat;
    if (!chat) return 'Book';
    if (chat.active_lorebook_embedded) {
        const charName = state.activeCharacter?.name || 'Character';
        return `${charName}'s book`;
    }
    if (chat.active_lorebook_id != null) {
        const book = state.lorebooks.find(b => b.id === chat.active_lorebook_id);
        return book?.name || 'Missing book';
    }
    return 'None';
}

/** True if the active prompt template references {{name}} or {{#name}}. */
export function templateHasVar(name) {
    const sp = state.systemPrompts.find(s => s.id === state.activeSystemPromptId);
    if (!sp) return true;  // no active template resolvable → don't mark (avoid false alarms)
    const re = new RegExp('\\{\\{#?' + name + '\\}\\}', 'i');
    return re.test(sp.content || '') || re.test(sp.post_history_content || '');
}

/**
 * Show the neutral ⊘ marker when the active prompt template has no
 * {{variable}} for something. Card fields pass `inUse` so an empty field stays
 * unmarked; the memory flyout marks its cards either way, since the point
 * there is to say the feature has nowhere to go before you fill it in.
 */
export function markUnusedVar(marker, varName, inUse = true) {
    if (marker) marker.hidden = !(inUse && !templateHasVar(varName));
}

function updateComposerContextControls(hasChat, hasCharacter) {
    if (el.chatFlyoutBtn) {
        const label = hasChat ? activeChatLabel() : 'Chats';
        el.chatFlyoutBtn.disabled = !hasChat;
        el.chatFlyoutBtn.title = hasChat ? `Chats: ${label}` : 'Select a character to manage chats';
        el.chatFlyoutBtn.setAttribute(
            'aria-label',
            hasChat ? `Open chats. Current chat: ${label}` : 'Select a character to manage chats'
        );
        if (!hasChat && el.chatFlyout) {
            el.chatFlyout.hidden = true;
            el.chatFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    }

    if (el.memoryFlyoutBtn) {
        const label = hasChat ? activeLorebookLabel() : 'Memory';
        el.memoryFlyoutBtn.disabled = !hasChat;
        el.memoryFlyoutBtn.title = hasChat
            ? `Memory — Author's Note & lorebook (${label})`
            : (hasCharacter ? 'Create or select a chat to use memory' : 'Select a character to use memory');
        el.memoryFlyoutBtn.setAttribute('aria-label', el.memoryFlyoutBtn.title);
        if (!hasChat && el.memoryFlyout) {
            el.memoryFlyout.hidden = true;
            el.memoryFlyoutBtn.setAttribute('aria-expanded', 'false');
        }
    }
}

export function updateComposerState() {
    if (!el.userInput || !el.sendBtn) return;
    if (state.apiModel) hideApiNotice();
    const hasChat = !!state.activeCharacter && !!state.activeChat;
    const hasCharacter = !!state.activeCharacter;
    el.inputContainer?.classList.toggle('composer-no-character', !hasCharacter);
    el.inputContainer?.classList.toggle('composer-no-chat', hasCharacter && !hasChat);
    el.userInput.disabled = !hasChat;
    if (hasChat) {
        const name = state.activeCharacter?.name || 'this character';
        el.userInput.placeholder = llm.abortController
            ? 'Generating response...'
            : `Message ${name}... (type / for commands)`;
    } else if (state.activeCharacter) {
        el.userInput.placeholder = 'Create or select a chat to start messaging';
    } else {
        el.userInput.placeholder = state.characters.length === 0
            ? 'Create a character to start chatting'
            : 'Select a character to start chatting';
    }
    if (!llm.abortController) el.sendBtn.disabled = !hasChat;
    updateComposerContextControls(hasChat, hasCharacter);
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

// Server-side thumbnail tiers (see thumbs.py). SM covers every circular
// avatar; LG supplies the expanded message avatar.
export const AVATAR = { SM: 128, LG: 1024 };

/**
 * Rewrite an avatar URL to request a downscaled copy.
 * Leaves anything that isn't a served avatar path alone, so blob: and
 * data: URLs used for upload previews pass through untouched.
 */
function thumbUrl(url, size) {
    if (!url || !size) return url;
    const m = /^\/(characters|personas)\/(.+?)(\?.*)?$/.exec(url);
    if (!m) return url;
    return `/thumbs/${m[1]}/${size}/${m[2]}${m[3] || ''}`;
}

/** Set avatar element — background-image if URL, else initials.
 *  Pass a size from AVATAR to use a thumbnail; omitting it serves the full
 *  image, so a call site that hasn't opted in is slow rather than blurry. */
export function applyAvatar(avatarEl, obj, fallbackName = '?', size = null) {
    if (obj && obj.avatar_url) {
        const src = thumbUrl(obj.avatar_url, size);
        avatarEl.style.backgroundImage = `url('${src}')`;
        avatarEl.dataset.hasImage = 'true';
        avatarEl.dataset.thumbSrc = src;
        // Kept so click-to-expand can swap up to a full-quality image whose
        // dimensions still carry the card's true aspect ratio.
        avatarEl.dataset.largeSrc = thumbUrl(obj.avatar_url, AVATAR.LG);
        avatarEl.textContent = '';
    } else {
        avatarEl.style.backgroundImage = '';
        avatarEl.dataset.hasImage = 'false';
        delete avatarEl.dataset.thumbSrc;
        delete avatarEl.dataset.largeSrc;
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
// CLIPBOARD
// ═══════════════════════════════════════════════════════════════════════════
/**
 * Copy text to the clipboard, resolving true on success and false on failure.
 * navigator.clipboard is undefined in insecure contexts (e.g. Cozy served over
 * a plain-http LAN IP, which is common on mobile), so fall back to a hidden
 * textarea + execCommand there instead of throwing.
 */
export async function copyText(text) {
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            // Fall through to the legacy path below.
        }
    }
    try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return ok;
    } catch {
        return false;
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
