import { state, el, icons, llm } from './state.js';
import { API } from './api.js';
import {
    applyAvatar, AVATAR, resolveTemplateVariables, showToast, showApiNotice,
    scrollToBottom, maybeScrollToBottom, showEmptyState, hideEmptyState,
    updateComposerState, setSendButtonMode, beginGeneration, endGeneration, whenGenerationEnds,
    branchesAt, displayChatName, parseDbStamp, fullStamp, shortStamp,
} from './utils.js';
import { parseThinkingContent, renderThinkingBlock } from './thinking.js';
import { updateContextViews, updateContextBoundary, placeContextBoundary } from './context-meter.js';
import { noteChatActivity } from './characters.js';
import { refreshOpenChat } from './chats.js';
import { generateResponse } from './request-builder.js';
import { ensureSummaryReadyForSend } from './summaries.js';
import { flushLLMSettingsSave } from './llm-settings.js';

// ═══════════════════════════════════════════════════════════════════════════
// CHAT — MESSAGES
// ═══════════════════════════════════════════════════════════════════════════

/** Message text with its ST-style variables filled in for this character and persona. */
function resolveMessageText(rawText) {
    const c = state.activeCharacter || {};
    const p = state.activePersona || {};
    return resolveTemplateVariables(rawText, {
        user:         p.name || 'User',
        char:         c.name || '',
        personality:  c.personality || '',
        scenario:     c.scenario || '',
        description:  c.description || '',
        persona:      p.description || '',
        mesExamples:  c.mes_example || '',
    });
}

/**
 * Render sanitised markdown into an element (resolves ST-style variables first).
 */
export function renderMarkdown(targetEl, rawText) {
    targetEl.innerHTML = DOMPurify.sanitize(marked.parse(resolveMessageText(rawText)));
}

/** A stored message as its bubble words it: variables filled in, reasoning left out. */
export function shownText(rawText) {
    const parsed = parseThinkingContent(rawText);
    return resolveMessageText(parsed.hasThinking ? parsed.response : rawText);
}

/** How long a stretch of streamed text takes to fade in. */
const STREAM_FADE_MS = 400;

/**
 * How often a streaming reply is redrawn. Each draw re-parses the whole reply
 * and lays its bubble out again, which is most of what streaming costs, so
 * drawing every frame kept the main thread half busy; the fade carries the eye
 * between draws.
 */
const STREAM_DRAW_MS = 45;

/**
 * Fade in the text each streaming draw adds. A draw rebuilds the whole bubble,
 * so the fade can't live on the nodes themselves: the fader remembers how far
 * the text reached at each draw and when, and wraps the stretches still fading
 * on every draw, each with its animation started that long ago so it carries on
 * from where the last draw left it.
 */
