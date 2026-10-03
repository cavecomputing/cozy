"""The Author's Note box only saves for the chat it was loaded with."""

from helpers import run_node_module


AUTHOR_NOTE_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import { loadAuthorNote, flushAuthorNote } from './static/js/lorebooks.js';

    globalThis.document = { createElement: () => ({ remove() {}, setAttribute() {}, appendChild() {}, classList: { add() {} } }), getElementById: () => null };
    // A real dataset stores strings, so the stub does too.
    const dataset = new Proxy({}, { set: (store, key, value) => { store[key] = String(value); return true; } });
    el.authorNoteInput = { value: '', dataset, disabled: false };
    el.authorNoteCounter = { textContent: '' };
    el.authorNoteMarker = null;
    state.templates = {};
    const saved = [];
    API.updateChat = async (id, body) => { saved.push([id, body]); return { id, ...body }; };
"""


def test_flush_before_the_box_is_loaded_does_not_wipe_the_note():
    """Opening an unrelated flyout flushes the box while it is still empty."""
    run_node_module(AUTHOR_NOTE_SETUP + r"""
        state.activeChat = { id: 1, author_note: 'Keep it cozy.' };
        state.chats = [state.activeChat];
        flushAuthorNote();
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.deepEqual(saved, []);
    """)


def test_flush_after_switching_chats_does_not_copy_the_old_note():
    """A box loaded for chat 1 must not overwrite chat 2's note."""
    run_node_module(AUTHOR_NOTE_SETUP + r"""
        state.activeChat = { id: 1, author_note: 'First chat note.' };
        state.chats = [state.activeChat, { id: 2, author_note: 'Second chat note.' }];
        loadAuthorNote();
        state.activeChat = state.chats[1];
        flushAuthorNote();
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.deepEqual(saved, []);
        loadAuthorNote();
        assert.equal(el.authorNoteInput.value, 'Second chat note.');
    """)


def test_edit_is_saved_for_the_loaded_chat():
    """The guard must not block a normal edit."""
    run_node_module(AUTHOR_NOTE_SETUP + r"""
        state.activeChat = { id: 1, author_note: '' };
        state.chats = [state.activeChat];
        loadAuthorNote();
        el.authorNoteInput.value = 'New note.';
        flushAuthorNote();
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.deepEqual(saved, [[1, { author_note: 'New note.' }]]);
        assert.equal(state.activeChat.author_note, 'New note.');
    """)
