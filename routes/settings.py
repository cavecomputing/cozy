"""Settings and system prompt routes."""

import json

from flask import Blueprint, request, jsonify, Response

from shared import (
    get_db,
    not_found,
    safe_download_name,
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_POST_HISTORY_TEMPLATE,
)

settings_bp = Blueprint('settings', __name__)

SETTINGS_KEYS = {
    'api_endpoint', 'api_key', 'api_model',
    'active_api_preset',
    'active_system_prompt',
    'sampler_temperature', 'sampler_top_p', 'sampler_top_k',
    'sampler_min_p', 'sampler_max_tokens', 'sampler_repetition_penalty',
    'sampler_dynatemp_range', 'sampler_dynatemp_exponent',
    'sampler_typical_p', 'sampler_top_n_sigma',
    'sampler_repeat_last_n',
    'sampler_presence_penalty', 'sampler_frequency_penalty',
    'sampler_dry_multiplier', 'sampler_dry_base',
    'sampler_dry_allowed_length', 'sampler_dry_penalty_last_n',
    'sampler_mirostat', 'sampler_mirostat_tau', 'sampler_mirostat_eta',
    'sampler_xtc_probability', 'sampler_xtc_threshold',
    'sampler_seed',
    'send_thinking',
    'active_samplers',
    'show_context_token_meter',
    'context_max_tokens',
    'lorebook_scan_depth_override',
    'lorebook_always_inject_all',
}


def mask_secret(value):
    if value:
        return {
            'api_key_masked': value[:3] + '…' + value[-4:] if len(value) > 8 else '•••••',
            'api_key_set': True,
        }
    return {'api_key_masked': '', 'api_key_set': False}


def is_masked_secret(value):
    if not value:
        return True
    value = str(value).strip()
    return not value or value.startswith('••') or '…' in value


def get_settings():
    with get_db() as conn:
        rows = conn.execute('SELECT key, value FROM settings').fetchall()
        return {r['key']: r['value'] for r in rows}


def _setting_value(value):
    return '' if value is None else str(value).strip()


def upsert_setting(conn, key, value):
    conn.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, value)
    )


@settings_bp.route('/api/settings', methods=['GET'])
def read_settings():
    s = get_settings()
    s.pop('context_max_messages', None)
    # Never send the full API key to the frontend — mask it
    s.update(mask_secret(s.get('api_key', '')))
    s.pop('api_key', None)
    return jsonify(s)


@settings_bp.route('/api/settings', methods=['PUT'])
def write_settings():
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        for key in SETTINGS_KEYS:
            if key in data:
                val = _setting_value(data[key])
                # Allow clearing, or setting a new value
                # For api_key, skip if the placeholder/masked value is sent back
                if key == 'api_key' and is_masked_secret(val):
                    continue
                upsert_setting(conn, key, val)
    return read_settings()


# ── System prompts CRUD ────────────────────────────────────────────────────

@settings_bp.route('/api/system-prompts/default-template', methods=['GET'])
def get_default_template():
    return jsonify({
        'template': DEFAULT_PROMPT_TEMPLATE,
        'post_history_template': DEFAULT_POST_HISTORY_TEMPLATE,
    })


@settings_bp.route('/api/system-prompts', methods=['GET'])
def list_system_prompts():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM system_prompts ORDER BY created_at ASC').fetchall()
        return jsonify([dict(r) for r in rows])


@settings_bp.route('/api/system-prompts', methods=['POST'])
def create_system_prompt():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    content = (data.get('content') or '').strip()
    post_history_content = data.get('post_history_content', DEFAULT_POST_HISTORY_TEMPLATE)
    if not isinstance(post_history_content, str):
        return jsonify({'error': '"post_history_content" must be a string'}), 400
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO system_prompts (name, content, post_history_content) VALUES (?, ?, ?)',
            (name, content, post_history_content)
        )
        row = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201


@settings_bp.route('/api/system-prompts/<int:prompt_id>', methods=['PUT'])
def update_system_prompt(prompt_id):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (prompt_id,)).fetchone()
        if not row:
            return not_found('System prompt')
        name = (data.get('name') or '').strip() or row['name']
        content = data.get('content', row['content'])
        post_history_content = data.get('post_history_content', row['post_history_content'])
        if not isinstance(post_history_content, str):
            return jsonify({'error': '"post_history_content" must be a string'}), 400
        conn.execute(
            'UPDATE system_prompts SET name = ?, content = ?, post_history_content = ?, '
            'updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (name, content, post_history_content, prompt_id)
        )
        updated = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (prompt_id,)).fetchone()
        return jsonify(dict(updated))


@settings_bp.route('/api/system-prompts/<int:prompt_id>', methods=['DELETE'])
def delete_system_prompt(prompt_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (prompt_id,)).fetchone()
        if not row:
            return not_found('System prompt')
        conn.execute('DELETE FROM system_prompts WHERE id = ?', (prompt_id,))
        return jsonify({'success': True})


# ── System prompt import / export ─────────────────────────────────────────

def _unique_prompt_name(conn, base):
    """Append " (n)" until the name is free."""
    base = (base or '').strip() or 'Imported Prompt'
    candidate = base
    n = 2
    while conn.execute(
        'SELECT 1 FROM system_prompts WHERE name = ?', (candidate,)
    ).fetchone():
        candidate = f'{base} ({n})'
        n += 1
    return candidate


