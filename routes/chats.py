"""Chat CRUD routes."""

import json
import os
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response

from card_store import get_character_card_data
from shared import get_db, not_found, safe_download_name
from summarizer import parse_summary_json, dump_summary_json

chats_bp = Blueprint('chats', __name__)


def chat_to_dict(row):
    """Coerce the SQLite Row into a plain dict with normalised lorebook flags."""
    d = dict(row)
    d['active_lorebook_id'] = d.get('active_lorebook_id')
    d['active_lorebook_embedded'] = bool(d.get('active_lorebook_embedded') or 0)
    d['lorebook_notice_dismissed'] = bool(d.get('lorebook_notice_dismissed') or 0)
    if 'summary_enabled' in d:
        d['summary_enabled'] = bool(d.get('summary_enabled') or 0)
        # Expose the summary as a structured object (the raw summary_json string
        # stays too, for pin-toggle round-trips).
        d['summary'] = parse_summary_json(d.get('summary_json'))
    return d


def _character_has_lorebook(conn, char_id):
    """True if the character's PNG card embeds a non-empty character_book."""
    data = get_character_card_data(conn, char_id)
    book = data.get('character_book')
    if not isinstance(book, dict):
        return False
    entries = book.get('entries')
    return isinstance(entries, list) and len(entries) > 0


def _ensure_utc_iso(value):
    """Return a UTC-aware ISO-8601 string.

    If *value* is falsy (NULL / empty) the current UTC time is used.
    Otherwise the value is parsed, forced to UTC, and re-serialised so
    callers always get a consistent ``+00:00``-suffixed string.
    """
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def _read_character_name(conn, char_id):
    data = get_character_card_data(conn, char_id)
    return data.get('name') or 'Character'


def _default_user_name(conn, chat_id):
    row = conn.execute('''
        SELECT p.name
        FROM messages m
        JOIN personas p ON m.persona_id = p.id
        WHERE m.chat_id=? AND m.role='user' AND p.name IS NOT NULL
        ORDER BY m.id ASC
        LIMIT 1
    ''', (chat_id,)).fetchone()
    if row and row['name']:
        return row['name']
    row = conn.execute(
        'SELECT name FROM personas WHERE is_default=1 ORDER BY id ASC LIMIT 1'
    ).fetchone()
    return row['name'] if row and row['name'] else 'User'


def _chat_jsonl(conn, chat_id):
    chat = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
    if not chat:
        return None, None

    char_name = _read_character_name(conn, chat['character_id'])
    user_name = _default_user_name(conn, chat_id)
    lines = [{
        'user_name': user_name,
        'character_name': char_name,
        'create_date': _ensure_utc_iso(chat['created_at']),
        'chat_metadata': {},
    }]

    rows = conn.execute('''
        SELECT m.*, p.name AS persona_name
        FROM messages m
        LEFT JOIN personas p ON m.persona_id = p.id
        WHERE m.chat_id=?
        ORDER BY m.id ASC
    ''', (chat_id,)).fetchall()
    swipes_by_message = {}
    if rows:
        swipes = conn.execute(
            '''
            SELECT s.message_id, s.content
            FROM message_swipes s
            JOIN messages m ON m.id = s.message_id
            WHERE m.chat_id=?
            ORDER BY s.message_id ASC, s.id ASC
            ''',
            (chat_id,),
        ).fetchall()
        for swipe in swipes:
            swipes_by_message.setdefault(swipe['message_id'], []).append(swipe['content'])

    for row in rows:
        is_user = row['role'] == 'user'
        swipe_texts = swipes_by_message.get(row['id']) or [row['content']]
        try:
            swipe_id = swipe_texts.index(row['content'])
        except ValueError:
            swipe_id = 0

        item = {
            'name': (row['persona_name'] if is_user else char_name) or user_name,
            'is_user': is_user,
            'send_date': _ensure_utc_iso(row['created_at']),
            'mes': row['content'],
        }
        if len(swipe_texts) > 1:
            item['swipe_id'] = swipe_id
            item['swipes'] = swipe_texts
        lines.append(item)

    body = '\n'.join(json.dumps(line, ensure_ascii=False) for line in lines) + '\n'
    return chat, body


def _parse_jsonl(raw):
    rows = []
    for idx, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f'Line {idx}: invalid JSON ({e.msg})') from e
        if not isinstance(parsed, dict):
            raise ValueError(f'Line {idx}: expected a JSON object')
        rows.append(parsed)
    if not rows:
        raise ValueError('Chat file is empty')
    return rows


def _normalise_swipes(message, warnings, line_no):
    content = str(message.get('mes') or '')
    raw_swipes = message.get('swipes')
    if raw_swipes is None:
        return [content], 0
    if not isinstance(raw_swipes, list):
        warnings.append(f'Line {line_no}: ignored non-array swipes field')
        return [content], 0
    swipes = [s for s in raw_swipes if isinstance(s, str)]
    if len(swipes) != len(raw_swipes):
        warnings.append(f'Line {line_no}: ignored non-text swipe entries')
    if not swipes:
        return [content], 0
    try:
        swipe_id = int(message.get('swipe_id', 0))
    except (TypeError, ValueError):
        swipe_id = 0
        warnings.append(f'Line {line_no}: invalid swipe_id; selected the first swipe')
    if content not in swipes:
        insert_at = min(max(swipe_id, 0), len(swipes))
        swipes.insert(insert_at, content)
        warnings.append(f'Line {line_no}: mes was not present in swipes; added it for round-trip safety')
    swipe_id = min(max(swipe_id, 0), len(swipes) - 1)
    return swipes, swipe_id


