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
