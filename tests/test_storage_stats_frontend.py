import shutil
import subprocess
import textwrap

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend storage-stat tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', textwrap.dedent(code)],
        cwd='.',
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_byte_formatting_and_storage_view_model():
    run_node_module(r'''
        import assert from 'node:assert/strict';
        const { formatBytes, buildStorageViewModel } = await import('./static/js/storage-stats.js');

        assert.equal(formatBytes(0), '0 B');
        assert.equal(formatBytes(1023), '1,023 B');
        assert.equal(formatBytes(1024), '1 KB');
        assert.equal(formatBytes(1536), '1.5 KB');
        assert.equal(formatBytes(1024 ** 3), '1 GB');

        const view = buildStorageViewModel({
            user_data_bytes: 3072,
            categories: {
                database: { bytes: 2048, files: 3 },
                characters: { bytes: 1024, files: 1 },
                personas: { bytes: 0, files: 0 },
                themes: { bytes: 0, files: 0 },
                other: { bytes: 0, files: 0 },
            },
            cache: { bytes: 4096, files: 8 },
        });

        assert.deepEqual(view.tiles.map(item => item.key), [
            'database', 'characters', 'personas', 'themes',
        ]);
        assert.equal(view.totalLabel, '3 KB');
        assert.equal(view.cache.sizeLabel, '4 KB');
        assert.equal(view.cache.fileLabel, '8 files');
        assert.equal(view.tiles[1].fileLabel, '1 file');
    ''')


def test_render_and_failure_state_are_accessible_and_retryable():
    run_node_module(r'''
        import assert from 'node:assert/strict';
        const { API } = await import('./static/js/api.js');
        const { loadStorageStats, renderStorageStats } = await import('./static/js/storage-stats.js');

        const root = {
            attributes: {},
            innerHTML: '',
            setAttribute(name, value) { this.attributes[name] = value; },
        };
        renderStorageStats(root, {
            user_data_bytes: 1024,
            categories: {
                database: { bytes: 1024, files: 1 },
                characters: { bytes: 0, files: 0 },
                personas: { bytes: 0, files: 0 },
                themes: { bytes: 0, files: 0 },
                other: { bytes: 0, files: 0 },
            },
            cache: { bytes: 2048, files: 2 },
        });
        assert.match(root.innerHTML, /Your data/);
        assert.match(root.innerHTML, /role="img" aria-label="Storage breakdown:/);
        assert.match(root.innerHTML, /not included in your data total/);
        assert.doesNotMatch(root.innerHTML, /about-storage-dot--other/);
        assert.equal(root.attributes['aria-busy'], 'false');

        API.getStorageStats = async () => { throw new Error('offline'); };
        await loadStorageStats(root);
        assert.match(root.innerHTML, /data-storage-retry/);
        assert.equal(root.attributes['aria-busy'], 'false');
    ''')
