import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend thinking tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_thinking_parser_reports_tags_independently_of_content():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { parseThinkingContent } from './static/js/thinking.js';

        const parsed = parseThinkingContent('<think></think>Visible answer');
        assert.equal(parsed.hasThinking, true);
        assert.equal(parsed.thinking, '');
        assert.equal(parsed.response, 'Visible answer');

        const plain = parseThinkingContent('Visible answer');
        assert.equal(plain.hasThinking, false);
        assert.equal(plain.response, 'Visible answer');
    """)


def test_visible_response_ignores_thinking_only_text():
    """A stream stopped mid-reasoning has nothing worth persisting."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { hasVisibleResponse } from './static/js/thinking.js';

        assert.equal(hasVisibleResponse('<think>still reasoning'), false);
        assert.equal(hasVisibleResponse('<think>done</think>   '), false);
        assert.equal(hasVisibleResponse(''), false);
        assert.equal(hasVisibleResponse(null), false);

        assert.equal(hasVisibleResponse('<think>done</think>An answer'), true);
        assert.equal(hasVisibleResponse('Just an answer'), true);
    """)


def test_close_incomplete_thinking_terminates_an_interrupted_block():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { closeIncompleteThinking, parseThinkingContent } from './static/js/thinking.js';

        const closed = closeIncompleteThinking('<think>cut off here');
        assert.equal(closed, '<think>cut off here</think>');
        // The result must re-parse as a complete block, or a later edit to the
        // message would be swallowed into the reasoning.
        assert.equal(parseThinkingContent(closed).incomplete, undefined);

        // Already-closed and tag-free text pass through untouched.
        assert.equal(closeIncompleteThinking('<think>r</think>body'), '<think>r</think>body');
        assert.equal(closeIncompleteThinking('plain body'), 'plain body');
        assert.equal(closeIncompleteThinking(''), '');
    """)


def test_incomplete_thinking_is_normalized_for_message_edits():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { parseThinkingContent } from './static/js/thinking.js';

        const parsed = parseThinkingContent('Preface<think>Interrupted reasoning');
        assert.equal(parsed.hasThinking, true);
        assert.equal(parsed.incomplete, true);
        assert.equal(parsed.response, 'Preface');
        assert.equal(
            parsed.thinkingSegment,
            '<think>Interrupted reasoning</think>',
        );

        const edited = `${parsed.thinkingSegment}\n\nEdited answer`;
        const reparsed = parseThinkingContent(edited);
        assert.equal(reparsed.incomplete, undefined);
        assert.equal(reparsed.thinking, 'Interrupted reasoning');
        assert.equal(reparsed.response, 'Edited answer');
    """)
