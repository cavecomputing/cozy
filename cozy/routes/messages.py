"""Message and swipe routes."""

from flask import Blueprint, request, jsonify

from cozy.routes.chats import chat_to_dict, default_chat_name
from cozy.shared import get_db, json_body, not_found, text_field
from cozy.summarizer import fork_summary

messages_bp = Blueprint('messages', __name__)


# The name is the persona's while it exists, and after that the copy
# delete_persona() left on the message.
MESSAGE_WITH_PERSONA_SQL = '''
    SELECT m.id, m.chat_id, m.role, m.content, m.persona_id, m.created_at,
           m.selected_swipe_id, COALESCE(p.name, m.persona_name) AS persona_name
    FROM messages m
    LEFT JOIN personas p ON m.persona_id = p.id
'''


def _swipe_to_dict(row):
    return {
        'id': row['id'],
        'content': row['content'],
        'created_at': row['created_at'],
        'edited_at': row['edited_at'],
    }


def _message_to_dict(row, swipes=()):
    message = dict(row)
    selected = message.pop('selected_swipe_id')
    # A message that was never swiped or edited has one swipe, a copy of its
    # own content. Sent with every message, that doubled the size of a chat,
    # so `swipes` is only there when it says more than `content` does, and
    # the page stands the content in for it otherwise.
    if len(swipes) > 1 or any(s['edited_at'] or s['content'] != row['content'] for s in swipes):
        message['swipes'] = swipes
        # Which of them is on show. None recorded means the first.
        message['swipe_index'] = next(
            (index for index, swipe in enumerate(swipes) if swipe['id'] == selected), 0
        )
    return message


@messages_bp.route('/api/chats/<int:chat_id>/fork', methods=['POST'])
def fork_chat(chat_id):
    """Create a new chat containing all messages up to and including message_id."""
    msg_id = request.args.get('message_id', type=int)
    if not msg_id:
        return jsonify({'error': 'message_id query parameter is required'}), 400

    with get_db() as conn:
        chat = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not chat:
            return not_found('Chat')
        msg = conn.execute('SELECT * FROM messages WHERE id=? AND chat_id=?', (msg_id, chat_id)).fetchone()
        if not msg:
            return not_found('Message')

        name = default_chat_name()

        # Where the fork hangs: on this chat at this message, unless this chat is
        # itself a fork cut at this very message, in which case the new one joins
        # it as a sibling so the branches that meet at a message stay one group.
        # (A parent that has since been deleted no longer counts.)
        parent_chat_id, parent_msg_id = chat_id, msg_id
        if chat['fork_msg_id'] == msg_id and chat['parent_chat_id'] and conn.execute(
            'SELECT 1 FROM chats WHERE id=?', (chat['parent_chat_id'],)
        ).fetchone():
            parent_chat_id, parent_msg_id = chat['parent_chat_id'], chat['parent_msg_id']

        # A fork continues the same conversation, so per-chat settings come with it.
        # The Author's Note especially: it is user-written text the docs point at for
        # anything that must always be remembered, and losing it here was silent.
        # Auto Summary too — its memory is remapped onto the copies below, since
        # leaving it behind turned memory off and let older history silently drop
        # out of the fork's context.
        cur = conn.execute(
            'INSERT INTO chats (character_id, name, active_lorebook_id, active_lorebook_embedded, '
            'author_note, persona_id, summary_enabled, parent_chat_id, parent_msg_id) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (chat['character_id'], name, chat['active_lorebook_id'], chat['active_lorebook_embedded'],
             chat['author_note'], chat['persona_id'], chat['summary_enabled'],
             parent_chat_id, parent_msg_id)
        )
        new_chat_id = cur.lastrowid

        messages_to_copy = conn.execute(
            'SELECT * FROM messages WHERE chat_id=? AND id <= ? ORDER BY id ASC',
            (chat_id, msg_id)
        ).fetchall()

        old_to_new = {}
        selected_swipes = {}
        for m in messages_to_copy:
            cur = conn.execute(
                'INSERT INTO messages (chat_id, role, content, persona_id, persona_name, created_at) '
                'VALUES (?,?,?,?,?,?)',
                (new_chat_id, m['role'], m['content'], m['persona_id'], m['persona_name'],
                 m['created_at'])
            )
            old_to_new[m['id']] = cur.lastrowid
            selected_swipes[m['selected_swipe_id']] = cur.lastrowid

        if old_to_new:
            placeholders = ','.join('?' for _ in old_to_new)
            swipes = conn.execute(
                f'SELECT * FROM message_swipes WHERE message_id IN ({placeholders}) ORDER BY id ASC',
                list(old_to_new.keys())
            ).fetchall()
            for s in swipes:
                cur = conn.execute(
                    'INSERT INTO message_swipes (message_id, content, created_at, edited_at) VALUES (?,?,?,?)',
                    (old_to_new[s['message_id']], s['content'], s['created_at'], s['edited_at'])
                )
                # The copy shows the swipe its original was showing.
                if s['id'] in selected_swipes:
                    conn.execute(
                        'UPDATE messages SET selected_swipe_id=? WHERE id=?',
                        (cur.lastrowid, selected_swipes[s['id']]),
                    )

        # The fork's own copy of the message it was cut at, where its branch
        # pill goes; the parent's is parent_msg_id.
        conn.execute('UPDATE chats SET fork_msg_id=? WHERE id=?', (old_to_new[msg_id], new_chat_id))

        summary_json, watermark = fork_summary(
            chat['summary_json'], chat['summary_up_to_msg_id'], msg_id, old_to_new
        )
        if summary_json:
            conn.execute(
                'UPDATE chats SET summary_json=?, summary_up_to_msg_id=? WHERE id=?',
                (summary_json, watermark, new_chat_id),
            )

        new_chat = conn.execute('SELECT * FROM chats WHERE id=?', (new_chat_id,)).fetchone()
        return jsonify(chat_to_dict(new_chat)), 201


