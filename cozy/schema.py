"""Database schema: table creation, additive column upgrades, and migrations.

``init_db()`` must stay idempotent. Adding a column goes in its CREATE TABLE
and, behind a column-presence check, at the marked spot after the script;
changing existing rows goes in the MIGRATIONS tuple below, appended with the
next version and a new name — a shipped entry must never be renumbered,
renamed or reordered.
"""

import logging
import os
import sqlite3

from cozy import shared
from cozy.shared import get_db

log = logging.getLogger('cozy')

# The version a new database is created at, and the oldest one this build
# opens. The steps that brought an older database up to it are gone: the last
# build that has them is LAST_UPGRADING_COMMIT.
BASELINE_VERSION = 21
LAST_UPGRADING_COMMIT = 'fc337a2'

# (version, name, function taking the connection), numbered from the baseline up.
MIGRATIONS = ()


def database_version():
    """The database version this build works at."""
    return MIGRATIONS[-1][0] if MIGRATIONS else BASELINE_VERSION


def _run_migrations(conn):
    """Run pending migrations in version order within the caller's transaction,
    and return the names of those that ran."""
    applied = []
    previous_version = BASELINE_VERSION
    for version, name, migrate in MIGRATIONS:
        if version <= previous_version:
            raise RuntimeError(
                'Schema migrations must have unique, increasing versions above the baseline')
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
        applied.append(name)
    return applied


def before_update_path():
    """Where the copy of the database from before its last migration is kept."""
    return f'{os.path.splitext(shared.DATABASE)[0]}.before-update.db'


def _version_on_disk():
    """The version of the database on disk: the highest in its ledger, 0 without one."""
    with get_db() as conn:
        try:
            return conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] or 0
        except sqlite3.OperationalError:
            return 0  # From before the ledger.


def _copy_before_migrating(version):
    """Copy the database beside itself when this start is about to migrate it.

    A migration rewrites rows where they are and nothing migrates back down, so
    an update that goes wrong, or one the user wants to undo, had no way back
    unless they had made a backup first. One copy is kept, as the database was
    when the build before this one last ran, and the next migrating start
    replaces it.
    """
    with get_db() as conn:
        copy = sqlite3.connect(before_update_path())
        try:
            conn.backup(copy)
        finally:
            copy.close()
    log.info('Kept the database as it was at version %d: %s', version, before_update_path())


def init_db():
    # Whether this call is creating the database for the first time. Only a
    # brand-new install gets the bundled character cards; upgrading an existing
    # install must not drop a character into a library the user already curates.
    fresh_install = not os.path.exists(shared.DATABASE)
    if not fresh_install:
        version = _version_on_disk()
        if version < BASELINE_VERSION:
            raise RuntimeError(
                f'{shared.DATABASE} is at database version {version}. This Cozy opens '
                f'version {BASELINE_VERSION} and later, and no longer upgrades an older '
                f'one. Start Cozy at commit {LAST_UPGRADING_COMMIT} once to upgrade it, '
                'then update again.'
            )
        if version < database_version():
            _copy_before_migrating(version)

    with get_db() as conn:
        # journal_mode lives in the database header, so setting it once here
        # covers every later connection. synchronous does not: it is
        # per-connection, so this line governs init_db's own writes only —
        # get_db() leaves request connections at SQLite's default of FULL.
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')

        conn.executescript('''
            CREATE TABLE IF NOT EXISTS characters (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                filename   TEXT    NOT NULL UNIQUE,
                crc        TEXT    NOT NULL,
                missing    INTEGER DEFAULT 0,
                pinned_at  DATETIME DEFAULT NULL,
                archived   INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                active_lorebook_id INTEGER DEFAULT NULL,
                active_lorebook_embedded INTEGER NOT NULL DEFAULT 0,
                author_note TEXT NOT NULL DEFAULT '',
                summary_enabled INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT NOT NULL DEFAULT '',
                summary_up_to_msg_id INTEGER DEFAULT NULL,
                summary_status TEXT NOT NULL DEFAULT 'idle',
                summary_status_detail TEXT NOT NULL DEFAULT '',
                persona_id INTEGER DEFAULT NULL,
                parent_chat_id INTEGER DEFAULT NULL,
                parent_msg_id INTEGER DEFAULT NULL,
                fork_msg_id INTEGER DEFAULT NULL,
                opened_at DATETIME DEFAULT NULL,
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL CHECK(role IN ('user','character')),
                content    TEXT    NOT NULL,
                persona_id INTEGER DEFAULT NULL,
                persona_name TEXT DEFAULT NULL,
                selected_swipe_id INTEGER DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS message_swipes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                content    TEXT    NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                edited_at  DATETIME DEFAULT NULL,
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
                version    TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
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

            CREATE INDEX IF NOT EXISTS idx_chats_character_created
                ON chats(character_id, created_at, id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat_id
                ON messages(chat_id, id);
            CREATE INDEX IF NOT EXISTS idx_message_swipes_message
                ON message_swipes(message_id, id);
        ''')

        # executescript() commits implicitly. Serialize everything after it so
        # concurrent startup processes cannot both observe a pending migration.
        conn.execute('BEGIN IMMEDIATE')

        # A column added since the baseline goes here, behind a check that the
        # database lacks it: a new database already has it from the script above.

        # A server restart kills any in-flight summary thread; clear stale state so
        # a chat isn't stuck showing "running" forever. Partial progress is safe:
        # the worker persists summary_json + watermark after each batch.
        conn.execute(
            "UPDATE chats SET summary_status='idle', summary_status_detail='' "
            "WHERE summary_status='running'"
        )

        if fresh_install:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, 'baseline')",
                (BASELINE_VERSION,),
            )
        applied = _run_migrations(conn)
        if fresh_install:
            log.info('Created the database at %s', shared.DATABASE)
        elif applied:
            log.info('Migrated the database to version %d: %s',
                     database_version(), ', '.join(applied))

        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('context_max_tokens', '32768') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('show_context_token_meter', '0') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('show_advanced_configuration', '0') "
            "ON CONFLICT(key) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('use_character_prompts', '0') "
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
        # The first-run setup (static/js/setup.js) opens while this is '1'. Only a
        # fresh install owes it; finishing or skipping the setup writes '0'.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('setup_pending', ?) "
            "ON CONFLICT(key) DO NOTHING",
            ('1' if fresh_install else '0',)
        )

        # Seed a default persona if the table is empty
        if conn.execute('SELECT COUNT(*) FROM personas').fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO personas (name, tagline, description, is_default) VALUES (?, ?, ?, 1)",
                ('User', 'Change me!', '')
            )

        # Bundled prompts have no flag of their own — seed_default_prompts()
        # restores whatever is missing on every start.
