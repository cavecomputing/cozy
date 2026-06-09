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
DEFAULT_PROMPT_TEMPLATE = """{{#system_prompt}}[System Instructions]
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
{{lorebook}}{{/lorebook}}"""


DEFAULT_POST_HISTORY_TEMPLATE = """{{#post_history_instructions}}[Post-History Instructions]
{{post_history_instructions}}{{/post_history_instructions}}"""


# ── Database helpers ────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
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

        # Migration: add pinned_at to existing databases
        cols = [c[1] for c in conn.execute('PRAGMA table_info(characters)').fetchall()]
        if 'pinned_at' not in cols:
            conn.execute('ALTER TABLE characters ADD COLUMN pinned_at DATETIME DEFAULT NULL')
        if 'archived_at' not in cols:
            conn.execute('ALTER TABLE characters ADD COLUMN archived_at DATETIME DEFAULT NULL')

        collection_cols = [c[1] for c in conn.execute('PRAGMA table_info(character_collections)').fetchall()]
        if collection_cols and 'icon' not in collection_cols:
            conn.execute("ALTER TABLE character_collections ADD COLUMN icon TEXT NOT NULL DEFAULT ''")

        system_prompt_cols = [c[1] for c in conn.execute('PRAGMA table_info(system_prompts)').fetchall()]
        if system_prompt_cols and 'post_history_content' not in system_prompt_cols:
            conn.execute("ALTER TABLE system_prompts ADD COLUMN post_history_content TEXT NOT NULL DEFAULT ''")
            conn.execute(
                'UPDATE system_prompts SET post_history_content = ? '
                "WHERE post_history_content = ''",
                (DEFAULT_POST_HISTORY_TEMPLATE,)
            )

        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('context_max_tokens', '32768') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('show_context_token_meter', '1') "
            "ON CONFLICT(key) DO NOTHING"
        )

        # One-shot cleanup: a previous bug re-seeded character greetings into
        # the END of long chats whenever the messages GET failed. Remove any
        # character message whose content matches the chat's first character
        # message (which is the legitimate greeting). Idempotent.
        conn.execute('''
            DELETE FROM messages
            WHERE role = 'character'
              AND id > (
                  SELECT MIN(id) FROM messages m2
                  WHERE m2.chat_id = messages.chat_id AND m2.role = 'character'
              )
              AND content = (
                  SELECT content FROM messages m3
                  WHERE m3.id = (
                      SELECT MIN(id) FROM messages m4
                      WHERE m4.chat_id = messages.chat_id AND m4.role = 'character'
                  )
              )
        ''')

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
