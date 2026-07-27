"""Tests for the BigBear prompt presets Cozy ships and seeds into system_prompts."""

import json
import os

import shared


def _seeded_flag():
    with shared.get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_prompts_seeded'"
        ).fetchone()
    return row['value'] if row else None


def _prompt_names():
    with shared.get_db() as conn:
        return sorted(
            r['name'] for r in
            conn.execute('SELECT name FROM system_prompts').fetchall()
        )


def _bundled_filenames():
    return sorted(
        f for f in os.listdir(shared.BUNDLED_PROMPTS_DIR)
        if f.lower().endswith('.json') and not f.startswith('.')
    )


class TestBundledPresets:
    def test_bundled_dir_ships_presets(self):
        assert os.path.isdir(shared.BUNDLED_PROMPTS_DIR)
        assert _bundled_filenames()

    def test_bundled_presets_match_the_export_payload_shape(self):
        for filename in _bundled_filenames():
            path = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
            with open(path, encoding='utf-8') as handle:
                preset = json.load(handle)
            assert set(preset) == {'name', 'content', 'post_history_content'}, filename
            assert preset['name'].startswith('BigBear'), filename
            assert preset['content'].strip(), filename
            assert preset['post_history_content'].strip(), filename

    def test_post_history_wraps_the_user_message(self):
        # Without {{user_message}} the template appends as a separate user
        # message and merges into the player's turn, leaving the directives
        # undelimited. See the <direction> wrapper in the build script.
        for filename in _bundled_filenames():
            path = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
            with open(path, encoding='utf-8') as handle:
                preset = json.load(handle)
            assert '{{user_message}}' in preset['post_history_content'], filename

    def test_presets_carry_no_turn_taking_clauses(self):
        # These are what made the model stop and wait for input; the whole
        # point of the Director/Adaptive Novel chassis is that they are gone.
        banned = (
            "Don't move the scene beyond {{user}}'s input",
            'Allow space for {{user}} input',
            'open ended action requires',
            'Stay locked in the current minute',
        )
        for filename in _bundled_filenames():
            path = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
            with open(path, encoding='utf-8') as handle:
                preset = json.load(handle)
            body = preset['content'] + preset['post_history_content']
            for clause in banned:
                assert clause not in body, f'{filename} still carries {clause!r}'


class TestSeeding:
    def test_fresh_install_seeds_every_bundled_preset(self):
        assert _seeded_flag() == '0'
        shared.seed_default_prompts()

        names = _prompt_names()
        for filename in _bundled_filenames():
            path = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
            with open(path, encoding='utf-8') as handle:
                assert json.load(handle)['name'] in names

    def test_stock_prompt_is_named_nanobear(self):
        assert 'NanoBear' in _prompt_names()
        assert 'Default' not in _prompt_names()

    def test_seeding_flips_the_flag_and_does_not_repeat(self):
        shared.seed_default_prompts()
        assert _seeded_flag() == '1'
        before = _prompt_names()

        shared.seed_default_prompts()
        assert _prompt_names() == before

    def test_a_deleted_preset_stays_deleted(self):
        shared.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute("DELETE FROM system_prompts WHERE name='BigBear - General'")

        shared.seed_default_prompts()
        assert 'BigBear - General' not in _prompt_names()

    def test_an_existing_name_is_not_duplicated(self):
        # Someone who imported a preset by hand before upgrading keeps their
        # copy rather than ending up with two rows of the same name.
        with shared.get_db() as conn:
            conn.execute(
                'INSERT INTO system_prompts (name, content, post_history_content) '
                "VALUES ('BigBear - General', 'mine', 'mine')",
            )

        shared.seed_default_prompts()
        with shared.get_db() as conn:
            rows = conn.execute(
                "SELECT content FROM system_prompts WHERE name='BigBear - General'"
            ).fetchall()
        assert [r['content'] for r in rows] == ['mine']


class TestNanoBearMigration:
    def test_rename_leaves_user_edits_intact(self):
        with shared.get_db() as conn:
            conn.execute("UPDATE system_prompts SET content='edited' WHERE name='NanoBear'")
            conn.execute("UPDATE system_prompts SET name='Default' WHERE name='NanoBear'")
            conn.execute("DELETE FROM schema_migrations WHERE name='rename_default_prompt_to_nanobear'")
            shared._run_migrations(conn)
            row = conn.execute(
                "SELECT content FROM system_prompts WHERE name='NanoBear'"
            ).fetchone()
        assert row['content'] == 'edited'

    def test_rename_is_skipped_when_nanobear_already_exists(self):
        with shared.get_db() as conn:
            conn.execute(
                'INSERT INTO system_prompts (name, content, post_history_content) '
                "VALUES ('Default', 'stock', '')",
            )
            conn.execute("DELETE FROM schema_migrations WHERE name='rename_default_prompt_to_nanobear'")
            shared._run_migrations(conn)
            names = sorted(
                r['name'] for r in
                conn.execute("SELECT name FROM system_prompts WHERE name IN ('Default','NanoBear')").fetchall()
            )
        assert names == ['Default', 'NanoBear']
