"""Frontend regex engine ([static/js/regex-engine.js]) exercised under node.

Skips rather than fails when node isn't on PATH, so a green run on a machine
without Node isn't actually covering this module.
"""
import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend regex-engine tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


SETUP = r"""
    import assert from 'node:assert/strict';
    import {
        runFilters, compileFilter, filterError, splitSlashForm,
        splitFilterFlags, combineFilterFlags, escapeForInput,
    } from './static/js/regex-engine.js';
"""


# ── runFilters ────────────────────────────────────────────────────────────

def test_straightens_german_quotes():
    """The reported use case: „…" becomes "…" everywhere in the reply."""
    run_node_module(SETUP + r"""
        const filters = [{ find: '„([^"]*)"', replace: '"$1"', flags: 'g' }];
        const input = 'Sie sagte: „Hallo!" Dann: „Tschüss!"';
        assert.equal(runFilters(input, filters), 'Sie sagte: "Hallo!" Dann: "Tschüss!"');
    """)


def test_without_g_only_the_first_match_changes():
    run_node_module(SETUP + r"""
        assert.equal(runFilters('a a a', [{ find: 'a', replace: 'b', flags: '' }]), 'b a a');
        assert.equal(runFilters('a a a', [{ find: 'a', replace: 'b', flags: 'g' }]), 'b b b');
    """)


def test_filters_compose_in_order():
    """Each filter sees the previous one's output — the reason the preview runs the whole list."""
    run_node_module(SETUP + r"""
        const out = runFilters('cat', [
            { find: 'cat', replace: 'dog', flags: 'g' },
            { find: 'dog', replace: 'bird', flags: 'g' },
        ]);
        assert.equal(out, 'bird');
    """)


def test_invalid_pattern_is_skipped_not_thrown():
    """A half-typed regex must never block a reply from being saved."""
    run_node_module(SETUP + r"""
        const out = runFilters('hello world', [
            { find: '([unclosed', replace: 'x', flags: 'g' },
            { find: 'world', replace: 'there', flags: 'g' },
        ]);
        assert.equal(out, 'hello there');
    """)


def test_empty_and_missing_filters_pass_text_through():
    run_node_module(SETUP + r"""
        assert.equal(runFilters('untouched', []), 'untouched');
        assert.equal(runFilters('untouched', null), 'untouched');
        assert.equal(runFilters('untouched', [{ find: '', replace: 'x', flags: 'g' }]), 'untouched');
        assert.equal(runFilters('', [{ find: 'a', replace: 'b', flags: 'g' }]), '');
    """)


def test_a_filter_that_would_empty_the_reply_is_ignored():
    """Losing a whole reply to an over-eager pattern is always a mistake."""
    run_node_module(SETUP + r"""
        const out = runFilters('the entire reply', [{ find: '.*', replace: '', flags: 'gs' }]);
        assert.equal(out, 'the entire reply');
    """)


def test_emptying_filter_does_not_undo_prior_filters():
    """Only the destructive row is ignored; earlier successful work survives."""
    run_node_module(SETUP + r"""
        const out = runFilters('cat', [
            { find: 'cat', replace: 'dog', flags: 'g' },
            { find: '.*', replace: '', flags: 'gs' },
        ]);
        assert.equal(out, 'dog');
    """)


def test_capture_groups_and_match_macros():
    run_node_module(SETUP + r"""
        assert.equal(
            runFilters('John Smith', [{ find: '(\\w+) (\\w+)', replace: '$2, $1', flags: 'g' }]),
            'Smith, John');
        // SillyTavern's {{match}} and JS's $& are the same thing.
        assert.equal(
            runFilters('wow', [{ find: 'wow', replace: '**{{match}}**', flags: 'g' }]),
            '**wow**');
        assert.equal(
            runFilters('wow', [{ find: 'wow', replace: '**$&**', flags: 'g' }]),
            '**wow**');
        // $$ is an escaped literal dollar.
        assert.equal(
            runFilters('price', [{ find: 'price', replace: '$$5', flags: 'g' }]),
            '$5');
    """)


def test_escape_sequences_in_the_replacement():
    """The Replace box is single-line, so \\n is the only way to insert a break."""
    run_node_module(SETUP + r"""
        assert.equal(
            runFilters('a\n\n\n\nb', [{ find: '\\n{3,}', replace: '\\n\\n', flags: 'g' }]),
            'a\n\nb');
        assert.equal(
            runFilters('x', [{ find: 'x', replace: 'a\\tb', flags: 'g' }]),
            'a\tb');
        // A doubled backslash stays a literal backslash-n.
        assert.equal(
            runFilters('x', [{ find: 'x', replace: '\\\\n', flags: 'g' }]),
            '\\n');
    """)


def test_escape_for_input_survives_a_single_line_input():
    """`<input type=text>` deletes CR and LF, so neither may reach one intact."""
    run_node_module(SETUP + r"""
        assert.equal(escapeForInput('„([^“”"\n]*)[“”"]'), '„([^“”"\\n]*)[“”"]');
        assert.equal(escapeForInput('a\r\nb\tc'), 'a\\r\\nb\\tc');
        // Backslashes are left alone: escaping them would double every `\d`
        // already in a pattern, and re-escape a Replace box that stores `\n`
        // as two characters to begin with.
        assert.equal(escapeForInput('\\d+'), '\\d+');
        assert.equal(escapeForInput('\\n'), '\\n');
        assert.equal(escapeForInput(''), '');
        assert.equal(escapeForInput(null), '');
    """)


