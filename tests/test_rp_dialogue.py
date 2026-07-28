"""Quoted-speech matching ([static/js/rp-dialogue.js]) exercised under node.

The marked extension itself can't be tested here — marked is a CDN global — but
the part that decides what counts as dialogue is plain string work.

Skips rather than fails when node isn't on PATH.
"""
from test_regex_engine import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { matchDialogue, dialogueStart } from './static/js/rp-dialogue.js';
    const match = s => matchDialogue(s);
"""


# ── The English pairs that already worked ─────────────────────────────────

def test_straight_and_curly_quotes_still_match():
    run_node_module(SETUP + r"""
        assert.deepEqual(match('"Hello," she said.'), {
            raw: '"Hello,"', text: 'Hello,', open: '"', close: '"' });
        assert.deepEqual(match('“Hello,” she said.'), {
            raw: '“Hello,”', text: 'Hello,', open: '“', close: '”' });
    """)


def test_curly_quotes_keep_their_own_marks():
    """Previously the renderer re-emitted every match with straight quotes."""
    run_node_module(SETUP + r"""
        const m = match('“Hallo”');
        assert.equal(m.open, '“');
        assert.equal(m.close, '”');
    """)


# ── German ────────────────────────────────────────────────────────────────

def test_german_low_high_quotes_match():
    """„…“ — the reported case. U+201E opens, U+201C closes."""
    run_node_module(SETUP + r"""
        assert.deepEqual(match('„Hallo!“ Dann ging sie.'), {
            raw: '„Hallo!“', text: 'Hallo!', open: '„', close: '“' });
    """)


def test_german_quotes_closed_with_u201d_also_match():
    """Models often emit the English closer even when opening the German way."""
    run_node_module(SETUP + r"""
        const m = match('„Hallo!”');
        assert.equal(m.text, 'Hallo!');
        assert.equal(m.close, '”');
    """)


def test_inward_guillemets_match():
    """»…« — German and Danish."""
    run_node_module(SETUP + r"""
        assert.deepEqual(match('»Guten Tag«, sagte er.'), {
            raw: '»Guten Tag«', text: 'Guten Tag', open: '»', close: '«' });
    """)


def test_outward_guillemets_match():
    """«…» — French, Swiss, Russian. Same marks, opposite direction."""
    run_node_module(SETUP + r"""
        assert.deepEqual(match('«Bonjour», dit-elle.'), {
            raw: '«Bonjour»', text: 'Bonjour', open: '«', close: '»' });
    """)


def test_french_spacing_inside_guillemets_is_kept():
    run_node_module(SETUP + r"""
        assert.equal(match('« Bonjour »').text, ' Bonjour ');
    """)


# ── Japanese ──────────────────────────────────────────────────────────────

def test_corner_brackets_match():
    """「…」 is the standard Japanese speech marker; 『…』 nests inside it."""
    run_node_module(SETUP + r"""
        assert.deepEqual(match('「こんにちは」と彼女は言った。'), {
            raw: '「こんにちは」', text: 'こんにちは', open: '「', close: '」' });
        assert.deepEqual(match('『引用』'), {
            raw: '『引用』', text: '引用', open: '『', close: '』' });
    """)


# ── Things that must NOT match ────────────────────────────────────────────

def test_apostrophes_never_open_dialogue():
    """Single marks are excluded on purpose — "don't" would swallow the line."""
    run_node_module(SETUP + r"""
        assert.equal(match("don't stop believing"), null);
        assert.equal(match('‘maybe’ he thought'), null);
        assert.equal(dialogueStart("don't"), undefined);
    """)


def test_unclosed_and_empty_quotes_do_not_match():
    run_node_module(SETUP + r"""
        assert.equal(match('"unclosed forever'), null);
        assert.equal(match('""'), null);              // nothing between the marks
        assert.equal(match('„unclosed'), null);
    """)


def test_a_quote_cannot_span_a_line_break():
    """Otherwise one stray mark styles the rest of the message as speech."""
    run_node_module(SETUP + r"""
        assert.equal(match('"first line\nsecond line"'), null);
        assert.equal(match('„first\nsecond“'), null);
    """)


def test_mismatched_pairs_do_not_match():
    run_node_module(SETUP + r"""
        // A low-9 opener closed by another low-9 is not a pair.
        assert.equal(match('„Hallo„'), null);
        // Guillemets pointing the same way are not a pair either.
        assert.equal(match('»Hallo»'), null);
    """)


# ── dialogueStart ─────────────────────────────────────────────────────────

def test_dialogue_start_finds_the_earliest_opener():
    run_node_module(SETUP + r"""
        assert.equal(dialogueStart('She smiled. „Hallo!“'), 12);
        assert.equal(dialogueStart('no quotes here'), undefined);
        // Whichever convention appears first wins.
        assert.equal(dialogueStart('a »b« c "d"'), 2);
        assert.equal(dialogueStart('a "b" c »d«'), 2);
    """)


def test_narration_before_dialogue_is_not_consumed():
    """match() is anchored; marked uses dialogueStart to skip ahead first."""
    run_node_module(SETUP + r"""
        assert.equal(match('She smiled. „Hallo!“'), null);
        const src = 'She smiled. „Hallo!“';
        assert.equal(match(src.slice(dialogueStart(src))).text, 'Hallo!');
    """)
