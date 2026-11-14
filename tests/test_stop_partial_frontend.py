"""How the page reads a reply the server is writing, and how it ends one.

The server keeps what arrived of a reply that a Stop or an error cut short
(tests/test_replies.py); the page's part is to ask for the stop, to land what
the stream says was saved, and to let go quietly when it only stops listening.
"""

from helpers import run_node_module


# ── api.js transport contract ──────────────────────────────────────────────

STREAM_SETUP = r"""
    import assert from 'node:assert/strict';
    import { API } from './static/js/api.js';

    const encoder = new TextEncoder();

    const requests = [];

    /**
     * Fake an SSE response that aborts as soon as the signal says so, and
     * fails with `dropAfter` once its chunks run out, if that is given.
     */
    function stubStream(chunks, dropAfter = null, { status = 200, swipeOf = null } = {}) {
        let next = 0;
        globalThis.fetch = async (url, options = {}) => {
            requests.push({ url, method: options.method });
            return {
                ok: true,
                status,
                headers: { get: name => (name === 'X-Swipe-Of' ? swipeOf : null) },
                body: {
                    getReader() {
                        return {
                            async read() {
                                if (options.signal?.aborted) {
                                    throw new DOMException('The operation was aborted.', 'AbortError');
                                }
                                if (next < chunks.length) return { done: false, value: encoder.encode(chunks[next++]) };
                                if (dropAfter) throw dropAfter;
                                return { done: true };
                            },
                        };
                    },
                },
            };
        };
    }
"""


def test_stream_rejects_on_abort_having_shown_what_arrived():
    """An abort only stops this page listening, so it has nothing to resolve with."""
    run_node_module(STREAM_SETUP + r"""
        stubStream([
            'data: {"choices":[{"delta":{"content":"Hello "}}]}\n',
            'data: {"choices":[{"delta":{"content":"world"}}]}\n',
        ]);

        const controller = new AbortController();
        const seen = [];
        await assert.rejects(
            API.streamChatCompletion(
                { model: 'm' },
                text => { seen.push(text); controller.abort(); },
                controller.signal, 7,
            ),
            err => err.name === 'AbortError' && !err.dropped,
        );
        assert.deepEqual(seen, ['Hello ']);
    """)


def test_stream_resolves_with_what_the_server_saved():
    run_node_module(STREAM_SETUP + r"""
        stubStream([
            'data: {"choices":[{"delta":{"reasoning_content":"weighing options"}}]}\n',
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            'data: [DONE]\n',
            'data: {"saved":{"id":9,"content":"<think>weighing options</think>Hello"}}\n',
        ]);
        const seen = [];
        const saved = await API.streamChatCompletion({ model: 'm' }, text => seen.push(text), null, 7);

        assert.deepEqual(saved, { id: 9, content: '<think>weighing options</think>Hello' });
        // Reasoning shows as an open block until the response starts.
        assert.deepEqual(seen, ['<think>weighing options', '<think>weighing options</think>Hello']);
        assert.equal(requests[0].url, '/api/llm/chat?chat_id=7');
        assert.equal(requests[0].method, 'POST');

        // A new swipe names the message it belongs to.
        stubStream(['data: [DONE]\n']);
        assert.equal(await API.streamChatCompletion({ model: 'm' }, () => {}, null, 7, 12), null);
        assert.equal(requests[1].url, '/api/llm/chat?chat_id=7&message_id=12');
    """)


def test_stream_says_why_a_reply_that_ended_early_stopped():
    """A reply cut at max_tokens, or by a dropped connection, looks finished."""
    run_node_module(STREAM_SETUP + r"""
        const toasts = [];
        globalThis.document = {
            createElement: () => ({ setAttribute() {}, remove() {} }),
            getElementById: () => ({ appendChild(toast) { toasts.push(toast.textContent); } }),
        };
        globalThis.setTimeout = () => 0;

        stubStream([
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            'data: [DONE]\n',
        ]);
        await API.streamChatCompletion({ model: 'm' }, () => {}, null, 7);
        assert.deepEqual(toasts, []);

        stubStream([
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
            'data: [DONE]\n',
        ]);
        await API.streamChatCompletion({ model: 'm' }, () => {}, null, 7);
        assert.match(toasts.pop(), /Max response tokens/);

        stubStream(['data: {"choices":[{"delta":{"content":"Hel"}}]}\n']);
        await API.streamChatCompletion({ model: 'm' }, () => {}, null, 7);
        assert.match(toasts.pop(), /connection closed/);
    """)


