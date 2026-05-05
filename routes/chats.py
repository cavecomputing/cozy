"""Chat CRUD routes."""

import os

from flask import Blueprint, request, jsonify

import shared
from card_store import read_character_card
from shared import get_db

chats_bp = Blueprint('chats', __name__)


def _chat_to_dict(row):
    """Coerce the SQLite Row into a plain dict with normalised types for the
    new lorebook fields (the legacy DB columns may not exist on cold rows from
    older fixtures, so .keys() is consulted defensively)."""
    d = dict(row)
    d['active_lorebook_id'] = d.get('active_lorebook_id')
    d['active_lorebook_embedded'] = bool(d.get('active_lorebook_embedded') or 0)
    d['lorebook_notice_dismissed'] = bool(d.get('lorebook_notice_dismissed') or 0)
    return d


def _character_has_lorebook(conn, char_id):
    """True if the character's PNG card embeds a non-empty character_book."""
    row = conn.execute(
        'SELECT filename, missing FROM characters WHERE id=?', (char_id,)
    ).fetchone()
    if not row or row['missing']:
        return False
    filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
    card = read_character_card(filepath)
    if not card:
        return False
    data = card.get('data', card)
    book = data.get('character_book')
    if not isinstance(book, dict):
        return False
    entries = book.get('entries')
    return isinstance(entries, list) and len(entries) > 0


@chats_bp.route('/api/characters/<int:char_id>/chats', methods=['GET'])
def list_chats(char_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM characters WHERE id=?', (char_id,)).fetchone():
            return jsonify({'error': 'Character not found'}), 404
        rows = conn.execute(
            'SELECT * FROM chats WHERE character_id=? ORDER BY created_at ASC', (char_id,)
        ).fetchall()
        return jsonify([_chat_to_dict(r) for r in rows])


@chats_bp.route('/api/characters/<int:char_id>/chats', methods=['POST'])
def create_chat(char_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM characters WHERE id=?', (char_id,)).fetchone():
            return jsonify({'error': 'Character not found'}), 404
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip() or 'New Chat'
        embedded_default = 1 if _character_has_lorebook(conn, char_id) else 0
        cur = conn.execute(
            'INSERT INTO chats (character_id, name, active_lorebook_embedded) '
            'VALUES (?, ?, ?)',
            (char_id, name, embedded_default)
        )
        chat_id = cur.lastrowid
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return jsonify(_chat_to_dict(row)), 201


@chats_bp.route('/api/chats/<int:chat_id>', methods=['PUT'])
def update_chat(chat_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Chat not found'}), 404
        data = request.get_json(silent=True) or {}

        name = (data.get('name') or '').strip() or row['name']
        cur_lb_id = row['active_lorebook_id']
        cur_lb_embedded = row['active_lorebook_embedded'] or 0
        cur_notice = row['lorebook_notice_dismissed'] or 0

        # `active_lorebook_id`: explicit None clears, integer sets, omitted leaves alone.
        if 'active_lorebook_id' in data:
            new_id = data['active_lorebook_id']
            if new_id in (None, '', 0):
                cur_lb_id = None
            else:
                try:
                    new_id = int(new_id)
                except (TypeError, ValueError):
                    return jsonify({'error': 'active_lorebook_id must be an integer or null'}), 400
                exists = conn.execute(
                    'SELECT 1 FROM lorebooks WHERE id=?', (new_id,)
                ).fetchone()
                if not exists:
                    return jsonify({'error': 'Lorebook not found'}), 404
                cur_lb_id = new_id

        if 'active_lorebook_embedded' in data:
            cur_lb_embedded = 1 if data['active_lorebook_embedded'] else 0

        # When embedded is selected, clear any standalone id (mutually exclusive).
        if cur_lb_embedded:
            cur_lb_id = None

        if 'lorebook_notice_dismissed' in data:
            cur_notice = 1 if data['lorebook_notice_dismissed'] else 0

        conn.execute(
            'UPDATE chats SET name=?, active_lorebook_id=?, active_lorebook_embedded=?, '
            'lorebook_notice_dismissed=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (name, cur_lb_id, cur_lb_embedded, cur_notice, chat_id)
        )
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return jsonify(_chat_to_dict(row))


@chats_bp.route('/api/chats/<int:chat_id>', methods=['DELETE'])
def delete_chat_route(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return jsonify({'error': 'Chat not found'}), 404
        conn.execute('DELETE FROM chats WHERE id=?', (chat_id,))
        return jsonify({'success': True})
