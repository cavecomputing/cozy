"""Shared state, path constants, and DB helpers used by app.py and all route modules."""

import json
import logging
import os
import shutil
import sqlite3
import tomllib
from contextlib import contextmanager

from flask import jsonify

log = logging.getLogger('cozy')

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
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
# JSON — the same payload the export endpoint produces. Inserted into
# system_prompts once, on the next start after upgrading; see
# seed_default_prompts(). Regenerate with scripts/build_bigbear_presets.py.
BUNDLED_PROMPTS_DIR = os.path.join(BASE_DIR, 'default_prompts')
ALLOWED_IMG  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
# Cap on decoded image dimensions (~8000x8000). Character cards and persona
# avatars are user-supplied, and a small crafted file can otherwise expand to
# gigabytes once decoded. Applied to Pillow by every module that decodes.
MAX_IMAGE_PIXELS = 64_000_000


def _read_version():
    """Read the app version from pyproject.toml — the single source of truth.

    pyproject.toml ships next to the code in both the Python and Docker
    installs, so this works in either. Only used for display (the About page
    in Settings); nothing branches on it, hence the quiet fallback.
    """
    try:
        with open(os.path.join(BASE_DIR, 'pyproject.toml'), 'rb') as f:
            return tomllib.load(f)['project']['version']
    except Exception:
        return 'unknown'


APP_VERSION = _read_version()


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


# ── Default Prompt Builder template ─────────────────────────────────────────
# Reproduces the legacy hardcoded system-block assembly. Conditional blocks
# ({{#var}}…{{/var}}) drop out when the variable is empty, so empty fields
# don't leave dangling section headers.
# Historical stock prompt values are exact-match migration sentinels. Do not
# edit an existing version; add a new version and migration instead.
_DEFAULT_PROMPT_TEMPLATE_V1 = """{{#system_prompt}}[System Instructions]
{{system_prompt}}{{/system_prompt}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}

{{#persona}}[{{user}}'s Persona]
{{persona}}{{/persona}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}

{{#lorebook}}[WORLD INFO / CHARACTER LORE]
{{lorebook}}{{/lorebook}}

{{#author_note}}[AUTHOR'S NOTE]
{{author_note}}{{/author_note}}"""


_DEFAULT_PROMPT_TEMPLATE_V2 = _DEFAULT_PROMPT_TEMPLATE_V1 + """

{{#summary}}[MEMORY — STORY SO FAR]
{{summary}}{{/summary}}"""


# V3 keeps V2's section ordering but adds a narrative-guidance preamble to the
# System Instructions block (roleplay framing, prose style, {{user}} boundaries).
_DEFAULT_PROMPT_TEMPLATE_V3 = """{{#system_prompt}}[System Instructions]
You are participating in a simulated world. Narrate the thoughts, feelings, actions, and dialogue of {{char}} and all side characters except {{user}}—avoid narrating for {{user}}. {{char}} and side characters should act autonomously according to their established traits, personality, and background, with their own opinions, goals, and a capacity for disagreement. {{char}} and all side characters can only know, mention, or act on information they have personally witnessed, learned, or could plausibly deduce.

Respond with 1-2 paragraphs using "show, don't tell", driving the story forward in interesting ways. Keep scenes grounded with nuanced descriptions and natural-sounding dialogue. Use a slow-burn pace while avoiding melodrama and leave openings for {{user}}'s physical or social engagement. You are allowed to explore mature themes that align with the narrative. Vary your prose and avoid repetitive phrases or formulaic descriptions—keep each response fresh and unique. ((OOC: OOC instructions like this are narrative guidance.))
{{system_prompt}}{{/system_prompt}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}

{{#persona}}[{{user}}'s Persona]
{{persona}}{{/persona}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}

{{#lorebook}}[WORLD INFO / CHARACTER LORE]
{{lorebook}}{{/lorebook}}

{{#author_note}}[AUTHOR'S NOTE]
{{author_note}}{{/author_note}}

{{#summary}}[MEMORY — STORY SO FAR]
{{summary}}{{/summary}}"""

