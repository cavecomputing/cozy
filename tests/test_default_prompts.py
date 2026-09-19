"""Tests for the prompt presets Cozy ships in default_prompts/ and seeds."""

import json
import os
import re

from cozy import shared
from cozy import defaults
from cozy import schema


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


from .helpers import (                                        # noqa: E402
    bundled_prompt_filenames as _bundled_filenames,
    bundled_prompt_titles as _bundled_titles,
    read_bundled_prompt as _read_preset,
)


def _seed_custom_bundle(tmp_path, files):
    """Seed from a scratch bundle dir; *files* maps filename to JSON text."""
    bundle = tmp_path / 'default_prompts'
    bundle.mkdir()
    for filename, content in files.items():
        (bundle / filename).write_text(content, encoding='utf-8')

    original_dir = shared.BUNDLED_PROMPTS_DIR
    shared.BUNDLED_PROMPTS_DIR = str(bundle)
    try:
        defaults.seed_default_prompts()
    finally:
        shared.BUNDLED_PROMPTS_DIR = original_dir


def _preset_file(name, content='x', version=None):
    preset = {'name': name, 'content': content, 'post_history_content': ''}
    if version is not None:
        preset['version'] = version
    return json.dumps(preset)


class TestBundledPresets:
    def test_bundled_dir_ships_presets(self):
        assert os.path.isdir(shared.BUNDLED_PROMPTS_DIR)
        assert _bundled_filenames()

    def test_bundled_presets_match_the_export_payload_shape(self):
        for filename in _bundled_filenames():
            preset = _read_preset(filename)
            assert {'name', 'version', 'content', 'post_history_content'} <= set(preset), filename
            assert set(preset) <= {'name', 'version', 'description', 'content',
                                   'post_history_content'}, filename
            assert preset['content'].strip(), filename
            assert preset['post_history_content'].strip(), filename
            assert preset['version'].strip(), filename
            # The picker's badge adds the "v" itself, so a bundled file
            # carrying one would render "vv2.1".
            assert re.fullmatch(r'\d+(?:\.\d+)?', preset['version']), filename
            if 'description' in preset:
                assert preset['description'].strip(), filename

    def test_preset_name_matches_its_filename(self):
        # Seeding titles from the filename is what lets a revised preset ship
        # as a new file. The name inside is what a hand-import uses, so the two
        # have to agree or the same preset lands under two different titles.
        for filename in _bundled_filenames():
            assert _read_preset(filename)['name'] == filename[:-len('.json')]

    def test_the_bundled_default_is_a_standard_nanobear(self):
        # A fresh install activates the standard NanoBear carrying the greatest
        # version, so the bundle has to ship one — and the Author variant must
        # never be the only NanoBear in it.
        matches = [t for t in _bundled_titles() if defaults.STANDARD_NANOBEAR_RE.match(t)]
        assert matches
        assert not any(t.startswith('NanoBear Author') for t in matches)


