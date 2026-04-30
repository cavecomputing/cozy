"""Standalone lorebook CRUD + cross-storage helpers.

Standalone lorebooks store the same V2 ``character_book`` JSON object that
embedded lorebooks use, so the frontend resolver works on both without
modification. The two cross-storage endpoints (embed / extract) shuttle that
JSON between this table and a character card's PNG tEXt chunk via the existing
character update path.
"""

import json
import os

from flask import Blueprint, request, jsonify, Response

import shared
from shared import get_db
from png_utils import extract_png_chara, write_png_chara

lorebooks_bp = Blueprint('lorebooks', __name__)


def _empty_book(name=''):
    return {
        'name': name,
        'description': '',
        'scan_depth': 20,
        'max_entries': 20,
        'recursive_scanning': False,
        'extensions': {},
        'entries': [],
    }


def _parse_book(raw):
    try:
        b = json.loads(raw or '{}')
        return b if isinstance(b, dict) else _empty_book()
    except (TypeError, ValueError):
        return _empty_book()


def _summarise(row):
    """Dict for list view. Includes the full book — the request builder reads
    entries straight off `state.lorebooks` to inject into prompts, and a
    single-user app's lorebook collection is small enough to ship in full."""
    book = _parse_book(row['book'])
    entries = book.get('entries') or []
    return {
        'id': row['id'],
        'name': row['name'],
        'entry_count': len(entries) if isinstance(entries, list) else 0,
        'book': book,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def _full_dict(row):
    return {
        'id': row['id'],
        'name': row['name'],
        'book': _parse_book(row['book']),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def _safe_int(v, default):
    try:
        return int(v) if v is not None and v != '' else default
    except (TypeError, ValueError):
        return default


def _normalize_imported_book(raw):
    """Normalize an imported lorebook JSON into V2 ``character_book`` shape.

    Accepts:
      - A bare V2 character_book object: ``{name, entries: [...], ...}``
      - A wrapped one: ``{character_book: {...}}``
      - A full character card: ``{spec, data: {character_book: {...}}}``
      - SillyTavern world-info JSON: object-keyed entries with key/keysecondary
        aliases, ``order`` instead of ``insertion_order``, ``disable`` (inverted
        ``enabled``), and similar variations.

    Always emits the V2 shape with ``extensions`` defaulted to ``{}`` on the
    book and on every entry — those fields are required by the spec.
    """
    if not isinstance(raw, dict):
        raise ValueError('Lorebook must be a JSON object')

    # Unwrap common containers
    if isinstance(raw.get('character_book'), dict):
        raw = raw['character_book']
    elif isinstance(raw.get('data'), dict) and isinstance(raw['data'].get('character_book'), dict):
        raw = raw['data']['character_book']

    if 'entries' not in raw:
        raise ValueError('Lorebook is missing required "entries" field')

    book = {
        'name': str(raw.get('name', '')).strip(),
        'description': str(raw.get('description', '') or ''),
        'scan_depth': _safe_int(raw.get('scan_depth'), 20),
        'recursive_scanning': bool(raw.get('recursive_scanning', False)),
        'extensions': raw['extensions'] if isinstance(raw.get('extensions'), dict) else {},
        'entries': [],
    }
    if 'token_budget' in raw:
        book['token_budget'] = _safe_int(raw.get('token_budget'), 0)
    book['max_entries'] = _safe_int(raw.get('max_entries'), 20)

    raw_entries = raw['entries']
    if isinstance(raw_entries, dict):
        # SillyTavern world info: object keyed by index — flatten in numeric order
        try:
            keys = sorted(raw_entries.keys(), key=lambda k: int(k))
        except (TypeError, ValueError):
            keys = list(raw_entries.keys())
        raw_entries = [raw_entries[k] for k in keys]
    if not isinstance(raw_entries, list):
        raise ValueError('Lorebook "entries" must be an array or object')

    def _coerce_keys(v):
        if v is None:
            return []
        if isinstance(v, str):
            # ST sometimes stores keys as a comma-separated string
            return [k.strip() for k in v.split(',') if k.strip()]
        if isinstance(v, list):
            return [str(k) for k in v if k is not None and str(k) != '']
        return []

    for re in raw_entries:
        if not isinstance(re, dict):
            continue
        keys = _coerce_keys(re.get('keys') if 'keys' in re else re.get('key'))
        secondary = _coerce_keys(
            re.get('secondary_keys') if 'secondary_keys' in re else re.get('keysecondary')
        )

        if 'enabled' in re:
            enabled = bool(re['enabled'])
        elif 'disable' in re:
            enabled = not bool(re['disable'])
        else:
            enabled = True

        entry = {
            'keys': keys,
            'secondary_keys': secondary,
            'content': str(re.get('content', '') or ''),
            'comment': str(re.get('comment', '') or ''),
            'enabled': enabled,
            'constant': bool(re.get('constant', False)),
            'selective': bool(re.get('selective', False)),
            'case_sensitive': bool(re.get('case_sensitive', False)),
            'insertion_order': _safe_int(
                re.get('insertion_order') if 'insertion_order' in re else re.get('order'),
                100,
            ),
            'extensions': re['extensions'] if isinstance(re.get('extensions'), dict) else {},
        }
        # Preserve optional V2 spec fields when present
        for opt in ('name', 'priority', 'id', 'position'):
            if opt in re and re[opt] is not None:
                entry[opt] = re[opt]
        book['entries'].append(entry)

    return book


@lorebooks_bp.route('/api/lorebooks', methods=['GET'])
def list_lorebooks():
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM lorebooks ORDER BY name COLLATE NOCASE ASC'
        ).fetchall()
        return jsonify([_summarise(r) for r in rows])


@lorebooks_bp.route('/api/lorebooks', methods=['POST'])
def create_lorebook():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    book = data.get('book') if isinstance(data.get('book'), dict) else _empty_book(name)
    book.setdefault('name', name)
    book['name'] = name
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO lorebooks (name, book) VALUES (?, ?)',
            (name, json.dumps(book))
        )
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(_full_dict(row)), 201


