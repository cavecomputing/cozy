"""Database initialization and migration-ledger tests."""

import pytest

import shared


def _migration_rows():
    with shared.get_db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                'SELECT version, name, applied_at '
                'FROM schema_migrations ORDER BY version'
            ).fetchall()
        ]


class TestSchemaMigrationLedger:
    def test_fresh_database_creates_empty_ledger(self, tmp_path, monkeypatch):
        fresh_db = tmp_path / 'fresh.db'
        monkeypatch.setattr(shared, 'DATABASE', str(fresh_db))

        shared.init_db()

        with shared.get_db() as conn:
            columns = {
                row['name']
                for row in conn.execute('PRAGMA table_info(schema_migrations)')
            }
        assert columns == {'version', 'name', 'applied_at'}
        assert _migration_rows() == []

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
        assert _migration_rows() == []

    def test_init_db_does_not_reapply_recorded_migrations(self, monkeypatch):
        def add_probe(conn):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('migration_probe', 'ran'),
            )

        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            ((1, 'add_migration_probe', add_probe),),
        )

        shared.init_db()
        before = _migration_rows()
        shared.init_db()
        shared.init_db()

        assert _migration_rows() == before
        assert len(before) == 1
        assert before[0]['name'] == 'add_migration_probe'

    def test_failed_migration_rolls_back_work_and_ledger(self, monkeypatch):
        def fail_after_write(conn):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('failed_migration_probe', 'rollback me'),
            )
            raise RuntimeError('migration failed')

        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            ((1, 'failing_migration', fail_after_write),),
        )

        with pytest.raises(RuntimeError, match='migration failed'):
            shared.init_db()

        with shared.get_db() as conn:
            probe = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                ('failed_migration_probe',),
            ).fetchone()
        assert probe is None
        assert _migration_rows() == []

    def test_reusing_a_version_with_a_different_name_fails(self, monkeypatch):
        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            ((1, 'original_name', lambda conn: None),),
        )
        shared.init_db()
        before = _migration_rows()

        monkeypatch.setattr(
            shared,
            'MIGRATIONS',
            ((1, 'replacement_name', lambda conn: None),),
        )
        with pytest.raises(RuntimeError, match='already recorded'):
            shared.init_db()

        assert _migration_rows() == before

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
                CREATE TABLE character_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
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

            assert {'pinned_at', 'archived_at'} <= columns('characters')
            assert 'icon' in columns('character_collections')
            assert 'author_note' in columns('chats')
            assert 'settings_json' in columns('api_presets')
            assert 'post_history_content' in columns('system_prompts')
            assert columns('schema_migrations') == {
                'version',
                'name',
                'applied_at',
            }
