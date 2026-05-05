import { state, el, icons } from './state.js';
import { API } from './api.js';
import {
    applyAvatar, getInitials, resolveTemplateVariables, showToast,
    scrollToBottom, maybeScrollToBottom, showEmptyState, hideEmptyState,
    updateComposerState,
} from './utils.js';
import { parseThinkingContent, renderThinkingBlock } from './thinking.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT — MESSAGES
// ═══════════════════════════════════════════════════════════════════════════

/** Render sanitised markdown into an element (resolves ST-style variables first). */
export function renderMarkdown(targetEl, rawText) {
    const c = state.activeCharacter || {};
    const p = state.activePersona || {};
    const resolved = resolveTemplateVariables(rawText, {
        user:         p.name || 'User',
        char:         c.name || '',
        personality:  c.personality || '',
        scenario:     c.scenario || '',
        description:  c.description || '',
        persona:      p.description || '',
        mesExamples:  c.mes_example || '',
    });
    targetEl.innerHTML = DOMPurify.sanitize(marked.parse(resolved));
}

/** Find the state.messages entry matching a message element (by DB id, then fallback to text). */
export function findStateMsg(swipes, msgEl) {
    const id = msgEl.dataset.msgId;
    if (id) {
        const byId = state.messages.find(m => String(m.id) === String(id));
        if (byId) return byId;
    }
    return state.messages.find(m =>
        swipes.some(s => s.content === m.text) || m.text === msgEl.dataset.rawText
    );
}

function iconButton(className, title, ariaLabel, icon) {
    const btn = document.createElement('button');
    btn.className = className;
    btn.title = title;
    btn.setAttribute('aria-label', ariaLabel);
    btn.innerHTML = icon;
    return btn;
}

function buildSwipeButton(direction, title, disabled) {
    const btn = iconButton(
        `swipe-btn swipe-${direction}`,
        title,
        direction === 'prev' ? 'Previous swipe' : title,
        direction === 'prev' ? icons.CHEVLEFT : icons.CHEVRIGHT,
    );
    btn.disabled = disabled;
    return btn;
}

function appendMessageActionButtons(bar) {
    bar.append(
        iconButton('msg-action-btn copy-msg-btn', 'Copy', 'Copy message', icons.COPY),
        iconButton('msg-action-btn edit-msg-btn', 'Edit', 'Edit message', icons.EDIT),
        iconButton('msg-action-btn delete-msg-btn', 'Delete', 'Delete message', icons.DELETE),
    );
}

/** Build the Discord-style floating action toolbar for a message. */
export function buildMsgActions(role, swipeCount = 1, activeSwipeIndex = 0, isGreeting = false) {
    const bar = document.createElement('div');
    bar.className = 'msg-actions';
    if (role === 'character') {
        const idx = activeSwipeIndex + 1;
        const atEnd = idx >= swipeCount;
        const nextDisabled = isGreeting && atEnd;
        const nextTitle = atEnd ? (isGreeting ? 'No more greetings' : 'Generate new') : 'Next';

        const nav = document.createElement('div');
        nav.className = 'swipe-nav';

        const counter = document.createElement('span');
        counter.className = 'swipe-counter';
        counter.textContent = `${idx}/${swipeCount}`;

        nav.append(
            buildSwipeButton('prev', 'Previous', idx <= 1),
            counter,
            buildSwipeButton('next', nextTitle, nextDisabled),
        );
        bar.append(nav);
    }
    appendMessageActionButtons(bar);
    return bar;
}

function buildEditActions() {
    const bar = document.createElement('div');
    bar.append(
        iconButton('msg-action-btn save-msg-btn', 'Save (Enter)', 'Save message edit', icons.SAVE),
        iconButton('msg-action-btn cancel-msg-btn', 'Cancel (Esc)', 'Cancel message edit', icons.CANCEL),
    );
    return bar;
}

/** Build the greeting prev/next nav row (only when multiple greetings exist). */
function buildGreetingNav(allGreetings, messageEl, contentEl) {
    const nav = document.createElement('div');
    nav.className = 'greeting-nav';

    const prevBtn = document.createElement('button');
    prevBtn.className = 'greeting-nav-btn';
    prevBtn.title = 'Previous greeting';
    prevBtn.setAttribute('aria-label', 'Previous greeting');
    prevBtn.innerHTML = icons.CHEVLEFT;

    const counter = document.createElement('span');
    counter.className = 'greeting-nav-count';

    const nextBtn = document.createElement('button');
    nextBtn.className = 'greeting-nav-btn';
    nextBtn.title = 'Next greeting';
    nextBtn.setAttribute('aria-label', 'Next greeting');
    nextBtn.innerHTML = icons.CHEVRIGHT;

    const update = () => {
        const idx = state.greetingIndex;
        counter.textContent = `${idx + 1} / ${allGreetings.length}`;
        prevBtn.disabled = idx === 0;
        nextBtn.disabled = idx === allGreetings.length - 1;
        renderMarkdown(contentEl, allGreetings[idx]);
        messageEl.dataset.rawText = allGreetings[idx];
    };

    prevBtn.addEventListener('click', () => { if (state.greetingIndex > 0) { state.greetingIndex--; update(); } });
    nextBtn.addEventListener('click', () => { if (state.greetingIndex < allGreetings.length - 1) { state.greetingIndex++; update(); } });

    update();
    nav.append(prevBtn, counter, nextBtn);
    return nav;
}

