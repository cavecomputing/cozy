"""Settings and system prompt routes."""

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import zipfile
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response

from cozy import shared
from cozy.defaults import (
    DEFAULT_POST_HISTORY_TEMPLATE, VERSION_RE, seed_default_prompts, version_key,
)
from cozy.schema import MIGRATIONS, init_db
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
    'sampler_temperature', 'sampler_top_p', 'sampler_top_k',
    'sampler_min_p', 'sampler_max_tokens', 'sampler_repetition_penalty',
    'sampler_dynatemp_range', 'sampler_dynatemp_exponent',
    'sampler_top_n_sigma',
    'sampler_repeat_last_n',
    'sampler_presence_penalty', 'sampler_frequency_penalty',
    'sampler_dry_multiplier', 'sampler_dry_base',
    'sampler_dry_allowed_length', 'sampler_dry_penalty_last_n',
    'sampler_xtc_probability', 'sampler_xtc_threshold',
    'sampler_seed',
    'active_samplers',
    'show_context_token_meter',
    'show_advanced_configuration',
    'context_max_tokens',
    'extra_request_params',
    # Auto Summaries — configuration (per-chat enablement lives on the chat row)
    'summary_api_endpoint', 'summary_api_key', 'summary_api_model',
    'summary_cap_pct',
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


def _invalid_version(version):
    """The 400 response for a malformed version, or None when it is fine."""
    if version == '' or VERSION_RE.match(version):
        return None
    return jsonify({
        'error': '"version" must be a number like "2" or "2.1", with no "v"'
    }), 400


@settings_bp.route('/api/settings', methods=['GET'])
def read_settings():
    s = get_settings()
    # Never send full secret values to the frontend — mask them and strip the raw key.
    for key in SECRET_KEYS:
        s.update(mask_secret(s.get(key, ''), key))
        s.pop(key, None)
    # Half of the sign-in cookie's signing key (cozy/auth.py); the page has no use for it.
    s.pop('secret_key', None)
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

def _prompt_order(prompt):
    """Group a prompt's editions together, newest first.

    Sorted here rather than in SQL because `version` is TEXT, where "2.10"
    would rank below "2.2". `created_at` breaks a tie between two rows sharing
    a name and a version, which only an old database can hold — the routes
    reject that pair now.
    """
    major, minor = version_key(prompt['version'])
    return (prompt['name'].lower(), -major, -minor, prompt['created_at'] or '')


