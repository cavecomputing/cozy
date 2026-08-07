from helpers import run_node_module


# A stand-in for the transcript scroller: the boundary sits 900px below the
# viewport top while the scroller starts at 100px, so the separator is 800px
# past the current scrollTop.
SCROLLER_FAKE = r"""
    function fakeClassList() {
        const classes = new Set();
        return {
            classes,
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); },
        };
    }

    function fakeScroller({ withBoundary = true, scrollTop = 1200, boundaryTop = 900 } = {}) {
        const boundary = {
            classList: fakeClassList(),
            getBoundingClientRect() { return { top: boundaryTop }; },
        };
        const scroller = {
            scrollTop,
            boundary: withBoundary ? boundary : null,
            querySelector(selector) {
                return selector === '.context-boundary' && withBoundary ? boundary : null;
            },
            getBoundingClientRect() { return { top: 100 }; },
        };
        return scroller;
    }
"""


def test_jump_scrolls_the_boundary_just_below_the_top_edge():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { jumpToContextBoundary } from './static/js/context-meter.js';
    """ + SCROLLER_FAKE + r"""
        const scroller = fakeScroller();
        el.chatHistory = scroller;
        state.autoScroll = true;

        assert.equal(jumpToContextBoundary(), true);
        // 1200 (scrollTop) + 800 (offset) - 28 (headroom)
        assert.equal(scroller.scrollTop, 1972);
        // Landing mid-transcript must not be undone by the next streamed token.
        assert.equal(state.autoScroll, false);
        assert.equal(scroller.boundary.classList.contains('context-boundary--flash'), true);
    """)


def test_jump_clamps_to_the_top_instead_of_scrolling_negative():
    """The whole history in context puts the separator at the very top, where
    the headroom subtraction would otherwise resolve above scrollTop 0."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { jumpToContextBoundary } from './static/js/context-meter.js';
    """ + SCROLLER_FAKE + r"""
        el.chatHistory = fakeScroller({ scrollTop: 10, boundaryTop: 100 });

        assert.equal(jumpToContextBoundary(), true);
        assert.equal(el.chatHistory.scrollTop, 0);
    """)


def test_jump_is_inert_when_no_boundary_is_drawn():
    """No context limit set (or an empty chat) means there is nowhere to jump."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { jumpToContextBoundary } from './static/js/context-meter.js';
    """ + SCROLLER_FAKE + r"""
        el.chatHistory = fakeScroller({ withBoundary: false });
        state.autoScroll = true;

        assert.equal(jumpToContextBoundary(), false);
        assert.equal(el.chatHistory.scrollTop, 1200);
        assert.equal(state.autoScroll, true);
    """)


def test_message_history_tooltip_advertises_the_jump_only_with_a_limit():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { tooltipForSegment } from './static/js/context-meter.js';

        state.messages = [{ id: 1, role: 'user', text: 'hello' }];
        const segment = { id: 'message_history', key: 'history:message_history', label: 'Message history', tokens: 40 };

        const limited = tooltipForSegment(segment, {
            maxTokens: 4096, allocatedTokens: 400, segments: [segment], selectedMessageIds: [1],
        });
        assert.match(limited, /jump to where the window starts/);

        const unlimited = tooltipForSegment(segment, {
            maxTokens: 0, allocatedTokens: 400, segments: [segment], selectedMessageIds: [1],
        });
        assert.doesNotMatch(unlimited, /jump to where the window starts/);
    """)


def test_response_reserve_follows_a_max_tokens_in_extra_request_params():
    """buildChatPayload merges extra_request_params over the samplers, so a
    max_tokens there — not the Max Response Tokens field — is what the model
    gets, and the reserve has to hold back that much instead."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { getResponseTokenReserve } from './static/js/context-budget.js';

        el.samplerMaxTokens = { value: '512' };
        const reserveWith = raw => {
            state.extraRequestParams = raw;
            return getResponseTokenReserve();
        };

        // Nothing usable to override with — the sampler field still wins.
        assert.equal(reserveWith(''), 512);
        assert.equal(reserveWith('{"stop":["###"]}'), 512);
        assert.equal(reserveWith('{max_tokens: 4096'), 512);   // malformed JSON
        assert.equal(reserveWith('[1,2,3]'), 512);             // not an object

        assert.equal(reserveWith('{"max_tokens": 4096}'), 4096);
        assert.equal(reserveWith('{"max_tokens": "2048"}'), 2048);
        // Present but unusable still lands in the payload and still wins, so
        // reserve nothing rather than a value we are not going to send.
        assert.equal(reserveWith('{"max_tokens": 0}'), 0);
        assert.equal(reserveWith('{"max_tokens": null}'), 0);
    """)


def test_meter_and_boundary_agree_about_the_draft():
    """The meter's tooltip offers to jump to the separator, so the message
    count it quotes has to be the window the separator is drawn from — both
    read the draft-inclusive analysis."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { getCurrentContextAnalysis, tooltipForSegment } from './static/js/context-meter.js';

        el.settingsContextTokens = { value: '600' };
        el.samplerMaxTokens = { value: '256' };
        state.extraRequestParams = '';
        state.activeCharacter = { name: 'Sasha', description: 'Short desc.' };
        state.activePersona = { name: 'Matt', description: '' };
        state.systemPrompts = [{ id: 1, content: 'You are {{char}}. {{description}}', post_history_content: '' }];
        state.activeSystemPromptId = 1;
        state.lorebooks = [];
        state.activeChat = { id: 1, summary_enabled: 0 };
        state.messages = Array.from({ length: 30 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'user' : 'assistant',
            text: `Turn ${i + 1}: ` + 'some words '.repeat(8),
        }));

        // A draft long enough to push older turns out of the window.
        el.userInput = { value: 'ramble '.repeat(60) };
        const analysis = getCurrentContextAnalysis({ includeDraft: true });
        assert.ok(analysis.selectedMessageIds.length < 30, 'draft should evict history');

        const segment = analysis.segments.find(s => s.id === 'message_history');
        const quoted = Number(/Reaches back (\d+) message/.exec(
            tooltipForSegment(segment, analysis),
        )[1]);
        assert.equal(quoted, analysis.selectedMessageIds.length);
        // updateContextBoundary() draws the separator at this id.
        assert.equal(analysis.firstSelectedMessageId, analysis.selectedMessageIds[0]);

        // Without the draft the window is strictly larger, which is exactly the
        // divergence that used to sit between the two views.
        el.userInput.value = '';
        const undrafted = getCurrentContextAnalysis({ includeDraft: true });
        assert.ok(undrafted.selectedMessageIds.length > analysis.selectedMessageIds.length);
    """)