@messages_bp.route('/api/chats/<int:chat_id>/messages', methods=['GET'])
def list_messages(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return not_found('Chat')
        rows = conn.execute(MESSAGE_WITH_PERSONA_SQL + '''
            WHERE m.chat_id=?
            ORDER BY m.id ASC
        ''', (chat_id,)).fetchall()
        swipes_by_message = {}
        if rows:
            swipes = conn.execute(
                '''
                SELECT s.id, s.message_id, s.content, s.created_at, s.edited_at
                FROM message_swipes s
                JOIN messages m ON m.id = s.message_id
                WHERE m.chat_id=?
                ORDER BY s.message_id ASC, s.id ASC
                ''',
                (chat_id,),
            ).fetchall()
            for swipe in swipes:
                swipes_by_message.setdefault(swipe['message_id'], []).append(_swipe_to_dict(swipe))

        return jsonify([
            _message_to_dict(row, swipes_by_message.get(row['id'], ()))
            for row in rows
        ])


@messages_bp.route('/api/chats/<int:chat_id>/messages', methods=['POST'])
def add_message(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return not_found('Chat')
        data       = json_body()
        role       = data.get('role', '')
        content    = text_field(data, 'content')
        persona_id = data.get('persona_id')
        if role not in ('user', 'character'):
            return jsonify({'error': 'role must be "user" or "character"'}), 400
        if not content:
            return jsonify({'error': 'content is required'}), 400
        cur = conn.execute(
            'INSERT INTO messages (chat_id, role, content, persona_id) VALUES (?,?,?,?)',
            (chat_id, role, content, persona_id)
        )
        msg_id = cur.lastrowid
        # Seed first swipe with the original content
        conn.execute(
            'INSERT INTO message_swipes (message_id, content) VALUES (?,?)', (msg_id, content)
        )
        # Bump the chat's updated_at so most-recent ordering works. A user message
        # also records its persona on the chat, so reopening it on another machine
        # restores the persona the conversation was using rather than whatever that
        # browser last selected.
        if role == 'user' and persona_id is not None:
            conn.execute(
                'UPDATE chats SET updated_at=CURRENT_TIMESTAMP, persona_id=? WHERE id=?',
                (persona_id, chat_id),
            )
        else:
            conn.execute('UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (chat_id,))
        row = conn.execute(MESSAGE_WITH_PERSONA_SQL + '''
            WHERE m.id=?
        ''', (msg_id,)).fetchone()
        return jsonify(_message_to_dict(row)), 201


# ── Swipe routes ───────────────────────────────────────────────────────────

@messages_bp.route('/api/messages/<int:msg_id>/swipes', methods=['POST'])
def add_swipe(msg_id):
    with get_db() as conn:
        msg = conn.execute('SELECT * FROM messages WHERE id=?', (msg_id,)).fetchone()
        if not msg:
            return not_found('Message')
        content = text_field(json_body(), 'content')
        if not content:
            return jsonify({'error': 'content is required'}), 400
        cur = conn.execute(
            'INSERT INTO message_swipes (message_id, content) VALUES (?,?)', (msg_id, content)
        )
        # A new swipe is the one on show.
        conn.execute(
            'UPDATE messages SET content=?, selected_swipe_id=? WHERE id=?',
            (content, cur.lastrowid, msg_id),
        )
        row = conn.execute('SELECT * FROM message_swipes WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201


# ── Message update / delete ────────────────────────────────────────────────

@messages_bp.route('/api/messages/<int:msg_id>', methods=['PUT'])
def update_message(msg_id):
    """Show one of a message's swipes, or rewrite it when `content` comes too.

    `swipe_index` names the swipe, counting from 0 in the order they were made.
    It is required either way. Text cannot name a swipe, since a regenerate that
    repeats itself leaves two holding the same text, and "the one on show" may
    not be the one the page had up: a page loaded before this route took an
    index pages through swipes by sending their text alone, which must not be
    read as an edit of whichever swipe is on show.
    """
    data = json_body()
    index = data.get('swipe_index')
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        return jsonify({'error': 'swipe_index is required'}), 400
    content = text_field(data, 'content')
    if 'content' in data and not content:
        return jsonify({'error': 'content required'}), 400
    with get_db() as conn:
        if not conn.execute('SELECT id FROM messages WHERE id=?', (msg_id,)).fetchone():
            return not_found('Message')
        swipe = conn.execute(
            'SELECT id, content FROM message_swipes WHERE message_id=? '
            'ORDER BY id ASC LIMIT 1 OFFSET ?',
            (msg_id, index),
        ).fetchone()
        if not swipe:
            return not_found('Swipe')
        # Saved unchanged, an edit is not an edit: the swipe is only marked
        # when its text actually differs from what it held.
        if content and content != swipe['content']:
            conn.execute(
                'UPDATE message_swipes SET content=?, edited_at=CURRENT_TIMESTAMP WHERE id=?',
                (content, swipe['id']),
            )
        # The message carries a copy of the text on show, for everything that
        # reads a chat without its swipes. These two routes are what write it.
        conn.execute(
            'UPDATE messages SET content=?, selected_swipe_id=? WHERE id=?',
            (content or swipe['content'], swipe['id'], msg_id),
        )
        return jsonify({'success': True})


@messages_bp.route('/api/messages/<int:msg_id>', methods=['DELETE'])
def delete_message(msg_id):
    with get_db() as conn:
        row = conn.execute('SELECT id FROM messages WHERE id=?', (msg_id,)).fetchone()
        if not row:
            return not_found('Message')
        conn.execute('DELETE FROM messages WHERE id=?', (msg_id,))
        return jsonify({'success': True})
