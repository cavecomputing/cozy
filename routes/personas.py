"""Persona CRUD routes."""

import os

from flask import Blueprint, request, jsonify

import shared
from shared import get_db

personas_bp = Blueprint('personas', __name__)


def _avatar_cache_key(updated_at):
    # updated_at is a SQLite timestamp like '2026-05-15 01:33:45'; strip the
    # space/colons so it's safe inside an unquoted CSS url(...).
    return (updated_at or '').replace(' ', 'T').replace(':', '')


def persona_avatar_url(avatar_path, updated_at):
    if not avatar_path:
        return None
    return f'/personas/{avatar_path}?v={_avatar_cache_key(updated_at)}'


def persona_to_dict(row):
    d = dict(row)
    d['avatar_url'] = persona_avatar_url(d.get('avatar_path'), d.get('updated_at'))
    return d


@personas_bp.route('/api/personas', methods=['GET'])
def list_personas():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM personas ORDER BY is_default DESC, created_at ASC').fetchall()
        return jsonify([persona_to_dict(r) for r in rows])


@personas_bp.route('/api/personas', methods=['POST'])
def create_persona():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    tagline = (data.get('tagline') or '').strip()
    description = (data.get('description') or '').strip()
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO personas (name, tagline, description) VALUES (?,?,?)', (name, tagline, description)
        )
        row = conn.execute('SELECT * FROM personas WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(persona_to_dict(row)), 201


@personas_bp.route('/api/personas/<int:persona_id>', methods=['PUT'])
def update_persona(persona_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip() or row['name']
        tagline = (data.get('tagline') or row['tagline'] or '').strip()
        description = (data.get('description') or row['description'] or '').strip()
        conn.execute(
            'UPDATE personas SET name=?, tagline=?, description=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (name, tagline, description, persona_id)
        )
        row = conn.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
        return jsonify(persona_to_dict(row))


@personas_bp.route('/api/personas/<int:persona_id>', methods=['DELETE'])
def delete_persona(persona_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row['is_default']:
            return jsonify({'error': 'Cannot delete the default persona'}), 400
        if row['avatar_path']:
            avatar_file = os.path.join(shared.PERSONAS_DIR, row['avatar_path'])
            if os.path.exists(avatar_file):
                os.remove(avatar_file)
        conn.execute('DELETE FROM personas WHERE id=?', (persona_id,))
        return jsonify({'success': True})


@personas_bp.route('/api/personas/<int:persona_id>/avatar', methods=['POST'])
def upload_persona_avatar(persona_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if 'avatar' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files['avatar']
        ext = (file.filename or '').rsplit('.', 1)[-1].lower()
        if ext not in shared.ALLOWED_IMG:
            return jsonify({'error': f'File type not allowed (use {", ".join(shared.ALLOWED_IMG)})'}), 400
        if row['avatar_path']:
            old = os.path.join(shared.PERSONAS_DIR, row['avatar_path'])
            if os.path.exists(old):
                os.remove(old)
        filename = f"{persona_id}.{ext}"
        file.save(os.path.join(shared.PERSONAS_DIR, filename))
        conn.execute(
            'UPDATE personas SET avatar_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (filename, persona_id)
        )
        row = conn.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
        return jsonify(persona_to_dict(row))
