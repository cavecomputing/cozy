from helpers import run_node_module


def test_keys_match_whole_words_whatever_their_edges():
    """\\b needs a word character at the key's edge, which accents, CJK and C++ lack."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { resolveLorebookEntries } from './static/js/lorebook.js';

        const hits = (key, text, extra = {}) => resolveLorebookEntries(
            { entries: [{ keys: [key], content: 'LORE', ...extra }] }, [{ text }],
        ).length === 1;

        assert.ok(hits('Öl', 'Das Öl ist teuer.'));
        assert.ok(hits('öl', 'Das Öl ist teuer.'));
        assert.ok(hits('café', 'We met at the café today.'));
        assert.ok(hits('東京', '東京に行きたい'));
        assert.ok(hits('C++', 'I write C++ code'));
        assert.ok(hits('dragon', 'A dragon flew.'));
        // Still whole words: no match inside a longer one.
        assert.ok(!hits('dragon', 'The dragons flew.'));
        assert.ok(!hits('Ana', 'Banana split'));
        assert.ok(!hits('café', 'Two cafés.'));
        assert.ok(!hits('Mira', 'mira was here', { case_sensitive: true }));
    """)


def test_reasoning_does_not_trigger_entries():
    """Reasoning never reaches the model, so a key it mentions has nothing to answer."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { resolveLorebookEntries } from './static/js/lorebook.js';

        const book = { entries: [{ keys: ['dragon'], content: 'LORE' }] };
        assert.deepEqual(resolveLorebookEntries(book, [{ text: '<think>a dragon?</think>Hello.' }]), []);
        assert.deepEqual(resolveLorebookEntries(book, [{ text: '<think>hm</think>A dragon.' }]), ['LORE']);
    """)


def test_a_key_keeps_its_own_case_rule():
    """Patterns are compiled once and kept, so the same key must not carry one
    entry's case rule over to another's."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { resolveLorebookEntries } from './static/js/lorebook.js';

        const book = { entries: [
            { keys: ['Mira'], content: 'STRICT', case_sensitive: true },
            { keys: ['Mira'], content: 'LOOSE' },
        ] };
        for (let run = 0; run < 2; run += 1) {
            assert.deepEqual(resolveLorebookEntries(book, [{ text: 'mira was here' }]), ['LOOSE']);
            assert.deepEqual(resolveLorebookEntries(book, [{ text: 'Mira was here' }]), ['STRICT', 'LOOSE']);
        }
    """)


def test_a_callers_map_answers_only_for_the_text_it_scanned():
    """One analysis resolves the same book for every trial assembly. The map it
    passes saves the repeats, and must never answer for different text."""
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { resolveLorebookEntries } from './static/js/lorebook.js';

        const book = { scan_depth: 2, entries: [
            { keys: ['dragon'], content: 'DRAGON' },
            { keys: ['castle'], content: 'CASTLE' },
        ] };
        const old = { text: 'An old castle.' };
        const tail = [{ text: 'A dragon flew.' }, { text: 'It landed.' }];
        const resolved = new Map();

        const first = resolveLorebookEntries(book, tail, resolved);
        assert.deepEqual(first, ['DRAGON']);
        // A longer window scans the same two newest messages: the same answer,
        // without matching again.
        assert.equal(resolveLorebookEntries(book, [old, ...tail], resolved), first);
        // Different scanned text is matched afresh.
        assert.deepEqual(resolveLorebookEntries(book, [tail[1], old], resolved), ['CASTLE']);
        assert.equal(resolved.size, 2);

        // Without a map nothing is remembered, so an edited book shows at once.
        book.entries[0].content = 'WYRM';
        assert.deepEqual(resolveLorebookEntries(book, tail), ['WYRM']);
    """)