@chats_bp.route('/api/characters/<int:char_id>/chats', methods=['GET'])
def list_chats(char_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM characters WHERE id=?', (char_id,)).fetchone():
            return not_found('Character')
        rows = conn.execute(
            'SELECT * FROM chats WHERE character_id=? ORDER BY created_at ASC, id ASC', (char_id,)
        ).fetchall()
        return jsonify([chat_to_dict(r) for r in rows])


@chats_bp.route('/api/characters/<int:char_id>/chats', methods=['POST'])
def create_chat(char_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM characters WHERE id=?', (char_id,)).fetchone():
            return not_found('Character')
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
        return jsonify(chat_to_dict(row)), 201


@chats_bp.route('/api/chats/<int:chat_id>/export', methods=['GET'])
def export_chat(chat_id):
    with get_db() as conn:
        chat, body = _chat_jsonl(conn, chat_id)
        if chat is None:
            return not_found('Chat')
        filename = f"{safe_download_name(chat['name'], 'chat')}.jsonl"
        return Response(
            body,
            mimetype='application/jsonl; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )


@chats_bp.route('/api/chats/import', methods=['POST'])
def import_chat():
    try:
        char_id = int(request.args.get('character_id', ''))
    except (TypeError, ValueError):
        return jsonify({'error': 'character_id is required'}), 400
    if not request.files or 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    upload = request.files['file']
    try:
        rows = _parse_jsonl(upload.read().decode('utf-8'))
    except UnicodeDecodeError:
        return jsonify({'error': 'File must be UTF-8 text'}), 400
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    header = rows[0]
    messages = rows[1:]
    warnings = []
    with get_db() as conn:
        if not conn.execute('SELECT id FROM characters WHERE id=?', (char_id,)).fetchone():
            return not_found('Character')

        name = os.path.splitext(upload.filename or '')[0] or 'Imported Chat'
        if header.get('create_date'):
            name = f'Imported {header["create_date"]}'
        cur = conn.execute(
            'INSERT INTO chats (character_id, name, active_lorebook_embedded) VALUES (?, ?, ?)',
            (char_id, name[:120], 1 if _character_has_lorebook(conn, char_id) else 0)
        )
        chat_id = cur.lastrowid

        for line_no, message in enumerate(messages, start=2):
            if message.get('is_system'):
                warnings.append(f'Line {line_no}: skipped system message; Cozy does not store hidden/system chat rows')
                continue
            if 'extra' in message:
                warnings.append(f'Line {line_no}: ignored extra metadata')
            if 'swipe_info' in message:
                warnings.append(f'Line {line_no}: ignored swipe_info metadata')

            content = str(message.get('mes') or '').strip()
            if not content:
                warnings.append(f'Line {line_no}: skipped empty message')
                continue
            role = 'user' if message.get('is_user') else 'character'
            swipes, swipe_id = _normalise_swipes(message, warnings, line_no)
            selected = swipes[swipe_id] if swipes else content

            cur = conn.execute(
                'INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)',
                (chat_id, role, selected)
            )
            msg_id = cur.lastrowid
            for swipe in swipes:
                if swipe.strip():
                    conn.execute(
                        'INSERT INTO message_swipes (message_id, content) VALUES (?, ?)',
                        (msg_id, swipe)
                    )

        conn.execute('UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (chat_id,))
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        result = chat_to_dict(row)
        result['warnings'] = warnings
        return jsonify(result), 201


@chats_bp.route('/api/chats/<int:chat_id>', methods=['PUT'])
def update_chat(chat_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not row:
            return not_found('Chat')
        data = request.get_json(silent=True) or {}

        name = (data.get('name') or '').strip() or row['name']
        cur_lb_id = row['active_lorebook_id']
        cur_lb_embedded = row['active_lorebook_embedded'] or 0
        cur_notice = row['lorebook_notice_dismissed'] or 0
        cur_author_note = row['author_note'] or ''
        cur_summary_enabled = row['summary_enabled'] or 0
        cur_summary_json = row['summary_json'] or ''

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
                    return not_found('Lorebook')
                cur_lb_id = new_id

        if 'active_lorebook_embedded' in data:
            cur_lb_embedded = 1 if data['active_lorebook_embedded'] else 0

        # When embedded is selected, clear any standalone id (mutually exclusive).
        if cur_lb_embedded:
            cur_lb_id = None

        if 'lorebook_notice_dismissed' in data:
            cur_notice = 1 if data['lorebook_notice_dismissed'] else 0

        if 'author_note' in data:
            cur_author_note = str(data['author_note'] or '')

        # Auto Summaries: the client toggles enablement and edits pins (the whole
        # summary object) here. The watermark (summary_up_to_msg_id) and status are
        # server-managed by the summary run endpoint and are NOT accepted from the client.
        if 'summary_enabled' in data:
            cur_summary_enabled = 1 if data['summary_enabled'] else 0
        if 'summary_json' in data:
            raw = data['summary_json']
            if isinstance(raw, (dict, list)):
                raw = json.dumps(raw)
            # Round-trip through the sanitizer so only well-formed lines are stored.
            cur_summary_json = dump_summary_json(parse_summary_json(str(raw or '')))

        conn.execute(
            'UPDATE chats SET name=?, active_lorebook_id=?, active_lorebook_embedded=?, '
            'lorebook_notice_dismissed=?, author_note=?, summary_enabled=?, summary_json=?, '
            'updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (name, cur_lb_id, cur_lb_embedded, cur_notice, cur_author_note,
             cur_summary_enabled, cur_summary_json, chat_id)
        )
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return jsonify(chat_to_dict(row))


@chats_bp.route('/api/chats/<int:chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    with get_db() as conn:
        if not conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone():
            return not_found('Chat')
        conn.execute('DELETE FROM chats WHERE id=?', (chat_id,))
        return jsonify({'success': True})
