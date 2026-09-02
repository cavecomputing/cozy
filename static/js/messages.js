import { state, el, icons, llm } from './state.js';
import { API } from './api.js';
import {
    applyAvatar, AVATAR, resolveTemplateVariables, showToast, showApiNotice,
    scrollToBottom, maybeScrollToBottom, showEmptyState, hideEmptyState,
    updateComposerState, setSendButtonMode, beginGeneration, endGeneration,
} from './utils.js';
import {
    parseThinkingContent, renderThinkingBlock, hasVisibleResponse, closeIncompleteThinking,
} from './thinking.js';
import { updateContextMeter, updateContextBoundary } from './context-meter.js';
import { generateResponse } from './request-builder.js';
import { applyDisplayFilters, applyOutputFilters } from './regex-filters.js';
import { ensureSummaryReadyForSend } from './summaries.js';
import { flushLLMSettingsSave } from './llm-settings.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT — MESSAGES
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Render sanitised markdown into an element (resolves ST-style variables first).
 *
 * `applyDisplay` runs the display-only regex filters over the text on its way
 * to the screen. Callers pass it for character messages only, and pass the raw
 * stored text: the rewrite is thrown away with the DOM, so nothing here may
 * feed back into `dataset.rawText`, the DB or the next prompt.
 */
