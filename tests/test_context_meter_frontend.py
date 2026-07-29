import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend context-meter tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