# V4 removes the per-turn prose-guidance paragraph from the System Instructions
# block (it moves to the post-history template, V2 below) and title-cases the
# world-info / author-note / memory section headers.
_DEFAULT_PROMPT_TEMPLATE_V4 = """{{#system_prompt}}[System Instructions]
You are participating in a simulated world. Narrate the thoughts, feelings, actions, and dialogue of {{char}} and all side characters except {{user}}—avoid narrating for {{user}}. {{char}} and side characters should act autonomously according to their established traits, personality, and background, with their own opinions, goals, and a capacity for disagreement. {{char}} and all side characters can only know, mention, or act on information they have personally witnessed, learned, or could plausibly deduce.
{{system_prompt}}{{/system_prompt}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}

{{#persona}}[{{user}}'s Persona]
{{persona}}{{/persona}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}

{{#lorebook}}[World Info / Character Lore]
{{lorebook}}{{/lorebook}}

{{#author_note}}[Author's Note]
{{author_note}}{{/author_note}}

{{#summary}}[Memory — Story So Far]
{{summary}}{{/summary}}"""

DEFAULT_PROMPT_TEMPLATE = _DEFAULT_PROMPT_TEMPLATE_V4


# Post-history templates are also versioned migration sentinels — same rule as
# the system templates above: never edit an existing version, add a new one.
_DEFAULT_POST_HISTORY_TEMPLATE_V1 = """{{#post_history_instructions}}[Post-History Instructions]
{{post_history_instructions}}{{/post_history_instructions}}"""


# V2 enforces the house prose style after the chat history and intentionally
# drops {{post_history_instructions}}, so a card's own post-history text is no
# longer rendered by default. The character editor surfaces this omission via
# the "field not used by active prompt" marker.
_DEFAULT_POST_HISTORY_TEMPLATE_V2 = """[Post-History Instructions]
Respond with 1-2 paragraphs using "show, don't tell", driving the story forward in interesting ways. Keep scenes grounded with nuanced descriptions and natural-sounding dialogue. Use a slow-burn pace while avoiding melodrama and leave openings for {{user}}'s physical or social engagement. You are allowed to explore mature themes that align with the narrative. Vary your prose and avoid repetitive phrases or formulaic descriptions—keep each response fresh and unique. ((OOC: OOC instructions like this are narrative guidance.))"""

DEFAULT_POST_HISTORY_TEMPLATE = _DEFAULT_POST_HISTORY_TEMPLATE_V2


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


def _retire_duplicate_greeting_cleanup(_conn):
    """Baseline databases after retiring the old destructive greeting repair."""
    # The repair ran on every startup before migration tracking existed. Current
    # databases are considered repaired; rerunning it could delete intentional
    # later messages that happen to repeat the opening greeting.
    return None


def _delete_legacy_context_max_messages(conn):
    """Remove the retired message-count context limit from old databases."""
    conn.execute(
        'DELETE FROM settings WHERE key=?',
        ('context_max_messages',),
    )


def _add_summary_to_legacy_default_prompt(conn):
    """Add summary memory to untouched copies of the former stock prompt."""
    conn.execute(
        'UPDATE system_prompts SET content=? WHERE content=?',
        (_DEFAULT_PROMPT_TEMPLATE_V2, _DEFAULT_PROMPT_TEMPLATE_V1),
    )


def _add_narrative_preamble_to_default_prompt(conn):
    """Upgrade untouched copies of the V2 stock prompt to the V3 preamble."""
    conn.execute(
        'UPDATE system_prompts SET content=? WHERE content=?',
        (_DEFAULT_PROMPT_TEMPLATE_V3, _DEFAULT_PROMPT_TEMPLATE_V2),
    )


def _upgrade_default_prompt_to_v4(conn):
    """Upgrade untouched copies of the V3 stock prompt to V4 (prose guidance
    moves to the post-history template; section headers title-cased)."""
    conn.execute(
        'UPDATE system_prompts SET content=? WHERE content=?',
        (_DEFAULT_PROMPT_TEMPLATE_V4, _DEFAULT_PROMPT_TEMPLATE_V3),
    )


def _enforce_house_style_post_history(conn):
    """Replace untouched copies of the V1 stock post-history template with the
    enforced house-style V2 (which drops {{post_history_instructions}})."""
    conn.execute(
        'UPDATE system_prompts SET post_history_content=? WHERE post_history_content=?',
        (_DEFAULT_POST_HISTORY_TEMPLATE_V2, _DEFAULT_POST_HISTORY_TEMPLATE_V1),
    )


