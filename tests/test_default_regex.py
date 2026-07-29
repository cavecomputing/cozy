"""The bundled regex presets — seeded once, inert until selected."""
import json

import pytest

import shared
from test_regex_engine import SETUP, run_node_module


GERMAN = 'German punctuation'
ALL_NAMES = [p['name'] for p in shared.DEFAULT_REGEX_PRESETS]


def _seeded_flag():
    with shared.get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_regex_seeded'"
        ).fetchone()
    return row['value'] if row else None


def _preset_names():
    with shared.get_db() as conn:
        return [r['name'] for r in conn.execute('SELECT name FROM regex_presets').fetchall()]


def _filters_for(name):
    return next(p['filters'] for p in shared.DEFAULT_REGEX_PRESETS if p['name'] == name)


class TestSeeding:
    def test_seeds_every_bundled_preset(self):
        assert _seeded_flag() == '0'
        shared.seed_default_regex_presets()
        for name in ALL_NAMES:
            assert name in _preset_names()

    def test_seeding_flips_the_flag_and_does_not_repeat(self):
        shared.seed_default_regex_presets()
        assert _seeded_flag() == '1'
        before = _preset_names()

        shared.seed_default_regex_presets()
        assert _preset_names() == before

    def test_a_deleted_preset_stays_deleted(self):
        shared.seed_default_regex_presets()
        with shared.get_db() as conn:
            conn.execute('DELETE FROM regex_presets WHERE name = ?', (GERMAN,))

        shared.seed_default_regex_presets()
        assert GERMAN not in _preset_names()

    def test_an_existing_name_is_not_duplicated(self):
        """Someone who imported their own copy keeps it rather than getting two rows."""
        with shared.get_db() as conn:
            conn.execute(
                'INSERT INTO regex_presets (name, scripts_json) VALUES (?, ?)',
                (GERMAN, '[]'),
            )

        shared.seed_default_regex_presets()
        with shared.get_db() as conn:
            rows = conn.execute(
                'SELECT scripts_json FROM regex_presets WHERE name = ?', (GERMAN,)
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]['scripts_json'] == '[]'

    def test_seeding_does_not_activate_anything(self):
        """They ship as worked examples, not as behaviour that starts on its own."""
        shared.seed_default_regex_presets()
        with shared.get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='active_regex_preset'"
            ).fetchone()
        assert row is None or row['value'] == ''

    def test_preset_names_are_unique(self):
        """The table has a UNIQUE name column — a clash would fail the insert."""
        assert len(ALL_NAMES) == len(set(ALL_NAMES))


class TestSeededContent:
    @pytest.mark.parametrize('name', ALL_NAMES)
    def test_survives_the_api_normaliser_unchanged(self, client, name):
        """What ships must be exactly what the API hands back — no silent rewriting."""
        shared.seed_default_regex_presets()
        listed = client.get('/api/regex-presets').get_json()
        preset = next(p for p in listed if p['name'] == name)
        assert preset['filters'] == _filters_for(name)

    def test_no_bundled_field_holds_a_raw_control_character(self):
        """Find and Replace are single-line inputs, which delete CR and LF.

        A pattern shipping a real newline came back from the DOM without it the
        first time any row in the preset was touched, and that truncated version
        was then saved — quietly turning "don't cross a line break" off.
        """
        for preset in shared.DEFAULT_REGEX_PRESETS:
            for f in preset['filters']:
                for field in ('find', 'replace'):
                    assert not set('\r\n\t') & set(f[field]), (
                        f"{preset['name']} / {f['name']} has a raw control "
                        f"character in {field}; write it as an escape instead"
                    )

    def test_every_bundled_pattern_compiles(self):
        """A shipped pattern that doesn't compile would be silently skipped."""
        every = [f for p in shared.DEFAULT_REGEX_PRESETS for f in p['filters']]
        run_node_module(SETUP + f"""
            const filters = {json.dumps(every)};
            for (const f of filters) {{
                assert.equal(filterError(f), '', 'did not compile: ' + f.name);
            }}
        """)


