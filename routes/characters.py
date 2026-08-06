"""Character routes — file-based character card storage with PNG embedding."""

import json
import os

from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

import shared
from card_store import (
    CARD_DATA_DEFAULTS, card_data_fields, ensure_png, file_crc, file_crc_cached, get_character_card,
    normalize_to_v2, normalize_character_book, read_character_card, read_character_card_cached,
    write_character_card,
)
from shared import get_db, json_download, not_found
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


def _char_to_dict(row, card_data=None):
    """Merge a DB character row with parsed card data into an API response dict."""
    pinned = row['pinned_at'] is not None
    if row['missing']:
        return {
            'id': row['id'],
            'filename': row['filename'],
            'missing': True,
            'name': row['filename'].rsplit('.', 1)[0],
            'avatar_url': None,
            'pinned': pinned,
            'pinned_at': row['pinned_at'],
            'created_at': row['created_at'],
        }

    d = {
        'id': row['id'],
        'filename': row['filename'],
        'missing': bool(row['missing']),
        'created_at': row['created_at'],
        'avatar_url': f"/characters/{row['filename']}?v={row['crc']}",
        'pinned': pinned,
        'pinned_at': row['pinned_at'],
    }
    d.update(card_data_fields(card_data or {}))
    if not d.get('name'):
        d['name'] = row['filename'].rsplit('.', 1)[0]
    return d


def _char_json(row):
    """Return a character row merged with its card data as JSON."""
    if row['missing']:
        return jsonify(_char_to_dict(row))
    # Cached reader: this path only serializes the card, never mutates it.
    card = read_character_card_cached(os.path.join(shared.CHARACTERS_DIR, row['filename']))
    return jsonify(_char_to_dict(row, card))


def _sync_characters(conn):
    """Reconcile data/characters/ folder with the DB index."""
    # Scan disk. scandir gives is_file() straight from the directory read, and
    # file_crc_cached does the one stat it needs, so an unchanged library costs
    # a stat per card here instead of a full read of every file.
    disk_files = {}
    with os.scandir(shared.CHARACTERS_DIR) as entries:
        for entry in entries:
            name = entry.name
            if not name.lower().endswith('.png') or name.startswith('.'):
                continue
            if not entry.is_file():
                continue
            crc = file_crc_cached(entry.path)
            if crc is not None:      # vanished between listing and stat
                disk_files[name] = crc

    # Fetch DB records
    db_rows = conn.execute('SELECT * FROM characters').fetchall()
    db_by_crc = {}
    for r in db_rows:
        db_by_crc.setdefault(r['crc'], r)
    db_by_filename = {r['filename']: r for r in db_rows}
    matched_ids = set()

    # Pass 1: a file whose exact name is already indexed keeps that row. This
    # has to finish before any CRC matching starts — byte-identical cards share
    # a CRC, so a copy processed first would otherwise claim the original's row
    # and rename it, dropping the original from the listing entirely.
    unclaimed = []
    for filename, crc in disk_files.items():
        row = db_by_filename.get(filename)
        if row is None:
            unclaimed.append((filename, crc))
            continue
        matched_ids.add(row['id'])
        if row['crc'] != crc or row['missing']:
            conn.execute('UPDATE characters SET crc=?, missing=0 WHERE id=?',
                         (crc, row['id']))

    # Pass 2: whatever is left is either a rename or genuinely new. Only an
    # unclaimed row can be moved, so a filename is never assigned to two rows.
    for filename, crc in unclaimed:
        row = db_by_crc.get(crc)
        if row is not None and row['id'] not in matched_ids:
            matched_ids.add(row['id'])
            conn.execute('UPDATE characters SET filename=?, missing=0 WHERE id=?',
                         (filename, row['id']))
            continue

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
        rows = conn.execute(
            'SELECT * FROM characters ORDER BY pinned_at DESC NULLS LAST, created_at ASC'
        ).fetchall()
        result = []
        for row in rows:
            if row['missing']:
                result.append(_char_to_dict(row))
            else:
                card = read_character_card_cached(
                    os.path.join(shared.CHARACTERS_DIR, row['filename'])
                )
                result.append(_char_to_dict(row, card))
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
        return jsonify(_char_to_dict(row, extract_png_chara(png_bytes))), 201


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
        return jsonify(_char_to_dict(row, extract_png_chara(png_bytes))), 201


# -- Single character CRUD ---------------------------------------------------
@characters_bp.route('/api/characters/<int:char_id>', methods=['GET'])
def get_character(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return not_found('Character')
        return _char_json(row)


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
        return _char_json(row)


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
    return json_download(card_data, f'{safe}.json')


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
        return jsonify(_char_to_dict(row, card))


@characters_bp.route('/api/characters/<int:char_id>', methods=['DELETE'])
def delete_character(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return not_found('Character')

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        try:
            os.remove(filepath)
        except FileNotFoundError:
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
        return jsonify(_char_to_dict(row, extract_png_chara(png_bytes)))
