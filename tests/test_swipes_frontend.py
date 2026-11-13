"""Paging through a reply's swipes, and editing the one on screen.

A message's swipes are held in three places: the server, `state.messages`, and
JSON on the bubble (`data-swipes`). These pin what each of them is told when a
swipe is shown or rewritten, so a change to how swipes are held can be shown to
leave that alone.
"""

from helpers import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import {
        handleSwipeAction, startEditing, finishEditing, flushEdit,
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
        { id: 1, role: 'user', text: 'hello there' },
        { id: 2, role: 'character', text: 'second take', swipes: takes(), activeSwipeIndex: 1 },
    ];

    const updates = [];
    API.updateMessage = async (...args) => { updates.push(args); return {}; };
    const selections = [];
    API.selectSwipe = async (...args) => { selections.push(args); return {}; };
    let generations = 0;
    API.streamChatCompletion = async () => { generations += 1; return 'a new take'; };
    API.addSwipe = async () => ({});

    /** A bubble as buildMessageEl() leaves it, as far as these paths read it. */
    function bubble(role, dataset) {
        const classes = new Set(['message', role]);
        const parts = {
            '.message-content': stubEl(),
            '.msg-body': { querySelector() { return null; }, insertBefore() {} },
            '.msg-edited': stubEl(),
        };
        const actionsBar = stubEl();
        return {
            dataset, style: {}, parts, actionsBar,
            classList: {
                add: name => classes.add(name),
                remove: name => classes.delete(name),
                contains: name => classes.has(name),
            },
            querySelector: selector => parts[selector] ?? null,
            closest: () => ({ querySelector: () => actionsBar }),
            getBoundingClientRect: () => ({ width: 200 }),
        };
    }
    const reply = bubble('character', {
        msgId: '2',
        rawText: 'second take',
        swipes: JSON.stringify(takes()),
        activeSwipeIndex: '1',
    });
    const shown = () => JSON.parse(reply.dataset.swipes).map(swipe => swipe.content);
    const settle = () => new Promise(resolve => setTimeout(resolve, 0));
