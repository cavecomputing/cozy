"""compareCharacters() decides the sidebar order: archived last, pinned first,
then by name. recentCharacters() picks the Recent block above it."""

from helpers import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { compareCharacters, recentCharacters } from './static/js/utils.js';

    let nextId = 1;
    const char = (name, pinned = false, id = nextId++) => ({ id, name, pinned, archived: false });
    const order = chars => [...chars].sort(compareCharacters).map(c => c.name);
"""


def test_pinned_come_first_and_sort_by_name_not_pin_time():
    run_node_module(SETUP + r"""
        // Listed in the order they were pinned; the sidebar ignores that.
        const chars = [char('Zed', true), char('Bea'), char('Mia', true), char('Al')];
        assert.deepEqual(order(chars), ['Mia', 'Zed', 'Al', 'Bea']);
    """)


def test_archived_come_last_and_sort_by_name():
    run_node_module(SETUP + r"""
        const archived = name => ({ ...char(name), archived: true });
        const chars = [archived('Ann'), char('Zed', true), archived('Ada'), char('Bea')];
        assert.deepEqual(order(chars), ['Zed', 'Bea', 'Ada', 'Ann']);
    """)


def test_case_is_ignored():
    run_node_module(SETUP + r"""
        assert.deepEqual(order([char('bob'), char('Carol'), char('alice')]),
                         ['alice', 'bob', 'Carol']);
    """)


def test_accents_are_ignored():
    run_node_module(SETUP + r"""
        assert.deepEqual(order([char('Zoe'), char('Emma'), char('Émile')]),
                         ['Émile', 'Emma', 'Zoe']);
    """)


def test_numbers_compare_as_numbers():
    run_node_module(SETUP + r"""
        assert.deepEqual(order([char('Bot 10'), char('Bot 2'), char('Bot 1')]),
                         ['Bot 1', 'Bot 2', 'Bot 10']);
    """)


def test_equal_names_fall_back_to_id():
    run_node_module(SETUP + r"""
        const chars = [char('Sam', false, 9), char('sam', false, 3), char('Sam', false, 5)];
        assert.deepEqual([...chars].sort(compareCharacters).map(c => c.id), [3, 5, 9]);
    """)


RECENT = SETUP + r"""
    // Thirteen listed characters, one more than a list short enough to skip Recent.
    const library = Array.from({ length: 13 }, (_, i) => char(`C${i}`));
    const recent = (chars, stamps) => recentCharacters(chars, new Map(stamps)).map(c => c.name);
"""


def test_recent_is_newest_first_and_five_long():
    run_node_module(RECENT + r"""
        const stamps = library.slice(0, 7).map((c, i) => [c.id, `2026-10-0${i + 1} 12:00:00`]);
        assert.deepEqual(recent(library, stamps), ['C6', 'C5', 'C4', 'C3', 'C2']);
    """)


def test_recent_skips_characters_never_written_to():
    run_node_module(RECENT + r"""
        assert.deepEqual(recent(library, [[library[3].id, '2026-10-01 12:00:00']]), ['C3']);
    """)


def test_recent_leaves_out_archived_and_missing():
    run_node_module(RECENT + r"""
        const chars = [...library, { ...char('Gone'), archived: true }, { ...char('Lost'), missing: true }];
        const stamps = chars.map(c => [c.id, c.name === 'C0' ? '2026-10-01 12:00:00' : '2026-10-09 12:00:00'])
            .filter(([id]) => ['Gone', 'Lost', 'C0'].includes(chars.find(c => c.id === id).name));
        assert.deepEqual(recent(chars, stamps), ['C0']);
    """)


def test_no_recent_while_the_list_is_short():
    run_node_module(RECENT + r"""
        const short = library.slice(0, 12);
        // Archived characters sit folded away, so they don't make the list long.
        const withArchived = [...short, { ...char('Old'), archived: true }];
        const stamps = [[short[0].id, '2026-10-01 12:00:00']];
        assert.deepEqual(recent(short, stamps), []);
        assert.deepEqual(recent(withArchived, stamps), []);
        assert.deepEqual(recent(library, stamps), ['C0']);
    """)