/** Build a message DOM element (pure DOM construction, no side effects). */
function buildMessageEl(role, text, isGreeting = false, timestamp = null, swipes = null, activeSwipeIndex = 0, persona = null, msgId = null) {
    const char = state.activeCharacter;
    const p = role === 'user' ? (persona || state.activePersona) : null;

    const container = document.createElement('div');
    container.className = `message-container ${role}`;

    const avatarDiv = document.createElement('div');
    if (role === 'user') {
        avatarDiv.className = 'avatar user-avatar';
        if (p && p.avatar_url) {
            avatarDiv.style.backgroundImage = `url('${p.avatar_url}?t=${Date.now()}')`;
            avatarDiv.dataset.hasImage = 'true';
        } else {
            avatarDiv.textContent = getInitials((p && p.name) || 'ME');
        }
    } else {
        avatarDiv.className = 'avatar';
        applyAvatar(avatarDiv, char);
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper';

    const message = document.createElement('div');
    message.className = `message ${role}`;
    message.dataset.rawText = text;

    const msgBody = document.createElement('div');
    msgBody.className = 'msg-body';

    const msgHeader = document.createElement('div');
    msgHeader.className = 'msg-header';
    const msgName = document.createElement('span');
    msgName.className = 'msg-name';
    msgName.textContent = role === 'user'
        ? (p?.name || 'You')
        : (char?.name || 'Character');
    const msgTime = document.createElement('span');
    msgTime.className = 'msg-time';
    const ts = timestamp ? new Date(timestamp + 'Z') : new Date();
    msgTime.textContent = ts.toLocaleString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit'
    });
    const msgSwipes = swipes || [{ content: text }];
    const actions = buildMsgActions(role, msgSwipes.length, activeSwipeIndex, isGreeting);
    msgHeader.append(msgName, msgTime, actions);

    message.dataset.swipes = JSON.stringify(msgSwipes);
    message.dataset.activeSwipeIndex = activeSwipeIndex;

    const headerDivider = document.createElement('div');
    headerDivider.className = 'msg-header-divider';

    const content = document.createElement('div');
    content.className = 'message-content';

    const parsed = parseThinkingContent(text);
    renderMarkdown(content, parsed.thinking ? parsed.response : text);

    msgBody.append(msgHeader, headerDivider, content);

    if (parsed.thinking) renderThinkingBlock(msgBody, parsed);
    message.append(avatarDiv, msgBody);
    wrapper.append(message);

    if (isGreeting) {
        message.dataset.isGreeting = 'true';
    }
    if (msgId) message.dataset.msgId = msgId;

    container.append(wrapper);
    return { container, message };
}

export function renderMessages() {
    el.chatHistory.querySelectorAll('.message-container').forEach(c => c.remove());

    const char = state.activeCharacter;
    if (!char && state.characters.length === 0) {
        showEmptyState('No characters yet', 'Create a character to start your first conversation.', true);
        updateComposerState();
        return;
    }
    if (!char) {
        showEmptyState('No character selected', 'Choose a character from the sidebar to continue.', false);
        updateComposerState();
        return;
    }
    if (!char || !state.activeChat) {
        showEmptyState('No chat selected', 'Create or select a chat to start messaging this character.', false);
        updateComposerState();
        return;
    }
    hideEmptyState();
    updateComposerState();
    if (state.messages.length === 0) {
        showEmptyState('Ready to chat', `Send the first message to ${char.name || 'this character'}.`, false);
        return;
    }

    const fragment = document.createDocumentFragment();
    state.messages.forEach((m, i) => {
        const isFirstMsg = i === 0 && m.role === 'character';
        const { container } = buildMessageEl(
            m.role, m.text, isFirstMsg, m.created_at,
            m.swipes, m.activeSwipeIndex, m.persona, m.id
        );
        fragment.appendChild(container);
    });
    el.chatHistory.appendChild(fragment);

    // Reset scroll-to-bottom button visibility — if there's no overflow,
    // no scroll event will fire so the button could stay stale from a previous chat.
    const atBottom =
        el.chatHistory.scrollHeight - el.chatHistory.scrollTop - el.chatHistory.clientHeight < 60;
    state.autoScroll = atBottom;
    el.scrollToBottomBtn?.classList.toggle('visible', !atBottom);
    scrollToBottom();
}

