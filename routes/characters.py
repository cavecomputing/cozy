"""Character routes — file-based character card storage with PNG embedding."""

import json
import os
import shutil
import sqlite3

from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

import shared
from card_store import (
    CARD_DATA_DEFAULTS, card_data_fields, ensure_png, file_crc, get_character_card, normalize_to_v2,
    normalize_character_book, read_character_card, write_character_card,
)
from shared import get_db, not_found
from png_utils import make_minimal_png, write_png_chara, extract_png_chara

characters_bp = Blueprint('characters', __name__)

ALLOWED_UPDATE_KEYS = set(CARD_DATA_DEFAULTS)


def _unique_filename(name):
    """Return a non-colliding PNG filename in shared.CHARACTERS_DIR based on *name*."""
    base = secure_filename(name) or 'character'
    if base.lower().endswith('.png'):
        base = base[:-4]
    candidate = f"{base}.png"
    counter = 2
    while os.path.exists(os.path.join(shared.CHARACTERS_DIR, candidate)):
        candidate = f"{base}_{counter}.png"
        counter += 1
    return candidate


def _collection_to_dict(row):
    keys = row.keys() if hasattr(row, 'keys') else []
    icon = (row['icon'] or '') if 'icon' in keys else ''
    return {
        'id': row['id'],
        'name': row['name'],
        'icon': icon,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def _collections_for_char(conn, char_id):
    rows = conn.execute(
        '''
        SELECT c.*
        FROM character_collections c
        JOIN character_collection_members m ON m.collection_id = c.id
        WHERE m.character_id=?
        ORDER BY c.name COLLATE NOCASE
        ''',
        (char_id,),
    ).fetchall()
    return [_collection_to_dict(row) for row in rows]


def _collections_by_char(conn, char_ids):
    if not char_ids:
        return {}
    placeholders = ','.join('?' for _ in char_ids)
    rows = conn.execute(
        f'''
        SELECT m.character_id, c.*
        FROM character_collection_members m
        JOIN character_collections c ON c.id = m.collection_id
        WHERE m.character_id IN ({placeholders})
        ORDER BY c.name COLLATE NOCASE
        ''',
        tuple(char_ids),
    ).fetchall()
    by_char = {char_id: [] for char_id in char_ids}
    for row in rows:
        by_char.setdefault(row['character_id'], []).append(_collection_to_dict(row))
    return by_char


def _char_to_dict(row, card_data=None, collections=None):
    """Merge a DB character row with parsed card data into an API response dict."""
    pinned = row['pinned_at'] is not None
    archived_at = row['archived_at'] if 'archived_at' in row.keys() else None
    if row['missing']:
        return {
            'id': row['id'],
            'filename': row['filename'],
            'missing': True,
            'name': row['filename'].rsplit('.', 1)[0],
            'avatar_url': None,
            'pinned': pinned,
            'pinned_at': row['pinned_at'],
            'archived_at': archived_at,
            'created_at': row['created_at'],
            'collections': collections or [],
        }

    d = {
        'id': row['id'],
        'filename': row['filename'],
        'missing': bool(row['missing']),
        'created_at': row['created_at'],
        'avatar_url': f"/characters/{row['filename']}?v={row['crc']}",
        'pinned': pinned,
        'pinned_at': row['pinned_at'],
        'archived_at': archived_at,
        'collections': collections or [],
    }
    d.update(card_data_fields(card_data or {}))
    if not d.get('name'):
        d['name'] = row['filename'].rsplit('.', 1)[0]
    return d


def _char_json(conn, row):
    """JSON response for a single character row: card data + collections."""
    collections = _collections_for_char(conn, row['id'])
    if row['missing']:
        return jsonify(_char_to_dict(row, collections=collections))
    card = read_character_card(os.path.join(shared.CHARACTERS_DIR, row['filename']))
    return jsonify(_char_to_dict(row, card, collections))


def _sync_characters(conn):
    """Reconcile data/characters/ folder with the DB index."""
    # Scan disk
    disk_files = {}
    for f in os.listdir(shared.CHARACTERS_DIR):
        if f.lower().endswith('.png') and not f.startswith('.'):
            filepath = os.path.join(shared.CHARACTERS_DIR, f)
            if os.path.isfile(filepath):
                disk_files[f] = file_crc(filepath)

    # Fetch DB records
    db_rows = conn.execute('SELECT * FROM characters').fetchall()
    db_by_crc = {}
    for r in db_rows:
        db_by_crc.setdefault(r['crc'], r)
    db_by_filename = {r['filename']: r for r in db_rows}
    matched_ids = set()
    disk_filenames = set(disk_files.keys())

    for filename, crc in disk_files.items():
        if crc in db_by_crc:
            row = db_by_crc[crc]
            matched_ids.add(row['id'])
            if row['filename'] != filename or row['missing']:
                # If the new filename is taken by a stale DB entry, remove it first
                stale = db_by_filename.get(filename)
                if stale and stale['id'] != row['id'] and stale['filename'] not in disk_filenames:
                    conn.execute('DELETE FROM characters WHERE id=?', (stale['id'],))
                conn.execute('UPDATE characters SET filename=?, missing=0 WHERE id=?',
                             (filename, row['id']))
        elif filename in db_by_filename:
            row = db_by_filename[filename]
            matched_ids.add(row['id'])
            conn.execute('UPDATE characters SET crc=?, missing=0 WHERE id=?',
                         (crc, row['id']))
        else:
            cur = conn.execute(
                'INSERT INTO characters (filename, crc) VALUES (?, ?)',
                (filename, crc)
            )
            matched_ids.add(cur.lastrowid)

    for row in db_rows:
        if row['id'] not in matched_ids and not row['missing']:
            conn.execute('UPDATE characters SET missing=1 WHERE id=?', (row['id'],))


# ── Character routes (file-based storage) ──────────────────────────────────

@characters_bp.route('/api/characters', methods=['GET'])
def list_characters():
    with get_db() as conn:
        _sync_characters(conn)
        include_archived = request.args.get('include_archived') in ('1', 'true', 'yes')
        archived_only = request.args.get('archived') in ('1', 'true', 'yes')
        where = ''
        if archived_only:
            where = 'WHERE archived_at IS NOT NULL'
        elif not include_archived:
            where = 'WHERE archived_at IS NULL'
        rows = conn.execute(
            f'SELECT * FROM characters {where} ORDER BY pinned_at DESC NULLS LAST, created_at ASC'
        ).fetchall()
        collections = _collections_by_char(conn, [row['id'] for row in rows])
        result = []
        for row in rows:
            if row['missing']:
                result.append(_char_to_dict(row, collections=collections.get(row['id'], [])))
            else:
                card = read_character_card(os.path.join(shared.CHARACTERS_DIR, row['filename']))
                result.append(_char_to_dict(row, card, collections.get(row['id'], [])))
        return jsonify(result)


@characters_bp.route('/api/characters', methods=['POST'])
def create_character():
    if 'image' not in request.files:
        return jsonify({'error': 'An image is required'}), 400

    try:
        data = json.loads(request.form.get('data', '{}'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({'error': 'Invalid character data'}), 400
    if not data.get('name', '').strip():
        return jsonify({'error': 'name is required'}), 400

    card = normalize_to_v2(data)
    png_bytes = ensure_png(request.files['image'].read())
    png_bytes = write_png_chara(png_bytes, card)

    filename = _unique_filename(data['name'])
    filepath = os.path.join(shared.CHARACTERS_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(png_bytes)

    crc = file_crc(filepath)
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO characters (filename, crc) VALUES (?, ?)', (filename, crc)
        )
        row = conn.execute('SELECT * FROM characters WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(_char_to_dict(row, extract_png_chara(png_bytes), [])), 201


# -- Import (must be registered before /<int:char_id> routes) ----------------
@characters_bp.route('/api/characters/import', methods=['POST'])
def import_character():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    fname = (file.filename or '')
    raw_bytes = file.read()
    card_data = None

    if fname.lower().endswith('.png'):
        card_data = extract_png_chara(raw_bytes)
        if not card_data:
            return jsonify({'error': 'No character data found in this PNG'}), 400
        png_bytes = raw_bytes
    elif fname.lower().endswith('.json'):
        try:
            card_data = json.loads(raw_bytes.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return jsonify({'error': 'Invalid JSON file'}), 400

        is_v2 = isinstance(card_data, dict) and card_data.get('spec') == 'chara_card_v2'
        is_v1 = isinstance(card_data, dict) and 'name' in card_data and 'data' not in card_data
        if not is_v2 and not is_v1:
            if not (isinstance(card_data, dict) and isinstance(card_data.get('data'), dict)):
                return jsonify({'error': 'File does not appear to be a valid Character Card (V1 or V2)'}), 400

        card = normalize_to_v2(card_data)
        png_bytes = write_png_chara(make_minimal_png(), card)
    else:
        return jsonify({'error': 'Unsupported file – use .json or .png'}), 400

    # Derive filename from character name
    data = card_data.get('data', card_data)
    name = data.get('name', 'character')
    filename = _unique_filename(name)
    filepath = os.path.join(shared.CHARACTERS_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(png_bytes)

    crc = file_crc(filepath)
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO characters (filename, crc) VALUES (?, ?)', (filename, crc)
        )
        row = conn.execute('SELECT * FROM characters WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(_char_to_dict(row, extract_png_chara(png_bytes), [])), 201


# -- Single character CRUD ---------------------------------------------------
@characters_bp.route('/api/characters/<int:char_id>', methods=['GET'])
def get_character(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return not_found('Character')
        return _char_json(conn, row)


@characters_bp.route('/api/characters/<int:char_id>/pin', methods=['POST'])
def toggle_pin_character(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return not_found('Character')
        if row['pinned_at']:
            conn.execute('UPDATE characters SET pinned_at=NULL WHERE id=?', (char_id,))
        else:
            conn.execute(
                'UPDATE characters SET pinned_at=CURRENT_TIMESTAMP WHERE id=?', (char_id,)
            )
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        return _char_json(conn, row)


@characters_bp.route('/api/characters/<int:char_id>/archive', methods=['POST'])
def archive_character(char_id):
    data = request.get_json(silent=True) or {}
    archived = bool(data.get('archived', True))
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return not_found('Character')
        if archived:
            conn.execute('UPDATE characters SET archived_at=CURRENT_TIMESTAMP WHERE id=?', (char_id,))
        else:
            conn.execute('UPDATE characters SET archived_at=NULL WHERE id=?', (char_id,))
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        return _char_json(conn, row)


@characters_bp.route('/api/characters/<int:char_id>/duplicate', methods=['POST'])
def duplicate_character(char_id):
    with get_db() as conn:
        row, card_data = get_character_card(conn, char_id)
        if not row:
            return not_found('Character')

        source_path = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        data = card_data.get('data', card_data) if card_data else {}
        copy_name = f"{data.get('name') or row['filename'].rsplit('.', 1)[0]} Copy"
        filename = _unique_filename(copy_name)
        target_path = os.path.join(shared.CHARACTERS_DIR, filename)
        shutil.copyfile(source_path, target_path)

        if card_data:
            card = normalize_to_v2(card_data)
            card.setdefault('data', {})
            card['data']['name'] = copy_name
            crc = write_character_card(target_path, card)
        else:
            crc = file_crc(target_path)
        cur = conn.execute(
            'INSERT INTO characters (filename, crc) VALUES (?, ?)', (filename, crc)
        )
        new_row = conn.execute('SELECT * FROM characters WHERE id=?', (cur.lastrowid,)).fetchone()
        new_card = read_character_card(target_path)
        return jsonify(_char_to_dict(new_row, new_card, [])), 201


@characters_bp.route('/api/characters/<int:char_id>/export', methods=['GET'])
def export_character(char_id):
    """
    Export a character card.
    ?fmt=png  -> serve the PNG file directly (it already has card data embedded)
    ?fmt=json -> extract card JSON from the PNG and return it
    """
    with get_db() as conn:
        row, card_data = get_character_card(conn, char_id)
        if not row:
            return not_found('Character')

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        data = card_data.get('data', card_data) if card_data else {}
        safe = shared.safe_download_name(data.get('name'), 'character')

    fmt = request.args.get('fmt', 'json').lower()

    if fmt == 'png':
        with open(filepath, 'rb') as f:
            return Response(
                f.read(),
                mimetype='image/png',
                headers={'Content-Disposition': f'attachment; filename="{safe}.png"'}
            )

    # Default: JSON
    if not card_data:
        card_data = normalize_to_v2({'name': safe})
    return Response(
        json.dumps(card_data, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{safe}.json"'}
    )


@characters_bp.route('/api/characters/<int:char_id>', methods=['PUT'])
def update_character(char_id):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row or row['missing']:
            return not_found('Character')

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        existing_card = read_character_card(filepath) or {'data': {}}
        existing_data = existing_card.get('data', existing_card)

        for key in data:
            if key in ALLOWED_UPDATE_KEYS:
                existing_data[key] = (
                    normalize_character_book(data[key])
                    if key == 'character_book'
                    else data[key]
                )

        card = {
            'spec': 'chara_card_v2',
            'spec_version': '2.0',
            'data': existing_data,
        }

        crc = write_character_card(filepath, card)
        conn.execute('UPDATE characters SET crc=? WHERE id=?', (crc, char_id))
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        return jsonify(_char_to_dict(row, card, _collections_for_char(conn, char_id)))


@characters_bp.route('/api/characters/<int:char_id>', methods=['DELETE'])
def delete_character(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return not_found('Character')

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass

        conn.execute('DELETE FROM characters WHERE id=?', (char_id,))
        return jsonify({'success': True})


# -- Avatar upload -----------------------------------------------------------
@characters_bp.route('/api/characters/<int:char_id>/avatar', methods=['POST'])
def upload_avatar(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row or row['missing']:
            return not_found('Character')

        if 'avatar' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['avatar']
        ext = shared.validate_image_extension(file)
        if not ext:
            return jsonify({'error': f'File type not allowed (use {", ".join(shared.ALLOWED_IMG)})'}), 400

        # Read card data from current file
        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        card_data = read_character_card(filepath)
        if not card_data:
            card_data = normalize_to_v2({'name': row['filename'].rsplit('.', 1)[0]})

        # Convert new image to PNG and embed card data
        png_bytes = ensure_png(file.read())
        png_bytes = write_png_chara(png_bytes, card_data)

        with open(filepath, 'wb') as f:
            f.write(png_bytes)

        crc = file_crc(filepath)
        conn.execute('UPDATE characters SET crc=? WHERE id=?', (crc, char_id))
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        return jsonify(_char_to_dict(row, extract_png_chara(png_bytes), _collections_for_char(conn, char_id)))


# -- Character collections ---------------------------------------------------
@characters_bp.route('/api/character-collections', methods=['GET'])
def list_character_collections():
    with get_db() as conn:
        rows = conn.execute(
            '''
            SELECT c.*, COUNT(ch.id) AS character_count
            FROM character_collections c
            LEFT JOIN character_collection_members m ON m.collection_id = c.id
            LEFT JOIN characters ch ON ch.id = m.character_id AND ch.archived_at IS NULL
            GROUP BY c.id
            ORDER BY c.name COLLATE NOCASE
            '''
        ).fetchall()
        return jsonify([
            {**_collection_to_dict(row), 'character_count': row['character_count']}
            for row in rows
        ])


@characters_bp.route('/api/character-collections', methods=['POST'])
def create_character_collection():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    icon = (data.get('icon') or '').strip()[:16]
    if not name:
        return jsonify({'error': 'name is required'}), 400
    with get_db() as conn:
        try:
            cur = conn.execute(
                'INSERT INTO character_collections (name, icon) VALUES (?, ?)',
                (name, icon),
            )
        except sqlite3.IntegrityError:
            return jsonify({'error': 'Collection name already exists'}), 400
        row = conn.execute(
            'SELECT * FROM character_collections WHERE id=?', (cur.lastrowid,)
        ).fetchone()
        return jsonify({**_collection_to_dict(row), 'character_count': 0}), 201


@characters_bp.route('/api/character-collections/<int:collection_id>', methods=['PUT'])
def update_character_collection(collection_id):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute(
            'SELECT * FROM character_collections WHERE id=?', (collection_id,)
        ).fetchone()
        if not row:
            return not_found('Collection')

        sets, params = [], []
        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                return jsonify({'error': 'name is required'}), 400
            sets.append('name=?')
            params.append(name)
        if 'icon' in data:
            sets.append('icon=?')
            params.append((data.get('icon') or '').strip()[:16])
        if not sets:
            return jsonify(_collection_to_dict(row))
        sets.append('updated_at=CURRENT_TIMESTAMP')
        params.append(collection_id)
        try:
            conn.execute(
                f'UPDATE character_collections SET {", ".join(sets)} WHERE id=?',
                params,
            )
        except sqlite3.IntegrityError:
            return jsonify({'error': 'Collection name already exists'}), 400
        row = conn.execute(
            'SELECT * FROM character_collections WHERE id=?', (collection_id,)
        ).fetchone()
        return jsonify(_collection_to_dict(row))


@characters_bp.route('/api/character-collections/<int:collection_id>', methods=['DELETE'])
def delete_character_collection(collection_id):
    with get_db() as conn:
        row = conn.execute(
            'SELECT * FROM character_collections WHERE id=?', (collection_id,)
        ).fetchone()
        if not row:
            return not_found('Collection')
        conn.execute('DELETE FROM character_collections WHERE id=?', (collection_id,))
        return jsonify({'success': True})


@characters_bp.route('/api/character-collections/<int:collection_id>/characters/<int:char_id>', methods=['POST'])
def add_character_to_collection(collection_id, char_id):
    with get_db() as conn:
        collection = conn.execute(
            'SELECT * FROM character_collections WHERE id=?', (collection_id,)
        ).fetchone()
        character = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not collection:
            return not_found('Collection')
        if not character:
            return not_found('Character')
        conn.execute(
            '''
            INSERT OR IGNORE INTO character_collection_members (collection_id, character_id)
            VALUES (?, ?)
            ''',
            (collection_id, char_id),
        )
        return _char_json(conn, character)


@characters_bp.route('/api/character-collections/<int:collection_id>/characters/<int:char_id>', methods=['DELETE'])
def remove_character_from_collection(collection_id, char_id):
    with get_db() as conn:
        conn.execute(
            '''
            DELETE FROM character_collection_members
            WHERE collection_id=? AND character_id=?
            ''',
            (collection_id, char_id),
        )
        character = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not character:
            return not_found('Character')
        return _char_json(conn, character)