@settings_bp.route('/api/system-prompts', methods=['GET'])
def list_system_prompts():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM system_prompts').fetchall()
        return jsonify(sorted((dict(r) for r in rows), key=_prompt_order))


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
    description = data.get('description', '')
    if not isinstance(description, str):
        return jsonify({'error': '"description" must be a string'}), 400
    version = data.get('version', '')
    if not isinstance(version, str):
        return jsonify({'error': '"version" must be a string'}), 400
    version = version.strip()
    bad_version = _invalid_version(version)
    if bad_version:
        return bad_version
    with get_db() as conn:
        if conn.execute(
            'SELECT 1 FROM system_prompts WHERE name = ? AND version = ?', (name, version)
        ).fetchone():
            return jsonify({'error': f'A prompt named "{name}" already exists'}), 409
        cur = conn.execute(
            'INSERT INTO system_prompts (name, version, description, content, post_history_content) '
            'VALUES (?, ?, ?, ?, ?)',
            (name, version, description, content, post_history_content)
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
        description = data.get('description', row['description'])
        version = data.get('version', row['version'])
        content = data.get('content', row['content'])
        post_history_content = data.get('post_history_content', row['post_history_content'])
        if not isinstance(description, str):
            return jsonify({'error': '"description" must be a string'}), 400
        if not isinstance(version, str):
            return jsonify({'error': '"version" must be a string'}), 400
        if not isinstance(post_history_content, str):
            return jsonify({'error': '"post_history_content" must be a string'}), 400
        version = version.strip()
        bad_version = _invalid_version(version)
        if bad_version:
            return bad_version
        # Name and version identify a prompt together, so a rename only clashes
        # with a row that matches both — the same prompt at another version is
        # meant to sit beside this one.
        if (name, version) != (row['name'], row['version']) and conn.execute(
            'SELECT 1 FROM system_prompts WHERE name = ? AND version = ? AND id != ?',
            (name, version, prompt_id)
        ).fetchone():
            return jsonify({'error': f'A prompt named "{name}" already exists'}), 409
        conn.execute(
            'UPDATE system_prompts SET name = ?, version = ?, description = ?, content = ?, '
            'post_history_content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (name, version, description, content, post_history_content, prompt_id)
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
    ``{"name": str, "version": str, "description": str, "content": str,
    "post_history_content": str}``. ``version`` and ``description`` are
    optional; a version must read as a number ("2", "2.1"). Legacy
    system-only prompt JSON is accepted and gets the default post-history
    template.

    Name and version identify a prompt together: importing a newer edition of
    one already installed keeps its own name and sits beside it, while the
    same name at the same version is a duplicate and is refused with 409.
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
    description = payload.get('description', '')
    if not isinstance(description, str):
        return jsonify({'error': '"description" must be a string'}), 400
    version = payload.get('version', '')
    if not isinstance(version, str):
        return jsonify({'error': '"version" must be a string'}), 400
    version = version.strip()
    bad_version = _invalid_version(version)
    if bad_version:
        return bad_version
    raw_name = payload.get('name')
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else 'Imported Prompt'

    with get_db() as conn:
        if conn.execute(
            'SELECT 1 FROM system_prompts WHERE name = ? AND version = ?', (name, version)
        ).fetchone():
            return jsonify({
                'error': f'A prompt named "{name}" at that version already exists'
            }), 409
        cur = conn.execute(
            'INSERT INTO system_prompts (name, version, description, content, post_history_content) '
            'VALUES (?, ?, ?, ?, ?)',
            (name, version, description, content, post_history_content)
        )
        row = conn.execute(
            'SELECT * FROM system_prompts WHERE id = ?', (cur.lastrowid,)
        ).fetchone()
        return jsonify(dict(row)), 201


@settings_bp.route('/api/system-prompts/<int:prompt_id>/export', methods=['GET'])
def export_system_prompt(prompt_id):
    """Download a paired prompt as a {name, version, description, content, post_history_content} JSON file."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT name, version, description, content, post_history_content '
            'FROM system_prompts WHERE id = ?', (prompt_id,)
        ).fetchone()
        if not row:
            return not_found('System prompt')

    body = {
        'name': row['name'],
        'version': row['version'],
        'description': row['description'],
        'content': row['content'],
        'post_history_content': row['post_history_content'],
    }
    # Two editions of one prompt would otherwise land in a downloads folder
    # under the same filename, where the second silently becomes "… (1)".
    # Appended after sanitizing, because safe_download_name() drops the dot —
    # a version is digits and at most one dot, so it carries nothing the
    # quoted Content-Disposition header needs protecting from.
    stem = safe_download_name(row['name'], 'prompt')
    if VERSION_RE.match(row['version'] or ''):
        stem = f"{stem} {row['version']}"
    return json_download(body, f'{stem}.json')


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


# ── Backup and restore ─────────────────────────────────────────────────────
#
# A backup is a zip of the data directory plus a manifest naming the schema
# version it was taken at. The thumbnail cache is left out — it is rebuilt on
# demand — and the database travels as a snapshot taken through SQLite's backup
# API, so a write in flight cannot tear it and the -wal/-shm sidecars have
# nothing left to say.
#
# Restoring is destructive by design: the data directory is emptied and the
# archive takes its place. init_db() then runs, so a backup from an older build
# is migrated up exactly as a normal start would migrate it.

BACKUP_MANIFEST = 'cozy-backup.json'

# A backup is an archive of the user's own data directory, so it dwarfs the
# app-wide upload cap, which is sized for one character card. Flask falls back
# to that cap when a per-request limit is None, so this has to be a number
# rather than "no limit": large enough that no real library meets it.
RESTORE_MAX_BYTES = 64 * 1024 ** 3

# One restore at a time. Two of them interleaved have one emptying the data
# directory while the other is moving files into it, which fails halfway
# through and leaves neither backup installed.
_RESTORE_LOCK = threading.Lock()


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _database_snapshot(path):
    """Copy the live database to *path* through SQLite's backup API, less the
    sign-in secret.

    That secret stays with this install (cozy/auth.py): a backup holding it,
    plus one sign-in cookie, would let the password be guessed offline.
    secure_delete overwrites the row instead of leaving it in a free page.
    """
    with get_db() as conn:
        destination = sqlite3.connect(path)
        try:
            conn.backup(destination)
            destination.execute('PRAGMA secure_delete = ON')
            destination.execute("DELETE FROM settings WHERE key = 'secret_key'")
            destination.commit()
        finally:
            destination.close()


def _backup_payload_files():
    """Yield (path, archive name) for every data file that travels.

    The database and its sidecars are covered by the snapshot and the thumbnail
    cache is rebuildable, so both are skipped here.
    """
    skipped = {
        os.path.normcase(os.path.abspath(p))
        for p in (shared.THUMBS_DIR, shared.DATABASE,
                  f'{shared.DATABASE}-wal', f'{shared.DATABASE}-shm',
                  f'{shared.DATABASE}-journal')
    }
    for directory, dirnames, filenames in os.walk(shared.DATA_DIR):
        dirnames[:] = [
            name for name in dirnames
            if os.path.normcase(os.path.abspath(os.path.join(directory, name))) not in skipped
        ]
        for filename in filenames:
            full = os.path.join(directory, filename)
            if os.path.normcase(os.path.abspath(full)) in skipped:
                continue
            yield full, os.path.relpath(full, shared.DATA_DIR).replace(os.sep, '/')


def _write_backup(zip_path):
    with tempfile.TemporaryDirectory() as workspace:
        snapshot = os.path.join(workspace, 'cozy_chat.db')
        _database_snapshot(snapshot)
        with get_db() as conn:
            row = conn.execute('SELECT MAX(version) AS version FROM schema_migrations').fetchone()
        manifest = {
            'app': 'cozy',
            'schema_version': row['version'] or 0,
            'build': shared.BUILD_INFO.display,
            'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(BACKUP_MANIFEST, json.dumps(manifest, indent=2))
            archive.write(snapshot, os.path.basename(shared.DATABASE))
            for full, arcname in _backup_payload_files():
                archive.write(full, arcname)


@settings_bp.route('/api/backup', methods=['GET'])
def download_backup():
    """Download the whole data directory as a restorable zip.

    Streamed from a temp copy that this response owns and deletes when it ends,
    and deliberately **not** resumable. Every request builds a fresh archive, so
    a client that fetches byte ranges — Chrome splits a download of a few
    megabytes across parallel range requests — would stitch together pieces of
    several different archives and save a corrupt zip. Advertising no ranges
    keeps it to one stream of one archive.
    """
    workspace = tempfile.mkdtemp(prefix='cozy-backup-')
    zip_path = os.path.join(workspace, 'backup.zip')
    try:
        _write_backup(zip_path)
        size = os.path.getsize(zip_path)
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    def stream():
        try:
            with open(zip_path, 'rb') as handle:
                while chunk := handle.read(256 * 1024):
                    yield chunk
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return Response(stream(), mimetype='application/zip', headers={
        'Content-Length': str(size),
        'Content-Disposition': f'attachment; filename="cozy-backup-{stamp}.zip"',
        'Accept-Ranges': 'none',
        'Cache-Control': 'no-store',
    })


def _usable_database(path):
    """True when SQLite can open *path* and read its schema.

    Checked while the archive is still staged, because the alternative is
    finding out after the data directory has already been emptied.
    """
    try:
        connection = sqlite3.connect(path)
        try:
            connection.execute('SELECT count(*) FROM sqlite_master')
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return True


def _stage_backup(archive, staged):
    """Extract *archive* into *staged*, refusing any member that escapes it."""
    root = os.path.abspath(staged)
    for member in archive.infolist():
        if member.is_dir() or member.filename == BACKUP_MANIFEST:
            continue
        target = os.path.abspath(os.path.join(root, member.filename))
        if not target.startswith(root + os.sep):
            raise ValueError(f'unsafe path {member.filename!r}')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.open(member) as source, open(target, 'wb') as out:
            shutil.copyfileobj(source, out)


def _replace_data_dir(staged):
    """Empty the data directory and move the staged backup into its place."""
    for entry in os.scandir(shared.DATA_DIR):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path, ignore_errors=True)
        else:
            _quiet_remove(entry.path)
    # File by file rather than directory by directory: a directory the wipe
    # could not remove (a file still open elsewhere) would otherwise collide
    # with the one being moved in, and take the restore down with it.
    for directory, _dirnames, filenames in os.walk(staged):
        for filename in filenames:
            source = os.path.join(directory, filename)
            target = os.path.join(shared.DATA_DIR, os.path.relpath(source, staged))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
    for directory in (shared.CHARACTERS_DIR, shared.PERSONAS_DIR,
                      shared.THEMES_DIR, shared.THUMBS_DIR):
        os.makedirs(directory, exist_ok=True)


@settings_bp.route('/api/backup/restore', methods=['POST'])
def restore_backup():
    """Replace the data directory with the contents of an uploaded backup."""
    # A backup is as large as the library it came from, so the app-wide upload
    # cap — sized for a character card — does not apply to Cozy's own archive.
    request.max_content_length = RESTORE_MAX_BYTES
    upload = request.files.get('file')
    if not upload:
        return jsonify({'error': 'No file uploaded'}), 400
    known_version = MIGRATIONS[-1][0]
    with _RESTORE_LOCK, tempfile.TemporaryDirectory() as workspace:
        staged = os.path.join(workspace, 'data')
        os.makedirs(staged)
        try:
            with zipfile.ZipFile(upload.stream) as archive:
                manifest = json.loads(archive.read(BACKUP_MANIFEST))
                if not isinstance(manifest, dict) or manifest.get('app') != 'cozy':
                    raise ValueError('no Cozy manifest')
                version = manifest.get('schema_version')
                if not isinstance(version, int):
                    raise ValueError('manifest names no schema version')
                if os.path.basename(shared.DATABASE) not in archive.namelist():
                    raise ValueError('no database in the archive')
                if version > known_version:
                    return jsonify({'error':
                        'This backup was made by a newer version of Cozy '
                        f'(database version {version}, this build understands '
                        f'{known_version}). Update Cozy, then restore it.'}), 409
                _stage_backup(archive, staged)
            if not _usable_database(os.path.join(staged, os.path.basename(shared.DATABASE))):
                raise ValueError('the database in it is unreadable')
        except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError) as e:
            return jsonify({'error': f'That file is not a Cozy backup ({e}).'}), 400
        # A backup carries no sign-in secret (_database_snapshot()). Put this
        # install's back, or the next start would make a new one and sign
        # every device out (cozy/auth.py).
        with get_db() as conn:
            secret = conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
        _replace_data_dir(staged)
    # An older backup arrives at whatever schema it was taken at, so upgrade it
    # and restore any bundled prompt it predates, the way a start would.
    init_db()
    if secret:
        with get_db() as conn:
            upsert_setting(conn, 'secret_key', secret['value'])
    seed_default_prompts()
    return jsonify({'success': True, 'schema_version': version})