def _rename_default_prompt_to_nanobear(conn):
    """Rename the stock 'Default' prompt to 'NanoBear'.

    Only the name changes — the templates are untouched, so a user who edited
    the stock prompt keeps their edits under the new label. Skipped entirely if
    a NanoBear already exists, which keeps a re-run from producing two.
    """
    if conn.execute("SELECT 1 FROM system_prompts WHERE name='NanoBear'").fetchone():
        return
    conn.execute("UPDATE system_prompts SET name='NanoBear' WHERE name='Default'")


MIGRATIONS = (
    (1, 'retire_duplicate_greeting_cleanup', _retire_duplicate_greeting_cleanup),
    (2, 'delete_legacy_context_max_messages', _delete_legacy_context_max_messages),
    (3, 'add_summary_to_legacy_default_prompt', _add_summary_to_legacy_default_prompt),
    (4, 'add_narrative_preamble_to_default_prompt', _add_narrative_preamble_to_default_prompt),
    (5, 'upgrade_default_prompt_to_v4', _upgrade_default_prompt_to_v4),
    (6, 'enforce_house_style_post_history', _enforce_house_style_post_history),
    (7, 'rename_default_prompt_to_nanobear', _rename_default_prompt_to_nanobear),
)


def _run_migrations(conn):
    """Run pending migrations in version order within the caller's transaction."""
    previous_version = 0
    for version, name, migrate in MIGRATIONS:
        if version <= previous_version:
            raise RuntimeError('Schema migrations must have unique, increasing versions')
        previous_version = version

        row = conn.execute(
            'SELECT name FROM schema_migrations WHERE version=?',
            (version,),
        ).fetchone()
        if row:
            if row['name'] != name:
                raise RuntimeError(
                    f'Schema migration version {version} is already recorded as '
                    f'{row["name"]!r}, not {name!r}'
                )
            continue

        migrate(conn)
        conn.execute(
            'INSERT INTO schema_migrations (version, name) VALUES (?, ?)',
            (version, name),
        )


