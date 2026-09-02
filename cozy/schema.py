"""Database schema: table creation, additive column upgrades, and migrations.

``init_db()`` must stay idempotent. Adding a column goes in the
``PRAGMA table_info`` block near the end of it; changing existing rows goes in
the MIGRATIONS tuple below, appended with the next version and a new name —
a shipped entry must never be renumbered, renamed or reordered.
"""

import os

from cozy import shared
from cozy.defaults import (
    _DEFAULT_POST_HISTORY_TEMPLATE_V1,
    _DEFAULT_POST_HISTORY_TEMPLATE_V2,
    _DEFAULT_PROMPT_TEMPLATE_V1,
    _DEFAULT_PROMPT_TEMPLATE_V2,
    _DEFAULT_PROMPT_TEMPLATE_V3,
    _DEFAULT_PROMPT_TEMPLATE_V4,
)
from cozy.shared import get_db


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


def _delete_summary_compress_batch(conn):
    """Remove the retired summary compression setting.

    A batch of messages now becomes exactly one summary entry, so there is no second
    pass merging entries and nothing left for the setting to size.
    """
    conn.execute(
        'DELETE FROM settings WHERE key=?',
        ('summary_compress_batch',),
    )


# Migrations 3-7 upgrade a stock prompt that init_db() used to insert inline.
# Nothing creates that row any more — the house prompt ships as a file in
# default_prompts/ and a revision arrives as a new file — so this is the last
# of them. They stay for databases that predate them, where the stock row is
# still whatever version was current when it was written.
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


def _remove_character_gallery(conn):
    """Retire gallery-only settings and organization state.

    Dropping ``archived_at`` makes every former archive row an ordinary
    character again without touching its card PNG or related chats.
    """
    conn.execute("DELETE FROM settings WHERE key='show_gallery_button'")
    conn.execute('DROP TABLE IF EXISTS character_collection_members')
    conn.execute('DROP TABLE IF EXISTS character_collections')
    character_cols = {
        row['name'] for row in conn.execute('PRAGMA table_info(characters)').fetchall()
    }
    if 'archived_at' in character_cols:
        conn.execute('ALTER TABLE characters DROP COLUMN archived_at')


def _backfill_chat_persona(conn):
    """Seed the new ``chats.persona_id`` from each chat's own history.

    Before this column the persona was a browser preference, so opening an old
    chat on a second machine used whatever persona that machine last touched.
    The messages already record who was speaking, so the last user message is
    the chat's own answer — better than leaving every existing chat to fall
    back to the local preference forever.

    A database old enough to predate ``messages.persona_id`` has nothing to read,
    and keeps every chat at NULL.
    """
    message_cols = {
        row['name'] for row in conn.execute('PRAGMA table_info(messages)').fetchall()
    }
    if 'persona_id' not in message_cols:
        return
    conn.execute('''
        UPDATE chats SET persona_id = (
            SELECT m.persona_id FROM messages m
            WHERE m.chat_id = chats.id AND m.role = 'user' AND m.persona_id IS NOT NULL
            ORDER BY m.id DESC LIMIT 1
        ) WHERE persona_id IS NULL
    ''')


def _delete_default_prompts_seeded(conn):
    """Remove the retired one-shot flag for bundled prompt seeding.

    Prompts are now restored from `default_prompts/` on every start, so there
    is no first run left for a flag to mark. See seed_default_prompts().
    """
    conn.execute(
        'DELETE FROM settings WHERE key=?',
        ('default_prompts_seeded',),
    )


MIGRATIONS = (
    (1, 'retire_duplicate_greeting_cleanup', _retire_duplicate_greeting_cleanup),
    (2, 'delete_legacy_context_max_messages', _delete_legacy_context_max_messages),
    (3, 'add_summary_to_legacy_default_prompt', _add_summary_to_legacy_default_prompt),
    (4, 'add_narrative_preamble_to_default_prompt', _add_narrative_preamble_to_default_prompt),
    (5, 'upgrade_default_prompt_to_v4', _upgrade_default_prompt_to_v4),
    (6, 'enforce_house_style_post_history', _enforce_house_style_post_history),
    (7, 'rename_default_prompt_to_nanobear', _rename_default_prompt_to_nanobear),
    (8, 'remove_character_gallery', _remove_character_gallery),
    (9, 'backfill_chat_persona', _backfill_chat_persona),
    (10, 'delete_summary_compress_batch', _delete_summary_compress_batch),
    (11, 'delete_default_prompts_seeded', _delete_default_prompts_seeded),
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
    fresh_install = not os.path.exists(shared.DATABASE)

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
                lorebook_notice_dismissed INTEGER NOT NULL DEFAULT 0,
                author_note TEXT NOT NULL DEFAULT '',
                summary_enabled INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT NOT NULL DEFAULT '',
                summary_up_to_msg_id INTEGER DEFAULT NULL,
                summary_status TEXT NOT NULL DEFAULT 'idle',
                summary_status_detail TEXT NOT NULL DEFAULT '',
                persona_id INTEGER DEFAULT NULL,
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
        if chat_cols and 'persona_id' not in chat_cols:
            conn.execute('ALTER TABLE chats ADD COLUMN persona_id INTEGER DEFAULT NULL')

        # A server restart kills any in-flight summary thread; clear stale state so
        # a chat isn't stuck showing "running" forever. Partial progress is safe:
        # the worker persists summary_json + watermark after each batch.
        conn.execute(
            "UPDATE chats SET summary_status='idle', summary_status_detail='' "
            "WHERE summary_status='running'"
        )

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

        # Bundled prompts have no flag of their own — seed_default_prompts()
        # restores whatever is missing on every start.

        # Bookkeeping for the bundled regex preset.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('default_regex_seeded', '0') "
            "ON CONFLICT(key) DO NOTHING"
        )