def test_escaped_pattern_matches_exactly_what_the_raw_one_did():
    """The escape is a display form, not a different regex."""
    run_node_module(SETUP + r"""
        const raw = '„([^“”"\n]*)[“”"]';
        const sample = 'Sie flüsterte: „Ich weiß es nicht.\n\nDann sagte er: „Doch.“';
        assert.equal(
            runFilters(sample, [{ find: escapeForInput(raw), replace: '"$1"', flags: 'g' }]),
            runFilters(sample, [{ find: raw, replace: '"$1"', flags: 'g' }]));
        // And it still refuses to run the quote past the blank line.
        assert.equal(
            runFilters(sample, [{ find: escapeForInput(raw), replace: '"$1"', flags: 'g' }]),
            'Sie flüsterte: „Ich weiß es nicht.\n\nDann sagte er: "Doch."');
    """)


def test_dotall_and_multiline_flags():
    run_node_module(SETUP + r"""
        // s lets a pattern span line breaks — needed to strip multi-line asides.
        assert.equal(
            runFilters('keep (OOC:\nnote) end', [{ find: '\\(OOC:.*?\\)', replace: '', flags: 'gs' }]),
            'keep  end');
        // m anchors ^ to each line rather than the whole reply.
        assert.equal(
            runFilters('  a\n  b', [{ find: '^ +', replace: '', flags: 'gm' }]),
            'a\nb');
    """)


def test_non_string_input_is_returned_unchanged():
    run_node_module(SETUP + r"""
        assert.equal(runFilters(null, [{ find: 'a', replace: 'b', flags: 'g' }]), null);
        assert.equal(runFilters(undefined, [{ find: 'a', replace: 'b', flags: 'g' }]), undefined);
    """)


def test_filters_run_over_thinking_blocks_too():
    """Documents the deliberate choice to filter the whole raw reply."""
    run_node_module(SETUP + r"""
        const out = runFilters('<think>cat</think>a cat', [
            { find: 'cat', replace: 'dog', flags: 'g' },
        ]);
        assert.equal(out, '<think>dog</think>a dog');
    """)


# ── compileFilter / filterError ───────────────────────────────────────────

def test_compile_uses_exactly_the_flags_given():
    """No hidden default: unticking every flag box must mean no flags."""
    run_node_module(SETUP + r"""
        assert.equal(compileFilter({ find: 'a' }).flags, '');
        assert.equal(compileFilter({ find: 'a', flags: '' }).flags, '');
        assert.equal(compileFilter({ find: 'a', flags: 'i' }).flags, 'i');
        assert.equal(compileFilter({ find: '' }), null);
        assert.equal(compileFilter(null), null);
        assert.equal(compileFilter({ find: '(' }), null);
    """)


def test_filter_error_reports_the_engine_message():
    run_node_module(SETUP + r"""
        assert.equal(filterError({ find: 'fine', flags: 'g' }), '');
        assert.equal(filterError({ find: '', flags: 'g' }), '');
        assert.match(filterError({ find: '([a', flags: 'g' }), /Invalid regular expression/);
        assert.match(filterError({ find: 'a', flags: 'Z' }), /flag/i);
    """)


# ── splitSlashForm ────────────────────────────────────────────────────────

def test_split_slash_form_parses_sillytavern_patterns():
    run_node_module(SETUP + r"""
        assert.deepEqual(splitSlashForm('/abc/gi'), { find: 'abc', flags: 'gi' });
        assert.deepEqual(splitSlashForm('/abc/'), { find: 'abc', flags: '' });
        assert.deepEqual(splitSlashForm('  /a\\/b/g  '), { find: 'a\\/b', flags: 'g' });
    """)


def test_split_slash_form_leaves_ordinary_patterns_alone():
    run_node_module(SETUP + r"""
        assert.equal(splitSlashForm('abc'), null);
        assert.equal(splitSlashForm(''), null);
        assert.equal(splitSlashForm(null), null);
        // Not the slash form — a literal pattern that happens to start with one.
        assert.equal(splitSlashForm('/me waves'), null);
        // Unknown trailing letters mean this was never flags.
        assert.equal(splitSlashForm('/path/to/file'), null);
        // '/x/gg' would throw as a regex, so it isn't treated as the slash form.
        assert.equal(splitSlashForm('/x/gg'), null);
    """)


# ── Visible / hidden flags ─────────────────────────────────────────────────

def test_advanced_flags_survive_visible_flag_edits():
    run_node_module(SETUP + r"""
        assert.deepEqual(splitFilterFlags('yug'), { visible: 'g', hidden: 'uy' });
        assert.equal(combineFilterFlags('gi', 'uy'), 'giuy');
        // Unticking g changes only the visible group; u and y stay attached.
        assert.equal(combineFilterFlags('', splitFilterFlags('guy').hidden), 'uy');
    """)


def test_slash_form_preserves_unicode_flag_semantics():
    run_node_module(SETUP + r"""
        const parsed = splitSlashForm('/\\p{L}+/gu');
        const groups = splitFilterFlags(parsed.flags);
        const flags = combineFilterFlags(groups.visible, groups.hidden);
        assert.equal(flags, 'gu');
        assert.equal(runFilters('café 123', [
            { find: parsed.find, replace: 'word', flags },
        ]), 'word 123');
    """)
