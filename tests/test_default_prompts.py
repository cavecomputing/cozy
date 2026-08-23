"""Tests for the prompt presets Cozy ships in default_prompts/ and seeds."""

import json
import os

import shared


def _active_prompt_name():
    with shared.get_db() as conn:
        active = conn.execute(
            "SELECT value FROM settings WHERE key='active_system_prompt'"
        ).fetchone()
        if active is None:
            return None
        row = conn.execute(
            'SELECT name FROM system_prompts WHERE id=?', (active['value'],)
        ).fetchone()
    return row['name'] if row else None


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


def _bundled_titles():
    return sorted(f[:-len('.json')] for f in _bundled_filenames())


def _read_preset(filename):
    path = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


class TestBundledPresets:
    def test_bundled_dir_ships_presets(self):
        assert os.path.isdir(shared.BUNDLED_PROMPTS_DIR)
        assert _bundled_filenames()

    def test_bundled_presets_match_the_export_payload_shape(self):
        for filename in _bundled_filenames():
            preset = _read_preset(filename)
            assert set(preset) == {'name', 'content', 'post_history_content'}, filename
            assert preset['content'].strip(), filename
            assert preset['post_history_content'].strip(), filename

    def test_preset_name_matches_its_filename(self):
        # Seeding titles from the filename is what lets a revised preset ship
        # as a new file. The name inside is what a hand-import uses, so the two
        # have to agree or the same preset lands under two different titles.
        for filename in _bundled_filenames():
            assert _read_preset(filename)['name'] == filename[:-len('.json')]

    def test_the_house_prompt_sorts_last(self):
        # A fresh install activates the alphabetically greatest title, so the
        # current NanoBear has to stay at the end of the listing. A new preset
        # titled after it would quietly become every new user's default.
        assert _bundled_titles()[-1] == 'NanoBear v2.1'

    def test_bigbear_post_history_wraps_the_user_message(self):
        # Without {{user_message}} the template appends as a separate user
        # message and merges into the player's turn, leaving the directives
        # undelimited. See the <direction> wrapper in the build script.
        for filename in _bundled_filenames():
            if not filename.startswith('BigBear'):
                continue
            preset = _read_preset(filename)
            assert '{{user_message}}' in preset['post_history_content'], filename

    def test_bigbear_presets_carry_no_turn_taking_clauses(self):
        # These are what made the model stop and wait for input; the whole
        # point of the Director/Adaptive Novel chassis is that they are gone.
        banned = (
            "Don't move the scene beyond {{user}}'s input",
            'Allow space for {{user}} input',
            'open ended action requires',
            'Stay locked in the current minute',
        )
        for filename in _bundled_filenames():
            if not filename.startswith('BigBear'):
                continue
            preset = _read_preset(filename)
            body = preset['content'] + preset['post_history_content']
            for clause in banned:
                assert clause not in body, f'{filename} still carries {clause!r}'