export async function appendMessage(role, text, persist = true, isGreeting = false, timestamp = null, swipes = null, activeSwipeIndex = 0, persona = null, msgId = null) {
    hideEmptyState();
    const p = role === 'user' ? (persona || state.activePersona) : null;
    const { container, message } = buildMessageEl(role, text, isGreeting, timestamp, swipes, activeSwipeIndex, persona, msgId);
    el.chatHistory.appendChild(container);

    if (persist && state.activeChat) {
        const personaId = (role === 'user' && p) ? p.id : null;
        try {
            const saved = await API.addMessage(state.activeChat.id, role, text, personaId);
            message.dataset.msgId = saved.id;
            state.messages.push({
                role, text, id: saved.id, created_at: saved.created_at,
                swipes: saved.swipes || [{ content: text }],
                activeSwipeIndex: 0,
                persona: p ? { name: p.name, avatar_url: p.avatar_url } : null,
            });
        } catch (err) {
            console.error('Could not save message:', err);
            state.messages.push({ role, text, swipes: [{ content: text }], activeSwipeIndex: 0,
                persona: p ? { name: p.name, avatar_url: p.avatar_url } : null });
        }
    }

    if (role === 'user') scrollToBottom(); else maybeScrollToBottom();
    return container;
}

// ═══════════════════════════════════════════════════════════════════════════
// MESSAGE EDITING
// ═══════════════════════════════════════════════════════════════════════════
export function startEditing(messageEl) {
    if (messageEl.classList.contains('editing')) return;
    const contentDiv = messageEl.querySelector('.message-content');
    const actionsBar = messageEl.closest('.message-wrapper').querySelector('.msg-actions');

    // Show raw markdown for editing
    messageEl.dataset.originalText = messageEl.dataset.rawText;
    messageEl.classList.add('editing');
    state.currentEdit = { element: messageEl, contentDiv, actionsBar };

    contentDiv.textContent = messageEl.dataset.rawText;  // raw markdown
    contentDiv.contentEditable = 'plaintext-only';
    contentDiv.focus();
    // Place cursor at end
    const range = document.createRange();
    range.selectNodeContents(contentDiv);
    range.collapse(false);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);

    // Swap toolbar to Save / Cancel
    actionsBar.replaceChildren(...buildEditActions().childNodes);
    actionsBar.classList.add('always-visible');

    const handler = e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); finishEditing(true); }
        if (e.key === 'Escape') finishEditing(false);
    };
    contentDiv.addEventListener('keydown', handler);
    messageEl._editHandler = handler;
}

export function finishEditing(save) {
    if (!state.currentEdit) return;
    const { element: messageEl, contentDiv, actionsBar } = state.currentEdit;

    const rawText = save ? contentDiv.textContent.trim() : messageEl.dataset.originalText;
    if (save && !rawText) { state.currentEdit = null; return; }

    messageEl.classList.remove('editing');
    contentDiv.removeAttribute('contenteditable');
    contentDiv.removeEventListener('keydown', messageEl._editHandler);
    delete messageEl._editHandler;

    // Persist edit to backend
    if (save) {
        const id = messageEl.dataset.msgId;
        const originalText = messageEl.dataset.originalText;
        const stateMsg = id
            ? state.messages.find(m => String(m.id) === String(id))
            : state.messages.find(m => m.text === originalText);
        if (stateMsg?.id) {
            stateMsg.text = rawText;
            API.updateMessage(stateMsg.id, rawText).catch(err => {
                console.error('Edit save failed:', err);
                showToast('Edit failed to save: ' + err.message);
            });
        }
    }

    // Re-render markdown with updated text
    messageEl.dataset.rawText = rawText;
    renderMarkdown(contentDiv, rawText);
    delete messageEl.dataset.originalText;

    // Restore the correct toolbar (preserve swipe state)
    const role = messageEl.classList.contains('user') ? 'user' : 'character';
    const swipes = JSON.parse(messageEl.dataset.swipes || '[]');
    const activeIdx = parseInt(messageEl.dataset.activeSwipeIndex || '0', 10);
    const isGreeting = messageEl.dataset.isGreeting === 'true';
    actionsBar.replaceChildren(...buildMsgActions(role, swipes.length, activeIdx, isGreeting).childNodes);
    actionsBar.classList.remove('always-visible');

    state.currentEdit = null;
}
