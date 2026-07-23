"""Shared state, path constants, and DB helpers used by app.py and all route modules."""

import os
import sqlite3
from contextlib import contextmanager

from flask import jsonify

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.environ.get('COZY_DATA_DIR', os.path.join(BASE_DIR, 'data'))
DATABASE     = os.path.join(DATA_DIR, 'cozy_chat.db')
CHARACTERS_DIR = os.path.join(DATA_DIR, 'characters')
PERSONAS_DIR   = os.path.join(DATA_DIR, 'personas')
THEMES_DIR     = os.path.join(DATA_DIR, 'themes')
BUILTIN_THEMES_DIR = os.path.join(BASE_DIR, 'static', 'themes')
ALLOWED_IMG  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


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

DEFAULT_PROMPT_TEMPLATE = _DEFAULT_PROMPT_TEMPLATE_V2


DEFAULT_POST_HISTORY_TEMPLATE = """{{#post_history_instructions}}[Post-History Instructions]
{{post_history_instructions}}{{/post_history_instructions}}"""


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


MIGRATIONS = (
    (1, 'retire_duplicate_greeting_cleanup', _retire_duplicate_greeting_cleanup),
    (2, 'delete_legacy_context_max_messages', _delete_legacy_context_max_messages),
    (3, 'add_summary_to_legacy_default_prompt', _add_summary_to_legacy_default_prompt),
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
            conn.execute(
                'UPDATE system_prompts SET post_history_content = ? '
                "WHERE post_history_content = ''",
                (DEFAULT_POST_HISTORY_TEMPLATE,)
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
            ('summary_trigger_interval', '20'),
        ):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING',
                (_sk, _sv)
            )

        # Seed a default persona if the table is empty
        if conn.execute('SELECT COUNT(*) FROM personas').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO personas (name, tagline, description, is_default) VALUES (?, ?, ?, 1)",
                ('Default User', 'The brave adventurer', '')
            )

        # Seed a default system prompt if the table is empty. Use the bare
        # default template — {{system_prompt}} stays as a live variable so the
        # per-character system_prompt field can fill it.
        if conn.execute('SELECT COUNT(*) FROM system_prompts').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO system_prompts (name, content, post_history_content) VALUES (?, ?, ?)",
                ('Default', DEFAULT_PROMPT_TEMPLATE, DEFAULT_POST_HISTORY_TEMPLATE)
            )