def init_db():
    # Whether this call is creating the database for the first time. Only a
    # brand-new install gets the bundled character cards; upgrading an existing
    # install must not drop a character into a library the user already curates.
    fresh_install = not os.path.exists(DATABASE)

    with get_db() as conn:
        # WAL and synchronous are file-level settings that persist once set.
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')

        conn.executescript('''
            CREATE TABLE IF NOT EXISTS characters (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                filename   TEXT    NOT NULL UNIQUE,
                crc        TEXT    NOT NULL,
                missing    INTEGER DEFAULT 0,
                pinned_at  DATETIME DEFAULT NULL,
                archived_at DATETIME DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS character_collections (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                icon       TEXT    NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS character_collection_members (
                collection_id INTEGER NOT NULL,
                character_id  INTEGER NOT NULL,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collection_id, character_id),
                FOREIGN KEY (collection_id) REFERENCES character_collections(id) ON DELETE CASCADE,
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                active_lorebook_id INTEGER DEFAULT NULL,
                active_lorebook_embedded INTEGER NOT NULL DEFAULT 0,
                lorebook_notice_dismissed INTEGER NOT NULL DEFAULT 0,
                author_note TEXT NOT NULL DEFAULT '',
                summary_enabled INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT NOT NULL DEFAULT '',
                summary_up_to_msg_id INTEGER DEFAULT NULL,
                summary_status TEXT NOT NULL DEFAULT 'idle',
                summary_status_detail TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL CHECK(role IN ('user','character')),
                content    TEXT    NOT NULL,
                persona_id INTEGER DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS message_swipes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                content    TEXT    NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS personas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                tagline     TEXT    DEFAULT '',
                description TEXT    DEFAULT '',
                avatar_path TEXT    DEFAULT NULL,
                is_default  INTEGER DEFAULT 0,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL UNIQUE,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS system_prompts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                post_history_content TEXT NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS api_presets (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                name                 TEXT NOT NULL UNIQUE,
                api_endpoint         TEXT NOT NULL DEFAULT '',
                api_key              TEXT NOT NULL DEFAULT '',
                api_model            TEXT NOT NULL DEFAULT '',
                context_max_tokens   TEXT NOT NULL DEFAULT '32768',
                settings_json        TEXT NOT NULL DEFAULT '{}',
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS regex_presets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL UNIQUE,
                scripts_json  TEXT NOT NULL DEFAULT '[]',
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lorebooks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                book        TEXT    NOT NULL DEFAULT '{}',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            DROP INDEX IF EXISTS idx_messages_chat_created;

            CREATE INDEX IF NOT EXISTS idx_chats_character_created
                ON chats(character_id, created_at, id);
            CREATE INDEX IF NOT EXISTS idx_char_members_character
                ON character_collection_members(character_id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat_id
                ON messages(chat_id, id);
            CREATE INDEX IF NOT EXISTS idx_message_swipes_message
                ON message_swipes(message_id, id);
        ''')

        # executescript() commits implicitly. Serialize everything after it so
        # concurrent startup processes cannot both observe a pending migration.
        conn.execute('BEGIN IMMEDIATE')

        # Migration: add pinned_at to existing databases
        cols = [c[1] for c in conn.execute('PRAGMA table_info(characters)').fetchall()]
        if 'pinned_at' not in cols:
            conn.execute('ALTER TABLE characters ADD COLUMN pinned_at DATETIME DEFAULT NULL')
        if 'archived_at' not in cols:
            conn.execute('ALTER TABLE characters ADD COLUMN archived_at DATETIME DEFAULT NULL')

        chat_cols = [c[1] for c in conn.execute('PRAGMA table_info(chats)').fetchall()]
        if chat_cols and 'author_note' not in chat_cols:
            conn.execute("ALTER TABLE chats ADD COLUMN author_note TEXT NOT NULL DEFAULT ''")
        if chat_cols and 'summary_enabled' not in chat_cols:
            conn.execute('ALTER TABLE chats ADD COLUMN summary_enabled INTEGER NOT NULL DEFAULT 0')
        if chat_cols and 'summary_json' not in chat_cols:
            conn.execute("ALTER TABLE chats ADD COLUMN summary_json TEXT NOT NULL DEFAULT ''")
        if chat_cols and 'summary_up_to_msg_id' not in chat_cols:
            conn.execute('ALTER TABLE chats ADD COLUMN summary_up_to_msg_id INTEGER DEFAULT NULL')
        if chat_cols and 'summary_status' not in chat_cols:
            conn.execute("ALTER TABLE chats ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'idle'")
        if chat_cols and 'summary_status_detail' not in chat_cols:
            conn.execute("ALTER TABLE chats ADD COLUMN summary_status_detail TEXT NOT NULL DEFAULT ''")

        # A server restart kills any in-flight summary thread; clear stale state so
        # a chat isn't stuck showing "running" forever. Partial progress is safe:
        # the worker persists summary_json + watermark after each batch.
        conn.execute(
            "UPDATE chats SET summary_status='idle', summary_status_detail='' "
            "WHERE summary_status='running'"
        )

        collection_cols = [c[1] for c in conn.execute('PRAGMA table_info(character_collections)').fetchall()]
        if collection_cols and 'icon' not in collection_cols:
            conn.execute("ALTER TABLE character_collections ADD COLUMN icon TEXT NOT NULL DEFAULT ''")

        preset_cols = [c[1] for c in conn.execute('PRAGMA table_info(api_presets)').fetchall()]
        if preset_cols and 'settings_json' not in preset_cols:
            conn.execute("ALTER TABLE api_presets ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")

        system_prompt_cols = [c[1] for c in conn.execute('PRAGMA table_info(system_prompts)').fetchall()]
        if system_prompt_cols and 'post_history_content' not in system_prompt_cols:
            conn.execute("ALTER TABLE system_prompts ADD COLUMN post_history_content TEXT NOT NULL DEFAULT ''")
            # Backfill with the V1 stock sentinel; the enforce_house_style_post_history
            # migration below then bumps these to the current default, keeping legacy
            # DBs on the same path as ones that already had the column.
            conn.execute(
                'UPDATE system_prompts SET post_history_content = ? '
                "WHERE post_history_content = ''",
                (_DEFAULT_POST_HISTORY_TEMPLATE_V1,)
            )

        _run_migrations(conn)

        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('context_max_tokens', '32768') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('show_context_token_meter', '1') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('extra_request_params', '') "
            "ON CONFLICT(key) DO NOTHING"
        )
        # Auto Summaries — summarizer config defaults (per-chat enablement lives
        # on the chat row).
        for _sk, _sv in (
            ('summary_api_endpoint', ''),
            ('summary_api_key', ''),
            ('summary_api_model', ''),
            ('summary_cap_pct', '10'),
            ('summary_trigger_interval', '10'),
            ('summary_compress_batch', '3'),
        ):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING',
                (_sk, _sv)
            )

        # Bundled-character bookkeeping. '0' means seed_default_characters()
        # still has work to do; '1' means the copy already happened (or was
        # never owed, on an upgraded install). Written once and never reset, so
        # deleting a bundled character keeps it deleted.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('default_characters_seeded', ?) "
            "ON CONFLICT(key) DO NOTHING",
            ('0' if fresh_install else '1',)
        )

        # Seed a default persona if the table is empty
        if conn.execute('SELECT COUNT(*) FROM personas').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO personas (name, tagline, description, is_default) VALUES (?, ?, ?, 1)",
                ('Default User', 'The brave adventurer', '')
            )

        # Bundled-prompt bookkeeping, mirroring default_characters_seeded above
        # with one difference: existing installs are owed the presets too, so
        # this starts at '0' regardless of fresh_install. Written once and never
        # reset, so deleting a bundled preset keeps it deleted.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('default_prompts_seeded', '0') "
            "ON CONFLICT(key) DO NOTHING"
        )

        # Same bookkeeping again for the bundled regex preset.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('default_regex_seeded', '0') "
            "ON CONFLICT(key) DO NOTHING"
        )

        # Seed a default system prompt if the table is empty. Use the bare
        # default template — {{system_prompt}} stays as a live variable so the
        # per-character system_prompt field can fill it.
        if conn.execute('SELECT COUNT(*) FROM system_prompts').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO system_prompts (name, content, post_history_content) VALUES (?, ?, ?)",
                ('NanoBear', DEFAULT_PROMPT_TEMPLATE, DEFAULT_POST_HISTORY_TEMPLATE)
            )


