"""compareCharacters() decides the sidebar order: pinned first, then by name."""

from helpers import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { compareCharacters } from './static/js/utils.js';

    let nextId = 1;
    const char = (name, pinned = false, id = nextId++) => ({ id, name, pinned });
    const order = chars => [...chars].sort(compareCharacters).map(c => c.name);
"""


def test_pinned_come_first_and_sort_by_name_not_pin_time():
    run_node_module(SETUP + r"""
        // Listed in the order they were pinned; the sidebar ignores that.
        const chars = [char('Zed', true), char('Bea'), char('Mia', true), char('Al')];
        assert.deepEqual(order(chars), ['Mia', 'Zed', 'Al', 'Bea']);
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
