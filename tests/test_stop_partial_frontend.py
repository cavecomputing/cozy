"""Stopping an in-flight stream keeps the text that already arrived."""

import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend stop-partial tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


# ── api.js transport contract ──────────────────────────────────────────────
# streamChatCompletion is deliberately untouched by the stop-partial feature:
# it still rejects on abort, and the partial survives only because the caller
# keeps the value handed to onToken. These pin that contract.

STREAM_SETUP = r"""
    import assert from 'node:assert/strict';
    import { API } from './static/js/api.js';

    const encoder = new TextEncoder();

    /** Fake an SSE response that aborts as soon as the signal says so. */
    function stubStream(chunks) {
        let next = 0;
        globalThis.fetch = async (url, options) => ({
            ok: true,
            body: {
                getReader() {
                    return {
                        async read() {
                            if (options.signal?.aborted) {
                                throw new DOMException('The operation was aborted.', 'AbortError');
                            }
                            if (next >= chunks.length) return { done: true };
                            return { done: false, value: encoder.encode(chunks[next++]) };
                        },
                    };
                },
            },
        });
    }
"""


def test_stream_rejects_on_abort_but_partial_reached_the_caller():
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
                controller.signal,
            ),
            err => err.name === 'AbortError',
        );

        // The rejection discards the return value, so onToken is the only
        // place the partial survives — this is what send.js/messages.js keep.
        assert.deepEqual(seen, ['Hello ']);
    """)


def test_stream_leaves_thinking_tag_open_when_cut_mid_reasoning():
    """Why closeIncompleteThinking exists: onToken's text can be unterminated."""
    run_node_module(STREAM_SETUP + r"""
        import { closeIncompleteThinking, parseThinkingContent } from './static/js/thinking.js';

        stubStream([
            'data: {"choices":[{"delta":{"reasoning_content":"weighing options"}}]}\n',
            'data: {"choices":[{"delta":{"content":"never arrives"}}]}\n',
        ]);

        const controller = new AbortController();
        let last = '';
        await assert.rejects(
            API.streamChatCompletion(
                { model: 'm' },
                text => { last = text; controller.abort(); },
                controller.signal,
            ),
            err => err.name === 'AbortError',
        );

        assert.equal(last, '<think>weighing options');
        assert.equal(parseThinkingContent(last).incomplete, true);
        assert.equal(closeIncompleteThinking(last), '<think>weighing options</think>');
    """)


# ── generateSwipe: stop mid-regen ──────────────────────────────────────────
# Harness mirrors test_summaries_frontend.py's generateSwipe test.

SWIPE_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el, llm } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import { stopGeneration } from './static/js/utils.js';
    import { generateSwipe } from './static/js/messages.js';

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
    };

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
        { id: 2, role: 'character', text: 'the original reply' },
    ];
    // Summaries off — this exercises the regen abort path, nothing else.
    state.activeChat = { id: 7, summary_enabled: false };
    state.chats = [state.activeChat];

    const savedSwipes = [];
    API.addSwipe = async (msgId, content) => {
        savedSwipes.push({ msgId, content });
        return {};
    };

    const contentEl = { innerHTML: '' };
    const msgBody = { querySelector() { return null; }, insertBefore() {} };
    const msgEl = {
        dataset: {
            msgId: '2',
            rawText: 'the original reply',
            swipes: JSON.stringify([{ content: 'the original reply' }]),
            activeSwipeIndex: '0',
        },
        querySelector(selector) {
            if (selector === '.message-content') return contentEl;
            if (selector === '.msg-body') return msgBody;
            return null;
        },
    };
    const swipes = [{ content: 'the original reply' }];

    function abortError() {
        return new DOMException('The operation was aborted.', 'AbortError');
    }
"""


def test_stopped_regen_commits_its_partial_as_a_new_swipe():
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async (payload, onToken) => {
            onToken('a half-finished ');
            onToken('a half-finished repl');
            stopGeneration();          // the real Stop path
            throw abortError();
        };

        const idx = await generateSwipe(msgEl, swipes, 0);

        assert.equal(idx, 1);
        assert.equal(swipes.length, 2);
        assert.equal(swipes[1].content, 'a half-finished repl');
        assert.deepEqual(savedSwipes, [{ msgId: 2, content: 'a half-finished repl' }]);
        assert.equal(msgEl.dataset.rawText, 'a half-finished repl');
        assert.equal(msgEl.dataset.activeSwipeIndex, 1);
        assert.equal(state.messages[1].text, 'a half-finished repl');
        // The original swipe stays reachable by swiping left.
        assert.equal(swipes[0].content, 'the original reply');
        // Stop state must not leak into the next generation.
        assert.equal(llm.stopRequested, false);
        assert.equal(llm.abortController, null);
        assert.equal(llm.generationActive, false);
    """)


def test_chat_switch_during_regen_discards_the_partial():
    """selectChat aborts without stopGeneration, so the partial is dropped."""
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async (payload, onToken) => {
            onToken('a half-finished repl');
            llm.abortController.abort();   // bare abort, as selectChat does
            throw abortError();
        };

        const idx = await generateSwipe(msgEl, swipes, 0);

        assert.equal(idx, null);
        assert.equal(swipes.length, 1);
        assert.deepEqual(savedSwipes, []);
        assert.equal(state.messages[1].text, 'the original reply');
        assert.equal(llm.generationActive, false);
    """)


def test_regen_stopped_mid_reasoning_discards_the_partial():
    """Thinking-only text is stripped from every later prompt, so it is dropped."""
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async (payload, onToken) => {
            onToken('<think>still weighing it up');
            stopGeneration();
            throw abortError();
        };

        const idx = await generateSwipe(msgEl, swipes, 0);

        assert.equal(idx, null);
        assert.equal(swipes.length, 1);
        assert.deepEqual(savedSwipes, []);
    """)


def test_regen_error_still_reverts_to_the_previous_swipe():
    """A real failure is not a stop — nothing is committed."""
    run_node_module(SWIPE_SETUP + r"""
        API.streamChatCompletion = async () => { throw new Error('upstream exploded'); };

        const idx = await generateSwipe(msgEl, swipes, 0);

        assert.equal(idx, null);
        assert.equal(swipes.length, 1);
        assert.deepEqual(savedSwipes, []);
        assert.equal(msgEl.dataset.rawText, 'the original reply');
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
            return 'the regenerated reply';
        };

        const first = generateSwipe(msgEl, swipes, 0);
        await streamStarted;
        const second = await generateSwipe(msgEl, swipes, 0);

        assert.equal(second, null);
        assert.equal(generationCalls, 1);
        assert.equal(llm.generationActive, true);

        releaseStream();
        assert.equal(await first, 1);
        assert.equal(generationCalls, 1);
        assert.equal(llm.generationActive, false);
    """)