def seed_default_characters():
    """Copy the bundled character cards into CHARACTERS_DIR on a fresh install.

    Runs at most once per data directory: the `default_characters_seeded`
    setting is flipped to '1' afterwards whether or not anything was copied, so
    a character the user later deletes stays deleted across restarts. The copies
    are ordinary cards on disk from then on — `_sync_characters` indexes them on
    the next `/api/characters` request, and nothing marks them as special.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_characters_seeded'"
        ).fetchone()
        if row is None or row['value'] != '0':
            return

        if os.path.isdir(BUNDLED_CHARACTERS_DIR):
            os.makedirs(CHARACTERS_DIR, exist_ok=True)
            for filename in sorted(os.listdir(BUNDLED_CHARACTERS_DIR)):
                if not filename.lower().endswith('.png') or filename.startswith('.'):
                    continue
                source = os.path.join(BUNDLED_CHARACTERS_DIR, filename)
                target = os.path.join(CHARACTERS_DIR, filename)
                if not os.path.isfile(source) or os.path.exists(target):
                    continue
                try:
                    shutil.copyfile(source, target)
                except OSError:
                    # A default character is a nicety, not a reason to refuse to
                    # start. Log it and leave the flag unset so a later run retries.
                    log.exception('Could not seed bundled character %s', filename)
                    return

        conn.execute(
            "UPDATE settings SET value='1' WHERE key='default_characters_seeded'"
        )


def seed_default_prompts():
    """Insert the bundled prompt presets into system_prompts, once per data dir.

    Unlike the bundled characters these also land on existing installs, since
    the presets are a feature rather than starter content. The
    `default_prompts_seeded` setting flips to '1' afterwards whether or not
    anything was inserted, so a preset the user later deletes stays deleted.
    From then on they are ordinary rows — editable, renameable, deletable.

    A preset whose name is already taken is skipped rather than duplicated,
    which keeps this from fighting a copy the user imported by hand.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_prompts_seeded'"
        ).fetchone()
        if row is None or row['value'] != '0':
            return

        if os.path.isdir(BUNDLED_PROMPTS_DIR):
            existing = {
                r['name'] for r in
                conn.execute('SELECT name FROM system_prompts').fetchall()
            }
            for filename in sorted(os.listdir(BUNDLED_PROMPTS_DIR)):
                if not filename.lower().endswith('.json') or filename.startswith('.'):
                    continue
                source = os.path.join(BUNDLED_PROMPTS_DIR, filename)
                try:
                    with open(source, encoding='utf-8') as handle:
                        preset = json.load(handle)
                    name = preset['name']
                    content = preset['content']
                    post_history = preset.get('post_history_content', '')
                except (OSError, ValueError, KeyError, TypeError):
                    # A bundled preset is a nicety, not a reason to refuse to
                    # start. Log it and leave the flag unset so a later run retries.
                    log.exception('Could not seed bundled prompt %s', filename)
                    return
                if name in existing:
                    continue
                conn.execute(
                    'INSERT INTO system_prompts (name, content, post_history_content) '
                    'VALUES (?, ?, ?)',
                    (name, content, post_history),
                )
                existing.add(name)

        conn.execute(
            "UPDATE settings SET value='1' WHERE key='default_prompts_seeded'"
        )


