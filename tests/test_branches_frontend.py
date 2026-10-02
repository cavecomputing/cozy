"""branchesAt() decides which chats a message's branch pill switches between."""

from helpers import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { branchesAt } from './static/js/utils.js';

    // A chat row as the server sends it: forks name their parent chat, the
    // message they were cut at there, and their own copy of that message.
    const chat = (id, fork = {}) => ({
        id, parent_chat_id: null, parent_msg_id: null, fork_msg_id: null, ...fork,
    });
    const forkOf = (id, parent, parentMsg, copy) =>
        chat(id, { parent_chat_id: parent, parent_msg_id: parentMsg, fork_msg_id: copy });
"""


def test_the_parent_and_its_fork_list_each_other_at_the_fork_point():
    run_node_module(SETUP + r"""
        const chats = [chat(1), forkOf(2, 1, 10, 50)];
        const both = [{ chatId: 1, msgId: 10 }, { chatId: 2, msgId: 50 }];
        assert.deepEqual(branchesAt(chats, 1, 10), both);
        assert.deepEqual(branchesAt(chats, 2, 50), both);
    """)


def test_other_messages_do_not_branch():
    run_node_module(SETUP + r"""
        const chats = [chat(1), forkOf(2, 1, 10, 50)];
        assert.deepEqual(branchesAt(chats, 1, 9), []);
        assert.deepEqual(branchesAt(chats, 1, 11), []);
        // The fork's copies of earlier messages are not branch points either.
        assert.deepEqual(branchesAt(chats, 2, 49), []);
        assert.deepEqual(branchesAt(chats, 2, 51), []);
    """)


def test_siblings_share_one_group_in_the_order_they_were_made():
    run_node_module(SETUP + r"""
        const chats = [chat(1), forkOf(2, 1, 10, 50), forkOf(3, 1, 10, 70)];
        const group = [{ chatId: 1, msgId: 10 }, { chatId: 2, msgId: 50 }, { chatId: 3, msgId: 70 }];
        for (const [id, msg] of [[1, 10], [2, 50], [3, 70]]) {
            assert.deepEqual(branchesAt(chats, id, msg), group);
        }
    """)


def test_a_fork_of_a_fork_is_its_own_group():
    run_node_module(SETUP + r"""
        // 2 was cut from 1 at 10 (its copy is 50); 3 was cut from 2 further on, at 60.
        const chats = [chat(1), forkOf(2, 1, 10, 50), forkOf(3, 2, 60, 80)];
        assert.deepEqual(branchesAt(chats, 2, 60),
            [{ chatId: 2, msgId: 60 }, { chatId: 3, msgId: 80 }]);
        assert.deepEqual(branchesAt(chats, 3, 80),
            [{ chatId: 2, msgId: 60 }, { chatId: 3, msgId: 80 }]);
        // The first group is untouched.
        assert.deepEqual(branchesAt(chats, 1, 10),
            [{ chatId: 1, msgId: 10 }, { chatId: 2, msgId: 50 }]);
    """)


def test_a_deleted_parent_leaves_its_forks_alone():
    run_node_module(SETUP + r"""
        // Chat 1 is gone from the list; its forks still point at it.
        const chats = [forkOf(2, 1, 10, 50), forkOf(3, 1, 10, 70)];
        assert.deepEqual(branchesAt(chats, 2, 50), []);
        assert.deepEqual(branchesAt(chats, 3, 70), []);
    """)


def test_a_fork_whose_copy_was_deleted_is_left_out():
    run_node_module(SETUP + r"""
        const chats = [chat(1), forkOf(2, 1, 10, null)];
        assert.deepEqual(branchesAt(chats, 1, 10), []);
    """)


def test_an_unknown_chat_has_no_branches():
    run_node_module(SETUP + r"""
        assert.deepEqual(branchesAt([chat(1), forkOf(2, 1, 10, 50)], 9, 10), []);
        assert.deepEqual(branchesAt([], 1, 10), []);
    """)