def test_stream_error_carries_what_was_saved_before_it():
    run_node_module(STREAM_SETUP + r"""
        stubStream([
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
            'data: {"saved":{"id":9,"content":"Hel"}}\n',
            'data: {"error":"Provider returned error"}\n',
        ]);
        await assert.rejects(
            API.streamChatCompletion({ model: 'm' }, () => {}, null, 7),
            err => err.message === 'Provider returned error' && err.saved.id === 9,
        );

        stubStream(['data: {"error":"429 Too Many Requests."}\n']);
        await assert.rejects(
            API.streamChatCompletion({ model: 'm' }, () => {}, null, 7),
            err => err.message === '429 Too Many Requests.' && !err.saved && !err.dropped,
        );
    """)


def test_a_dropped_connection_is_told_apart_from_an_error():
    """The reply goes on being written, so the page goes back for it."""
    run_node_module(STREAM_SETUP + r"""
        stubStream(['data: {"choices":[{"delta":{"content":"Hel"}}]}\n'], new TypeError('Load failed'));
        await assert.rejects(
            API.streamChatCompletion({ model: 'm' }, () => {}, null, 7),
            err => err.dropped === true,
        );
    """)


def test_follow_reply_reads_the_four_answers():
    run_node_module(STREAM_SETUP + r"""
        stubStream([], null, { status: 204 });
        assert.equal(await API.followReply(7), null);
        assert.equal(requests[0].url, '/api/llm/chat?chat_id=7');
        assert.equal(requests[0].method, undefined);

        // One that ended moments ago: nothing to read, but worth another look.
        stubStream([], null, { status: 205 });
        assert.deepEqual(await API.followReply(7), { swipeOf: null, read: null });

        stubStream([
            'data: {"choices":[{"delta":{"content":"a second take"}}]}\n',
            'data: [DONE]\n',
            'data: {"saved":{"id":31,"content":"a second take"}}\n',
        ], null, { status: 200, swipeOf: '12' });
        const reply = await API.followReply(7);
        assert.equal(reply.swipeOf, 12);
        let text = '';
        assert.deepEqual(await reply.read(t => { text = t; }), { id: 31, content: 'a second take' });
        assert.equal(text, 'a second take');

        // One that failed with no page listening: why, in place of a stream.
        const failure = { error: 'overloaded', cut_short: false, swipe_of: null };
        globalThis.fetch = async () => ({
            ok: true, status: 200,
            headers: { get: name => (name === 'Content-Type' ? 'application/json' : null) },
            json: async () => failure,
        });
        assert.deepEqual(await API.followReply(7), { failure });
    """)


# ── generateSwipe: a new swipe and the ways it ends ────────────────────────
# Harness mirrors test_summaries_frontend.py's generateSwipe test.

SWIPE_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el, llm } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import { stopGeneration } from './static/js/utils.js';
    import { generateSwipe, drawnMessages } from './static/js/messages.js';

    globalThis.DOMPurify = { sanitize: value => value };
    globalThis.marked = { parse: value => value };
    // renderThinkingBlock and showToast build real elements and reach into
    // them, so createElement has to hand back something navigable.
    function stubEl() {
        return {
            className: '', textContent: '', innerHTML: '', type: '', hidden: false,
            remove() {}, setAttribute() {}, appendChild() {}, insertBefore() {},
            addEventListener() {},
            querySelector() { return stubEl(); },
            classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        };
    }
    globalThis.document = {
        createElement: stubEl,
        getElementById: stubEl,
        // The fade-in walks the text a draw put in the bubble.
        createTreeWalker: () => ({ nextNode: () => null }),
    };
    globalThis.NodeFilter = { SHOW_TEXT: 4 };
    // messages.js coalesces its stream redraws onto an animation frame. A timer
    // stands in for one: node has no rAF, and the draws still have to run so
    // this exercises the render path the way a browser would.
    globalThis.requestAnimationFrame = fn => setTimeout(fn, 0);
    globalThis.cancelAnimationFrame = id => clearTimeout(id);

    Object.assign(el, {
        apiEndpoint: { value: 'http://main.example/v1' },
        settingsContextTokens: { value: '0' },
        sendBtn: {
            disabled: false, innerHTML: '', title: '',
            setAttribute() {}, classList: { toggle() {} },
        },
        chatHistory: { querySelector() { return null; }, insertBefore() {} },
    });
    state.apiModel = 'main-model';
    state.activeCharacter = { name: 'Mira' };
    state.activePersona = { name: 'Morgan' };
    state.systemPrompts = [{ id: 1, content: 'Reply as {{char}}.' }];
    state.activeSystemPromptId = 1;
    state.activeSamplers = new Set();
    state.contextMaxTokens = '0';
    state.messages = [
        { id: 1, role: 'user', text: 'hello there' },
        {
            id: 2, role: 'character', text: 'the original reply',
            swipes: [{ content: 'the original reply' }], activeSwipeIndex: 0,
        },
    ];
    const reply = state.messages[1];
    // Summaries off — this exercises the regen abort path, nothing else.
    state.activeChat = { id: 7, summary_enabled: false };
    state.chats = [state.activeChat];

    const stops = [];
    API.stopReply = async chatId => { stops.push(chatId); return {}; };

    const contentEl = { innerHTML: '' };
    const msgBody = { querySelector() { return null; }, insertBefore() {} };
    const msgEl = {
        dataset: { msgId: '2' },
        classList: { add() {}, remove() {} },
        querySelector(selector) {
            if (selector === '.message-content') return contentEl;
            if (selector === '.msg-body') return msgBody;
            if (selector === '.msg-edited') return stubEl();
            return null;
        },
    };
    // The bubble holds no copy of the message, only this link to it.
    drawnMessages.set(msgEl, reply);

    function abortError() {
        return new DOMException('The operation was aborted.', 'AbortError');
    }