# Bundled regex presets, seeded by seed_default_regex_presets(). Small enough to
# live inline rather than in a bundled-file directory like default_prompts/.
#
# All of these are *optional conversions*, not fixes for anything broken. Cozy
# styles German, guillemet and Japanese speech as dialogue natively (see
# static/js/rp-dialogue.js), so none of this is needed to make a reply render
# correctly — these exist for people who simply want ASCII punctuation in the
# text that gets stored. They ship inactive, and double as worked examples of
# what the Regex tab can do.
#
# The quote characters are the entire point and are near-indistinguishable in a
# source listing, so, for reference:
#   U+201E „  German opening mark (sits on the baseline)
#   U+201C “  English opening mark — and German's *closing* mark
#   U+201D ”  English closing mark; models often emit it to close „ as well
#   U+00AB «  U+00BB »  guillemets
#   U+2018 ‘  U+2019 ’  curly singles, also used as apostrophes
#   U+00A0    no-break space   U+202F narrow no-break space (both invisible)
DEFAULT_REGEX_PRESETS = [
    {
        'name': 'German punctuation',
        'filters': [
            {
                # The pair-rebuilding rule: capture what's between the marks and
                # put it back inside straight ones.
                'name': 'Straighten German quotation marks',
                'find': '„([^“”"\n]*)[“”"]',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Straighten inward guillemets',
                'find': '»([^«\n]*)«',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                # Mop-up for any curly mark the pair rules didn't sit around.
                # Must run last, or it would eat the closers above.
                'name': 'Straighten stray curly quotes',
                'find': '[“”]',
                'replace': '"',
                'flags': 'g',
            },
        ],
    },
    {
        'name': 'French punctuation',
        'filters': [
            {
                # French pads the inside of its guillemets with a no-break
                # space, so the trims are part of the rule rather than optional.
                'name': 'Straighten guillemets',
                'find': '«[   ]*([^»\n]*?)[   ]*»',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                # French also spaces off ; : ! ?, which reads as a typo in
                # English-looking text and often renders as a visible gap.
                'name': 'Remove space before ; : ! ?',
                'find': '[   ]+([;:!?])',
                'replace': '$1',
                'flags': 'g',
            },
        ],
    },
    {
        'name': 'Straighten all quote marks',
        'filters': [
            {
                'name': 'German pairs',
                'find': '„([^“”"\n]*)[“”"]',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Inward guillemets',
                'find': '»([^«\n]*)«',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Outward guillemets',
                'find': '«[   ]*([^»\n]*?)[   ]*»',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Leftover curly doubles',
                'find': '[“”]',
                'replace': '"',
                'flags': 'g',
            },
            {
                # Also catches curly apostrophes — that’s the point, but it is
                # why this preset is separate from the language-specific ones.
                'name': 'Curly singles and apostrophes',
                'find': '[‘’]',
                'replace': "'",
                'flags': 'g',
            },
        ],
    },
]


def seed_default_regex_presets():
    """Insert the bundled regex preset into regex_presets, once per data dir.

    Follows seed_default_prompts(): existing installs are owed it too, the
    `default_regex_seeded` setting flips to '1' afterwards whether or not
    anything was inserted, and a name that is already taken is skipped rather
    than duplicated. From then on it is an ordinary row.

    Deliberately does *not* set `active_regex_preset`. The preset ships as a
    worked example to read, not as behaviour that silently rewrites replies —
    filtering stays off until it is picked from the dropdown, and the app then
    keeps using whatever was selected last.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_regex_seeded'"
        ).fetchone()
        if row is None or row['value'] != '0':
            return

        existing = {
            r['name'] for r in
            conn.execute('SELECT name FROM regex_presets').fetchall()
        }
        for preset in DEFAULT_REGEX_PRESETS:
            if preset['name'] in existing:
                continue
            conn.execute(
                'INSERT INTO regex_presets (name, scripts_json) VALUES (?, ?)',
                (preset['name'], json.dumps(preset['filters'], ensure_ascii=False)),
            )
            existing.add(preset['name'])

        conn.execute(
            "UPDATE settings SET value='1' WHERE key='default_regex_seeded'"
        )
