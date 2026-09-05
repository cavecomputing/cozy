from helpers import run_node_module


def test_preset_match_prefers_case_insensitive_exact_match():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [{ id: 1, name: 'NanoBear v2.1' }, { id: 2, name: 'nanobear' }];
        // Exact (case-insensitive) beats the prefix matches around it.
        assert.equal(matchPresetByName(presets, 'NANOBEAR').preset.id, 2);
        assert.equal(matchPresetByName(presets, '  nanobear v2.1 ').preset.id, 1);
    """)


def test_preset_match_accepts_a_unique_prefix():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [{ id: 1, name: 'NanoBear v2.1' }, { id: 2, name: 'Sasha' }];
        assert.equal(matchPresetByName(presets, 'nano').preset.id, 1);
        assert.equal(matchPresetByName(presets, 'SASH').preset.id, 2);
    """)


def test_preset_match_reports_unknown_ambiguous_and_missing():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { matchPresetByName } from './static/js/preset-match.js';

        const presets = [{ id: 1, name: 'NanoBear v2.1' }, { id: 2, name: 'NanoBear Lite' }];
        // Unknown names offer the whole list for the toast.
        const unknown = matchPresetByName(presets, 'sasha');
        assert.equal(unknown.preset, undefined);
        assert.equal(unknown.error, 'unknown');
        assert.deepEqual(unknown.candidates, ['NanoBear v2.1', 'NanoBear Lite']);

        // An ambiguous prefix offers just the contenders.
        const ambiguous = matchPresetByName(presets, 'nano');
        assert.equal(ambiguous.preset, undefined);
        assert.equal(ambiguous.error, 'ambiguous');
        assert.deepEqual(ambiguous.candidates, ['NanoBear v2.1', 'NanoBear Lite']);

        // A blank query offers the whole list alongside the current preset.
        const missing = matchPresetByName(presets, '   ');
        assert.equal(missing.error, 'missing');
        assert.deepEqual(missing.candidates, ['NanoBear v2.1', 'NanoBear Lite']);

        // Malformed entries never match and never leak into offers.
        const skew = matchPresetByName([{ id: 1 }, { id: 2, name: null }], 'x');
        assert.equal(skew.error, 'unknown');
        assert.deepEqual(skew.candidates, []);
    """)
