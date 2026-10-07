import { state, el, icons, llm } from './state.js';
import { API } from './api.js';
import {
    applyAvatar, AVATAR, resolveTemplateVariables, showToast, showApiNotice,
    scrollToBottom, maybeScrollToBottom, showEmptyState, hideEmptyState,
    updateComposerState, setSendButtonMode, beginGeneration, endGeneration,
    branchesAt, displayChatName, parseDbStamp, fullStamp, shortStamp,
} from './utils.js';
import {
    parseThinkingContent, renderThinkingBlock, hasVisibleResponse, closeIncompleteThinking,
} from './thinking.js';
import { updateContextViews, updateContextBoundary, placeContextBoundary } from './context-meter.js';
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

/** How long a stretch of streamed text takes to fade in. */
export const STREAM_FADE_MS = 400;

/**
 * Fade in the text each streaming draw adds. A draw rebuilds the whole bubble,
 * so the fade can't live on the nodes themselves: the fader remembers how far
 * the text reached at each draw and when, and wraps the stretches still fading
 * on every draw, each with its animation started that long ago so it carries on
 * from where the last draw left it.
 */
export function createTextFader() {
    const reveals = [{ end: 0, at: 0 }];
    return contentEl => {
        const now = performance.now();
        const texts = [];
        let length = 0;
        const walker = document.createTreeWalker(contentEl, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            texts.push([node, length]);
            length += node.length;
        }
        if (length > reveals.at(-1).end) reveals.push({ end: length, at: now });
        while (reveals.length > 1 && now - reveals[1].at >= STREAM_FADE_MS) reveals.shift();

        // Back to front, so splitting a node leaves the offsets before it intact.
        for (let i = texts.length - 1; i >= 0; i--) {
            const [node, start] = texts[i];
            for (let k = reveals.length - 1; k >= 1; k--) {
                const from = Math.max(reveals[k - 1].end, start);
                const to = Math.min(reveals[k].end, start + node.length);
                if (from >= to) continue;
                const stretch = node.splitText(from - start);
                if (to - from < stretch.length) stretch.splitText(to - from);
                // Whitespace between blocks can sit where a span can't, in a list or table.
                if (!/\S/.test(stretch.data)) continue;
                const span = document.createElement('span');
                span.style.animation = `streamFade ${STREAM_FADE_MS}ms ease-out ${reveals[k].at - now}ms both`;
                stretch.replaceWith(span);
                span.append(stretch);
            }
        }
    };
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
    next.classList.toggle('swipe-generate', atEnd && !isGreeting);
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
    // Draw at most once per frame — see the same guard in send.js. Here the
    // bubble survives the stream, so a queued draw must also be cancelled: it
    // would otherwise repaint the unfiltered stream over the saved swipe.
    let frame = 0;
    // Revealed toward what has arrived, at the pace send.js sets.
    let shown = 0;
    const fadeIn = createTextFader();
    const drawStreamed = () => {
        frame = 0;
        shown += Math.max(1, Math.ceil((streamed.length - shown) / 8));
        const parsed = parseThinkingContent(streamed.slice(0, shown));
        renderThinkingBlock(msgBody, parsed);
        renderMarkdown(contentEl, parsed.response, true);
        fadeIn(contentEl);
        maybeScrollToBottom();
        if (shown < streamed.length) frame = requestAnimationFrame(drawStreamed);
    };
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
            if (!frame) frame = requestAnimationFrame(drawStreamed);
        }, regenSignal);
        // Let the reveal catch up and the last words finish fading before the
        // swipe is drawn plain, as send.js does.
        while (frame && !regenSignal.aborted && !document.hidden) await new Promise(requestAnimationFrame);
        if (!regenSignal.aborted && !document.hidden) await new Promise(r => setTimeout(r, STREAM_FADE_MS));
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
    } finally {
        cancelAnimationFrame(frame);
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
    updateContextViews();
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
    updateContextViews();
}