@lorebooks_bp.route('/api/lorebooks/<int:book_id>', methods=['GET'])
def get_lorebook(book_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(_full_dict(row))


@lorebooks_bp.route('/api/lorebooks/<int:book_id>', methods=['PUT'])
def update_lorebook(book_id):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404

        existing = _parse_book(row['book'])
        if isinstance(data.get('book'), dict):
            existing = data['book']

        # Determine the canonical name. Explicit `name` field wins; otherwise
        # fall back to whatever is on the (possibly new) book; otherwise keep
        # the existing column value.
        name = (data.get('name') or '').strip()
        if not name:
            name = (existing.get('name') or row['name'] or '').strip() or row['name']
        existing['name'] = name

        conn.execute(
            'UPDATE lorebooks SET name=?, book=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (name, json.dumps(existing), book_id)
        )
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
        return jsonify(_full_dict(row))


@lorebooks_bp.route('/api/lorebooks/<int:book_id>', methods=['DELETE'])
def delete_lorebook(book_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        # Manually clear references on chats — SQLite ALTER TABLE can't add an
        # ON DELETE SET NULL FK after the fact.
        conn.execute(
            'UPDATE chats SET active_lorebook_id=NULL WHERE active_lorebook_id=?',
            (book_id,)
        )
        conn.execute('DELETE FROM lorebooks WHERE id=?', (book_id,))
        return jsonify({'ok': True})


# ── Cross-storage helpers ──────────────────────────────────────────────────

def _read_character_card(filepath):
    try:
        with open(filepath, 'rb') as f:
            return extract_png_chara(f.read())
    except (OSError, IOError):
        return None


def _write_character_book(char_id, new_book):
    """Embed *new_book* into the character card PNG, overwriting any existing
    ``character_book``. Returns the updated card dict on success, or an error
    string if the character is missing/unreadable."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT * FROM characters WHERE id=?', (char_id,)
        ).fetchone()
        if not row or row['missing']:
            return None, 'Character not found'

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        with open(filepath, 'rb') as f:
            png_bytes = f.read()

        existing_card = extract_png_chara(png_bytes) or {'data': {}}
        existing_data = existing_card.get('data', existing_card)
        existing_data['character_book'] = new_book

        card = {
            'spec': 'chara_card_v2',
            'spec_version': '2.0',
            'data': existing_data,
        }
        png_bytes = write_png_chara(png_bytes, card)
        with open(filepath, 'wb') as f:
            f.write(png_bytes)

        # Refresh CRC index
        import zlib
        with open(filepath, 'rb') as f:
            crc = format(zlib.crc32(f.read()) & 0xFFFFFFFF, '08x')
        conn.execute('UPDATE characters SET crc=? WHERE id=?', (crc, char_id))
        return card, None


@lorebooks_bp.route('/api/lorebooks/<int:book_id>/embed-in-character/<int:char_id>', methods=['POST'])
def embed_in_character(book_id, char_id):
    delete_standalone = request.args.get('delete_standalone') == '1'
    with get_db() as conn:
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Lorebook not found'}), 404
        book = _parse_book(row['book'])

    card, err = _write_character_book(char_id, book)
    if err:
        return jsonify({'error': err}), 404

    if delete_standalone:
        with get_db() as conn:
            conn.execute(
                'UPDATE chats SET active_lorebook_id=NULL WHERE active_lorebook_id=?',
                (book_id,)
            )
            conn.execute('DELETE FROM lorebooks WHERE id=?', (book_id,))

    return jsonify({'ok': True, 'character_book': book})


@lorebooks_bp.route('/api/characters/<int:char_id>/extract-lorebook', methods=['POST'])
def extract_from_character(char_id):
    clear_embedded = request.args.get('clear_embedded') == '1'
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row or row['missing']:
            return jsonify({'error': 'Character not found'}), 404

    filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
    card = _read_character_card(filepath)
    char_data = (card or {}).get('data', card or {})
    book = char_data.get('character_book')
    if not isinstance(book, dict) or not book.get('entries'):
        return jsonify({'error': 'This character has no embedded lorebook to extract'}), 400

    # Use the embedded book's name, falling back to the character's name.
    name = (book.get('name') or char_data.get('name') or 'Lorebook').strip()
    book.setdefault('name', name)

    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO lorebooks (name, book) VALUES (?, ?)',
            (name, json.dumps(book))
        )
        new_row = conn.execute(
            'SELECT * FROM lorebooks WHERE id=?', (cur.lastrowid,)
        ).fetchone()

    if clear_embedded:
        _write_character_book(char_id, None)

    return jsonify(_full_dict(new_row)), 201


# ── Import / export ────────────────────────────────────────────────────────

def _safe_filename(name, fallback='lorebook'):
    safe = ''.join(c for c in (name or '') if c.isalnum() or c in (' ', '-', '_')).strip()
    return safe or fallback


@lorebooks_bp.route('/api/lorebooks/import', methods=['POST'])
def import_lorebook():
    """Create a new standalone lorebook from a JSON payload.

    Accepts either a multipart upload with a ``file`` field, or a raw JSON
    body. The payload may be a bare V2 ``character_book`` object, a wrapped
    one, a full character card with embedded book, or SillyTavern world-info
    JSON — see :func:`_normalize_imported_book`.
    """
    payload = None
    if request.files and 'file' in request.files:
        try:
            payload = json.loads(request.files['file'].read().decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return jsonify({'error': f'Invalid JSON in upload: {e}'}), 400
    else:
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({'error': 'No JSON payload provided'}), 400

    try:
        book = _normalize_imported_book(payload)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    name_override = (request.form.get('name') or request.args.get('name') or '').strip()
    name = (name_override or book.get('name') or 'Imported Lorebook').strip() or 'Imported Lorebook'
    book['name'] = name

    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO lorebooks (name, book) VALUES (?, ?)',
            (name, json.dumps(book))
        )
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(_full_dict(row)), 201


@lorebooks_bp.route('/api/lorebooks/<int:book_id>/export', methods=['GET'])
def export_lorebook(book_id):
    """Download a standalone lorebook as a V2 ``character_book`` JSON file."""
    with get_db() as conn:
        row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        book = _parse_book(row['book'])

    filename = f"{_safe_filename(row['name'])}.json"
    return Response(
        json.dumps(book, indent=2, ensure_ascii=False),
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@lorebooks_bp.route('/api/characters/<int:char_id>/export-lorebook', methods=['GET'])
def export_character_lorebook(char_id):
    """Download a character's embedded lorebook as JSON."""
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row or row['missing']:
            return jsonify({'error': 'Character not found'}), 404

    filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
    card = _read_character_card(filepath)
    char_data = (card or {}).get('data', card or {})
    book = char_data.get('character_book')
    if not isinstance(book, dict) or not book.get('entries'):
        return jsonify({'error': 'This character has no embedded lorebook'}), 400

    name = book.get('name') or char_data.get('name') or 'lorebook'
    filename = f"{_safe_filename(name)}.json"
    return Response(
        json.dumps(book, indent=2, ensure_ascii=False),
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
