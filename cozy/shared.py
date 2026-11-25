"""Path constants, small request helpers, and the DB connection used everywhere.

Kept deliberately thin: the schema lives in cozy/schema.py and the bundled
content in cozy/defaults.py, both of which reach paths back through this
module by attribute (``shared.DATABASE``) so the test fixture that patches
them keeps working.
"""

import json
import os
import sqlite3
import tempfile
import unicodedata
from contextlib import contextmanager
from urllib.parse import quote

from flask import jsonify, request, Response

from cozy.build_info import get_build_info

# ── Paths ──────────────────────────────────────────────────────────────────
# The repository root, one level above this package — the anchor for
# static/themes and the build-info lookup.
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
BUNDLED_CHARACTERS_DIR = os.path.join(BASE_DIR, 'cozy', 'bundled', 'characters')
# Prompt presets shipped with Cozy as {name, version, description, content,
# post_history_content} JSON — the same payload the export endpoint produces.
# The *filename* minus .json is the title each one is seeded under, and a preset
# is that title together with its version, so shipping a revision means bumping
# "version" inside the existing file: "NanoBear" 2.2 seeds beside "NanoBear" 2.1
# and neither disturbs the other. Anything missing from system_prompts is
# restored from here on every start, so this directory — not the database — is
# the source of truth for which presets exist; see seed_default_prompts().
BUNDLED_PROMPTS_DIR = os.path.join(BASE_DIR, 'cozy', 'bundled', 'prompts')
ALLOWED_IMG  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
# Cap on decoded image dimensions (~8000x8000). Character cards and persona
# avatars are user-supplied, and a small crafted file can otherwise expand to
# gigabytes once decoded. Applied to Pillow by every module that decodes.
# Pillow only warns above its limit and refuses above twice it, so the limit
# is half the 64 megapixels this allows.
MAX_IMAGE_PIXELS = 32_000_000


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


def write_file_atomic(path, data):
    """Write *data* to *path* whole or not at all.

    Writing over a file in place truncates it first, so a crash or a full disk
    partway through left a card that no longer opened, or an avatar that was
    gone. The bytes go to a sibling file that is renamed over the original only
    once they are all on disk, and the folder is synced so the rename survives
    a power cut too. Each write gets its own sibling, so two saves of one file
    can't truncate each other's, and its name starts with a dot and ends in .tmp
    so no folder scan mistakes it for a card or a theme.
    """
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f'.{os.path.basename(path)}.', suffix='.tmp')
    os.close(fd)
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if hasattr(os, 'O_DIRECTORY'):          # Windows can't open a folder to sync it
        try:
            dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass                            # some filesystems refuse a folder fsync

os.makedirs(CHARACTERS_DIR, exist_ok=True)
os.makedirs(PERSONAS_DIR, exist_ok=True)
os.makedirs(THEMES_DIR, exist_ok=True)
os.makedirs(THUMBS_DIR, exist_ok=True)


def not_found(resource):
    """Standard 404 JSON response used by all route modules."""
    return jsonify({'error': f'{resource} not found'}), 404


def json_body():
    """The request's JSON object.

    A body that is missing, unparseable, or JSON of another shape (a list, a
    bare string) reads as ``{}``, so the route answers with its own "name is
    required" instead of failing on ``.get``.
    """
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def text_field(data, key):
    """A string field of a request body, stripped.

    Anything that is not a string — absent, null, a number, a list — reads as
    ``''``, which every caller already treats as "not given".
    """
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ''


def safe_download_name(name, fallback='download'):
    """Return a conservative display filename stem for download headers: the
    name's letters, their accents and its digits in any script, and nothing a
    file system gives a meaning to."""
    safe = ''.join(
        c for c in str(name or '')
        if unicodedata.category(c)[0] in 'LMN' or c in (' ', '-', '_')
    ).strip()
    return safe or fallback


def attachment(filename):
    """The Content-Disposition value that saves a response as *filename*.

    A header is ASCII. A name with other letters goes in the filename* form
    (RFC 6266), percent-encoded, which every current browser reads ahead of the
    plain one; that one is left what ASCII the name has, without a separator
    dangling where the rest was.
    """
    if filename.isascii():
        return f'attachment; filename="{filename}"'
    stem, extension = os.path.splitext(filename)
    plain = stem.encode('ascii', 'ignore').decode().strip(' -_') or 'download'
    return f"""attachment; filename="{plain}{extension}"; filename*=UTF-8''{quote(filename, safe='')}"""


def json_download(body, filename):
    """Serve *body* as a pretty-printed JSON file attachment."""
    return Response(
        json.dumps(body, indent=2, ensure_ascii=False),
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': attachment(filename)},
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
