"""Settings and system prompt routes."""

from flask import Blueprint, request, jsonify

from shared import get_db, DEFAULT_PROMPT_TEMPLATE

settings_bp = Blueprint('settings', __name__)

SETTINGS_KEYS = {
    'api_endpoint', 'api_key', 'api_model',
    'active_system_prompt',
    'sampler_temperature', 'sampler_top_p', 'sampler_top_k',
    'sampler_min_p', 'sampler_max_tokens', 'sampler_repetition_penalty',
    'sampler_dynatemp_range', 'sampler_dynatemp_exponent',
    'sampler_typical_p', 'sampler_top_n_sigma', 'sampler_tfs_z',
    'sampler_repeat_last_n',
    'sampler_presence_penalty', 'sampler_frequency_penalty',
    'sampler_dry_multiplier', 'sampler_dry_base',
    'sampler_dry_allowed_length', 'sampler_dry_penalty_last_n',
    'sampler_mirostat', 'sampler_mirostat_tau', 'sampler_mirostat_eta',
    'sampler_xtc_probability', 'sampler_xtc_threshold',
    'sampler_seed',
    'send_thinking',
    'active_samplers',
    'context_max_messages',
    'lorebook_scan_depth_override',
    'lorebook_always_inject_all',
}


def get_settings():
    with get_db() as conn:
        rows = conn.execute('SELECT key, value FROM settings').fetchall()
        return {r['key']: r['value'] for r in rows}


@settings_bp.route('/api/settings', methods=['GET'])
def read_settings():
    s = get_settings()
    # Never send the full API key to the frontend — mask it
    if 'api_key' in s and s['api_key']:
        k = s['api_key']
        s['api_key_masked'] = k[:3] + '…' + k[-4:] if len(k) > 8 else '••••'
        s['api_key_set'] = True
    else:
        s['api_key_masked'] = ''
        s['api_key_set'] = False
    s.pop('api_key', None)
    return jsonify(s)


@settings_bp.route('/api/settings', methods=['PUT'])
def write_settings():
    data = request.get_json(force=True) or {}
    with get_db() as conn:
        for key in SETTINGS_KEYS:
            if key in data:
                val = str(data[key] or '').strip()
                # Allow clearing, or setting a new value
                # For api_key, skip if the placeholder/masked value is sent back
                if key == 'api_key' and (not val or val.startswith('••') or '\u2026' in val):
                    continue
                conn.execute(
                    'INSERT INTO settings (key, value) VALUES (?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, val)
                )
    return read_settings()


# ── System prompts CRUD ────────────────────────────────────────────────────

@settings_bp.route('/api/system-prompts/default-template', methods=['GET'])
def get_default_template():
    return jsonify({'template': DEFAULT_PROMPT_TEMPLATE})


@settings_bp.route('/api/system-prompts', methods=['GET'])
def list_system_prompts():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM system_prompts ORDER BY created_at ASC').fetchall()
        return jsonify([dict(r) for r in rows])


@settings_bp.route('/api/system-prompts', methods=['POST'])
def create_system_prompt():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    content = (data.get('content') or '').strip()
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO system_prompts (name, content) VALUES (?, ?)',
            (name, content)
        )
        row = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201


@settings_bp.route('/api/system-prompts/<int:prompt_id>', methods=['PUT'])
def update_system_prompt(prompt_id):
    data = request.get_json(force=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (prompt_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        name = (data.get('name') or '').strip() or row['name']
        content = data.get('content', row['content'])
        conn.execute(
            'UPDATE system_prompts SET name = ?, content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (name, content, prompt_id)
        )
        updated = conn.execute('SELECT * FROM system_prompts WHERE id = ?', (prompt_id,)).fetchone()
        return jsonify(dict(updated))


@settings_bp.route('/api/system-prompts/<int:prompt_id>', methods=['DELETE'])
def delete_system_prompt(prompt_id):
    with get_db() as conn:
        conn.execute('DELETE FROM system_prompts WHERE id = ?', (prompt_id,))
        return jsonify({'ok': True})


# ── API Presets CRUD ──────────────────────────────────────────────────────

PRESET_FIELDS = ('api_endpoint', 'api_key', 'api_model', 'context_max_messages')


def _mask_preset(row):
    """Return a dict with the api_key masked (same logic as read_settings)."""
    d = dict(row)
    k = d.get('api_key', '')
    if k:
        d['api_key_masked'] = k[:3] + '\u2026' + k[-4:] if len(k) > 8 else '\u2022\u2022\u2022\u2022'
        d['api_key_set'] = True
    else:
        d['api_key_masked'] = ''
        d['api_key_set'] = False
    d.pop('api_key', None)
    return d


@settings_bp.route('/api/presets', methods=['GET'])
def list_presets():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM api_presets ORDER BY created_at ASC').fetchall()
        return jsonify([_mask_preset(r) for r in rows])


@settings_bp.route('/api/presets', methods=['POST'])
def create_preset():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    # If no real key was provided, inherit the current one from settings
    key_val = data.get('api_key', '')
    if not key_val or key_val.startswith('\u2022\u2022') or '\u2026' in key_val:
        s = get_settings()
        key_val = s.get('api_key', '')
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO api_presets (name, api_endpoint, api_key, api_model, context_max_messages) '
            'VALUES (?, ?, ?, ?, ?)',
            (name, data.get('api_endpoint', ''), key_val,
             data.get('api_model', ''), data.get('context_max_messages', '0'))
        )
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (cur.lastrowid,)).fetchone()
        return jsonify(_mask_preset(row)), 201


@settings_bp.route('/api/presets/<int:preset_id>', methods=['PUT'])
def update_preset(preset_id):
    data = request.get_json(force=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        name = (data.get('name') or '').strip() or row['name']
        endpoint = data.get('api_endpoint', row['api_endpoint'])
        model = data.get('api_model', row['api_model'])
        ctx = data.get('context_max_messages', row['context_max_messages'])
        # Only update api_key if a real (non-masked) value was sent
        key_val = data.get('api_key', '')
        if key_val and not key_val.startswith('\u2022\u2022') and '\u2026' not in key_val:
            key = key_val
        else:
            key = row['api_key']
        conn.execute(
            'UPDATE api_presets SET name=?, api_endpoint=?, api_key=?, api_model=?, '
            'context_max_messages=? WHERE id=?',
            (name, endpoint, key, model, ctx, preset_id)
        )
        updated = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        return jsonify(_mask_preset(updated))


@settings_bp.route('/api/presets/<int:preset_id>', methods=['DELETE'])
def delete_preset(preset_id):
    with get_db() as conn:
        conn.execute('DELETE FROM api_presets WHERE id = ?', (preset_id,))
        return jsonify({'ok': True})


@settings_bp.route('/api/presets/<int:preset_id>/activate', methods=['POST'])
def activate_preset(preset_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM api_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        # Write preset fields into the settings table
        for key in PRESET_FIELDS:
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, row[key])
            )
    return read_settings()
