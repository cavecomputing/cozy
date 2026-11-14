"""Paging through a reply's swipes, and editing the one on screen.

A message's swipes are held by the server and, in the page, by its entry in
`state.messages`. The bubble keeps no copy: `drawnMessages` links it to the
entry it was drawn from. These pin what the entry and the server are told when
a swipe is shown or rewritten.
"""

from helpers import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import {
        handleSwipeAction, startEditing, finishEditing, flushEdit, drawnMessages,
    } from './static/js/messages.js';

    globalThis.DOMPurify = { sanitize: value => value };
    globalThis.marked = { parse: value => value };
    function stubEl() {
        return {
            className: '', textContent: '', innerHTML: '', type: '', hidden: false, title: '',
            childNodes: [], style: {},
            remove() {}, setAttribute() {}, removeAttribute() {}, append() {}, appendChild() {},
            insertBefore() {}, replaceChildren() {}, addEventListener() {},
            removeEventListener() {}, focus() {}, blur() {},
            querySelector() { return stubEl(); },
            classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        };
    }
    globalThis.document = {
        createElement: stubEl,
        getElementById: stubEl,
        createRange: () => ({ selectNodeContents() {}, collapse() {} }),
    };
    globalThis.window = {
        matchMedia: () => ({ matches: false }),
        getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
    };

    Object.assign(el, {
        apiEndpoint: { value: 'http://main.example/v1' },
        settingsContextTokens: { value: '0' },
        sendBtn: { disabled: false, innerHTML: '', title: '', setAttribute() {}, classList: { toggle() {} } },
        chatHistory: { querySelector() { return null; }, insertBefore() {} },
        userInput: stubEl(),
    });
    state.apiModel = 'main-model';
    state.activeCharacter = { name: 'Mira' };
    state.activePersona = { name: 'Morgan' };
    state.systemPrompts = [{ id: 1, content: 'Reply as {{char}}.' }];
    state.activeSystemPromptId = 1;
    state.activeSamplers = new Set();
    state.contextMaxTokens = '0';
    state.activeChat = { id: 7, summary_enabled: false };
    state.chats = [state.activeChat];

    const takes = () => [{ content: 'first take' }, { content: 'second take' }, { content: 'third take' }];
    state.messages = [
        { id: 1, role: 'user', text: 'hello there', swipes: [{ content: 'hello there' }], activeSwipeIndex: 0 },
        { id: 2, role: 'character', text: 'second take', swipes: takes(), activeSwipeIndex: 1 },
    ];

    const updates = [];
    API.updateMessage = async (...args) => { updates.push(args); return {}; };
    const selections = [];
    API.selectSwipe = async (...args) => { selections.push(args); return {}; };
    let generations = 0;
    API.streamChatCompletion = async () => { generations += 1; return { content: 'a new take' }; };

    /** A bubble as buildMessageEl() leaves it, as far as these paths read it. */
    function bubble(msg) {
        const classes = new Set(['message', msg.role]);
        const dataset = { msgId: String(msg.id) };
        const parts = {
            '.message-content': stubEl(),
            '.msg-body': { querySelector() { return null; }, insertBefore() {} },
            '.msg-edited': stubEl(),
        };
        const actionsBar = stubEl();
        const msgEl = {
            dataset, style: {}, parts, actionsBar, isConnected: true,
            classList: {
                add: name => classes.add(name),
                remove: name => classes.delete(name),
                contains: name => classes.has(name),
            },
            querySelector: selector => parts[selector] ?? null,
            closest: () => ({ querySelector: () => actionsBar }),
            getBoundingClientRect: () => ({ width: 200 }),
        };
        drawnMessages.set(msgEl, msg);
        return msgEl;
    }
    const reply = state.messages[1];
    const replyEl = bubble(reply);
    const shown = () => reply.swipes.map(swipe => swipe.content);
    const settle = () => new Promise(resolve => setTimeout(resolve, 0));