class TestSeeding:
    def test_init_db_seeds_no_prompt_of_its_own(self):
        # The stock prompt used to be inserted inline by init_db(); it now
        # arrives from the bundle like every other preset.
        assert _prompt_names() == []

    def test_fresh_install_seeds_every_bundled_preset(self):
        defaults.seed_default_prompts()
        assert _prompt_names() == _bundled_titles()

    def test_seeding_stores_each_bundled_description(self):
        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            rows = {
                r['name']: r['description'] for r in
                conn.execute('SELECT name, description FROM system_prompts').fetchall()
            }
        for filename in _bundled_filenames():
            preset = _read_preset(filename)
            assert rows[preset['name']] == preset.get('description', ''), filename

    def test_seeding_stores_each_bundled_version(self):
        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            rows = {
                r['name']: r['version'] for r in
                conn.execute('SELECT name, version FROM system_prompts').fetchall()
            }
        for filename in _bundled_filenames():
            preset = _read_preset(filename)
            assert rows[preset['name']] == preset['version'], filename

    def test_a_preset_file_without_a_version_seeds_blank(self, tmp_path):
        # _preset_file() writes no version key. A bundle predating the field
        # still has to seed rather than being skipped as unreadable.
        _seed_custom_bundle(tmp_path, {'Plain v1.json': _preset_file('Plain v1')})
        with shared.get_db() as conn:
            row = conn.execute(
                "SELECT version FROM system_prompts WHERE name='Plain v1'"
            ).fetchone()
        assert row is not None and row['version'] == ''

    def test_every_bundled_preset_carries_a_description(self):
        # The description is what the Prompt page shows under the picker, so a
        # bundled preset without one ships a blank line there.
        filenames = _bundled_filenames()
        assert filenames, 'no bundled presets found to check'
        for filename in filenames:
            assert _read_preset(filename).get('description', '').strip(), filename

    def test_fresh_install_starts_on_the_greatest_standard_nanobear(self):
        defaults.seed_default_prompts()
        expected = max(
            (t for t in _bundled_titles() if defaults.STANDARD_NANOBEAR_RE.match(t)),
            key=lambda t: defaults.version_key(_read_preset(t + '.json')['version']),
        )
        assert _active_prompt_name() == expected

    def test_an_author_variant_never_becomes_the_default(self, tmp_path):
        # Zulu sorts last and the Author variant carries the highest version;
        # neither may take the default from the standard NanoBear.
        _seed_custom_bundle(tmp_path, {
            'NanoBear.json': _preset_file('NanoBear', version='2.1'),
            'NanoBear Author.json': _preset_file('NanoBear Author', version='9.0'),
            'Zulu.json': _preset_file('Zulu', version='9.0'),
        })
        assert _active_prompt_name() == 'NanoBear'

    def test_among_several_standard_nanobears_the_greatest_version_wins(self, tmp_path):
        # Title order is deliberately the reverse of version order here: the
        # pick follows the version, and nothing about the filename.
        _seed_custom_bundle(tmp_path, {
            'NanoBear Classic.json': _preset_file('NanoBear Classic', version='3.0'),
            'NanoBear.json': _preset_file('NanoBear', version='2.1'),
            'NanoBear Author.json': _preset_file('NanoBear Author', version='9.0'),
        })
        assert _active_prompt_name() == 'NanoBear Classic'

    def test_versions_compare_as_numbers_not_text(self, tmp_path):
        # "2.10" sorts below "2.2" as text. The old greatest-title rule had
        # exactly that caveat; comparing the halves as integers retires it.
        _seed_custom_bundle(tmp_path, {
            'NanoBear.json': _preset_file('NanoBear', version='2.2'),
            'NanoBear Next.json': _preset_file('NanoBear Next', version='2.10'),
        })
        assert _active_prompt_name() == 'NanoBear Next'

    def test_with_no_standard_nanobear_the_greatest_title_wins(self, tmp_path):
        _seed_custom_bundle(tmp_path, {
            'Alpha v1.json': _preset_file('Alpha v1'),
            'Zulu v1.json': _preset_file('Zulu v1'),
        })
        assert _active_prompt_name() == 'Zulu v1'

    def test_a_bumped_version_reaches_an_install_that_already_has_the_title(self, tmp_path):
        # The whole point of versioned identity: bumping the number inside the
        # bundled file is the entire release, and it lands beside the edition
        # the user already has rather than being skipped as a known title.
        defaults.seed_default_prompts()
        before = _prompt_names()

        bumped = {
            filename: json.dumps({**_read_preset(filename), 'version': '9.9'})
            for filename in _bundled_filenames()
        }
        _seed_custom_bundle(tmp_path, bumped)

        with shared.get_db() as conn:
            rows = conn.execute(
                'SELECT name, version FROM system_prompts ORDER BY id'
            ).fetchall()
        # Every bundled title now exists twice: the shipped version and 9.9.
        assert len(rows) == len(before) * 2
        for title in before:
            versions = {r['version'] for r in rows if r['name'] == title}
            assert '9.9' in versions and len(versions) == 2, title

    def test_an_unbumped_file_changes_nothing_on_restart(self, tmp_path):
        # Editing content without bumping the version is not a release: the
        # pair is already present, so the row the user has is left alone.
        defaults.seed_default_prompts()
        edited = {
            filename: json.dumps({**_read_preset(filename), 'content': 'rewritten'})
            for filename in _bundled_filenames()
        }
        _seed_custom_bundle(tmp_path, edited)

        with shared.get_db() as conn:
            contents = [
                r['content'] for r in
                conn.execute('SELECT content FROM system_prompts').fetchall()
            ]
        assert 'rewritten' not in contents
        assert len(contents) == len(_bundled_titles())

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
            defaults.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert _active_prompt_name() == 'Alpha v1'

    def test_seeding_does_not_duplicate_on_restart(self):
        defaults.seed_default_prompts()
        before = _prompt_names()

        defaults.seed_default_prompts()
        assert _prompt_names() == before

    def test_a_deleted_preset_comes_back_on_the_next_start(self):
        # The directory is the source of truth: removing a preset for good
        # means deleting its file, not deleting the row.
        title = _bundled_titles()[0]
        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute('DELETE FROM system_prompts WHERE name=?', (title,))
        assert title not in _prompt_names()

        defaults.seed_default_prompts()
        assert title in _prompt_names()

    def test_a_preset_whose_file_is_gone_stays_deleted(self, tmp_path):
        gone = _bundled_titles()[0]
        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute('DELETE FROM system_prompts WHERE name=?', (gone,))

        kept = tmp_path / 'default_prompts'
        kept.mkdir()
        for filename in _bundled_filenames():
            if filename == gone + '.json':
                continue
            (kept / filename).write_text(
                json.dumps(_read_preset(filename)), encoding='utf-8'
            )

        original_dir = shared.BUNDLED_PROMPTS_DIR
        shared.BUNDLED_PROMPTS_DIR = str(kept)
        try:
            defaults.seed_default_prompts()
        finally:
            shared.BUNDLED_PROMPTS_DIR = original_dir

        assert gone not in _prompt_names()

    def test_an_edited_preset_is_never_overwritten(self):
        # Editing a bundled preset in place has to survive a restart, or the
        # restore-on-start behaviour would quietly undo the user's work.
        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            conn.execute(
                "UPDATE system_prompts SET content='my edit' WHERE name=?",
                ('NanoBear',),
            )

        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            rows = conn.execute(
                'SELECT content FROM system_prompts WHERE name=?',
                ('NanoBear',),
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

        defaults.seed_default_prompts()
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

        defaults.seed_default_prompts()
        with shared.get_db() as conn:
            active = conn.execute(
                "SELECT value FROM settings WHERE key='active_system_prompt'"
            ).fetchone()
        assert active is None

    def test_a_newly_bundled_preset_reaches_an_existing_install(self, tmp_path):
        defaults.seed_default_prompts()
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
            defaults.seed_default_prompts()
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
            defaults.seed_default_prompts()
            assert _prompt_names() == ['Good v1']

            (broken / 'Broken v1.json').write_text(
                json.dumps({'name': 'Broken v1', 'content': 'fixed', 'post_history_content': ''}),
                encoding='utf-8',
            )
            defaults.seed_default_prompts()
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

        schema.init_db()

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

        schema.init_db()
        defaults.seed_default_prompts()

        assert _prompt_names() == sorted([*_bundled_titles(), 'NanoBear'])
        with shared.get_db() as conn:
            old = conn.execute(
                "SELECT content FROM system_prompts WHERE name='NanoBear'"
            ).fetchone()
        assert old['content'] == 'the old stock prompt'
