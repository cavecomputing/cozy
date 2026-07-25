"""applyAvatar() rewrites avatar URLs to request server-side thumbnails."""

import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend avatar tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


# applyAvatar only touches style/dataset/textContent, so a plain object stands
# in for the element and these run without a DOM.
SETUP = r"""
    import assert from 'node:assert/strict';
    import { applyAvatar, AVATAR } from './static/js/utils.js';

    const makeEl = () => ({ style: {}, dataset: {}, textContent: '' });
    const bg = el => {
        const m = /url\('(.*)'\)/.exec(el.style.backgroundImage || '');
        return m ? m[1] : null;
    };
"""


def test_character_url_is_rewritten_to_requested_tier():
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/Sasha.png?v=abc123' }, '?', AVATAR.SM);
        assert.equal(bg(el), '/thumbs/characters/128/Sasha.png?v=abc123');
    """)


def test_each_tier_maps_to_its_own_path():
    run_node_module(SETUP + r"""
        for (const [size, expected] of [
            [AVATAR.SM, '/thumbs/characters/128/a.png?v=1'],
            [AVATAR.MD, '/thumbs/characters/512/a.png?v=1'],
            [AVATAR.LG, '/thumbs/characters/1024/a.png?v=1'],
        ]) {
            const el = makeEl();
            applyAvatar(el, { avatar_url: '/characters/a.png?v=1' }, '?', size);
            assert.equal(bg(el), expected);
        }
    """)


def test_persona_urls_are_rewritten_too():
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/personas/3.jpg?v=2024' }, '?', AVATAR.SM);
        assert.equal(bg(el), '/thumbs/personas/128/3.jpg?v=2024');
    """)


def test_cache_buster_is_preserved():
    """?v= must survive: it is what busts the browser cache when a card changes."""
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/a.png?v=deadbeef' }, '?', AVATAR.SM);
        assert.ok(bg(el).endsWith('?v=deadbeef'));
    """)


def test_url_without_query_is_handled():
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/a.png' }, '?', AVATAR.SM);
        assert.equal(bg(el), '/thumbs/characters/128/a.png');
    """)


def test_omitting_size_serves_the_original():
    """The default must degrade to today's behaviour, not to a blurry image."""
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/a.png?v=1' });
        assert.equal(bg(el), '/characters/a.png?v=1');
    """)


def test_preview_urls_pass_through_untouched():
    """Upload previews use blob:/data: URLs that have no server-side thumbnail."""
    run_node_module(SETUP + r"""
        for (const url of [
            'blob:http://localhost:5001/9f2c-4e01',
            'data:image/png;base64,iVBORw0KGgo=',
            'https://example.com/remote.png',
        ]) {
            const el = makeEl();
            applyAvatar(el, { avatar_url: url }, '?', AVATAR.SM);
            assert.equal(bg(el), url, `rewrote ${url}`);
        }
    """)


def test_large_source_is_stashed_for_expand():
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/a.png?v=1' }, '?', AVATAR.SM);
        assert.equal(el.dataset.thumbSrc, '/thumbs/characters/128/a.png?v=1');
        assert.equal(el.dataset.largeSrc, '/thumbs/characters/1024/a.png?v=1');
    """)


def test_falls_back_to_initials_and_clears_sources():
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/a.png?v=1' }, '?', AVATAR.SM);
        applyAvatar(el, { name: 'Sasha Vane' }, '?', AVATAR.SM);
        assert.equal(el.textContent, 'SA');
        assert.equal(el.dataset.hasImage, 'false');
        assert.equal(el.dataset.thumbSrc, undefined);
        assert.equal(el.dataset.largeSrc, undefined);
    """)


def test_filenames_with_parens_and_spaces_survive():
    """The old expand handler regex broke on parens; nothing may reintroduce that."""
    run_node_module(SETUP + r"""
        const el = makeEl();
        applyAvatar(el, { avatar_url: '/characters/Kim (v2) card.png?v=9' }, '?', AVATAR.SM);
        assert.equal(bg(el), '/thumbs/characters/128/Kim (v2) card.png?v=9');
        assert.equal(el.dataset.largeSrc, '/thumbs/characters/1024/Kim (v2) card.png?v=9');
    """)