"""


def test_paging_back_and_forward_shows_and_saves_the_neighbouring_swipe():
    run_node_module(SETUP + r"""
        await handleSwipeAction(replyEl, true);

        assert.equal(reply.text, 'first take');
        assert.equal(reply.activeSwipeIndex, 0);
        // The server is told which swipe is on show by its place, not its text.
        assert.deepEqual(selections, [[2, 0]]);

        // Already at the first: stays there.
        await handleSwipeAction(replyEl, true);
        assert.equal(reply.activeSwipeIndex, 0);

        await handleSwipeAction(replyEl, false);
        await handleSwipeAction(replyEl, false);
        assert.equal(reply.text, 'third take');
        assert.equal(reply.activeSwipeIndex, 2);
        assert.deepEqual(selections.at(-1), [2, 2]);
        assert.deepEqual(updates, []);

        // Paging never changes the list or asks the model for anything.
        assert.deepEqual(shown(), ['first take', 'second take', 'third take']);
        assert.equal(generations, 0);
    """)


def test_next_on_the_last_swipe_of_the_newest_reply_generates_one():
    run_node_module(SETUP + r"""
        reply.activeSwipeIndex = 2;
        reply.text = 'third take';
        globalThis.requestAnimationFrame = fn => setTimeout(fn, 0);
        globalThis.cancelAnimationFrame = id => clearTimeout(id);

        await handleSwipeAction(replyEl, false);

        assert.equal(generations, 1);
        assert.deepEqual(shown(), ['first take', 'second take', 'third take', 'a new take']);
        assert.equal(reply.activeSwipeIndex, 3);
        assert.equal(reply.text, 'a new take');
    """)


def test_next_on_the_last_swipe_of_an_older_reply_does_nothing():
    """A new swipe answers the end of the chat, so only the newest reply gets one."""
    run_node_module(SETUP + r"""
        state.messages.push({ id: 3, role: 'user', text: 'and then?' });
        reply.activeSwipeIndex = 2;
        reply.text = 'third take';

        await handleSwipeAction(replyEl, false);

        assert.equal(generations, 0);
        assert.equal(reply.activeSwipeIndex, 2);
        assert.deepEqual(selections, []);
    """)


def test_next_on_the_last_greeting_does_nothing():
    run_node_module(SETUP + r"""
        replyEl.dataset.isGreeting = 'true';
        reply.activeSwipeIndex = 2;
        reply.text = 'third take';

        await handleSwipeAction(replyEl, false);

        assert.equal(generations, 0);
        assert.equal(reply.activeSwipeIndex, 2);
        assert.deepEqual(selections, []);
    """)


def test_an_edit_rewrites_the_swipe_on_screen_and_names_it_to_the_server():
    run_node_module(SETUP + r"""
        startEditing(replyEl);
        assert.equal(replyEl.parts['.message-content'].textContent, 'second take');
        replyEl.parts['.message-content'].textContent = '  second take, reworded  ';
        finishEditing(true);

        assert.equal(reply.text, 'second take, reworded');
        assert.deepEqual(shown(), ['first take', 'second take, reworded', 'third take']);
        assert.equal(reply.activeSwipeIndex, 1);
        // Only the rewritten swipe is marked edited.
        assert.deepEqual(reply.swipes.map(swipe => Boolean(swipe.edited_at)), [false, true, false]);
        // The index goes along, because two swipes can hold the same text.
        assert.deepEqual(updates, [[2, 'second take, reworded', 1]]);
        assert.equal(state.currentEdit, null);
        assert.equal(replyEl.classList.contains('editing'), false);
    """)


def test_an_edit_that_cannot_be_saved_goes_back_and_reopens_on_what_was_typed():
    """The server kept the old text. Shown as saved, the new text only lasted
    until the next catch-up put the old one back without a word."""
    run_node_module(SETUP + r"""
        API.updateMessage = async () => { throw new Error('unreachable'); };
        // The toast's own timer would hold node open for its five seconds.
        const setTimer = globalThis.setTimeout;
        globalThis.setTimeout = (fn, ms) => { const timer = setTimer(fn, ms); if (ms >= 5000) timer.unref(); return timer; };

        startEditing(replyEl);
        replyEl.parts['.message-content'].textContent = 'second take, reworded';
        finishEditing(true);
        await settle();

        assert.equal(reply.text, 'second take');
        assert.deepEqual(shown(), ['first take', 'second take', 'third take']);
        assert.equal(Boolean(reply.swipes[1].edited_at), false);
        // Open again on what was typed, so Save can be pressed once more.
        assert.equal(state.currentEdit.element, replyEl);
        assert.equal(replyEl.classList.contains('editing'), true);
        assert.equal(replyEl.parts['.message-content'].textContent, 'second take, reworded');
    """)


def test_an_edit_keeps_the_reasoning_that_came_before_the_reply():
    run_node_module(SETUP + r"""
        const thought = '<think>weighing it up</think>\n\nsecond take';
        reply.text = thought;
        reply.swipes[1].content = thought;

        startEditing(replyEl);
        // Only the reply is put up for editing.
        assert.equal(replyEl.parts['.message-content'].textContent, 'second take');
        replyEl.parts['.message-content'].textContent = 'a different answer';
        finishEditing(true);

        assert.equal(reply.text, '<think>weighing it up</think>\n\na different answer');
        assert.deepEqual(updates, [[2, '<think>weighing it up</think>\n\na different answer', 1]]);
    """)


def test_saving_an_edit_unchanged_does_not_mark_the_swipe_edited():
    run_node_module(SETUP + r"""
        startEditing(replyEl);
        finishEditing(true);

        assert.deepEqual(reply.swipes, takes());
        assert.deepEqual(updates, [[2, 'second take', 1]]);
    """)


def test_cancelling_an_edit_changes_and_sends_nothing():
    run_node_module(SETUP + r"""
        startEditing(replyEl);
        replyEl.parts['.message-content'].textContent = 'typed, then thought better of';
        finishEditing(false);

        assert.equal(reply.text, 'second take');
        assert.deepEqual(reply.swipes, takes());
        assert.deepEqual(updates, []);
        assert.equal(state.currentEdit, null);
    """)


def test_an_emptied_edit_stays_open_until_it_is_flushed_back():
    run_node_module(SETUP + r"""
        startEditing(replyEl);
        replyEl.parts['.message-content'].textContent = '   ';
        finishEditing(true);
        // Nothing to save, so the editor stays open for more typing.
        assert.equal(state.currentEdit.element, replyEl);
        assert.equal(replyEl.classList.contains('editing'), true);

        // Leaving the chat puts the original back, since nothing can be saved.
        flushEdit();
        assert.equal(state.currentEdit, null);
        assert.equal(reply.text, 'second take');
        assert.deepEqual(updates, []);
    """)


def test_opening_a_second_edit_saves_the_first():
    """state.currentEdit is one slot; a second edit used to strand the first."""
    run_node_module(SETUP + r"""
        const questionEl = bubble(state.messages[0]);
        startEditing(questionEl);
        questionEl.parts['.message-content'].textContent = 'hello again';
        startEditing(replyEl);

        assert.equal(questionEl.classList.contains('editing'), false);
        assert.equal(state.messages[0].text, 'hello again');
        assert.deepEqual(state.messages[0].swipes.map(swipe => swipe.content), ['hello again']);
        assert.deepEqual(updates, [[1, 'hello again', 0]]);
        assert.equal(state.currentEdit.element, replyEl);
    """)
