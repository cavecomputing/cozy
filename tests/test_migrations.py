"""Database initialization and migration-ledger tests."""

import os

import pytest

import shared
from png_utils import make_minimal_png


def _migration_rows():
    with shared.get_db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                'SELECT version, name, applied_at '
                'FROM schema_migrations ORDER BY version'
            ).fetchall()
        ]


def _registered_migrations():
    return [
        {'version': version, 'name': name}
        for version, name, _migrate in shared.MIGRATIONS
    ]


class TestSchemaMigrationLedger:
    def test_fresh_database_creates_ledger(self, tmp_path, monkeypatch):
        fresh_db = tmp_path / 'fresh.db'
        monkeypatch.setattr(shared, 'DATABASE', str(fresh_db))

        shared.init_db()

        with shared.get_db() as conn:
            columns = {
                row['name']
                for row in conn.execute('PRAGMA table_info(schema_migrations)')
            }
            tables = {
                row['name']
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            character_columns = {
                row['name'] for row in conn.execute('PRAGMA table_info(characters)')
            }
            gallery_setting = conn.execute(
                "SELECT 1 FROM settings WHERE key='show_gallery_button'"
            ).fetchone()
        assert columns == {'version', 'name', 'applied_at'}
        assert 'character_collections' not in tables
        assert 'character_collection_members' not in tables
        assert 'archived_at' not in character_columns
        assert gallery_setting is None
        assert [
            {'version': row['version'], 'name': row['name']}
            for row in _migration_rows()
        ] == _registered_migrations()

    def test_existing_unversioned_database_gets_ledger_without_data_loss(self):
        with shared.get_db() as conn:
            conn.execute('DROP TABLE schema_migrations')
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('pre_ledger_probe', 'keep me'),
            )

        shared.init_db()

        with shared.get_db() as conn:
            probe = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                ('pre_ledger_probe',),
            ).fetchone()
        assert probe['value'] == 'keep me'
        assert [
            {'version': row['version'], 'name': row['name']}
            for row in _migration_rows()
        ] == _registered_migrations()

    def test_init_db_does_not_reapply_recorded_migrations(self, monkeypatch):
        def add_probe(conn):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('migration_probe', 'ran'),
            )

        probe_version = shared.MIGRATIONS[-1][0] + 1
        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            (*shared.MIGRATIONS, (probe_version, 'add_migration_probe', add_probe)),
        )

        shared.init_db()
        before = _migration_rows()
        shared.init_db()
        shared.init_db()

        assert _migration_rows() == before
        assert before[-1]['version'] == probe_version
        assert before[-1]['name'] == 'add_migration_probe'

    def test_failed_migration_rolls_back_work_and_ledger(self, monkeypatch):
        def fail_after_write(conn):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('failed_migration_probe', 'rollback me'),
            )
            raise RuntimeError('migration failed')

        before = _migration_rows()
        probe_version = shared.MIGRATIONS[-1][0] + 1
        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            (*shared.MIGRATIONS, (probe_version, 'failing_migration', fail_after_write)),
        )

        with pytest.raises(RuntimeError, match='migration failed'):
            shared.init_db()

        with shared.get_db() as conn:
            probe = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                ('failed_migration_probe',),
            ).fetchone()
        assert probe is None
        assert _migration_rows() == before

    def test_reusing_a_version_with_a_different_name_fails(self, monkeypatch):
        original_migrations = shared.MIGRATIONS
        probe_version = original_migrations[-1][0] + 1
        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            (*original_migrations, (probe_version, 'original_name', lambda conn: None)),
        )
        shared.init_db()
        before = _migration_rows()

        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            (*original_migrations, (probe_version, 'replacement_name', lambda conn: None)),
        )
        with pytest.raises(RuntimeError, match='already recorded'):
            shared.init_db()

        assert _migration_rows() == before

    def test_unversioned_upgrade_preserves_legitimate_repeated_greeting(
        self,
        client,
        sample_chat,
    ):
        chat_id = sample_chat['id']
        first = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character',
            'content': 'Welcome back.',
        }).get_json()
        middle = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user',
            'content': 'It has been a while.',
        }).get_json()
        repeated = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character',
            'content': 'Welcome back.',
        }).get_json()

        with shared.get_db() as conn:
            conn.execute('DROP TABLE schema_migrations')

        shared.init_db()
        shared.init_db()

        messages = client.get(f'/api/chats/{chat_id}/messages').get_json()
        assert [
            (message['id'], message['role'], message['content'])
            for message in messages
        ] == [
            (first['id'], 'character', 'Welcome back.'),
            (middle['id'], 'user', 'It has been a while.'),
            (repeated['id'], 'character', 'Welcome back.'),
        ]
        assert [
            {'version': row['version'], 'name': row['name']}
            for row in _migration_rows()
        ] == _registered_migrations()

    def test_unversioned_upgrade_deletes_legacy_context_message_setting(
        self,
        client,
    ):
        with shared.get_db() as conn:
            conn.execute('DROP TABLE schema_migrations')
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('context_max_messages', '64'),
            )

        shared.init_db()
        after_upgrade = _migration_rows()
        shared.init_db()

        with shared.get_db() as conn:
            legacy_row = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                ('context_max_messages',),
            ).fetchone()
        assert legacy_row is None
        assert _migration_rows() == after_upgrade
        assert [
            {'version': row['version'], 'name': row['name']}
            for row in after_upgrade
        ] == _registered_migrations()
        assert 'context_max_messages' not in client.get('/api/settings').get_json()

    def test_unversioned_upgrade_adds_summary_to_legacy_default_prompt(self):
        with shared.get_db() as conn:
            conn.execute('DROP TABLE schema_migrations')
            conn.execute(
                'UPDATE system_prompts SET content=? WHERE name=?',
                (shared._DEFAULT_PROMPT_TEMPLATE_V1, 'NanoBear'),
            )

        shared.init_db()
        after_upgrade = _migration_rows()
        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['content'] == shared.DEFAULT_PROMPT_TEMPLATE
        assert '{{#summary}}' in prompt['content']
        assert _migration_rows() == after_upgrade

    def test_summary_prompt_migration_preserves_customized_prompt(self):
        custom_prompt = shared._DEFAULT_PROMPT_TEMPLATE_V1 + '\n\nCustom instructions.'
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'add_summary_to_legacy_default_prompt'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET content=? WHERE name=?',
                (custom_prompt, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
            migration = conn.execute(
                'SELECT name FROM schema_migrations WHERE version=?',
                (migration_version,),
            ).fetchone()
        assert prompt['content'] == custom_prompt
        assert migration['name'] == 'add_summary_to_legacy_default_prompt'

    def test_unversioned_upgrade_adds_narrative_preamble_to_default_prompt(self):
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'add_narrative_preamble_to_default_prompt'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET content=? WHERE name=?',
                (shared._DEFAULT_PROMPT_TEMPLATE_V2, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['content'] == shared._DEFAULT_PROMPT_TEMPLATE_V3
        assert 'simulated world' in prompt['content']

    def test_narrative_preamble_migration_preserves_customized_prompt(self):
        custom_prompt = shared._DEFAULT_PROMPT_TEMPLATE_V2 + '\n\nCustom instructions.'
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'add_narrative_preamble_to_default_prompt'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET content=? WHERE name=?',
                (custom_prompt, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['content'] == custom_prompt

    def test_unversioned_upgrade_upgrades_default_prompt_to_v4(self):
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'upgrade_default_prompt_to_v4'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET content=? WHERE name=?',
                (shared._DEFAULT_PROMPT_TEMPLATE_V3, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['content'] == shared._DEFAULT_PROMPT_TEMPLATE_V4
        assert prompt['content'] == shared.DEFAULT_PROMPT_TEMPLATE

    def test_v4_prompt_migration_preserves_customized_prompt(self):
        custom_prompt = shared._DEFAULT_PROMPT_TEMPLATE_V3 + '\n\nCustom instructions.'
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'upgrade_default_prompt_to_v4'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET content=? WHERE name=?',
                (custom_prompt, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['content'] == custom_prompt

    def test_enforce_house_style_post_history_upgrades_untouched(self):
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'enforce_house_style_post_history'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET post_history_content=? WHERE name=?',
                (shared._DEFAULT_POST_HISTORY_TEMPLATE_V1, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT post_history_content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['post_history_content'] == shared.DEFAULT_POST_HISTORY_TEMPLATE
        assert '{{post_history_instructions}}' not in prompt['post_history_content']

    def test_post_history_migration_preserves_customized(self):
        custom_phi = shared._DEFAULT_POST_HISTORY_TEMPLATE_V1 + '\n\nExtra house rule.'
        migration_version = next(
            v for v, name, _ in shared.MIGRATIONS
            if name == 'enforce_house_style_post_history'
        )
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE system_prompts SET post_history_content=? WHERE name=?',
                (custom_phi, 'NanoBear'),
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration_version,),
            )

        shared.init_db()

        with shared.get_db() as conn:
            prompt = conn.execute(
                'SELECT post_history_content FROM system_prompts WHERE name=?',
                ('NanoBear',),
            ).fetchone()
        assert prompt['post_history_content'] == custom_phi

    def test_init_db_runs_twice_safely(self):
        """Running init_db repeatedly on an existing DB should be a no-op."""
        shared.init_db()
        shared.init_db()

        with shared.get_db() as conn:
            cols = {row['name'] for row in conn.execute('PRAGMA table_info(chats)')}
            assert 'active_lorebook_id' in cols
            assert 'active_lorebook_embedded' in cols
            assert 'lorebook_notice_dismissed' in cols
            tables = {
                row['name']
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {'lorebooks', 'schema_migrations'} <= tables

    def test_init_db_migrates_legacy_columns(self, tmp_path, monkeypatch):
        legacy_db = tmp_path / 'legacy.db'
        monkeypatch.setattr(shared, 'DATABASE', str(legacy_db))
        with shared.get_db() as conn:
            conn.executescript('''
                CREATE TABLE characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    crc TEXT NOT NULL,
                    missing INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    active_lorebook_id INTEGER DEFAULT NULL,
                    active_lorebook_embedded INTEGER NOT NULL DEFAULT 0,
                    lorebook_notice_dismissed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE api_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    api_endpoint TEXT NOT NULL DEFAULT '',
                    api_key TEXT NOT NULL DEFAULT '',
                    api_model TEXT NOT NULL DEFAULT '',
                    context_max_tokens TEXT NOT NULL DEFAULT '32768',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE system_prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            ''')

        shared.init_db()

        with shared.get_db() as conn:
            def columns(table):
                return {
                    row['name']
                    for row in conn.execute(f'PRAGMA table_info({table})')
                }

            assert 'pinned_at' in columns('characters')
            assert 'archived_at' not in columns('characters')
            assert 'author_note' in columns('chats')
            assert 'settings_json' in columns('api_presets')
            assert 'post_history_content' in columns('system_prompts')
            assert columns('schema_migrations') == {
                'version',
                'name',
                'applied_at',
            }

    def test_gallery_removal_migrates_existing_data_safely(
        self, tmp_path, monkeypatch, client
    ):
        legacy_db = tmp_path / 'gallery.db'
        monkeypatch.setattr(shared, 'DATABASE', str(legacy_db))
        filename = 'Archived.png'
        card_path = os.path.join(shared.CHARACTERS_DIR, filename)
        card_bytes = make_minimal_png()
        with open(card_path, 'wb') as card_file:
            card_file.write(card_bytes)

        with shared.get_db() as conn:
            conn.executescript('''
                CREATE TABLE characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    crc TEXT NOT NULL,
                    missing INTEGER DEFAULT 0,
                    pinned_at DATETIME DEFAULT NULL,
                    archived_at DATETIME DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE character_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    icon TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE character_collection_members (
                    collection_id INTEGER NOT NULL,
                    character_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (collection_id, character_id),
                    FOREIGN KEY (collection_id) REFERENCES character_collections(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (character_id) REFERENCES characters(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
                );
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            conn.executemany(
                'INSERT INTO schema_migrations (version, name) VALUES (?, ?)',
                [(version, name) for version, name, _migrate in shared.MIGRATIONS[:7]],
            )
            conn.execute(
                '''INSERT INTO characters
                   (id, filename, crc, pinned_at, archived_at)
                   VALUES (1, ?, 'legacy', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)''',
                (filename,),
            )
            conn.execute(
                "INSERT INTO character_collections (id, name, icon) VALUES (1, 'Old', '★')"
            )
            conn.execute(
                'INSERT INTO character_collection_members (collection_id, character_id) '
                'VALUES (1, 1)'
            )
            conn.execute(
                "INSERT INTO chats (id, character_id, name) VALUES (1, 1, 'Keep')"
            )
            conn.execute(
                "INSERT INTO messages (id, chat_id, role, content) "
                "VALUES (1, 1, 'character', 'Keep this too')"
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('show_gallery_button', '0')"
            )

        shared.init_db()
        shared.init_db()

        with shared.get_db() as conn:
            tables = {
                row['name']
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            character_columns = {
                row['name'] for row in conn.execute('PRAGMA table_info(characters)')
            }
            character = conn.execute(
                'SELECT id, filename, pinned_at FROM characters WHERE id=1'
            ).fetchone()
            chat = conn.execute('SELECT name FROM chats WHERE id=1').fetchone()
            message = conn.execute('SELECT content FROM messages WHERE id=1').fetchone()
            setting = conn.execute(
                "SELECT value FROM settings WHERE key='show_gallery_button'"
            ).fetchone()
            migration = conn.execute(
                'SELECT name FROM schema_migrations WHERE version=8'
            ).fetchone()

        assert 'character_collections' not in tables
        assert 'character_collection_members' not in tables
        assert 'archived_at' not in character_columns
        assert character['id'] == 1
        assert character['filename'] == filename
        assert character['pinned_at'] is not None
        assert chat['name'] == 'Keep'
        assert message['content'] == 'Keep this too'
        assert setting is None
        assert migration['name'] == 'remove_character_gallery'
        with open(card_path, 'rb') as card_file:
            assert card_file.read() == card_bytes

        listing = client.get('/api/characters').get_json()
        assert [item['id'] for item in listing] == [1]