@settings_bp.route('/api/system-prompts/import', methods=['POST'])
def import_system_prompt():
    """Create a paired prompt from an uploaded JSON file.

    Expects multipart upload with a ``file`` field whose contents parse as
    ``{"name": str, "content": str, "post_history_content": str}``.
    Legacy system-only prompt JSON is accepted and gets the default
    post-history template.
    """
    if not request.files or 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        payload = json.loads(request.files['file'].read().decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return jsonify({'error': f'Invalid JSON: {e}'}), 400
    if not isinstance(payload, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400

    content = payload.get('content', '')
    if not isinstance(content, str):
        return jsonify({'error': '"content" must be a string'}), 400
    post_history_content = payload.get('post_history_content', DEFAULT_POST_HISTORY_TEMPLATE)
    if not isinstance(post_history_content, str):
        return jsonify({'error': '"post_history_content" must be a string'}), 400
    raw_name = payload.get('name')
    base_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else 'Imported Prompt'

    with get_db() as conn:
        name = _unique_prompt_name(conn, base_name)
        cur = conn.execute(
            'INSERT INTO system_prompts (name, content, post_history_content) VALUES (?, ?, ?)',
            (name, content, post_history_content)
        )
        row = conn.execute(
            'SELECT * FROM system_prompts WHERE id = ?', (cur.lastrowid,)
        ).fetchone()
        return jsonify(dict(row)), 201


@settings_bp.route('/api/system-prompts/<int:prompt_id>/export', methods=['GET'])
def export_system_prompt(prompt_id):
    """Download a paired prompt as a {name, content, post_history_content} JSON file."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT name, content, post_history_content FROM system_prompts WHERE id = ?', (prompt_id,)
        ).fetchone()
        if not row:
            return not_found('System prompt')

    body = {
        'name': row['name'],
        'content': row['content'],
        'post_history_content': row['post_history_content'],
    }
    filename = f"{safe_download_name(row['name'], 'prompt')}.json"
    return Response(
        json.dumps(body, indent=2, ensure_ascii=False),
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── API Presets CRUD ──────────────────────────────────────────────────────

PRESET_FIELDS = ('api_endpoint', 'api_key', 'api_model', 'context_max_tokens')


def _mask_preset(row):
    """Return a dict with the api_key masked (same logic as read_settings)."""
    row = dict(row)
    d = {
        'id': row['id'],
        'name': row['name'],
        'api_endpoint': row['api_endpoint'],
        'api_model': row['api_model'],
        'context_max_tokens': row['context_max_tokens'],
        'created_at': row['created_at'],
    }
    d.update(mask_secret(row.get('api_key', '')))
    return d


@settings_bp.route('/api/presets', methods=['GET'])
def list_presets():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM api_presets ORDER BY created_at ASC').fetchall()
        return jsonify([_mask_preset(r) for r in rows])


@settings_bp.route('/api/presets', methods=['POST'])
def create_preset():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    # If no real key was provided, inherit the current one from settings
    key_val = data.get('api_key', '')
    if is_masked_secret(key_val):
        s = get_settings()
        key_val = s.get('api_key', '')
    with get_db() as conn:
        existing = conn.execute(
            'SELECT 1 FROM api_presets WHERE name = ?', (name,)
        ).fetchone()
        if existing:
            return jsonify({'error': f'A preset named "{name}" already exists'}), 409
        cur = conn.execute(
            'INSERT INTO api_presets (name, api_endpoint, api_key, api_model, context_max_tokens) '
            'VALUES (?, ?, ?, ?, ?)',
            (name, data.get('api_endpoint', ''), key_val,
             data.get('api_model', ''), data.get('context_max_tokens', '32768'))
        )
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (cur.lastrowid,)).fetchone()
        return jsonify(_mask_preset(row)), 201


@settings_bp.route('/api/presets/<int:preset_id>', methods=['PUT'])
def update_preset(preset_id):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return not_found('Preset')
        name = (data.get('name') or '').strip() or row['name']
        if name != row['name']:
            clash = conn.execute(
                'SELECT 1 FROM api_presets WHERE name = ? AND id != ?',
                (name, preset_id)
            ).fetchone()
            if clash:
                return jsonify({'error': f'A preset named "{name}" already exists'}), 409
        endpoint = data.get('api_endpoint', row['api_endpoint'])
        model = data.get('api_model', row['api_model'])
        ctx_tokens = data.get('context_max_tokens', row['context_max_tokens'])
        # Only update api_key if a real (non-masked) value was sent
        key_val = data.get('api_key', '')
        if not is_masked_secret(key_val):
            key = key_val
        else:
            key = row['api_key']
        conn.execute(
            'UPDATE api_presets SET name=?, api_endpoint=?, api_key=?, api_model=?, '
            'context_max_tokens=? WHERE id=?',
            (name, endpoint, key, model, ctx_tokens, preset_id)
        )
        updated = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        return jsonify(_mask_preset(updated))


@settings_bp.route('/api/presets/<int:preset_id>', methods=['DELETE'])
def delete_preset(preset_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return not_found('Preset')
        active = conn.execute(
            'SELECT value FROM settings WHERE key = ?',
            ('active_api_preset',)
        ).fetchone()
        conn.execute('DELETE FROM api_presets WHERE id = ?', (preset_id,))
        if active and active['value'] == str(preset_id):
            upsert_setting(conn, 'active_api_preset', '')
        return jsonify({'success': True})


@settings_bp.route('/api/presets/<int:preset_id>/activate', methods=['POST'])
def activate_preset(preset_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return not_found('Preset')
        # Write preset fields into the settings table
        for key in PRESET_FIELDS:
            upsert_setting(conn, key, row[key])
        upsert_setting(conn, 'active_api_preset', str(preset_id))
    return read_settings()