"""


def test_paging_back_and_forward_shows_and_saves_the_neighbouring_swipe():
    run_node_module(SETUP + r"""
        await handleSwipeAction(reply, true);

        assert.equal(reply.dataset.rawText, 'first take');
        assert.equal(reply.dataset.activeSwipeIndex, 0);
        assert.equal(state.messages[1].text, 'first take');
        assert.equal(state.messages[1].activeSwipeIndex, 0);
        // The server is told which swipe is on show by its place, not its text.
        assert.deepEqual(selections, [[2, 0]]);

        // Already at the first: stays there.
        await handleSwipeAction(reply, true);
        assert.equal(reply.dataset.activeSwipeIndex, 0);

        await handleSwipeAction(reply, false);
        await handleSwipeAction(reply, false);
        assert.equal(reply.dataset.rawText, 'third take');
        assert.equal(reply.dataset.activeSwipeIndex, 2);
        assert.equal(state.messages[1].text, 'third take');
        assert.equal(state.messages[1].activeSwipeIndex, 2);
        assert.deepEqual(selections.at(-1), [2, 2]);
        assert.deepEqual(updates, []);

        // Paging never changes the list or asks the model for anything.
        assert.deepEqual(shown(), ['first take', 'second take', 'third take']);
        assert.equal(generations, 0);
    """)


def test_next_on_the_last_swipe_of_the_newest_reply_generates_one():
    run_node_module(SETUP + r"""
        reply.dataset.activeSwipeIndex = '2';
        reply.dataset.rawText = 'third take';
        globalThis.requestAnimationFrame = fn => setTimeout(fn, 0);
        globalThis.cancelAnimationFrame = id => clearTimeout(id);

        await handleSwipeAction(reply, false);

        assert.equal(generations, 1);
        assert.deepEqual(shown(), ['first take', 'second take', 'third take', 'a new take']);
        assert.equal(reply.dataset.activeSwipeIndex, 3);
        assert.equal(state.messages[1].text, 'a new take');
    """)


def test_next_on_the_last_swipe_of_an_older_reply_does_nothing():
    """A new swipe answers the end of the chat, so only the newest reply gets one."""
    run_node_module(SETUP + r"""
        state.messages.push({ id: 3, role: 'user', text: 'and then?' });
        reply.dataset.activeSwipeIndex = '2';
        reply.dataset.rawText = 'third take';

        await handleSwipeAction(reply, false);

        assert.equal(generations, 0);
        assert.equal(reply.dataset.activeSwipeIndex, '2');
        assert.deepEqual(selections, []);
    """)


def test_next_on_the_last_greeting_does_nothing():
    run_node_module(SETUP + r"""
        reply.dataset.isGreeting = 'true';
        reply.dataset.activeSwipeIndex = '2';
        reply.dataset.rawText = 'third take';

        await handleSwipeAction(reply, false);

        assert.equal(generations, 0);
        assert.equal(reply.dataset.activeSwipeIndex, '2');
        assert.deepEqual(selections, []);
    """)


def test_an_edit_rewrites_the_swipe_on_screen_and_names_it_to_the_server():
    run_node_module(SETUP + r"""
        startEditing(reply);
        assert.equal(reply.parts['.message-content'].textContent, 'second take');
        reply.parts['.message-content'].textContent = '  second take, reworded  ';
        finishEditing(true);

        assert.equal(reply.dataset.rawText, 'second take, reworded');
        assert.deepEqual(shown(), ['first take', 'second take, reworded', 'third take']);
        assert.equal(state.messages[1].text, 'second take, reworded');
        assert.deepEqual(
            state.messages[1].swipes.map(swipe => swipe.content),
            ['first take', 'second take, reworded', 'third take'],
        );
        // Only the rewritten swipe is marked edited, in both copies.
        const edited = JSON.parse(reply.dataset.swipes).map(swipe => Boolean(swipe.edited_at));
        assert.deepEqual(edited, [false, true, false]);
        assert.deepEqual(state.messages[1].swipes.map(swipe => Boolean(swipe.edited_at)), edited);
        // The index goes along, because two swipes can hold the same text.
        assert.deepEqual(updates, [[2, 'second take, reworded', 1]]);
        assert.equal(state.currentEdit, null);
        assert.equal(reply.classList.contains('editing'), false);
    """)


def test_an_edit_keeps_the_reasoning_that_came_before_the_reply():
    run_node_module(SETUP + r"""
        const thought = '<think>weighing it up</think>\n\nsecond take';
        reply.dataset.rawText = thought;
        const withThought = takes();
        withThought[1].content = thought;
        reply.dataset.swipes = JSON.stringify(withThought);

        startEditing(reply);
        // Only the reply is put up for editing.
        assert.equal(reply.parts['.message-content'].textContent, 'second take');
        reply.parts['.message-content'].textContent = 'a different answer';
        finishEditing(true);

        assert.equal(reply.dataset.rawText, '<think>weighing it up</think>\n\na different answer');
        assert.deepEqual(updates, [[2, '<think>weighing it up</think>\n\na different answer', 1]]);
    """)


def test_saving_an_edit_unchanged_does_not_mark_the_swipe_edited():
    run_node_module(SETUP + r"""
        startEditing(reply);
        finishEditing(true);

        assert.deepEqual(JSON.parse(reply.dataset.swipes), takes());
        assert.deepEqual(state.messages[1].swipes, takes());
        assert.deepEqual(updates, [[2, 'second take', 1]]);
    """)


def test_cancelling_an_edit_changes_and_sends_nothing():
    run_node_module(SETUP + r"""
        startEditing(reply);
        reply.parts['.message-content'].textContent = 'typed, then thought better of';
        finishEditing(false);

        assert.equal(reply.dataset.rawText, 'second take');
        assert.deepEqual(JSON.parse(reply.dataset.swipes), takes());
        assert.deepEqual(state.messages[1].swipes, takes());
        assert.equal(state.messages[1].text, 'second take');
        assert.deepEqual(updates, []);
        assert.equal(state.currentEdit, null);
    """)


def test_an_emptied_edit_stays_open_until_it_is_flushed_back():
    run_node_module(SETUP + r"""
        startEditing(reply);
        reply.parts['.message-content'].textContent = '   ';
        finishEditing(true);
        // Nothing to save, so the editor stays open for more typing.
        assert.equal(state.currentEdit.element, reply);
        assert.equal(reply.classList.contains('editing'), true);

        // Leaving the chat puts the original back, since nothing can be saved.
        flushEdit();
        assert.equal(state.currentEdit, null);
        assert.equal(reply.dataset.rawText, 'second take');
        assert.deepEqual(updates, []);
    """)


def test_opening_a_second_edit_saves_the_first():
    """state.currentEdit is one slot; a second edit used to strand the first."""
    run_node_module(SETUP + r"""
        const question = bubble('user', { msgId: '1', rawText: 'hello there' });
        startEditing(question);
        question.parts['.message-content'].textContent = 'hello again';
        startEditing(reply);

        assert.equal(question.dataset.rawText, 'hello again');
        assert.equal(question.classList.contains('editing'), false);
        assert.equal(state.messages[0].text, 'hello again');
        // A message with no swipes of its own still names index 0.
        assert.deepEqual(updates, [[1, 'hello again', 0]]);
        assert.equal(state.currentEdit.element, reply);
    """)
