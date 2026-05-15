"""Character routes — file-based character card storage with PNG embedding."""

import os
import json

from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

import shared
from card_store import (
    ensure_png, file_crc, normalize_to_v2, read_character_card,
    write_character_card,
)
from shared import get_db
from png_utils import make_minimal_png, write_png_chara, extract_png_chara

characters_bp = Blueprint('characters', __name__)


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
    if row['missing']:
        return {
            'id': row['id'],
            'filename': row['filename'],
            'missing': True,
            'name': row['filename'].rsplit('.', 1)[0],
            'avatar_url': None,
            'created_at': row['created_at'],
        }

    d = {
        'id': row['id'],
        'filename': row['filename'],
        'missing': bool(row['missing']),
        'created_at': row['created_at'],
        'avatar_url': f"/characters/{row['filename']}",
    }
    if card_data:
        data = card_data.get('data', card_data)
        d.update({
            'name':                      data.get('name', 'Unnamed'),
            'description':               data.get('description', ''),
            'personality':               data.get('personality', ''),
            'scenario':                  data.get('scenario', ''),
            'first_mes':                 data.get('first_mes', ''),
            'mes_example':               data.get('mes_example', ''),
            'creator_notes':             data.get('creator_notes', ''),
            'system_prompt':             data.get('system_prompt', ''),
            'post_history_instructions': data.get('post_history_instructions', ''),
            'alternate_greetings':       data.get('alternate_greetings', []),
            'character_book':            data.get('character_book'),
            'tags':                      data.get('tags', []),
            'creator':                   data.get('creator', ''),
            'character_version':         data.get('character_version', ''),
            'extensions':                data.get('extensions', {}),
        })
    else:
        d['name'] = row['filename'].rsplit('.', 1)[0]
    return d


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
        rows = conn.execute('SELECT * FROM characters ORDER BY created_at ASC').fetchall()
        result = []
        for row in rows:
            if row['missing']:
                result.append(_char_to_dict(row))
            else:
                card = read_character_card(os.path.join(shared.CHARACTERS_DIR, row['filename']))
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

    file      = request.files['file']
    fname     = (file.filename or '')
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
        except Exception:
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
            return jsonify({'error': 'Not found'}), 404
        if row['missing']:
            return jsonify(_char_to_dict(row))
        card = read_character_card(os.path.join(shared.CHARACTERS_DIR, row['filename']))
        return jsonify(_char_to_dict(row, card))


@characters_bp.route('/api/characters/<int:char_id>/export', methods=['GET'])
def export_character(char_id):
    """
    Export a character card.
    ?fmt=png  -> serve the PNG file directly (it already has card data embedded)
    ?fmt=json -> extract card JSON from the PNG and return it
    """
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row or row['missing']:
            return jsonify({'error': 'Not found'}), 404

    filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
    card_data = read_character_card(filepath)
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
            return jsonify({'error': 'Not found'}), 404

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        with open(filepath, 'rb') as f:
            png_bytes = f.read()

        existing_card = extract_png_chara(png_bytes) or {'data': {}}
        existing_data = existing_card.get('data', existing_card)

        for key in data:
            existing_data[key] = data[key]

        card = {
            'spec': 'chara_card_v2',
            'spec_version': '2.0',
            'data': existing_data,
        }

        png_bytes, crc = write_character_card(filepath, card)
        conn.execute('UPDATE characters SET crc=? WHERE id=?', (crc, char_id))
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        return jsonify(_char_to_dict(row, card))


@characters_bp.route('/api/characters/<int:char_id>', methods=['DELETE'])
def delete_character(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404

        filepath = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)

        conn.execute('DELETE FROM characters WHERE id=?', (char_id,))
        return jsonify({'success': True})


# -- Avatar upload -----------------------------------------------------------
@characters_bp.route('/api/characters/<int:char_id>/avatar', methods=['POST'])
def upload_avatar(char_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
        if not row or row['missing']:
            return jsonify({'error': 'Not found'}), 404

        if 'avatar' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['avatar']
        ext  = (file.filename or '').rsplit('.', 1)[-1].lower()
        if ext not in shared.ALLOWED_IMG:
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
