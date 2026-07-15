"""Message and swipe routes."""

from datetime import datetime

from flask import Blueprint, request, jsonify

from routes.chats import chat_to_dict
from shared import get_db, not_found, persona_avatar_url

messages_bp = Blueprint('messages', __name__)


MESSAGE_WITH_PERSONA_SQL = '''
    SELECT m.*, p.name AS persona_name, p.tagline AS persona_tagline,
           p.avatar_path AS persona_avatar_path,
           p.updated_at AS persona_updated_at
    FROM messages m
    LEFT JOIN personas p ON m.persona_id = p.id
'''


def _swipe_to_dict(row):
    return {
        'id': row['id'],
        'content': row['content'],
        'created_at': row['created_at'],
    }


def _message_to_dict(row, swipes=None):
    message = dict(row)
    message['persona_avatar_url'] = persona_avatar_url(
        message.get('persona_avatar_path'),
        message.get('persona_updated_at'),
    )
    message.pop('persona_avatar_path', None)
    message.pop('persona_updated_at', None)
    message['swipes'] = swipes or [{'id': None, 'content': row['content']}]
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

        name = datetime.now().strftime('%b %d %H:%M:%S')

        cur = conn.execute(
            'INSERT INTO chats (character_id, name, active_lorebook_id, active_lorebook_embedded) VALUES (?,?,?,?)',
            (chat['character_id'], name, chat['active_lorebook_id'], chat['active_lorebook_embedded'])
        )
        new_chat_id = cur.lastrowid

        messages_to_copy = conn.execute(
            'SELECT * FROM messages WHERE chat_id=? AND id <= ? ORDER BY id ASC',
            (chat_id, msg_id)
        ).fetchall()

        old_to_new = {}
        for m in messages_to_copy:
            cur = conn.execute(
                'INSERT INTO messages (chat_id, role, content, persona_id, created_at) VALUES (?,?,?,?,?)',
                (new_chat_id, m['role'], m['content'], m['persona_id'], m['created_at'])
            )
            old_to_new[m['id']] = cur.lastrowid

        if old_to_new:
            placeholders = ','.join('?' for _ in old_to_new)
            swipes = conn.execute(
                f'SELECT * FROM message_swipes WHERE message_id IN ({placeholders}) ORDER BY id ASC',
                list(old_to_new.keys())
            ).fetchall()
            for s in swipes:
                conn.execute(
                    'INSERT INTO message_swipes (message_id, content, created_at) VALUES (?,?,?)',
                    (old_to_new[s['message_id']], s['content'], s['created_at'])
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
                SELECT s.id, s.message_id, s.content, s.created_at
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
            _message_to_dict(row, swipes_by_message.get(row['id']))
            for row in rows
        ])


@messages_bp.route('/api/chats/<int:chat_id>/messages', methods=['POST'])
def add_message(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return not_found('Chat')
        data       = request.get_json(silent=True) or {}
        role       = data.get('role', '')
        content    = (data.get('content') or '').strip()
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
        # Bump the chat's updated_at so most-recent ordering works
        conn.execute('UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (chat_id,))
        row = conn.execute(MESSAGE_WITH_PERSONA_SQL + '''
            WHERE m.id=?
        ''', (msg_id,)).fetchone()
        return jsonify(_message_to_dict(row, [{'id': None, 'content': content}])), 201


# ── Swipe routes ───────────────────────────────────────────────────────────

@messages_bp.route('/api/messages/<int:msg_id>/swipes', methods=['GET'])
def list_swipes(msg_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM messages WHERE id=?', (msg_id,)).fetchone():
            return not_found('Message')
        rows = conn.execute(
            'SELECT id, content, created_at FROM message_swipes WHERE message_id=? ORDER BY id ASC',
            (msg_id,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@messages_bp.route('/api/messages/<int:msg_id>/swipes', methods=['POST'])
def add_swipe(msg_id):
    with get_db() as conn:
        msg = conn.execute('SELECT * FROM messages WHERE id=?', (msg_id,)).fetchone()
        if not msg:
            return not_found('Message')
        data = request.get_json(silent=True) or {}
        content = (data.get('content') or '').strip()
        if not content:
            return jsonify({'error': 'content is required'}), 400
        # Seed original content as first swipe if none exist yet
        existing = conn.execute(
            'SELECT COUNT(*) FROM message_swipes WHERE message_id=?', (msg_id,)
        ).fetchone()[0]
        if existing == 0:
            conn.execute(
                'INSERT INTO message_swipes (message_id, content) VALUES (?,?)',
                (msg_id, msg['content'])
            )
        cur = conn.execute(
            'INSERT INTO message_swipes (message_id, content) VALUES (?,?)', (msg_id, content)
        )
        # Update the message's main content to the new swipe
        conn.execute('UPDATE messages SET content=? WHERE id=?', (content, msg_id))
        row = conn.execute('SELECT * FROM message_swipes WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201


# ── Message update / delete ────────────────────────────────────────────────

@messages_bp.route('/api/messages/<int:msg_id>', methods=['PUT'])
def update_message(msg_id):
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    with get_db() as conn:
        row = conn.execute('SELECT id, content FROM messages WHERE id=?', (msg_id,)).fetchone()
        if not row:
            return not_found('Message')

        # Update the message's primary content
        conn.execute('UPDATE messages SET content=? WHERE id=?', (content, msg_id))
        # On edit (update_swipe), rewrite the matching swipe too so swiping
        # away and back doesn't resurrect the pre-edit text. Swipe *selection*
        # omits the flag — there the content must keep pointing at an existing
        # swipe row, not overwrite it.
        if data.get('update_swipe'):
            conn.execute(
                'UPDATE message_swipes SET content=? WHERE message_id=? AND content=?',
                (content, msg_id, row['content'])
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