function createTextFader() {
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

/**
 * Draw a reply into its bubble as it streams, for a send and a new swipe alike.
 *
 * `onToken` is generateResponse's callback. `text` is everything that has
 * arrived, drawn or not: a Stop salvages that, not whatever is on screen.
 */
export function streamInto(msgBody, contentEl) {
    let text = '';
    // Draw at most once every STREAM_DRAW_MS, on a frame. A fast endpoint
    // delivers several tokens per frame, and every draw re-parses the whole
    // reply and then reads scrollHeight to scroll — a forced layout — so
    // drawing per token costs time quadratic in the reply length.
    let frame = 0;
    let drawnAt = -Infinity;
    // Reveal the reply toward what has arrived rather than all of it at once:
    // each draw shows a third of the backlog, so the pace follows the stream,
    // steady while tokens trickle and quicker when they burst.
    let shown = 0;
    const fadeIn = createTextFader();
    const draw = () => {
        if (performance.now() - drawnAt < STREAM_DRAW_MS) {
            frame = requestAnimationFrame(draw);
            return;
        }
        drawnAt = performance.now();
        frame = 0;
        shown += Math.max(1, Math.ceil((text.length - shown) / 3));
        const parsed = parseThinkingContent(text.slice(0, shown));
        renderThinkingBlock(msgBody, parsed);
        renderMarkdown(contentEl, parsed.response);
        fadeIn(contentEl);
        maybeScrollToBottom();
        if (shown < text.length) frame = requestAnimationFrame(draw);
    };
    return {
        onToken(accumulated) {
            text = accumulated;
            if (!frame) frame = requestAnimationFrame(draw);
        },
        get text() { return text; },
        /**
         * Wait for the reveal to catch up and the last words to finish fading
         * in, so the finished reply doesn't pop into place. Not in a hidden
         * tab, where frames stall and the save would wait on them.
         */
        async settle(signal) {
            while (frame && !signal.aborted && !document.hidden) await new Promise(requestAnimationFrame);
            if (!signal.aborted && !document.hidden) await new Promise(r => setTimeout(r, STREAM_FADE_MS));
        },
        /** Drop any draw still queued, so it can't repaint the bubble after the stream has ended. */
        stop() { cancelAnimationFrame(frame); },
    };
}

// The state.messages entry each bubble was drawn from. A bubble keeps no copy
// of its message: the text, the swipes and which of them is on show are read
// from the entry, so a redraw and the bubble it replaces cannot disagree.
export const drawnMessages = new WeakMap();

/** Put one of a message's swipes on show. Its text is only ever set from them. */
function setActiveSwipe(msg, idx) {
    msg.activeSwipeIndex = idx;
    msg.text = msg.swipes[idx].content;
}

function updateSwipeNav(msgEl, msg) {
    const nav = msgEl.querySelector('.swipe-nav');
    if (!nav) return;
    const idx = msg.activeSwipeIndex;
    const isGreeting = msgEl.dataset.isGreeting === 'true';
    nav.querySelector('.swipe-counter').textContent = `${idx + 1}/${msg.swipes.length}`;
    nav.querySelector('.swipe-prev').disabled = idx <= 0;
    const atEnd = idx >= msg.swipes.length - 1;
    const next = nav.querySelector('.swipe-next');
    next.disabled = isGreeting && atEnd;
    next.title = atEnd ? (isGreeting ? 'No more greetings' : 'Generate new') : 'Next';
    next.classList.toggle('swipe-generate', atEnd && !isGreeting);
    const oneSwipe = msg.swipes.length === 1;
    nav.querySelector('.swipe-prev').hidden = nav.querySelector('.swipe-counter').hidden = oneSwipe;
    nav.hidden = oneSwipe && isGreeting;
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
    renderMarkdown(contentEl, parsed.hasThinking ? parsed.response : text);
}

/** Ask for another take on a reply and put it on show. False when none came of it. */
export async function generateSwipe(msgEl) {
    if (!state.apiModel) {
        showApiNotice();
        return false;
    }
    if (!beginGeneration()) return false;

    try {
        return await receiveSwipe(msgEl);
    } finally {
        endGeneration();
    }
}

/**
 * Draw a new swipe into its bubble as the server writes it, and put it on show
 * once saved. `followed` reads a swipe already being written, one this page
 * was sent away from or another device asked for; without it one is asked for.
 * False when none came of it.
 */
export async function receiveSwipe(msgEl, followed = null) {
    const msg = drawnMessages.get(msgEl);
    const chatId = state.activeChat.id;
    const contentEl = msgEl.querySelector('.message-content');
    const msgBody = msgEl.querySelector('.msg-body');
    const prevThinkBlock = msgBody.querySelector('.thinking-block');
    if (prevThinkBlock) prevThinkBlock.remove();

    contentEl.innerHTML = '<div class="message-loading"><span></span><span></span><span></span></div>';
    // A swipe being rejoined came with the controller its stream was opened under.
    llm.abortController ??= new AbortController();
    const regenSignal = llm.abortController.signal;
    setSendButtonMode('stop');
    el.sendBtn.disabled = false;
    updateComposerState();

    let saved = null;
    const stream = streamInto(msgBody, contentEl);
    const read = followed || (onToken => generateResponse(1, onToken, regenSignal, chatId, msg.id));
    // The memory update and the reply can be pointed at different endpoints, so
    // an upstream error is only actionable if the toast says which one failed.
    let source = 'Settings could not be saved';
    try {
        if (!followed) {
            await flushLLMSettingsSave({ strict: true });
            source = 'Auto Summaries API';
            await ensureSummaryReadyForSend(regenSignal, { excludeLastN: 1 });
        }
        source = 'Chat API';
        saved = await read(stream.onToken);
        await stream.settle(regenSignal);
    } catch (err) {
        // The server keeps what it had of a swipe that an error cut off, as it
        // does for a Stop: a swipe of its own, so the previous one stays
        // reachable by swiping left. A lost connection ends neither the swipe
        // nor this page's interest in it.
        saved = err.saved;
        if (err.dropped) {
            whenGenerationEnds(() => refreshOpenChat(true));
        } else if (err.name !== 'AbortError') {
            console.error('Regen error:', err);
            showToast(`${saved ? 'Reply cut short. ' : ''}${source}: ${err.message}`);
        }
    } finally {
        // The bubble outlives the stream here, and a draw still queued would
        // repaint a partial stream over the saved swipe.
        stream.stop();
    }

    // Nothing kept: a Stop or an error before any of the response arrived, a
    // chat switch, or reasoning that never reached a response. Back to the
    // swipe that was on screen.
    if (!saved) {
        renderSwipeBody(msgBody, contentEl, msg.text);
        return false;
    }
    msg.swipes.push(saved);
    setActiveSwipe(msg, msg.swipes.length - 1);
    const parsed = parseThinkingContent(msg.text);
    renderThinkingBlock(msgBody, parsed);
    renderMarkdown(contentEl, parsed.response);
    updateSwipeNav(msgEl, msg);
    setEditedLabel(msgEl, saved.edited_at);
    updateContextViews();
    return true;
}

function showSwipe(msgEl, msg, idx) {
    setActiveSwipe(msg, idx);
    renderSwipeBody(msgEl.querySelector('.msg-body'), msgEl.querySelector('.message-content'), msg.text);
    if (msg.id) {
        API.selectSwipe(msg.id, idx).catch(err => {
            console.error('Failed to persist swipe selection:', err);
            showToast('Swipe selection failed to save: ' + err.message);
        });
    }
    updateContextViews();
}

export async function handleSwipeAction(msgEl, isPrev) {
    const msg = drawnMessages.get(msgEl);
    const idx = msg.activeSwipeIndex;

    if (isPrev || idx < msg.swipes.length - 1) {
        showSwipe(msgEl, msg, isPrev ? Math.max(0, idx - 1) : idx + 1);
    } else {
        // Past the last swipe is a new one. A greeting has none to make, and a
        // new swipe answers the end of the chat (generateResponse leaves out
        // only its last message), so only the newest reply can have one.
        if (msgEl.dataset.isGreeting === 'true' || msg !== state.messages.at(-1)) return;
        await generateSwipe(msgEl);
        return;
    }

    updateSwipeNav(msgEl, msg);
    setEditedLabel(msgEl, msg.swipes[msg.activeSwipeIndex].edited_at);
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
    while (last.activeSwipeIndex < last.swipes.length - 1) {
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
        const prev = buildSwipeButton('prev', 'Previous', idx <= 1);
        // One swipe has nothing to page through: a reply shows only the arrow
        // that makes a second, and a greeting without alternates nothing.
        const oneSwipe = swipeCount === 1;
        prev.hidden = counter.hidden = oneSwipe;
        nav.hidden = oneSwipe && isGreeting;
        nav.append(prev, counter, next);
        bar.append(nav);
    }
    appendMessageActionButtons(bar);
    if (role === 'character') {
        bar.append(iconButton('msg-action-btn fork-msg-btn', 'Fork', 'Fork chat from here', icons.FORK));
    }
    bar.append(iconButton('msg-action-btn delete-msg-btn', 'Delete (Shift+click skips the confirm)', 'Delete message', icons.TRASH));
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

/** Build the bubble for one state.messages entry (pure DOM construction, no side effects). */
function buildMessageEl(msg, isGreeting = false) {
    const { role, text } = msg;
    const char = state.activeCharacter;
    const p = role === 'user' ? (msg.persona || state.activePersona) : null;

    const container = document.createElement('div');
    container.className = `message-container ${role}`;

    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper';

    const message = document.createElement('div');
    message.className = `message ${role}`;
    drawnMessages.set(message, msg);

    const msgBody = document.createElement('div');
    msgBody.className = 'msg-body';

    const msgHeader = document.createElement('div');
    msgHeader.className = 'msg-header';
    const msgName = document.createElement('span');
    msgName.className = 'msg-name';
    msgName.textContent = role === 'user'
        ? (p?.name || 'You')
        : (char?.name || 'Character');
    // Your own bubble shows no picture; its name says which persona sent it.
    const msgTime = document.createElement('span');
    msgTime.className = 'msg-time';
    const sentAt = parseDbStamp(msg.created_at);
    msgTime.textContent = shortStamp(sentAt);
    msgTime.title = fullStamp(sentAt);
    const msgEdited = document.createElement('span');
    msgEdited.className = 'msg-edited';
    msgEdited.textContent = '(edited)';
    const actions = buildMsgActions(role, msg.swipes.length, msg.activeSwipeIndex, isGreeting);
    msgHeader.append(msgName, msgTime, msgEdited, actions);

    const content = document.createElement('div');
    content.className = 'message-content';

    const parsed = parseThinkingContent(text);
    renderMarkdown(content, parsed.hasThinking ? parsed.response : text);

    msgBody.append(msgHeader, content);

    if (parsed.hasThinking) renderThinkingBlock(msgBody, parsed);
    const branchPill = msg.id && buildBranchPill(msg.id);
    if (branchPill) msgBody.append(branchPill);
    if (role === 'character') {
        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'avatar';
        applyAvatar(avatarDiv, char, '?', AVATAR.SM);
        message.append(avatarDiv);
    }
    message.append(msgBody);
    wrapper.append(message);
    setEditedLabel(message, msg.swipes[msg.activeSwipeIndex].edited_at);

    if (isGreeting) {
        message.dataset.isGreeting = 'true';
    }
    if (msg.id) message.dataset.msgId = msg.id;

    container.append(wrapper);
    return { container, message };
}

// A long chat is drawn from its newest end, a page at a time as the reader
// scrolls up: building every message at once took seconds on long chats and
// kept all of them in the DOM. state.messages still holds the whole chat, so
// context, memory and the request never see the difference.
// Pages stay small, since a hundred messages held an older phone's main thread
// for the best part of half a second. The first only has to cover the screen,
// and drawOlderNearTop() tops it up when short messages leave it shy; older
// ones are built mid-scroll.
const DRAW_PAGE = 40;
const OLDER_PAGE = 40;
// The state.messages array on screen, and the index of its oldest drawn entry.
let drawnList = null;
let drawnFrom = 0;

/** Message elements for state.messages[start, end), in order. */
function buildMessageRange(start, end) {
    const fragment = document.createDocumentFragment();
    for (let i = start; i < end; i += 1) {
        const msg = state.messages[i];
        fragment.appendChild(buildMessageEl(msg, i === 0 && msg.role === 'character').container);
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
        const { text } = drawnMessages.get(messageEl);
        const parsed = parseThinkingContent(text);
        renderMarkdown(
            messageEl.querySelector('.message-content'),
            parsed.hasThinking ? parsed.response : text,
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

/**
 * Draw a new message at the end of the chat and, unless `persist` is false,
 * save it and add it to state.messages. `saved` is its row when the server
 * holds it already, as it does a reply. Resolves with its container, or null
 * for a user message that could not be saved.
 */
export async function appendMessage(role, text, persist = true, saved = null) {
    hideEmptyState();
    const persona = role === 'user' ? state.activePersona : null;
    const msg = {
        role, text, swipes: [{ content: text }], activeSwipeIndex: 0,
        persona: persona ? { name: persona.name } : null,
    };
    const { container, message } = buildMessageEl(msg);
    el.chatHistory.appendChild(container);
    // Scroll before the save rather than after it. The browser paints while
    // the request is out, and a finished reply that came out taller than its
    // stream would sit pushed below the fold until then.
    maybeScrollToBottom();

    if (persist && state.activeChat) {
        const chat = state.activeChat;
        try {
            saved ??= await API.addMessage(chat.id, role, text, persona ? persona.id : null);
            // The server bumped the chat's updated_at too; the chats list reads it.
            chat.updated_at = saved.created_at;
            if (role === 'user') noteChatActivity(chat.character_id, saved.created_at);
            message.dataset.msgId = saved.id;
            msg.id = saved.id;
            msg.created_at = saved.created_at;
        } catch (err) {
            console.error('Could not save message:', err);
            showToast('Message failed to save: ' + err.message);
            // An unsaved bubble reads as sent and is gone on the next load, so
            // the sender gets the text back. A reply has nowhere to go back to.
            if (role === 'user') {
                container.remove();
                return null;
            }
        }
        state.messages.push(msg);
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
    messageEl.classList.add('editing');
    state.currentEdit = { element: messageEl, contentDiv, actionsBar };

    const { text } = drawnMessages.get(messageEl);
    const editParsed = parseThinkingContent(text);
    contentDiv.textContent = editParsed.hasThinking ? editParsed.response : text;
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

    const msg = drawnMessages.get(messageEl);
    const editedResponse = save ? contentDiv.textContent.trim() : null;
    if (save && !editedResponse) return;

    if (save) {
        // Reattach the original thinking segment so it persists through the edit
        const { thinkingSegment } = parseThinkingContent(msg.text);
        const rawText = thinkingSegment ? thinkingSegment + '\n\n' + editedResponse : editedResponse;
        const idx = msg.activeSwipeIndex;
        // Saving the text unchanged is not an edit, as the server agrees.
        if (rawText !== msg.text) {
            const editedAt = new Date().toISOString().slice(0, 19).replace('T', ' ');
            msg.swipes[idx] = { ...msg.swipes[idx], content: rawText, edited_at: editedAt };
            setActiveSwipe(msg, idx);
        }
        if (msg.id) {
            API.updateMessage(msg.id, rawText, idx).catch(err => {
                console.error('Edit save failed:', err);
                showToast('Edit failed to save: ' + err.message);
            });
        }
    }

    const role = messageEl.classList.contains('user') ? 'user' : 'character';

    // Re-render both parts from the same parsed result. This also removes a
    // stale block if an edit/cancel follows an interrupted reasoning stream.
    const finalParsed = parseThinkingContent(msg.text);
    renderThinkingBlock(messageEl.querySelector('.msg-body'), finalParsed, { collapse: true });
    renderMarkdown(contentDiv, finalParsed.hasThinking ? finalParsed.response : msg.text);

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
    const isGreeting = messageEl.dataset.isGreeting === 'true';
    actionsBar.replaceChildren(
        ...buildMsgActions(role, msg.swipes.length, msg.activeSwipeIndex, isGreeting).childNodes,
    );
    setEditedLabel(messageEl, msg.swipes[msg.activeSwipeIndex].edited_at);

    state.currentEdit = null;
    updateContextBoundary();
}
