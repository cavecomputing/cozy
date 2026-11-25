"""Database initialization and migration-ledger tests."""

import os
import sqlite3

import pytest

from cozy import shared
from cozy import schema


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
    def test_a_new_database_starts_at_the_baseline(self, tmp_path, monkeypatch):
        fresh_db = tmp_path / 'fresh.db'
        monkeypatch.setattr(shared, 'DATABASE', str(fresh_db))

        schema.init_db()

        with shared.get_db() as conn:
            columns = {
                row['name']
                for row in conn.execute('PRAGMA table_info(schema_migrations)')
            }
        assert columns == {'version', 'name', 'applied_at'}
        assert [(row['version'], row['name']) for row in _migration_rows()] == [
            (schema.BASELINE_VERSION, 'baseline'),
            *[(version, name) for version, name, _ in schema.MIGRATIONS],
        ]

    @pytest.mark.parametrize('ledger', [
        'DROP TABLE schema_migrations',
        'DELETE FROM schema_migrations',
        # Every row moved down so the newest sits just below the baseline.
        'UPDATE schema_migrations SET version=version-'
        f'{schema.database_version() - schema.BASELINE_VERSION + 1}',
    ])
    def test_a_database_from_before_the_baseline_is_refused_untouched(self, ledger):
        with shared.get_db() as conn:
            conn.execute(ledger)
            conn.execute("INSERT INTO settings (key, value) VALUES ('older_probe', 'keep me')")

        with pytest.raises(RuntimeError, match=schema.LAST_UPGRADING_COMMIT):
            schema.init_db()

        with shared.get_db() as conn:
            probe = conn.execute("SELECT value FROM settings WHERE key='older_probe'").fetchone()
            tables = {row['name'] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert probe['value'] == 'keep me'
        assert ('schema_migrations' in tables) == (not ledger.startswith('DROP'))
        assert not os.path.exists(schema.before_update_path())

    def test_a_migration_numbered_at_or_below_the_baseline_fails(self, monkeypatch):
        monkeypatch.setattr(
            schema, 'MIGRATIONS', ((schema.BASELINE_VERSION, 'too_low', lambda conn: None),))
        with pytest.raises(RuntimeError, match='above the baseline'):
            schema.init_db()

    def test_init_db_does_not_reapply_recorded_migrations(self, monkeypatch):
        def add_probe(conn):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('migration_probe', 'ran'),
            )

        probe_version = schema.database_version() + 1
        monkeypatch.setattr(
            schema,
            'MIGRATIONS',
            (*schema.MIGRATIONS, (probe_version, 'add_migration_probe', add_probe)),
        )

        schema.init_db()
        before = _migration_rows()
        schema.init_db()
        schema.init_db()

        assert _migration_rows() == before
        assert before[-1]['version'] == probe_version
        assert before[-1]['name'] == 'add_migration_probe'

    def test_a_database_is_copied_as_it_was_before_a_start_migrates_it(self, monkeypatch):
        copy_path = schema.before_update_path()

        def versions(path):
            conn = sqlite3.connect(path)
            try:
                return [row[0] for row in conn.execute('SELECT version FROM schema_migrations ORDER BY version')]
            finally:
                conn.close()

        # Nothing pending, nothing copied.
        schema.init_db()
        assert not os.path.exists(copy_path)

        def add_probe(conn):
            conn.execute("INSERT INTO settings (key, value) VALUES ('migration_probe', 'ran')")

        last = schema.database_version()
        monkeypatch.setattr(schema, 'MIGRATIONS', (*schema.MIGRATIONS, (last + 1, 'add_probe', add_probe)))
        schema.init_db()

        assert versions(copy_path)[-1] == last
        assert versions(shared.DATABASE)[-1] == last + 1
        # A whole database of its own, with no sidecars left beside it.
        assert not [name for name in os.listdir(os.path.dirname(copy_path))
                    if name.startswith(os.path.basename(copy_path) + '-')]

        # One copy: the next migrating start replaces it, and no other start touches it.
        schema.init_db()
        assert versions(copy_path)[-1] == last
        monkeypatch.setattr(schema, 'MIGRATIONS', (*schema.MIGRATIONS, (last + 2, 'add_another', lambda conn: None)))
        schema.init_db()
        assert versions(copy_path)[-1] == last + 1

    def test_failed_migration_rolls_back_work_and_ledger(self, monkeypatch):
        def fail_after_write(conn):
            conn.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)',
                ('failed_migration_probe', 'rollback me'),
            )
            raise RuntimeError('migration failed')

        before = _migration_rows()
        probe_version = schema.database_version() + 1
        monkeypatch.setattr(
            schema,
            'MIGRATIONS',
            (*schema.MIGRATIONS, (probe_version, 'failing_migration', fail_after_write)),
        )

        with pytest.raises(RuntimeError, match='migration failed'):
            schema.init_db()

        with shared.get_db() as conn:
            probe = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                ('failed_migration_probe',),
            ).fetchone()
        assert probe is None
        assert _migration_rows() == before

    def test_reusing_a_version_with_a_different_name_fails(self, monkeypatch):
        original_migrations = schema.MIGRATIONS
        probe_version = schema.database_version() + 1
        monkeypatch.setattr(
            schema,
            'MIGRATIONS',
            (*original_migrations, (probe_version, 'original_name', lambda conn: None)),
        )
        schema.init_db()
        before = _migration_rows()

        monkeypatch.setattr(
            schema,
            'MIGRATIONS',
            (*original_migrations, (probe_version, 'replacement_name', lambda conn: None)),
        )
        with pytest.raises(RuntimeError, match='already recorded'):
            schema.init_db()

        assert _migration_rows() == before

    def test_init_db_runs_twice_safely(self):
        """Running init_db repeatedly on an existing DB should be a no-op."""
        schema.init_db()
        schema.init_db()

        with shared.get_db() as conn:
            cols = {row['name'] for row in conn.execute('PRAGMA table_info(chats)')}
            assert 'active_lorebook_id' in cols
            assert 'active_lorebook_embedded' in cols
            assert 'lorebook_notice_dismissed' not in cols
            tables = {
                row['name']
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {'lorebooks', 'schema_migrations'} <= tables