class TestGermanPreset:
    def test_straightens_german_quotes_and_guillemets(self):
        filters = json.dumps(_filters_for(GERMAN))
        run_node_module(SETUP + f"""
            const filters = {filters};
            // The reported case: „…" closed by U+201C.
            assert.equal(
                runFilters('Sie sagte: \\u201eHallo!\\u201c Dann ging sie.', filters),
                'Sie sagte: "Hallo!" Dann ging sie.');
            // Models often emit U+201D as the closer instead.
            assert.equal(
                runFilters('Er rief: \\u201eHalt!\\u201d', filters),
                'Er rief: "Halt!"');
            // Inward guillemets, the other German convention.
            assert.equal(
                runFilters('\\u00bbGuten Tag\\u00ab, sagte er.', filters),
                '"Guten Tag", sagte er.');
            // A stray curly mark with no opener is still cleaned up.
            assert.equal(runFilters('a \\u201cb\\u201d c', filters), 'a "b" c');
            // Text with nothing to fix is left exactly alone.
            assert.equal(runFilters('Plain "text" here.', filters), 'Plain "text" here.');
        """)

    def test_leaves_apostrophes_alone(self):
        """Only the all-marks preset touches singles; the German one must not."""
        filters = json.dumps(_filters_for(GERMAN))
        run_node_module(SETUP + f"""
            assert.equal(runFilters("don\\u2019t stop", {filters}), 'don\\u2019t stop');
        """)


class TestFrenchPreset:
    def test_straightens_guillemets_and_drops_french_spacing(self):
        filters = json.dumps(_filters_for('French punctuation'))
        run_node_module(SETUP + f"""
            const filters = {filters};
            // Plain guillemets.
            assert.equal(
                runFilters('\\u00abBonjour\\u00bb, dit-elle.', filters),
                '"Bonjour", dit-elle.');
            // Padded with the no-break spaces French actually uses.
            assert.equal(
                runFilters('\\u00ab\\u00a0Bonjour\\u00a0\\u00bb', filters),
                '"Bonjour"');
            assert.equal(
                runFilters('\\u00ab\\u202fBonjour\\u202f\\u00bb', filters),
                '"Bonjour"');
            // The space French puts before ; : ! ? goes away.
            assert.equal(runFilters('Vraiment\\u00a0?', filters), 'Vraiment?');
            assert.equal(runFilters('Attends\\u202f!', filters), 'Attends!');
            assert.equal(runFilters('Alors : voici', filters), 'Alors: voici');
        """)


class TestStraightenAllPreset:
    def test_handles_every_convention_including_apostrophes(self):
        filters = json.dumps(_filters_for('Straighten all quote marks'))
        run_node_module(SETUP + f"""
            const filters = {filters};
            assert.equal(runFilters('\\u201eHallo\\u201c', filters), '"Hallo"');
            assert.equal(runFilters('\\u00bbHallo\\u00ab', filters), '"Hallo"');
            assert.equal(runFilters('\\u00abBonjour\\u00bb', filters), '"Bonjour"');
            assert.equal(runFilters('\\u201cHello\\u201d', filters), '"Hello"');
            // Unlike the language presets, this one straightens apostrophes too.
            assert.equal(runFilters('don\\u2019t', filters), "don't");
            assert.equal(runFilters('\\u2018maybe\\u2019', filters), "'maybe'");
        """)

    def test_mixed_language_text_in_one_pass(self):
        filters = json.dumps(_filters_for('Straighten all quote marks'))
        run_node_module(SETUP + f"""
            assert.equal(
                runFilters('\\u201eA\\u201c and \\u00bbB\\u00ab and \\u00abC\\u00bb', {filters}),
                '"A" and "B" and "C"');
        """)
