from helpers import run_node_module


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


def test_a_lone_close_tag_ends_reasoning_the_template_opened():
    """QwQ and R1-style templates put <think> in the prompt, so the reply only closes it."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { parseThinkingContent } from './static/js/thinking.js';

        const parsed = parseThinkingContent('Let me weigh this.\n</think>\n\nThe answer.');
        assert.equal(parsed.hasThinking, true);
        assert.equal(parsed.thinking, 'Let me weigh this.');
        assert.equal(parsed.response, 'The answer.');
        // Edits reattach the segment, so it carries both tags.
        assert.equal(parsed.thinkingSegment, '<think>Let me weigh this.\n</think>');

        const plain = parseThinkingContent('No reasoning here.');
        assert.equal(plain.hasThinking, false);
    """)


def test_reveal_cutoff_skips_past_partial_thinking_tags():
    """Incremental streaming reveal must not slice mid-tag and flash `<think` or `</think`."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { parseThinkingContent, revealPastThinkingTag } from './static/js/thinking.js';

        const opening = '<think>Hmm';
        for (let cutoff = 1; cutoff < '<think>'.length; cutoff++) {
            const snapped = revealPastThinkingTag(opening, cutoff);
            assert.equal(snapped, '<think>'.length);
            const parsed = parseThinkingContent(opening.slice(0, snapped));
            assert.equal(parsed.hasThinking, true);
            assert.equal(parsed.response, '');
        }
        // Outside a tag the cutoff stays where it was.
        assert.equal(revealPastThinkingTag(opening, 7), 7);
        assert.equal(revealPastThinkingTag(opening, 8), 8);

        const closing = '<think>Hmm</think>Hello';
        const closeStart = closing.indexOf('</think>');
        const closeEnd = closeStart + '</think>'.length;
        for (let cutoff = closeStart + 1; cutoff < closeEnd; cutoff++) {
            const snapped = revealPastThinkingTag(closing, cutoff);
            assert.equal(snapped, closeEnd);
            const parsed = parseThinkingContent(closing.slice(0, snapped));
            assert.equal(parsed.thinking, 'Hmm');
            assert.equal(parsed.incomplete, undefined);
        }

        // Works for the alternate thinking tag pairs as well.
        assert.equal(revealPastThinkingTag('<thinking>plan', 4), '<thinking>'.length);
        assert.equal(revealPastThinkingTag('<|thinking|>plan', 4), '<|thinking|>'.length);
        assert.equal(revealPastThinkingTag('Plain reply', 5), 5);
    """)
