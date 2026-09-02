"""Path constants, small request helpers, and the DB connection used everywhere.

Kept deliberately thin: the schema lives in cozy/schema.py and the bundled
content in cozy/defaults.py, both of which reach paths back through this
module by attribute (``shared.DATABASE``) so the test fixture that patches
them keeps working.
"""

import json
import os
import sqlite3
from contextlib import contextmanager

from flask import jsonify, Response

from cozy.build_info import get_build_info

# ── Paths ──────────────────────────────────────────────────────────────────
# The repository root, one level above this package — the anchor for the
# bundled content directories, static/themes, and the build-info lookup.
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR     = os.environ.get('COZY_DATA_DIR', os.path.join(BASE_DIR, 'data'))
DATABASE     = os.path.join(DATA_DIR, 'cozy_chat.db')
CHARACTERS_DIR = os.path.join(DATA_DIR, 'characters')
PERSONAS_DIR   = os.path.join(DATA_DIR, 'personas')
THEMES_DIR     = os.path.join(DATA_DIR, 'themes')
# Derived avatar thumbnails, generated on demand from CHARACTERS_DIR and
# PERSONAS_DIR. Pure cache — safe to delete at any time; entries regenerate on
# the next request. See thumbs.py.
THUMBS_DIR     = os.path.join(DATA_DIR, 'thumbs')
BUILTIN_THEMES_DIR = os.path.join(BASE_DIR, 'static', 'themes')
# Character cards shipped with Cozy. Copied into CHARACTERS_DIR once, on the
# first run of a fresh install — see seed_default_characters().
BUNDLED_CHARACTERS_DIR = os.path.join(BASE_DIR, 'default_characters')
# Prompt presets shipped with Cozy as {name, content, post_history_content}
# JSON — the same payload the export endpoint produces. The *filename* minus
# .json is the title each one is seeded under, so shipping a revised preset
# means adding a file rather than editing one: "NanoBear v2.1.json" is a
# different preset from "NanoBear v2.0.json" and neither disturbs the other.
# Anything missing from system_prompts is restored from here on every start,
# so this directory — not the database — is the source of truth for which
# presets exist; see seed_default_prompts(). default_prompts/PROVENANCE.md
# records how the BigBear set was derived from its upstream preset.
BUNDLED_PROMPTS_DIR = os.path.join(BASE_DIR, 'default_prompts')
ALLOWED_IMG  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
# Cap on decoded image dimensions (~8000x8000). Character cards and persona
# avatars are user-supplied, and a small crafted file can otherwise expand to
# gigabytes once decoded. Applied to Pillow by every module that decodes.
MAX_IMAGE_PIXELS = 64_000_000


BUILD_INFO = get_build_info(BASE_DIR)


def validate_image_extension(file_storage):
    """Return the lower-case extension if allowed, or None."""
    ext = (file_storage.filename or '').rsplit('.', 1)[-1].lower()
    return ext if ext in ALLOWED_IMG else None


def avatar_cache_key(updated_at):
    """Build a cache-busting key from a SQLite timestamp."""
    return (updated_at or '').replace(' ', 'T').replace(':', '')


def persona_avatar_url(avatar_path, updated_at):
    if not avatar_path:
        return None
    return f'/personas/{avatar_path}?v={avatar_cache_key(updated_at)}'

os.makedirs(CHARACTERS_DIR, exist_ok=True)
os.makedirs(PERSONAS_DIR, exist_ok=True)
os.makedirs(THEMES_DIR, exist_ok=True)
os.makedirs(THUMBS_DIR, exist_ok=True)


def not_found(resource):
    """Standard 404 JSON response used by all route modules."""
    return jsonify({'error': f'{resource} not found'}), 404


def safe_download_name(name, fallback='download'):
    """Return a conservative display filename stem for download headers."""
    safe = ''.join(
        c for c in (name or '')
        if (c.isascii() and c.isalnum()) or c in (' ', '-', '_')
    ).strip()
    return safe or fallback


def json_download(body, filename):
    """Serve *body* as a pretty-printed JSON file attachment."""
    return Response(
        json.dumps(body, indent=2, ensure_ascii=False),
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── Database helpers ────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