"""


def test_stop_asks_the_server_and_lands_the_swipe_it_kept():
    run_node_module(SWIPE_SETUP + r"""
        let aborted;
        API.streamChatCompletion = async (payload, onToken, signal, chatId, swipeOf) => {
            assert.deepEqual([chatId, swipeOf], [7, 2]);
            onToken('a half-finished ');
            onToken('a half-finished repl');
            stopGeneration();          // the real Stop path
            aborted = signal.aborted;
            // The server ends the stream with what it kept.
            return { id: 31, content: 'a half-finished repl' };
        };

        assert.equal(await generateSwipe(msgEl), true);

        // Stop went to the server. This page kept listening for the ending.
        assert.deepEqual(stops, [7]);
        assert.equal(aborted, false);
        assert.deepEqual(reply.swipes, [
            // The original swipe stays reachable by swiping left.
            { content: 'the original reply' }, { id: 31, content: 'a half-finished repl' },
        ]);
        assert.equal(reply.activeSwipeIndex, 1);
        assert.equal(reply.text, 'a half-finished repl');
        // Stop state must not leak into the next generation.
        assert.equal(llm.stopRequested, false);
        assert.equal(llm.abortController, null);
        assert.equal(llm.replyChatId, null);
        assert.equal(llm.generationActive, false);
    """)


def test_stop_before_the_reply_is_asked_for_only_stops_this_page():
    """Nothing is running on the server yet, so there is nothing to tell it."""
    run_node_module(SWIPE_SETUP + r"""
        import { queueLLMSettingsSave } from './static/js/llm-settings.js';

        let generationCalls = 0;
        API.streamChatCompletion = async () => { generationCalls += 1; return { content: 'unwanted' }; };
        API.saveSettings = async () => {
            stopGeneration();
            throw abortError();
        };
        queueLLMSettingsSave({ temperature: '0.7' });

        assert.equal(await generateSwipe(msgEl), false);

        assert.deepEqual(stops, []);
        assert.equal(generationCalls, 0);
        assert.equal(reply.swipes.length, 1);
    """)


def test_a_new_swipe_joins_the_list_a_redraw_reads():
    """renderMessages() builds from state.messages. A swipe once went only into
    a copy on the bubble, so any redraw lost it and its index named another."""
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async () => ({ id: 31, content: 'a second take' });

        assert.equal(await generateSwipe(msgEl), true);

        assert.deepEqual(
            state.messages[1].swipes.map(swipe => swipe.content),
            ['the original reply', 'a second take'],
        );
        assert.equal(state.messages[1].activeSwipeIndex, 1);
        assert.equal(state.messages[1].text, 'a second take');
        // Nothing about the message is kept on the bubble to fall out of step.
        assert.deepEqual(msgEl.dataset, { msgId: '2' });
    """)