class TestSeeding:
    def test_init_db_seeds_no_prompt_of_its_own(self):
        # The stock prompt used to be inserted inline by init_db(); it now
        # arrives from the bundle like every other preset.
        assert _prompt_names() == []

    def test_fresh_install_seeds_every_bundled_preset(self):
        shared.seed_default_prompts()
        assert _prompt_names() == _bundled_titles()

    def test_fresh_install_starts_on_the_last_title_alphabetically(self):
        shared.seed_default_prompts()
        assert _active_prompt_name() == _bundled_titles()[-1]

    def test_a_later_version_takes_over_the_default_on_a_fresh_install(self, tmp_path):
        # The point of the alphabetical rule: shipping NanoBear v2.2 makes it
        # the default for new installs with nothing else to update.
        later = tmp_path / 'default_prompts'
        later.mkdir()
        for filename in _bundled_filenames():
            (later / filename).write_text(
                json.dumps(_read_preset(filename)), encoding='utf-8'
            )
        (later / 'NanoBear v2.2.json').write_text(
            json.dumps({'name': 'NanoBear v2.2', 'content': 'newer', 'post_history_content': ''}),
            encoding='utf-8',
        )

        original_dir = shared.BUNDLED_PROMPTS_DIR
        shared.BUNDLED_PROMPTS_DIR = str(later)
        try:
            shared.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert _active_prompt_name() == 'NanoBear v2.2'

    def test_a_broken_last_title_falls_back_to_the_one_below_it(self, tmp_path):
        bundle = tmp_path / 'default_prompts'
        bundle.mkdir()
        (bundle / 'Alpha v1.json').write_text(
            json.dumps({'name': 'Alpha v1', 'content': 'a', 'post_history_content': ''}),
            encoding='utf-8',
        )
        (bundle / 'Zulu v1.json').write_text('{not json', encoding='utf-8')

        original_dir = shared.BUNDLED_PROMPTS_DIR
        shared.BUNDLED_PROMPTS_DIR = str(bundle)
        try:
            shared.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert _active_prompt_name() == 'Alpha v1'

    def test_seeding_does_not_duplicate_on_restart(self):
        shared.seed_default_prompts()
        before = _prompt_names()

        shared.seed_default_prompts()
        assert _prompt_names() == before

    def test_a_deleted_preset_comes_back_on_the_next_start(self):
        # The directory is the source of truth: removing a preset for good
        # means deleting its file, not deleting the row.
        shared.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute("DELETE FROM system_prompts WHERE name='BigBear - General'")
        assert 'BigBear - General' not in _prompt_names()

        shared.seed_default_prompts()
        assert 'BigBear - General' in _prompt_names()

    def test_a_preset_whose_file_is_gone_stays_deleted(self, tmp_path):
        shared.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute("DELETE FROM system_prompts WHERE name='BigBear - General'")

        kept = tmp_path / 'default_prompts'
        kept.mkdir()
        for filename in _bundled_filenames():
            if filename == 'BigBear - General.json':
                continue
            (kept / filename).write_text(
                json.dumps(_read_preset(filename)), encoding='utf-8'
            )

        original_dir = shared.BUNDLED_PROMPTS_DIR
        shared.BUNDLED_PROMPTS_DIR = str(kept)
        try:
            shared.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert 'BigBear - General' not in _prompt_names()

    def test_an_edited_preset_is_never_overwritten(self):
        # Editing a bundled preset in place has to survive a restart, or the
        # restore-on-start behaviour would quietly undo the user's work.
        shared.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute(
                "UPDATE system_prompts SET content='my edit' WHERE name=?",
                ('NanoBear v2.1',),
            )

        shared.seed_default_prompts()
        with shared.get_db() as conn:
            rows = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear v2.1',),
            ).fetchall()
        assert [r['content'] for r in rows] == ['my edit']

    def test_an_existing_title_is_not_duplicated(self):
        # Someone who imported a preset by hand before upgrading keeps their
        # copy rather than ending up with two rows of the same title.
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

    def test_an_existing_install_keeps_its_active_prompt(self):
        # Gaining presets must not move a selection the user already has.
        with shared.get_db() as conn:
            conn.execute(
                'INSERT INTO system_prompts (name, content, post_history_content) '
                "VALUES ('Mine', 'mine', 'mine')",
            )

        shared.seed_default_prompts()
        with shared.get_db() as conn:
            active = conn.execute(
                "SELECT value FROM settings WHERE key='active_system_prompt'"
            ).fetchone()
        assert active is None

    def test_a_newly_bundled_preset_reaches_an_existing_install(self, tmp_path):
        shared.seed_default_prompts()
        assert 'Later Release v1' not in _prompt_names()

        later = tmp_path / 'default_prompts'
        later.mkdir()
        for filename in _bundled_filenames():
            (later / filename).write_text(
                json.dumps(_read_preset(filename)), encoding='utf-8'
            )
        (later / 'Later Release v1.json').write_text(
            json.dumps({'name': 'Later Release v1', 'content': 'new', 'post_history_content': ''}),
            encoding='utf-8',
        )

        original_dir = shared.BUNDLED_PROMPTS_DIR
        shared.BUNDLED_PROMPTS_DIR = str(later)
        try:
            shared.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert _prompt_names() == sorted([*_bundled_titles(), 'Later Release v1'])

    def test_a_broken_preset_is_skipped_and_retried(self, tmp_path):
        broken = tmp_path / 'default_prompts'
        broken.mkdir()
        (broken / 'Good v1.json').write_text(
            json.dumps({'name': 'Good v1', 'content': 'fine', 'post_history_content': ''}),
            encoding='utf-8',
        )
        (broken / 'Broken v1.json').write_text('{not json', encoding='utf-8')

        original_dir = shared.BUNDLED_PROMPTS_DIR
        shared.BUNDLED_PROMPTS_DIR = str(broken)
        try:
            shared.seed_default_prompts()
            assert _prompt_names() == ['Good v1']

            (broken / 'Broken v1.json').write_text(
                json.dumps({'name': 'Broken v1', 'content': 'fixed', 'post_history_content': ''}),
                encoding='utf-8',
            )
            shared.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert _prompt_names() == ['Broken v1', 'Good v1']


class TestUpgradeFromTheSeededFlag:
    def test_the_retired_flag_is_deleted(self):
        with shared.get_db() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('default_prompts_seeded', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
            conn.execute(
                'DELETE FROM schema_migrations WHERE name=?',
                ('delete_default_prompts_seeded',),
            )

        shared.init_db()

        with shared.get_db() as conn:
            flag = conn.execute(
                "SELECT 1 FROM settings WHERE key='default_prompts_seeded'"
            ).fetchone()
        assert flag is None

    def test_the_new_nanobear_reaches_an_install_that_already_seeded(self):
        # The old bundle had a plain "NanoBear" inserted by init_db(). The
        # versioned titles are new files, so an upgrade is owed them and the
        # prompt the user is already on is left exactly as it was.
        with shared.get_db() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('default_prompts_seeded', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
            conn.execute(
                'INSERT INTO system_prompts (name, content, post_history_content) '
                "VALUES ('NanoBear', 'the old stock prompt', '')",
            )

        shared.init_db()
        shared.seed_default_prompts()

        assert _prompt_names() == sorted([*_bundled_titles(), 'NanoBear'])
        with shared.get_db() as conn:
            old = conn.execute(
                "SELECT content FROM system_prompts WHERE name='NanoBear'"
            ).fetchone()
        assert old['content'] == 'the old stock prompt'
