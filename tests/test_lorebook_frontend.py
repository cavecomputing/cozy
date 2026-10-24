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
