"""Settings and system prompt routes."""

import json
import os
import stat

from flask import Blueprint, request, jsonify

from cozy import shared
from cozy.defaults import DEFAULT_PROMPT_TEMPLATE, DEFAULT_POST_HISTORY_TEMPLATE
from cozy.shared import get_db, json_download, not_found, safe_download_name

settings_bp = Blueprint('settings', __name__)


def _file_stats(path):
    """Return one regular file's size/count, or zeroes if it vanished."""
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return {'bytes': 0, 'files': 0}
    if not stat.S_ISREG(info.st_mode):
        return {'bytes': 0, 'files': 0}
    return {'bytes': info.st_size, 'files': 1}


def _directory_stats(path, *, excluded_paths=()):
    """Recursively count regular files without following links."""
    excluded = {os.path.normcase(os.path.abspath(p)) for p in excluded_paths}
    totals = {'bytes': 0, 'files': 0}

    def scan(directory):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = os.path.normcase(os.path.abspath(entry.path))
                    if entry_path in excluded:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            scan(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            totals['bytes'] += info.st_size
                            totals['files'] += 1
                    except OSError:
                        # A cache entry or upload may disappear between scandir
                        # and stat. Storage figures are a point-in-time view, so
                        # skipping that file is more useful than failing it all.
                        continue
        except OSError:
            return

    scan(path)
    return totals


def collect_storage_stats():
    """Measure durable Cozy data separately from its thumbnail cache."""
    database_paths = [
        shared.DATABASE,
        f'{shared.DATABASE}-wal',
        f'{shared.DATABASE}-shm',
        f'{shared.DATABASE}-journal',
    ]
    database = {'bytes': 0, 'files': 0}
    for path in database_paths:
        measured = _file_stats(path)
        database['bytes'] += measured['bytes']
        database['files'] += measured['files']

    known_directories = (
        shared.CHARACTERS_DIR,
        shared.PERSONAS_DIR,
        shared.THEMES_DIR,
        shared.THUMBS_DIR,
    )
    excluded = (*known_directories, *database_paths)
    categories = {
        'database': database,
        'characters': _directory_stats(shared.CHARACTERS_DIR),
        'personas': _directory_stats(shared.PERSONAS_DIR),
        'themes': _directory_stats(shared.THEMES_DIR),
        'other': _directory_stats(shared.DATA_DIR, excluded_paths=excluded),
    }
    cache = _directory_stats(shared.THUMBS_DIR)
    return {
        'user_data_bytes': sum(item['bytes'] for item in categories.values()),
        'categories': categories,
        'cache': cache,
    }

SETTINGS_KEYS = {
    'api_endpoint', 'api_key', 'api_model',
    'active_api_preset',
    'active_system_prompt',
    'active_regex_preset',
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
    'active_samplers',
    'show_context_token_meter',
    'show_advanced_configuration',
    'context_max_tokens',
    'lorebook_scan_depth_override',
    'lorebook_always_inject_all',
    'extra_request_params',
    # Auto Summaries — configuration (per-chat enablement lives on the chat row)
    'summary_api_endpoint', 'summary_api_key', 'summary_api_model',
    'summary_cap_pct', 'summary_trigger_interval',
}

# Settings keys holding secrets: masked on read, and skipped on write when the
# masked placeholder is echoed back (so the real value isn't clobbered).
SECRET_KEYS = ('api_key', 'summary_api_key')


def mask_secret(value, field='api_key'):
    if value:
        return {
            f'{field}_masked': value[:3] + '…' + value[-4:] if len(value) > 8 else '•••••',
            f'{field}_set': True,
        }
    return {f'{field}_masked': '', f'{field}_set': False}


def is_masked_secret(value):
    value = '' if value is None else str(value).strip()
    # Empty is an explicit clear. Only the UI's masked placeholder is ignored.
    return bool(value) and (value.startswith('••') or '…' in value)


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


def _unique_name(conn, table, base, fallback):
    """Append " (n)" until the name is free in *table*.

    ``table`` is always a literal from this module, never user input.
    """
    base = (base or '').strip() or fallback
    candidate = base
    n = 2
    while conn.execute(
        f'SELECT 1 FROM {table} WHERE name = ?', (candidate,)
    ).fetchone():
        candidate = f'{base} ({n})'
        n += 1
    return candidate


@settings_bp.route('/api/settings', methods=['GET'])
def read_settings():
    s = get_settings()
    # Never send full secret values to the frontend — mask them and strip the raw key.
    for key in SECRET_KEYS:
        s.update(mask_secret(s.get(key, ''), key))
        s.pop(key, None)
    return jsonify(s)


@settings_bp.route('/api/storage-stats', methods=['GET'])
def storage_stats():
    return jsonify(collect_storage_stats())


@settings_bp.route('/api/settings', methods=['PUT'])
def write_settings():
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        for key in SETTINGS_KEYS:
            if key in data:
                val = _setting_value(data[key])
                # Allow an explicit empty string to clear, or set a new value.
                # For secret keys, skip only if the placeholder/masked value is sent back
                # so the stored key isn't clobbered by its own mask.
                if key in SECRET_KEYS and is_masked_secret(val):
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
        name = _unique_name(conn, 'system_prompts', base_name, 'Imported Prompt')
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
    return json_download(body, f"{safe_download_name(row['name'], 'prompt')}.json")


# ── API Presets CRUD ──────────────────────────────────────────────────────

PRESET_FIELDS = ('api_endpoint', 'api_key', 'api_model', 'context_max_tokens')

# Extra page state a preset snapshots beyond the discrete connection columns:
# every sampler value (incl. sampler_max_tokens), the active-sampler selection,
# and extra_request_params. Derived from SETTINGS_KEYS so it can't drift as
# samplers are added/removed.
PRESET_SETTINGS_KEYS = {k for k in SETTINGS_KEYS if k.startswith('sampler_')} | {
    'active_samplers', 'extra_request_params',
}


def _pack_preset_settings(data, base=None):
    """Overlay any PRESET_SETTINGS_KEYS present in ``data`` onto ``base`` and
    return a JSON string. ``base`` (a dict) lets callers merge into an existing
    blob so a partial update doesn't wipe unrelated keys."""
    merged = dict(base or {})
    for key in PRESET_SETTINGS_KEYS:
        if key in data:
            merged[key] = _setting_value(data[key])
    return json.dumps(merged)


def _unpack_preset_settings(row):
    """Parse a preset row's settings_json blob, tolerant of missing/bad JSON."""
    row = dict(row)
    raw = row.get('settings_json') or '{}'
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mask_preset(row):
    """Return a dict with the api_key masked (same logic as read_settings)."""
    row = dict(row)
    d = {
        'id': row['id'],
        'name': row['name'],
        'api_endpoint': row['api_endpoint'],
        'api_model': row['api_model'],
        'context_max_tokens': row['context_max_tokens'],
        'settings': _unpack_preset_settings(row),
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
    key_supplied = 'api_key' in data
    key_val = _setting_value(data.get('api_key', ''))
    if not key_supplied or is_masked_secret(key_val):
        s = get_settings()
        key_val = s.get('api_key', '')
    with get_db() as conn:
        existing = conn.execute(
            'SELECT 1 FROM api_presets WHERE name = ?', (name,)
        ).fetchone()
        if existing:
            return jsonify({'error': f'A preset named "{name}" already exists'}), 409
        cur = conn.execute(
            'INSERT INTO api_presets (name, api_endpoint, api_key, api_model, '
            'context_max_tokens, settings_json) VALUES (?, ?, ?, ?, ?, ?)',
            (name, data.get('api_endpoint', ''), key_val,
             data.get('api_model', ''), data.get('context_max_tokens', '32768'),
             _pack_preset_settings(data))
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
        # Missing or masked leaves the key alone; explicit empty clears it.
        key_val = _setting_value(data.get('api_key', ''))
        if 'api_key' not in data or is_masked_secret(key_val):
            key = row['api_key']
        else:
            key = key_val
        settings_json = _pack_preset_settings(data, base=_unpack_preset_settings(row))
        conn.execute(
            'UPDATE api_presets SET name=?, api_endpoint=?, api_key=?, api_model=?, '
            'context_max_tokens=?, settings_json=? WHERE id=?',
            (name, endpoint, key, model, ctx_tokens, settings_json, preset_id)
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
        # Unpack the preset's snapshot of sampler/thinking/extra settings. Only
        # keys actually present are written, so a legacy preset (empty blob)
        # leaves the current sampler settings untouched.
        for key, value in _unpack_preset_settings(row).items():
            if key in SETTINGS_KEYS:
                upsert_setting(conn, key, _setting_value(value))
        upsert_setting(conn, 'active_api_preset', str(preset_id))
    return read_settings()


# ── Regex presets CRUD ────────────────────────────────────────────────────
#
# A preset is a named, ordered list of find/replace filters applied to the
# character's reply in the browser. The server only stores and normalises them —
# patterns are never compiled here, because the flavour that matters is the one
# in the user's JS engine.

# Every flag `new RegExp()` accepts. Anything else would throw in the browser.
ALLOWED_REGEX_FLAGS = 'dgimsuvy'

# SillyTavern's regex_placement enum value for AI output — the only target Cozy
# has an equivalent for.
ST_PLACEMENT_AI_OUTPUT = 2


def _filter_text(value):
    """Coerce to str *without* stripping.

    Deliberately not `_setting_value`: leading/trailing whitespace is
    meaningful in both halves of a filter (` {2,}` → ` ` is a real rule, and
    stripping would turn the replacement into an empty string).
    """
    return '' if value is None else str(value)


def _escape_controls(value, escape_backslash=False):
    """Rewrite real control characters as the escapes Cozy's UI round-trips.

    Both filter fields are single-line ``<input>`` elements, which drop CR and
    LF outright, so a value carrying either would come back from the DOM with it
    silently deleted. ``\\n`` means the same thing in a Find pattern, and the
    Replace box expands ``\\n`` at run time, so neither meaning changes.

    ``escape_backslash`` is for replacement text only, where it makes this the
    exact inverse of that expansion. A Find pattern is regex source, where a
    backslash is already load-bearing and must be left alone.
    """
    if escape_backslash:
        value = value.replace('\\', '\\\\')
    return value.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def _clean_filter(entry):
    """Normalise one filter to {name, find, replace, flags, display}, or None if unusable."""
    if not isinstance(entry, dict):
        return None
    flags = _filter_text(entry.get('flags', '')).strip()
    return {
        'name': _filter_text(entry.get('name', '')).strip(),
        'find': _filter_text(entry.get('find', '')),
        'replace': _filter_text(entry.get('replace', '')),
        # De-duplicated, and unknown letters dropped so a stored preset can
        # never hand the browser a flag string that throws.
        'flags': ''.join(dict.fromkeys(c for c in flags if c in ALLOWED_REGEX_FLAGS)),
        # Missing means False, so a preset saved before this option existed
        # keeps rewriting the stored reply exactly as it always did.
        'display': bool(entry.get('display')),
    }


def _pack_scripts(value):
    """Normalise a filter list (or a JSON string of one) into a JSON array string."""
    if isinstance(value, str):
        try:
            value = json.loads(value or '[]')
        except (ValueError, TypeError):
            value = []
    if not isinstance(value, list):
        value = []
    cleaned = [f for f in (_clean_filter(e) for e in value) if f]
    return json.dumps(cleaned, ensure_ascii=False)


def _unpack_scripts(row):
    """Parse a preset row's scripts_json, tolerant of missing/corrupt JSON."""
    raw = dict(row).get('scripts_json') or '[]'
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [f for f in (_clean_filter(e) for e in parsed) if f]


def _regex_preset_dict(row):
    row = dict(row)
    return {
        'id': row['id'],
        'name': row['name'],
        'filters': _unpack_scripts(row),
        'created_at': row['created_at'],
    }


def _split_slash_form(raw):
    """Split SillyTavern's ``/pattern/flags`` form into ``(pattern, flags)``.

    Anything that isn't that shape — including a pattern that merely happens to
    start with a slash — comes back unchanged with no flags.
    """
    s = _filter_text(raw)
    if not s.startswith('/'):
        return s, ''
    end = s.rfind('/')
    if end <= 0:
        return s, ''
    flags = s[end + 1:]
    if any(c not in ALLOWED_REGEX_FLAGS for c in flags):
        return s, ''
    return s[1:end], flags


def _filters_from_payload(payload):
    """Read an uploaded file into ``(filters, base_name, warnings)``.

    Accepts a Cozy preset ``{name, filters}``, a single SillyTavern regex
    script, or a bare list of either.
    """
    warnings = []

    def from_st(script):
        name = _filter_text(script.get('scriptName', '')).strip()
        if script.get('disabled') is True:
            warnings.append(
                f'"{name or "Untitled"}" was disabled in SillyTavern and was skipped.'
            )
            return None
        find, flags = _split_slash_form(script.get('findRegex', ''))
        placement = script.get('placement')
        if isinstance(placement, list) and ST_PLACEMENT_AI_OUTPUT not in placement:
            warnings.append(
                f'"{name or "Untitled"}" targeted something other than AI output in '
                'SillyTavern; in Cozy it will apply to character replies.'
            )
        return {
            'name': name,
            'find': _escape_controls(find),
            # SillyTavern writes a real line break here; Cozy's Replace field
            # spells one `\n`, so a multi-line replacement has to be converted
            # rather than handed over to be truncated at the first newline.
            'replace': _escape_controls(
                _filter_text(script.get('replaceString', '')), escape_backslash=True
            ),
            'flags': flags,
            # ST's "Alter Chat Display" is Cozy's display-only filter.
            'display': bool(script.get('markdownOnly')),
        }

    def one(entry):
        if not isinstance(entry, dict):
            return None
        return from_st(entry) if 'findRegex' in entry else _clean_filter(entry)

    if isinstance(payload, list):
        return [f for f in (one(e) for e in payload) if f], '', warnings
    if not isinstance(payload, dict):
        return [], '', warnings
    if 'findRegex' in payload:
        name = _filter_text(payload.get('scriptName', '')).strip()
        script = from_st(payload)
        return ([script] if script else []), name, warnings

    raw_filters = payload.get('filters')
    if not isinstance(raw_filters, list):
        raw_filters = []
    name = _filter_text(payload.get('name', '')).strip()
    return [f for f in (one(e) for e in raw_filters) if f], name, warnings


@settings_bp.route('/api/regex-presets', methods=['GET'])
def list_regex_presets():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM regex_presets ORDER BY created_at ASC').fetchall()
        return jsonify([_regex_preset_dict(r) for r in rows])


@settings_bp.route('/api/regex-presets', methods=['POST'])
def create_regex_preset():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    with get_db() as conn:
        if conn.execute('SELECT 1 FROM regex_presets WHERE name = ?', (name,)).fetchone():
            return jsonify({'error': f'A preset named "{name}" already exists'}), 409
        cur = conn.execute(
            'INSERT INTO regex_presets (name, scripts_json) VALUES (?, ?)',
            (name, _pack_scripts(data.get('filters', [])))
        )
        row = conn.execute('SELECT * FROM regex_presets WHERE id = ?', (cur.lastrowid,)).fetchone()
        return jsonify(_regex_preset_dict(row)), 201


@settings_bp.route('/api/regex-presets/<int:preset_id>', methods=['PUT'])
def update_regex_preset(preset_id):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute('SELECT * FROM regex_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return not_found('Regex preset')
        name = (data.get('name') or '').strip() or row['name']
        if name != row['name']:
            clash = conn.execute(
                'SELECT 1 FROM regex_presets WHERE name = ? AND id != ?', (name, preset_id)
            ).fetchone()
            if clash:
                return jsonify({'error': f'A preset named "{name}" already exists'}), 409
        # Absent "filters" leaves the list alone; an explicit [] clears it.
        scripts = _pack_scripts(data['filters']) if 'filters' in data else row['scripts_json']
        conn.execute(
            'UPDATE regex_presets SET name = ?, scripts_json = ? WHERE id = ?',
            (name, scripts, preset_id)
        )
        updated = conn.execute('SELECT * FROM regex_presets WHERE id = ?', (preset_id,)).fetchone()
        return jsonify(_regex_preset_dict(updated))


@settings_bp.route('/api/regex-presets/<int:preset_id>', methods=['DELETE'])
def delete_regex_preset(preset_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM regex_presets WHERE id = ?', (preset_id,)).fetchone()
        if not row:
            return not_found('Regex preset')
        active = conn.execute(
            'SELECT value FROM settings WHERE key = ?', ('active_regex_preset',)
        ).fetchone()
        conn.execute('DELETE FROM regex_presets WHERE id = ?', (preset_id,))
        # Deleting the active preset must leave filtering off, not dangling.
        if active and active['value'] == str(preset_id):
            upsert_setting(conn, 'active_regex_preset', '')
        return jsonify({'success': True})


@settings_bp.route('/api/regex-presets/import', methods=['POST'])
def import_regex_preset():
    """Create a preset from an uploaded JSON file.

    Accepts Cozy's own ``{name, filters}`` export, a SillyTavern regex script
    (``{scriptName, findRegex, replaceString, …}``), or a list of either.
    """
    if not request.files or 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        payload = json.loads(request.files['file'].read().decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return jsonify({'error': f'Invalid JSON: {e}'}), 400
    if not isinstance(payload, (dict, list)):
        return jsonify({'error': 'Expected a JSON object or array'}), 400

    filters, base_name, warnings = _filters_from_payload(payload)
    if not filters:
        if warnings:
            return jsonify({
                'error': 'No enabled regex filters found; disabled scripts were skipped.',
                'warnings': warnings,
            }), 400
        return jsonify({'error': 'No regex filters found in that file'}), 400

    with get_db() as conn:
        name = _unique_name(conn, 'regex_presets', base_name, 'Imported Filters')
        cur = conn.execute(
            'INSERT INTO regex_presets (name, scripts_json) VALUES (?, ?)',
            (name, _pack_scripts(filters))
        )
        row = conn.execute('SELECT * FROM regex_presets WHERE id = ?', (cur.lastrowid,)).fetchone()
        body = _regex_preset_dict(row)
        body['warnings'] = warnings
        return jsonify(body), 201


@settings_bp.route('/api/regex-presets/<int:preset_id>/export', methods=['GET'])
def export_regex_preset(preset_id):
    """Download a preset as a {name, filters} JSON file."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT name, scripts_json FROM regex_presets WHERE id = ?', (preset_id,)
        ).fetchone()
        if not row:
            return not_found('Regex preset')

    body = {'name': row['name'], 'filters': _unpack_scripts(row)}
    return json_download(body, f"{safe_download_name(row['name'], 'regex')}.json")
