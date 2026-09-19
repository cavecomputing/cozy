from helpers import run_node_module


def test_preset_match_prefers_case_insensitive_exact_match():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [{ id: 1, name: 'NanoBear Classic' }, { id: 2, name: 'nanobear' }];
        // Exact (case-insensitive) beats the prefix matches around it.
        assert.equal(matchPresetByName(presets, 'NANOBEAR').preset.id, 2);
        assert.equal(matchPresetByName(presets, '  nanobear classic ').preset.id, 1);
    """)


def test_preset_match_accepts_a_unique_prefix():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [{ id: 1, name: 'NanoBear Classic' }, { id: 2, name: 'Sasha' }];
        assert.equal(matchPresetByName(presets, 'nano').preset.id, 1);
        assert.equal(matchPresetByName(presets, 'SASH').preset.id, 2);
    """)


def test_preset_match_reports_unknown_ambiguous_and_missing():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [{ id: 1, name: 'NanoBear Classic' }, { id: 2, name: 'NanoBear Lite' }];
        // Unknown names offer the whole list for the toast.
        const unknown = matchPresetByName(presets, 'sasha');
        assert.equal(unknown.preset, undefined);
        assert.equal(unknown.error, 'unknown');
        assert.deepEqual(unknown.candidates, ['NanoBear Classic', 'NanoBear Lite']);

        // An ambiguous prefix offers just the contenders.
        const ambiguous = matchPresetByName(presets, 'nano');
        assert.equal(ambiguous.preset, undefined);
        assert.equal(ambiguous.error, 'ambiguous');
        assert.deepEqual(ambiguous.candidates, ['NanoBear Classic', 'NanoBear Lite']);

        // A blank query offers the whole list alongside the current preset.
        const missing = matchPresetByName(presets, '   ');
        assert.equal(missing.error, 'missing');
        assert.deepEqual(missing.candidates, ['NanoBear Classic', 'NanoBear Lite']);

        // Malformed entries never match and never leak into offers.
        const skew = matchPresetByName([{ id: 1 }, { id: 2, name: null }], 'x');
        assert.equal(skew.error, 'unknown');
        assert.deepEqual(skew.candidates, []);
    """)


def test_preset_match_picks_the_newest_edition_by_default():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        // Deliberately not in the order /api/system-prompts returns: the pick
        // must come from the version, not from the position in the list.
        const presets = [
            { id: 1, name: 'NanoBear', version: '2.1' },
            { id: 2, name: 'NanoBear', version: '2.10' },
            { id: 3, name: 'NanoBear', version: '2.2' },
        ];
        // No version given: the newest wins, and "2.10" beats "2.2" numerically.
        assert.equal(matchPresetByName(presets, 'NanoBear').preset.id, 2);
        assert.equal(matchPresetByName(presets, 'NanoBear').label, 'NanoBear 2.10');
        // Several editions of one name are not an ambiguous prefix.
        assert.equal(matchPresetByName(presets, 'nano').preset.id, 2);
    """)


def test_preset_match_accepts_an_explicit_version():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [
            { id: 1, name: 'NanoBear', version: '2.1' },
            { id: 2, name: 'NanoBear', version: '2.2' },
            { id: 3, name: 'NanoBear Author', version: '2.1' },
        ];
        assert.equal(matchPresetByName(presets, 'NanoBear 2.1').preset.id, 1);
        assert.equal(matchPresetByName(presets, 'nanobear author 2.1').preset.id, 3);

        // A version that is not installed offers that name's editions, not
        // the whole library.
        const missed = matchPresetByName(presets, 'NanoBear 9.9');
        assert.equal(missed.preset, undefined);
        assert.equal(missed.error, 'unknown');
        assert.deepEqual(missed.candidates, ['NanoBear 2.1', 'NanoBear 2.2']);
    """)


def test_preset_match_falls_back_to_a_name_ending_in_a_number():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        // Someone's own preset literally called "Grimdark 2.0": reading the
        // trailing number as a version finds nothing, so the whole string is
        // retried as a name.
        const presets = [{ id: 1, name: 'Grimdark 2.0' }, { id: 2, name: 'Cozy' }];
        assert.equal(matchPresetByName(presets, 'Grimdark 2.0').preset.id, 1);
    """)


def test_preset_labels_qualify_only_repeated_names():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { labelPresets } from './static/js/preset-match.js';

        assert.deepEqual(labelPresets([
            { id: 1, name: 'NanoBear', version: '2.1' },
            { id: 2, name: 'NanoBear', version: '2.2' },
            { id: 3, name: 'NanoBear Author', version: '2.1' },
            { id: 4, name: 'Mine' },
        ]), ['NanoBear 2.1', 'NanoBear 2.2', 'NanoBear Author', 'Mine']);
    """)


def test_api_presets_without_versions_keep_their_listed_order():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        // API presets carry no version at all, so a tie must resolve to the
        // first entry exactly as it did before versions existed.
        const presets = [{ id: 1, name: 'Local' }, { id: 2, name: 'Local' }];
        assert.equal(matchPresetByName(presets, 'Local').preset.id, 1);
        assert.deepEqual(matchPresetByName(presets, 'Local').label, 'Local');
    """)


def test_newest_preset_is_exported_for_the_default_prompt_pick():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { newestPreset } from './static/js/preset-match.js';

        // system-prompts.js picks the fallback default with this, mirroring
        // what seed_default_prompts() activates on a fresh install.
        assert.equal(newestPreset([
            { id: 1, name: 'NanoBear', version: '2.2' },
            { id: 2, name: 'NanoBear', version: '2.10' },
        ]).id, 2);
        // No versions anywhere: the first entry stands.
        assert.equal(newestPreset([{ id: 5, name: 'A' }, { id: 6, name: 'B' }]).id, 5);
    """)