def test_a_swipe_being_written_elsewhere_is_read_not_asked_for():
    """Rejoined after the page was away, or asked for by another device."""
    run_node_module(SWIPE_SETUP + r"""
        import { receiveSwipe } from './static/js/messages.js';

        let generationCalls = 0;
        API.streamChatCompletion = async () => { generationCalls += 1; };
        llm.abortController = new AbortController();

        const made = await receiveSwipe(msgEl, async onToken => {
            onToken('a second take');
            return { id: 31, content: 'a second take' };
        });

        assert.equal(made, true);
        assert.equal(generationCalls, 0);
        assert.equal(reply.text, 'a second take');
        assert.equal(reply.activeSwipeIndex, 1);
    """)


def test_chat_switch_during_regen_leaves_the_bubble_as_it_was():
    """selectChat aborts without stopGeneration: this page only stops listening."""
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async (payload, onToken) => {
            onToken('a half-finished repl');
            llm.abortController.abort();   // bare abort, as selectChat does
            throw abortError();
        };

        assert.equal(await generateSwipe(msgEl), false);

        assert.deepEqual(stops, []);
        assert.equal(reply.swipes.length, 1);
        assert.equal(reply.text, 'the original reply');
        assert.equal(llm.generationActive, false);
    """)


def test_a_stop_that_kept_nothing_goes_back_to_the_previous_swipe():
    """Stopped mid-reasoning, the server has nothing worth a swipe."""
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async (payload, onToken) => {
            onToken('<think>still weighing it up');
            stopGeneration();
            return null;
        };

        assert.equal(await generateSwipe(msgEl), false);

        assert.deepEqual(stops, [7]);
        assert.equal(reply.swipes.length, 1);
        assert.equal(reply.text, 'the original reply');
    """)


def test_regen_error_reverts_to_the_previous_swipe_unless_some_was_kept():
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async () => { throw new Error('upstream exploded'); };

        assert.equal(await generateSwipe(msgEl), false);

        assert.equal(reply.swipes.length, 1);
        assert.equal(reply.text, 'the original reply');
        assert.equal(reply.activeSwipeIndex, 0);
        assert.equal(llm.generationActive, false);

        // Cut off partway, the server kept the part that arrived.
        API.streamChatCompletion = async () => {
            throw Object.assign(new Error('upstream exploded'), { saved: { id: 31, content: 'half a rep' } });
        };

        assert.equal(await generateSwipe(msgEl), true);

        assert.equal(reply.text, 'half a rep');
        assert.equal(reply.activeSwipeIndex, 1);
    """)


def test_a_dropped_connection_sends_the_page_back_for_the_swipe():
    run_node_module(SWIPE_SETUP + r"""
        const asked = [];
        API.getMessages = async chatId => {
            asked.push(['messages', chatId]);
            return [{ id: 1, content: 'hello there' }, { id: 2, content: 'the original reply' }];
        };
        API.followReply = async chatId => { asked.push(['reply', chatId]); return null; };
        API.streamChatCompletion = async (payload, onToken) => {
            onToken('a half-finished repl');
            throw Object.assign(new TypeError('Load failed'), { dropped: true });
        };

        assert.equal(await generateSwipe(msgEl), false);
        await new Promise(resolve => setTimeout(resolve, 0));

        // No toast and no swipe: it looked the chat up again, then for the reply.
        assert.deepEqual(asked, [['messages', 7], ['reply', 7]]);
        assert.equal(reply.swipes.length, 1);
        assert.equal(llm.generationActive, false);
    """)


def test_rapid_second_regen_does_not_start_another_generation():
    run_node_module(SWIPE_SETUP + r"""
        let markStreamStarted;
        const streamStarted = new Promise(resolve => { markStreamStarted = resolve; });
        let releaseStream;
        const streamBlocked = new Promise(resolve => { releaseStream = resolve; });
        let generationCalls = 0;
        API.streamChatCompletion = async () => {
            generationCalls += 1;
            markStreamStarted();
            await streamBlocked;
            return { content: 'the regenerated reply' };
        };

        const first = generateSwipe(msgEl);
        await streamStarted;
        const second = await generateSwipe(msgEl);

        assert.equal(second, false);
        assert.equal(generationCalls, 1);
        assert.equal(llm.generationActive, true);

        releaseStream();
        assert.equal(await first, true);
        assert.equal(reply.activeSwipeIndex, 1);
        assert.equal(generationCalls, 1);
        assert.equal(llm.generationActive, false);
    """)