export function renderMarkdown(targetEl, rawText, applyDisplay = false) {
    const c = state.activeCharacter || {};
    const p = state.activePersona || {};
    const resolved = resolveTemplateVariables(applyDisplay ? applyDisplayFilters(rawText) : rawText, {
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

function updateSwipeNav(msgEl, swipes, idx, isGreeting) {
    const nav = msgEl.querySelector('.swipe-nav');
    if (!nav) return;
    nav.querySelector('.swipe-counter').textContent = `${idx + 1}/${swipes.length}`;
    nav.querySelector('.swipe-prev').disabled = idx <= 0;
    const atEnd = idx >= swipes.length - 1;
    const next = nav.querySelector('.swipe-next');
    next.disabled = isGreeting && atEnd;
    next.title = atEnd ? (isGreeting ? 'No more greetings' : 'Generate new') : 'Next';
}

/**
 * Render a message body from raw swipe text. Drops any existing thinking block
 * first — the text is a different swipe, so an old (or half-streamed) block
 * must not survive into it.
 */
function renderSwipeBody(msgBody, contentEl, text) {
    const parsed = parseThinkingContent(text);
    msgBody.querySelector('.thinking-block')?.remove();
    renderThinkingBlock(msgBody, parsed);
    renderMarkdown(contentEl, parsed.hasThinking ? parsed.response : text, true);
}

export async function generateSwipe(msgEl, swipes, idx) {
    if (!state.apiModel) {
        showApiNotice();
        return null;
    }
    if (!beginGeneration()) return null;

    try {
        return await generateSwipeOnce(msgEl, swipes, idx);
    } finally {
        llm.abortController = null;
        llm.stopRequested = false;
        setSendButtonMode('send');
        endGeneration();
        updateComposerState();
    }
}

async function generateSwipeOnce(msgEl, swipes, idx) {
    const stateMsg = findStateMsg(swipes, msgEl);
    const msgId = stateMsg?.id;
    const contentEl = msgEl.querySelector('.message-content');
    const msgBody = msgEl.querySelector('.msg-body');
    const prevThinkBlock = msgBody.querySelector('.thinking-block');
    if (prevThinkBlock) prevThinkBlock.remove();

    contentEl.innerHTML = '<div class="message-loading"><span></span><span></span><span></span></div>';
    llm.abortController = new AbortController();
    llm.stopRequested = false;
    const regenSignal = llm.abortController.signal;
    setSendButtonMode('stop');
    el.sendBtn.disabled = false;
    updateComposerState();

    let newContent;
    // Kept in step with the stream so a Stop can still salvage it.
    let streamed = '';
    // The memory update and the reply can be pointed at different endpoints, so
    // an upstream error is only actionable if the toast says which one failed.
    let source = 'Settings could not be saved';
    try {
        await flushLLMSettingsSave({ strict: true });
        source = 'Auto Summaries API';
        await ensureSummaryReadyForSend(regenSignal, { excludeLastN: 1 });
        source = 'Chat API';
        newContent = await generateResponse(1, (accumulated) => {
            streamed = accumulated;
            const parsed = parseThinkingContent(accumulated);
            renderThinkingBlock(msgBody, parsed);
            renderMarkdown(contentEl, parsed.response, true);
            maybeScrollToBottom();
        }, regenSignal);
    } catch (err) {
        if (err.name !== 'AbortError') {
            console.error('Regen error:', err);
            showToast(`${source}: ${err.message}`);
        }
        // An explicit Stop keeps its partial as a swipe of its own, so the
        // previous one stays reachable by swiping left. Anything else — an
        // error, a chat switch, or reasoning that never reached a response —
        // falls back to the swipe that was on screen.
        const kept = err.name === 'AbortError' && llm.stopRequested && hasVisibleResponse(streamed)
            ? closeIncompleteThinking(streamed)
            : '';
        if (!kept) {
            renderSwipeBody(msgBody, contentEl, swipes[idx]?.content || '');
            return null;
        }
        newContent = kept;
    }

    // Filter before rendering so the swipe on screen matches the one stored.
    newContent = applyOutputFilters(newContent);
    const parsed = parseThinkingContent(newContent);
    renderThinkingBlock(msgBody, parsed);
    renderMarkdown(contentEl, parsed.response, true);

    swipes.push({ content: newContent });
    idx = swipes.length - 1;
    msgEl.dataset.swipes = JSON.stringify(swipes);
    msgEl.dataset.activeSwipeIndex = idx;
    msgEl.dataset.rawText = newContent;

    if (msgId) {
        API.addSwipe(msgId, newContent).catch(err => {
            console.error('Swipe save failed:', err);
            showToast('Swipe failed to save: ' + err.message);
        });
    }
    if (stateMsg) {
        stateMsg.text = newContent;
        stateMsg.activeSwipeIndex = idx;
    }
    updateContextMeter();
    updateContextBoundary();
    return idx;
}

function showSwipe(msgEl, swipes, idx) {
    const newText = swipes[idx].content;
    const contentEl = msgEl.querySelector('.message-content');
    const msgBody = msgEl.querySelector('.msg-body');
    msgEl.dataset.rawText = newText;
    renderSwipeBody(msgBody, contentEl, newText);

    const stateMsg = findStateMsg(swipes, msgEl);
    if (stateMsg) {
        stateMsg.text = newText;
        stateMsg.activeSwipeIndex = idx;
        if (stateMsg.id) {
            API.updateMessage(stateMsg.id, newText).catch(err => {
                console.error('Failed to persist swipe selection:', err);
                showToast('Swipe selection failed to save: ' + err.message);
            });
        }
    }
    updateContextMeter();
    updateContextBoundary();
}

export async function handleSwipeAction(msgEl, isPrev) {
    const swipes = JSON.parse(msgEl.dataset.swipes || '[]');
    let idx = parseInt(msgEl.dataset.activeSwipeIndex || '0', 10);
    const isGreeting = msgEl.dataset.isGreeting === 'true';

    if (!isPrev && idx >= swipes.length - 1 && !isGreeting) {
        const generatedIdx = await generateSwipe(msgEl, swipes, idx);
        if (generatedIdx == null) return;
        idx = generatedIdx;
    } else if (!isPrev && idx >= swipes.length - 1) {
        return;
    } else {
        idx = isPrev ? Math.max(0, idx - 1) : Math.min(swipes.length - 1, idx + 1);
        msgEl.dataset.activeSwipeIndex = idx;
        showSwipe(msgEl, swipes, idx);
    }

    updateSwipeNav(msgEl, swipes, idx, isGreeting);
}

export async function regenerateLastAssistantMessage() {
    if (!state.activeChat) {
        showToast('Select a chat first');
        return;
    }
    const last = [...state.messages].reverse().find(m => m.role === 'character');
    if (!last?.id) {
        showToast('No assistant message to retry yet');
        return;
    }
    const msgEl = el.chatHistory.querySelector(`.message.character[data-msg-id="${last.id}"]`);
    if (!msgEl || msgEl.dataset.isGreeting === 'true') {
        showToast('No assistant message to retry yet');
        return;
    }
    const swipes = JSON.parse(msgEl.dataset.swipes || '[]');
    while (parseInt(msgEl.dataset.activeSwipeIndex || '0', 10) < swipes.length - 1) {
        await handleSwipeAction(msgEl, false);
    }
    await handleSwipeAction(msgEl, false);
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
    if (role === 'character') {
        bar.append(iconButton('msg-action-btn fork-msg-btn', 'Fork', 'Fork chat from here', icons.FORK));
    }
    bar.append(iconButton('msg-action-btn delete-msg-btn', 'Delete', 'Delete message', icons.TRASH));
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

/** Build a message DOM element (pure DOM construction, no side effects). */
function buildMessageEl(role, text, isGreeting = false, timestamp = null, swipes = null, activeSwipeIndex = 0, persona = null, msgId = null) {
    const char = state.activeCharacter;
    const p = role === 'user' ? (persona || state.activePersona) : null;

    const container = document.createElement('div');
    container.className = `message-container ${role}`;

    const avatarDiv = document.createElement('div');
    if (role === 'user') {
        avatarDiv.className = 'avatar user-avatar';
        applyAvatar(avatarDiv, p, 'ME', AVATAR.SM);
    } else {
        avatarDiv.className = 'avatar';
        applyAvatar(avatarDiv, char, '?', AVATAR.SM);
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
    renderMarkdown(content, parsed.hasThinking ? parsed.response : text, role !== 'user');

    msgBody.append(msgHeader, headerDivider, content);

    if (parsed.hasThinking) renderThinkingBlock(msgBody, parsed);
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
    if (!state.activeChat) {
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
    updateContextBoundary();
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
            showToast('Message failed to save: ' + err.message);
            state.messages.push({ role, text, swipes: [{ content: text }], activeSwipeIndex: 0,
                persona: p ? { name: p.name, avatar_url: p.avatar_url } : null });
        }
    }

    if (role === 'user') scrollToBottom(); else maybeScrollToBottom();
    updateContextMeter();
    updateContextBoundary();
    return container;
}

// ═══════════════════════════════════════════════════════════════════════════
// MESSAGE EDITING
// ═══════════════════════════════════════════════════════════════════════════
export function startEditing(messageEl) {
    if (messageEl.classList.contains('editing')) return;
    const contentDiv = messageEl.querySelector('.message-content');
    const actionsBar = messageEl.closest('.message-wrapper').querySelector('.msg-actions');

    // Show raw markdown for editing — response only, thinking stays in its block
    messageEl.dataset.originalText = messageEl.dataset.rawText;
    messageEl.classList.add('editing');
    state.currentEdit = { element: messageEl, contentDiv, actionsBar };

    const editParsed = parseThinkingContent(messageEl.dataset.rawText);
    contentDiv.textContent = editParsed.hasThinking ? editParsed.response : messageEl.dataset.rawText;
    contentDiv.contentEditable = 'plaintext-only';
    // preventScroll: don't let the browser "scroll the focused element into
    // view". The message sits inside the #chat-scroll container, and on iOS
    // WebKit that focus-scroll overshoots and yanks the whole page up (the
    // composer, a plain textarea outside any scroll container, never triggers
    // this — which is why composing was fine but editing shoved the screen).
    contentDiv.focus({ preventScroll: true });
    // Place cursor at end
    const range = document.createRange();
    range.selectNodeContents(contentDiv);
    range.collapse(false);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);

    // Swap toolbar to Save / Cancel
    actionsBar.replaceChildren(...buildEditActions().childNodes);

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

    const originalText = messageEl.dataset.originalText;
    const originalParsed = parseThinkingContent(originalText);
    const editedResponse = save ? contentDiv.textContent.trim() : null;
    if (save && !editedResponse) return;
    // Reattach the original thinking segment so it persists through the edit
    const rawText = save
        ? (originalParsed.thinkingSegment
            ? originalParsed.thinkingSegment + '\n\n' + editedResponse
            : editedResponse)
        : originalText;

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

        // Sync the active swipe so swiping away and back keeps the edit
        const editSwipes = JSON.parse(messageEl.dataset.swipes || '[]');
        const editIdx = parseInt(messageEl.dataset.activeSwipeIndex || '0', 10);
        if (editSwipes[editIdx]) {
            editSwipes[editIdx] = { ...editSwipes[editIdx], content: rawText };
            messageEl.dataset.swipes = JSON.stringify(editSwipes);
        }
        if (stateMsg) {
            stateMsg.text = rawText;
            if (stateMsg.swipes?.[editIdx]) {
                stateMsg.swipes[editIdx] = { ...stateMsg.swipes[editIdx], content: rawText };
            }
            if (stateMsg.id) {
                API.updateMessage(stateMsg.id, rawText, true, editIdx).catch(err => {
                    console.error('Edit save failed:', err);
                    showToast('Edit failed to save: ' + err.message);
                });
            }
        }
    }

    const role = messageEl.classList.contains('user') ? 'user' : 'character';

    // Re-render both parts from the same parsed result. This also removes a
    // stale block if an edit/cancel follows an interrupted reasoning stream.
    messageEl.dataset.rawText = rawText;
    const finalParsed = parseThinkingContent(rawText);
    renderThinkingBlock(messageEl.querySelector('.msg-body'), finalParsed, { collapse: true });
    renderMarkdown(
        contentDiv, finalParsed.hasThinking ? finalParsed.response : rawText, role !== 'user'
    );
    delete messageEl.dataset.originalText;

    // Restore the correct toolbar (preserve swipe state)
    const swipes = JSON.parse(messageEl.dataset.swipes || '[]');
    const activeIdx = parseInt(messageEl.dataset.activeSwipeIndex || '0', 10);
    const isGreeting = messageEl.dataset.isGreeting === 'true';
    actionsBar.replaceChildren(...buildMsgActions(role, swipes.length, activeIdx, isGreeting).childNodes);

    state.currentEdit = null;
    updateContextBoundary();
}
