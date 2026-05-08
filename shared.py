"""Shared state, path constants, and DB helpers used by app.py and all route modules."""

import os
import sqlite3
import logging
from contextlib import contextmanager

log = logging.getLogger('cozy')

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.environ.get('COZY_DATA_DIR', os.path.join(BASE_DIR, 'data'))
DATABASE     = os.path.join(DATA_DIR, 'cozy_chat.db')
CHARACTERS_DIR = os.path.join(DATA_DIR, 'characters')
PERSONAS_DIR   = os.path.join(DATA_DIR, 'personas')
THEMES_DIR     = os.path.join(DATA_DIR, 'themes')
BUILTIN_THEMES_DIR = os.path.join(BASE_DIR, 'static', 'themes')
ALLOWED_IMG  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

os.makedirs(CHARACTERS_DIR, exist_ok=True)
os.makedirs(PERSONAS_DIR, exist_ok=True)
os.makedirs(THEMES_DIR, exist_ok=True)


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

# Used by the migration to detect rows already in template form. Keep adjacent
# to DEFAULT_PROMPT_TEMPLATE so they stay in sync.
_BUILDER_VARS = ('{{description}}', '{{personality}}', '{{scenario}}',
                 '{{persona}}', '{{mesExamples}}', '{{lorebook}}',
                 '{{system_prompt}}')


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
    os.makedirs(CHARACTERS_DIR, exist_ok=True)
    os.makedirs(PERSONAS_DIR, exist_ok=True)
    with get_db() as conn:
        # WAL and synchronous are file-level settings that persist once set.
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')

        # Migrate from old DB-stored character data to file-based storage.
        # If the old schema is detected, drop character + dependent tables.
        try:
            conn.execute('SELECT description FROM characters LIMIT 0')
            log.info('Old characters schema detected — migrating to file-based storage')
            conn.executescript('''
                DROP TABLE IF EXISTS message_swipes;
                DROP TABLE IF EXISTS messages;
                DROP TABLE IF EXISTS chats;
                DROP TABLE IF EXISTS characters;
            ''')
        except sqlite3.OperationalError:
            pass

        conn.executescript('''
            CREATE TABLE IF NOT EXISTS characters (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                filename   TEXT    NOT NULL UNIQUE,
                crc        TEXT    NOT NULL,
                missing    INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
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
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS api_presets (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                name                 TEXT NOT NULL UNIQUE,
                api_endpoint         TEXT NOT NULL DEFAULT '',
                api_key              TEXT NOT NULL DEFAULT '',
                api_model            TEXT NOT NULL DEFAULT '',
                context_max_messages TEXT NOT NULL DEFAULT '0',
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lorebooks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                book        TEXT    NOT NULL DEFAULT '{}',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # Additive migration: per-chat lorebook selection columns. Runs once
        # against existing DBs; no-op afterwards. The FK on active_lorebook_id
        # cannot be added by ALTER TABLE in SQLite, so we enforce the cascade
        # behaviour at delete time inside routes/lorebooks.py.
        chat_cols = {row['name'] for row in conn.execute('PRAGMA table_info(chats)').fetchall()}
        if 'active_lorebook_id' not in chat_cols:
            conn.execute('ALTER TABLE chats ADD COLUMN active_lorebook_id INTEGER DEFAULT NULL')
        if 'active_lorebook_embedded' not in chat_cols:
            conn.execute('ALTER TABLE chats ADD COLUMN active_lorebook_embedded INTEGER NOT NULL DEFAULT 0')
        if 'lorebook_notice_dismissed' not in chat_cols:
            conn.execute('ALTER TABLE chats ADD COLUMN lorebook_notice_dismissed INTEGER NOT NULL DEFAULT 0')

        preset_cols = {row['name'] for row in conn.execute('PRAGMA table_info(api_presets)').fetchall()}
        if 'context_max_tokens' not in preset_cols:
            conn.execute("ALTER TABLE api_presets ADD COLUMN context_max_tokens TEXT NOT NULL DEFAULT '32768'")

        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('context_max_tokens', '32768') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('show_context_token_meter', '1') "
            "ON CONFLICT(key) DO NOTHING"
        )

        # Seed a default persona if the table is empty
        if conn.execute('SELECT COUNT(*) FROM personas').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO personas (name, tagline, description, is_default) VALUES (?, ?, ?, 1)",
                ('Default User', 'The brave adventurer', '')
            )

        # One-shot migration: wrap legacy plain-text system_prompts rows in the
        # default template, injecting their old prose at the {{system_prompt}}
        # slot. Gated by a sentinel settings row so it never runs twice.
        sentinel = conn.execute(
            "SELECT value FROM settings WHERE key='prompt_template_migration'"
        ).fetchone()
        if not sentinel:
            rows = conn.execute('SELECT id, content FROM system_prompts').fetchall()
            for row in rows:
                c = row['content'] or ''
                if not any(v in c for v in _BUILDER_VARS):
                    new_content = DEFAULT_PROMPT_TEMPLATE.replace('{{system_prompt}}', c)
                    conn.execute(
                        'UPDATE system_prompts SET content=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                        (new_content, row['id'])
                    )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('prompt_template_migration', '1')"
            )

        # Seed a default system prompt if the table is empty. Use the bare
        # default template — {{system_prompt}} stays as a live variable so the
        # per-character system_prompt field can fill it.
        if conn.execute('SELECT COUNT(*) FROM system_prompts').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO system_prompts (name, content) VALUES (?, ?)",
                ('Default', DEFAULT_PROMPT_TEMPLATE)
            )