export async function handleSwipeAction(msgEl, isPrev) {
    const swipes = JSON.parse(msgEl.dataset.swipes || '[]');
    let idx = parseInt(msgEl.dataset.activeSwipeIndex || '0', 10);
    const isGreeting = msgEl.dataset.isGreeting === 'true';

    if (!isPrev && idx >= swipes.length - 1 && !isGreeting) {
        // A new swipe answers the end of the chat (generateResponse leaves out
        // only its last message), so only the newest reply can have one.
        if (findStateMsg(swipes, msgEl) !== state.messages.at(-1)) return;
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
    setEditedLabel(msgEl, swipes[idx]?.edited_at);
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
    if (last !== state.messages.at(-1)) {
        showToast('Your last message has no reply yet. Press Send to get one.');
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

        const next = buildSwipeButton('next', nextTitle, nextDisabled);
        next.classList.toggle('swipe-generate', atEnd && !isGreeting);
        nav.append(buildSwipeButton('prev', 'Previous', idx <= 1), counter, next);
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

/** Show "(edited)" while the swipe on screen is one the reader has rewritten. */
function setEditedLabel(msgEl, editedAt) {
    const label = msgEl.querySelector('.msg-edited');
    label.hidden = !editedAt;
    label.title = editedAt ? `Edited ${fullStamp(parseDbStamp(editedAt))}` : '';
}

/** The button under a message that other chats branch at, or null: it opens the next of them. */
function buildBranchPill(msgId) {
    const branches = branchesAt(state.chats, state.activeChat?.id, msgId);
    if (!branches.length) return null;
    const at = branches.findIndex(b => b.chatId === state.activeChat.id);
    const next = state.chats.find(c => c.id === branches[(at + 1) % branches.length].chatId);
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'branch-pill';
    pill.title = `Branch ${at + 1} of ${branches.length}. Next: ${displayChatName(next)}`;
    pill.setAttribute('aria-label', `Branch ${at + 1} of ${branches.length}, open the next one: ${displayChatName(next)}`);
    pill.innerHTML = `${icons.FORK}<span>${at + 1}/${branches.length}</span>`;
    return pill;
}

/** Redraw the branch pills on screen, for when the chat list changes under an open chat. */
export function refreshBranchPills() {
    el.chatHistory.querySelectorAll('.branch-pill').forEach(pill => pill.remove());
    el.chatHistory.querySelectorAll('.message[data-msg-id]').forEach(msgEl => {
        const pill = buildBranchPill(Number(msgEl.dataset.msgId));
        if (pill) msgEl.querySelector('.msg-body').append(pill);
    });
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
    const sentAt = parseDbStamp(timestamp);
    msgTime.textContent = shortStamp(sentAt);
    msgTime.title = fullStamp(sentAt);
    const msgEdited = document.createElement('span');
    msgEdited.className = 'msg-edited';
    msgEdited.textContent = '(edited)';
    const msgSwipes = swipes || [{ content: text }];
    const actions = buildMsgActions(role, msgSwipes.length, activeSwipeIndex, isGreeting);
    msgHeader.append(msgName, msgTime, msgEdited, actions);

    message.dataset.swipes = JSON.stringify(msgSwipes);
    message.dataset.activeSwipeIndex = activeSwipeIndex;

    const content = document.createElement('div');
    content.className = 'message-content';

    const parsed = parseThinkingContent(text);
    renderMarkdown(content, parsed.hasThinking ? parsed.response : text, role !== 'user');

    msgBody.append(msgHeader, content);

    if (parsed.hasThinking) renderThinkingBlock(msgBody, parsed);
    const branchPill = msgId && buildBranchPill(msgId);
    if (branchPill) msgBody.append(branchPill);
    message.append(avatarDiv, msgBody);
    wrapper.append(message);
    setEditedLabel(message, msgSwipes[activeSwipeIndex]?.edited_at);

    if (isGreeting) {
        message.dataset.isGreeting = 'true';
    }
    if (msgId) message.dataset.msgId = msgId;

    container.append(wrapper);
    return { container, message };
}

// A long chat is drawn from its newest end, a page at a time as the reader
// scrolls up: building every message at once took seconds on long chats and
// kept all of them in the DOM. state.messages still holds the whole chat, so
// context, memory and the request never see the difference.
const DRAW_PAGE = 100;
// Older pages are smaller: each is built mid-scroll, where a hundred messages
// held an older phone's main thread for the best part of half a second.
const OLDER_PAGE = 40;
// The state.messages array on screen, and the index of its oldest drawn entry.
let drawnList = null;
let drawnFrom = 0;

/** Message elements for state.messages[start, end), in order. */
function buildMessageRange(start, end) {
    const fragment = document.createDocumentFragment();
    for (let i = start; i < end; i += 1) {
        const m = state.messages[i];
        const isFirstMsg = i === 0 && m.role === 'character';
        const { container } = buildMessageEl(
            m.role, m.text, isFirstMsg, m.created_at,
            m.swipes, m.activeSwipeIndex, m.persona, m.id
        );
        fragment.appendChild(container);
    }
    return fragment;
}

/**
 * Draw the page of messages just above the oldest one on screen, holding the
 * view still. Returns false when there is nothing older left to draw.
 */
export function drawOlderMessages() {
    // A chat switch replaces the array; what is on screen is about to go.
    if (drawnList !== state.messages || drawnFrom <= 0) return false;
    const scroller = el.chatHistory;
    const start = Math.max(0, drawnFrom - OLDER_PAGE);
    const anchor = scroller.querySelector('.message-container');
    const anchorTop = anchor?.getBoundingClientRect().top ?? 0;
    scroller.prepend(buildMessageRange(start, drawnFrom));
    drawnFrom = start;
    // Before measuring: a separator landing in the new page pushes the view down too.
    placeContextBoundary();
    // Measured rather than assumed from scrollHeight: browsers with scroll
    // anchoring have already compensated by now, and adding the height again
    // would throw the reader a page down.
    scroller.scrollTop += (anchor?.getBoundingClientRect().top ?? 0) - anchorTop;
    return true;
}

/**
 * Scroll message `id` to `offset` px below the top of the transcript, drawing
 * older pages down to it first. False when this chat has no such message.
 */
export function revealMessage(id, offset = 0) {
    const scroller = el.chatHistory;
    const drawn = () => scroller.querySelector(`.message[data-msg-id="${id}"]`);
    while (!drawn() && drawOlderMessages()) { /* next page */ }
    const msgEl = drawn();
    if (!msgEl) return false;
    scroller.scrollTop += msgEl.getBoundingClientRect().top - scroller.getBoundingClientRect().top - offset;
    // As jumpToContextBoundary: an arriving token must not pull the view back down.
    state.autoScroll = false;
    return true;
}

/** Draw older pages until at least a screenful sits above the view, or none are left. */
export function drawOlderNearTop() {
    const scroller = el.chatHistory;
    while (scroller.scrollTop < scroller.clientHeight && drawOlderMessages()) { /* next page */ }
}

/**
 * Redraw the message bodies already on screen. {{user}} and the other template
 * variables are resolved at render time from state.activePersona, so switching
 * persona leaves every drawn message naming the old one until something
 * rebuilds them — a full renderMessages() would do it, but it also scrolls to
 * the bottom and drops a stream in progress.
 */
export function rerenderMessageText() {
    el.chatHistory.querySelectorAll('.message').forEach(messageEl => {
        // An open edit is showing raw text in a contenteditable, not a render.
        if (messageEl.classList.contains('editing')) return;
        const parsed = parseThinkingContent(messageEl.dataset.rawText || '');
        renderMarkdown(
            messageEl.querySelector('.message-content'),
            parsed.hasThinking ? parsed.response : (messageEl.dataset.rawText || ''),
            !messageEl.classList.contains('user'),
        );
    });
}

export function renderMessages() {
    el.chatHistory.querySelectorAll('.message-container').forEach(c => c.remove());
    drawnList = null;

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

    drawnList = state.messages;
    drawnFrom = Math.max(0, state.messages.length - DRAW_PAGE);
    el.chatHistory.appendChild(buildMessageRange(drawnFrom, state.messages.length));

    // Reset scroll-to-bottom button visibility — if there's no overflow,
    // no scroll event will fire so the button could stay stale from a previous chat.
    const atBottom =
        el.chatHistory.scrollHeight - el.chatHistory.scrollTop - el.chatHistory.clientHeight < 60;
    state.autoScroll = atBottom;
    el.scrollToBottomBtn?.classList.toggle('visible', !atBottom);
    scrollToBottom();
    // Short messages can leave the newest page without enough height to scroll,
    // and a transcript that cannot scroll never asks for the older pages.
    drawOlderNearTop();
    updateContextBoundary();
}

export async function appendMessage(role, text, persist = true, isGreeting = false, timestamp = null, swipes = null, activeSwipeIndex = 0, persona = null, msgId = null) {
    hideEmptyState();
    const p = role === 'user' ? (persona || state.activePersona) : null;
    const { container, message } = buildMessageEl(role, text, isGreeting, timestamp, swipes, activeSwipeIndex, persona, msgId);
    el.chatHistory.appendChild(container);
    // Scroll before the save rather than after it. The browser paints while
    // the request is out, and a finished reply that came out taller than its
    // stream (an output filter, say) would sit pushed below the fold until then.
    maybeScrollToBottom();

    if (persist && state.activeChat) {
        const chat = state.activeChat;
        const personaId = (role === 'user' && p) ? p.id : null;
        try {
            const saved = await API.addMessage(chat.id, role, text, personaId);
            // The server bumped the chat's updated_at too; the chats list reads it.
            chat.updated_at = saved.created_at;
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

    updateContextViews();
    return container;
}

// ═══════════════════════════════════════════════════════════════════════════
// MESSAGE EDITING
// ═══════════════════════════════════════════════════════════════════════════
export function startEditing(messageEl) {
    if (messageEl.classList.contains('editing')) return;
    // Only one message can be open at a time: state.currentEdit is a single
    // slot, and finishEditing acts on whatever it holds rather than on the
    // message whose button was clicked. Opening a second edit without closing
    // the first stranded the first one in .editing forever — its Save, Cancel
    // and Esc all landed on the newer message and then found the slot empty.
    flushEdit();
    const contentDiv = messageEl.querySelector('.message-content');
    const actionsBar = messageEl.closest('.message-wrapper').querySelector('.msg-actions');

    // A user bubble is only as wide as its text and its buttons, and editing
    // swaps both, so it would narrow or widen and slide everything in it
    // sideways. Hold the width it had until editing ends.
    if (messageEl.classList.contains('user')) {
        messageEl.style.width = `${messageEl.getBoundingClientRect().width}px`;
    }

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
        // Match the composer: on touch shells Enter is the on-screen keyboard's
        // line-break key, not a submit shortcut; desktop keeps Enter-to-save.
        if (e.key === 'Enter' && !e.shiftKey && !window.matchMedia('(pointer: coarse)').matches) { e.preventDefault(); finishEditing(true); }
        if (e.key === 'Escape') { e.preventDefault(); finishEditing(false); }
    };
    contentDiv.addEventListener('keydown', handler);
    messageEl._editHandler = handler;
}

/**
 * Commit any open edit before the view changes underneath it. Leaving a chat or
 * a character removes the message element, which would strand state.currentEdit
 * on a detached node and drop what was typed without a word.
 */
export function flushEdit() {
    if (!state.currentEdit) return;
    finishEditing(true);
    // An emptied field has nothing to save, and finishEditing bails before
    // tearing down — put the original text back instead of leaving it open.
    if (state.currentEdit) finishEditing(false);
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

    // Persist edit to backend
    if (save) {
        const id = messageEl.dataset.msgId;
        const stateMsg = id
            ? state.messages.find(m => String(m.id) === String(id))
            : state.messages.find(m => m.text === originalText);

        // Sync the active swipe so swiping away and back keeps the edit. Saving
        // the text unchanged is not an edit, as the server agrees.
        const editSwipes = JSON.parse(messageEl.dataset.swipes || '[]');
        const editIdx = parseInt(messageEl.dataset.activeSwipeIndex || '0', 10);
        const editedAt = new Date().toISOString().slice(0, 19).replace('T', ' ');
        const rewritten = swipe => ({
            ...swipe, content: rawText, ...(rawText !== originalText && { edited_at: editedAt }),
        });
        if (editSwipes[editIdx]) {
            editSwipes[editIdx] = rewritten(editSwipes[editIdx]);
            messageEl.dataset.swipes = JSON.stringify(editSwipes);
        }
        if (stateMsg) {
            stateMsg.text = rawText;
            if (stateMsg.swipes?.[editIdx]) {
                stateMsg.swipes[editIdx] = rewritten(stateMsg.swipes[editIdx]);
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

    // Only now leave the editor. Its raw text held in pre-wrap is what keeps
    // the message its full height; dropping that first collapsed the raw text
    // for a moment, and at the bottom of the chat the scroll position clamped
    // up to the shorter message and stayed there once the render grew it back.
    messageEl.classList.remove('editing');
    messageEl.style.width = '';
    contentDiv.removeAttribute('contenteditable');
    // Back to the composer, as Slack and Discord do when an edit ends; a touch
    // shell just lets go, since focusing the composer throws up the keyboard.
    // Either way focus leaves the text: Safari may otherwise keep it, and a
    // focused message holds its buttons up (the hover rule's :focus-visible
    // case in style.css).
    if (!el.userInput.disabled && !window.matchMedia('(pointer: coarse)').matches) {
        el.userInput.focus({ preventScroll: true });
    } else {
        contentDiv.blur();
    }
    contentDiv.removeEventListener('keydown', messageEl._editHandler);
    delete messageEl._editHandler;

    // Restore the correct toolbar (preserve swipe state)
    const swipes = JSON.parse(messageEl.dataset.swipes || '[]');
    const activeIdx = parseInt(messageEl.dataset.activeSwipeIndex || '0', 10);
    const isGreeting = messageEl.dataset.isGreeting === 'true';
    actionsBar.replaceChildren(...buildMsgActions(role, swipes.length, activeIdx, isGreeting).childNodes);
    setEditedLabel(messageEl, swipes[activeIdx]?.edited_at);

    state.currentEdit = null;
    updateContextBoundary();
}
