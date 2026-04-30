"""Message and swipe routes."""

from flask import Blueprint, request, jsonify

from shared import get_db

messages_bp = Blueprint('messages', __name__)


@messages_bp.route('/api/chats/<int:chat_id>/messages', methods=['GET'])
def list_messages(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return jsonify({'error': 'Chat not found'}), 404
        rows = conn.execute('''
            SELECT m.*, p.name AS persona_name, p.tagline AS persona_tagline,
                   p.avatar_path AS persona_avatar_path
            FROM messages m
            LEFT JOIN personas p ON m.persona_id = p.id
            WHERE m.chat_id=?
            ORDER BY m.created_at ASC
        ''', (chat_id,)).fetchall()
        messages = []
        for r in rows:
            m = dict(r)
            if m.get('persona_avatar_path'):
                m['persona_avatar_url'] = f'/personas/{m["persona_avatar_path"]}'
            else:
                m['persona_avatar_url'] = None
            del m['persona_avatar_path']
            swipes = conn.execute(
                'SELECT id, content, created_at FROM message_swipes WHERE message_id=? ORDER BY id ASC',
                (r['id'],)
            ).fetchall()
            m['swipes'] = [dict(s) for s in swipes] if swipes else [{'id': None, 'content': r['content']}]
            messages.append(m)
        return jsonify(messages)


@messages_bp.route('/api/chats/<int:chat_id>/messages', methods=['POST'])
def add_message(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return jsonify({'error': 'Chat not found'}), 404
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
        row = conn.execute('''
            SELECT m.*, p.name AS persona_name, p.tagline AS persona_tagline,
                   p.avatar_path AS persona_avatar_path
            FROM messages m
            LEFT JOIN personas p ON m.persona_id = p.id
            WHERE m.id=?
        ''', (msg_id,)).fetchone()
        result = dict(row)
        if result.get('persona_avatar_path'):
            result['persona_avatar_url'] = f'/personas/{result["persona_avatar_path"]}'
        else:
            result['persona_avatar_url'] = None
        result.pop('persona_avatar_path', None)
        result['swipes'] = [{'id': None, 'content': content}]  # inline for convenience
        return jsonify(result), 201


# ── Swipe routes ───────────────────────────────────────────────────────────

@messages_bp.route('/api/messages/<int:msg_id>/swipes', methods=['GET'])
def list_swipes(msg_id):
    with get_db() as conn:
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
            return jsonify({'error': 'Message not found'}), 404
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
    data = request.get_json(force=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    with get_db() as conn:
        row = conn.execute('SELECT id FROM messages WHERE id = ?', (msg_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Message not found'}), 404
        conn.execute('UPDATE messages SET content = ? WHERE id = ?', (content, msg_id))
        return jsonify({'ok': True})


@messages_bp.route('/api/messages/<int:msg_id>', methods=['DELETE'])
def delete_message(msg_id):
    with get_db() as conn:
        row = conn.execute('SELECT id FROM messages WHERE id = ?', (msg_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Message not found'}), 404
        conn.execute('DELETE FROM messages WHERE id = ?', (msg_id,))
        return jsonify({'ok': True})
