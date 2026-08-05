"""Tests for derived avatar thumbnails — content keying, generation, serving."""

import json
import os
import threading
from io import BytesIO

import pytest
from PIL import Image

import shared
import thumbs
from helpers import v2_card
from png_utils import make_minimal_png, png_pixel_key, write_png_chara


# ── Helpers ────────────────────────────────────────────────────────────────

def _png(width, height, colour=(200, 60, 60)):
    """Return PNG bytes for a solid-colour image of the given size."""
    buf = BytesIO()
    Image.new('RGB', (width, height), colour).save(buf, format='PNG')
    return buf.getvalue()


def _thumb_files():
    return sorted(f for f in os.listdir(shared.THUMBS_DIR) if f.endswith('.webp'))


def _make_char(client, png, name='Thumbed'):
    """Create a character from raw PNG bytes and return its API dict."""
    r = client.post('/api/characters', data={
        'data': json.dumps({'name': name}),
        'image': (BytesIO(png), 'c.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert r.status_code == 201
    return r.get_json()


def _size_of(response):
    return Image.open(BytesIO(response.data)).size


@pytest.fixture(autouse=True)
def _clear_thumb_caches():
    """Reset thumbs.py's in-process memos between tests (dirs are per-test)."""
    thumbs._key_memo.clear()
    thumbs._failed.clear()
    thumbs._locks.clear()
    yield


# ── Pixel keying ───────────────────────────────────────────────────────────

class TestPngPixelKey:
    def test_returns_16_hex_chars(self):
        key = png_pixel_key(make_minimal_png())
        assert key is not None
        assert len(key) == 16
        int(key, 16)  # raises if not hex

    def test_stable_across_repeated_calls(self):
        png = _png(40, 20)
        assert png_pixel_key(png) == png_pixel_key(png)

    def test_unchanged_by_embedding_card_data(self):
        """The whole point: a tEXt rewrite must not change the key."""
        png = _png(40, 20)
        bare = png_pixel_key(png)
        assert png_pixel_key(write_png_chara(png, v2_card('A'))) == bare

    def test_unchanged_by_editing_card_data(self):
        """Editing a card's text changes its CRC but must not change the key."""
        png = _png(40, 20)
        one = write_png_chara(png, v2_card('A', description='first'))
        two = write_png_chara(png, v2_card('A', description='second, much longer'))
        assert one != two                      # the files really do differ
        assert png_pixel_key(one) == png_pixel_key(two)

    def test_differs_for_different_images(self):
        assert png_pixel_key(_png(40, 20)) != png_pixel_key(_png(20, 40))

    def test_differs_for_same_size_different_pixels(self):
        assert png_pixel_key(_png(8, 8, (0, 0, 0))) != png_pixel_key(_png(8, 8, (255, 255, 255)))

    def test_none_for_non_png(self):
        assert png_pixel_key(b'not a png at all') is None
        assert png_pixel_key(b'') is None

    def test_none_for_truncated_png(self):
        png = _png(40, 20)
        assert png_pixel_key(png[:len(png) // 2]) is None

    def test_none_for_header_only(self):
        assert png_pixel_key(b'\x89PNG\r\n\x1a\n') is None


# ── Serving ────────────────────────────────────────────────────────────────

class TestServing:
    @pytest.mark.parametrize('size', [128, 1024])
    def test_serves_webp_at_each_tier(self, client, size):
        char = _make_char(client, _png(600, 900))
        r = client.get(f'/thumbs/characters/{size}/{char["filename"]}')
        assert r.status_code == 200
        assert r.content_type == 'image/webp'
        assert r.data[:4] == b'RIFF' and r.data[8:12] == b'WEBP'

    def test_thumbnail_is_immutable_and_cacheable(self, client):
        char = _make_char(client, _png(600, 900))
        r = client.get(f'/thumbs/characters/128/{char["filename"]}')
        cache_control = r.headers['Cache-Control']
        assert 'immutable' in cache_control
        assert 'max-age=31536000' in cache_control

    def test_revalidation_returns_304(self, client):
        char = _make_char(client, _png(600, 900))
        first = client.get(f'/thumbs/characters/128/{char["filename"]}')
        r = client.get(
            f'/thumbs/characters/128/{char["filename"]}',
            headers={'If-None-Match': first.headers['ETag']},
        )
        assert r.status_code == 304

    def test_thumbnail_is_far_smaller_than_source(self, client):
        """The entire point of the feature."""
        png = _png(900, 1300)
        char = _make_char(client, png)
        r = client.get(f'/thumbs/characters/128/{char["filename"]}')
        source = os.path.getsize(os.path.join(shared.CHARACTERS_DIR, char['filename']))
        assert len(r.data) < source / 10

    def test_persona_avatar_thumbnail(self, client, sample_persona):
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(_png(400, 400)), 'p.png', 'image/png')},
            content_type='multipart/form-data',
        )
        name = os.listdir(shared.PERSONAS_DIR)[0]
        r = client.get(f'/thumbs/personas/128/{name}')
        assert r.status_code == 200
        assert r.data[:4] == b'RIFF'
        assert _size_of(r) == (128, 128)

    def test_persona_jpeg_uses_stat_fallback_key(self, client, sample_persona):
        """png_pixel_key returns None for JPEG; the stat key must carry it."""
        buf = BytesIO()
        Image.new('RGB', (300, 300), (10, 120, 200)).save(buf, format='JPEG')
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(buf.getvalue()), 'p.jpg', 'image/jpeg')},
            content_type='multipart/form-data',
        )
        name = os.listdir(shared.PERSONAS_DIR)[0]
        r = client.get(f'/thumbs/personas/128/{name}')
        assert r.status_code == 200
        assert _size_of(r) == (128, 128)


# ── Sizing: crop vs aspect ─────────────────────────────────────────────────

class TestSizing:
    def test_square_crop_for_small_tiers(self, client):
        char = _make_char(client, _png(600, 900))
        assert _size_of(client.get(f'/thumbs/characters/128/{char["filename"]}')) == (128, 128)

    def test_large_tier_preserves_aspect(self, client):
        char = _make_char(client, _png(600, 900))
        w, h = _size_of(client.get(f'/thumbs/characters/1024/{char["filename"]}'))
        assert (w, h) == (600, 900)          # already under 1024: no upscale
        assert abs((w / h) - (600 / 900)) < 0.01

    def test_large_tier_downscales_and_keeps_aspect(self, client):
        char = _make_char(client, _png(1600, 2400))
        w, h = _size_of(client.get(f'/thumbs/characters/1024/{char["filename"]}'))
        assert max(w, h) == 1024
        assert abs((w / h) - (1600 / 2400)) < 0.01

    def test_crop_never_upscales_wide_source(self, client):
        char = _make_char(client, _png(40, 20))
        assert _size_of(client.get(f'/thumbs/characters/128/{char["filename"]}')) == (20, 20)

    @pytest.mark.parametrize('size', [128, 1024])
    def test_one_by_one_source_is_not_upscaled(self, client, size):
        """The whole existing suite builds characters from a 1x1 PNG."""
        char = _make_char(client, make_minimal_png())
        r = client.get(f'/thumbs/characters/{size}/{char["filename"]}')
        assert r.status_code == 200
        assert _size_of(r) == (1, 1)

    def test_transparency_is_preserved(self, client):
        buf = BytesIO()
        Image.new('RGBA', (200, 200), (255, 0, 0, 0)).save(buf, format='PNG')
        char = _make_char(client, buf.getvalue())
        r = client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert Image.open(BytesIO(r.data)).mode in ('RGBA', 'LA', 'P')

    def test_palette_source_is_handled(self, client):
        buf = BytesIO()
        Image.new('RGB', (200, 200), (30, 200, 90)).convert('P').save(buf, format='PNG')
        char = _make_char(client, buf.getvalue())
        assert client.get(f'/thumbs/characters/128/{char["filename"]}').status_code == 200

    def test_grayscale_source_is_handled(self, client):
        buf = BytesIO()
        Image.new('L', (200, 200), 128).save(buf, format='PNG')
        char = _make_char(client, buf.getvalue())
        assert client.get(f'/thumbs/characters/128/{char["filename"]}').status_code == 200


# ── Cache keying ───────────────────────────────────────────────────────────

class TestCacheKeying:
    def test_text_edit_does_not_regenerate(self, client):
        """A description edit changes the card's CRC but not its artwork."""
        char = _make_char(client, _png(600, 900))
        client.get(f'/thumbs/characters/128/{char["filename"]}')
        before = _thumb_files()
        assert len(before) == 1

        r = client.put(f'/api/characters/{char["id"]}', json={'description': 'Rewritten.'})
        assert r.status_code == 200
        updated = r.get_json()
        assert updated['avatar_url'] != char['avatar_url']    # CRC really moved

        client.get(f'/thumbs/characters/128/{updated["filename"]}')
        assert _thumb_files() == before

    def test_lorebook_embed_does_not_regenerate(self, client):
        char = _make_char(client, _png(600, 900))
        client.get(f'/thumbs/characters/128/{char["filename"]}')
        before = _thumb_files()

        book = client.post('/api/lorebooks', json={'name': 'B', 'entries': []}).get_json()
        r = client.post(f'/api/lorebooks/{book["id"]}/embed-in-character/{char["id"]}')
        assert r.status_code == 200

        client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert _thumb_files() == before

    def test_avatar_replacement_does_regenerate(self, client):
        char = _make_char(client, _png(600, 900, (10, 10, 200)))
        client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert len(_thumb_files()) == 1

        client.post(
            f'/api/characters/{char["id"]}/avatar',
            data={'avatar': (BytesIO(_png(600, 900, (200, 10, 10))), 'n.png', 'image/png')},
            content_type='multipart/form-data',
        )
        client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert len(_thumb_files()) == 2

    def test_identical_artwork_shares_one_thumbnail(self, client):
        artwork = _png(600, 900)
        first = _make_char(client, artwork)
        second = _make_char(client, artwork)
        client.get(f'/thumbs/characters/128/{first["filename"]}')
        client.get(f'/thumbs/characters/128/{second["filename"]}')
        assert len(_thumb_files()) == 1

    def test_rename_on_disk_reuses_thumbnail(self, client):
        char = _make_char(client, _png(600, 900))
        client.get(f'/thumbs/characters/128/{char["filename"]}')
        before = _thumb_files()

        os.rename(
            os.path.join(shared.CHARACTERS_DIR, char['filename']),
            os.path.join(shared.CHARACTERS_DIR, 'renamed.png'),
        )
        client.get('/api/characters')        # runs _sync_characters
        r = client.get('/thumbs/characters/128/renamed.png')
        assert r.status_code == 200
        assert _thumb_files() == before

    def test_each_tier_is_cached_separately(self, client):
        char = _make_char(client, _png(600, 900))
        for size in (128, 1024):
            client.get(f'/thumbs/characters/{size}/{char["filename"]}')
        assert len(_thumb_files()) == 2

    def test_second_request_is_a_cache_hit(self, client):
        char = _make_char(client, _png(600, 900))
        first = client.get(f'/thumbs/characters/128/{char["filename"]}')
        mtime = os.path.getmtime(os.path.join(shared.THUMBS_DIR, _thumb_files()[0]))
        second = client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert second.data == first.data
        assert os.path.getmtime(os.path.join(shared.THUMBS_DIR, _thumb_files()[0])) == mtime


# ── Safety and failure modes ───────────────────────────────────────────────

class TestSafety:
    @pytest.mark.parametrize('size', [1, 96, 512, 999, 16000])
    def test_disallowed_size_is_404(self, client, size):
        char = _make_char(client, _png(600, 900))
        assert client.get(f'/thumbs/characters/{size}/{char["filename"]}').status_code == 404

    def test_no_thumbnail_written_for_disallowed_size(self, client):
        char = _make_char(client, _png(600, 900))
        client.get(f'/thumbs/characters/16000/{char["filename"]}')
        assert _thumb_files() == []

    @pytest.mark.parametrize('path', [
        '../cozy_chat.db',
        '..%2fcozy_chat.db',
        '%2e%2e%2fcozy_chat.db',
        'sub/../../cozy_chat.db',
    ])
    def test_traversal_is_rejected(self, client, path):
        assert client.get(f'/thumbs/characters/128/{path}').status_code == 404

    def test_missing_source_is_404(self, client):
        assert client.get('/thumbs/characters/128/nope.png').status_code == 404

    def test_corrupt_source_falls_back_to_original(self, client):
        """Never worse than having no thumbnails: serve the original bytes."""
        corrupt = b'\x89PNG\r\n\x1a\n' + b'garbage' * 50
        path = os.path.join(shared.CHARACTERS_DIR, 'corrupt.png')
        with open(path, 'wb') as f:
            f.write(corrupt)

        r = client.get('/thumbs/characters/128/corrupt.png')
        assert r.status_code == 200          # not a 500
        assert r.data == corrupt
        assert r.headers['Cache-Control'] == 'no-store'
        assert _thumb_files() == []

    def test_corrupt_source_is_not_decoded_twice(self, client):
        path = os.path.join(shared.CHARACTERS_DIR, 'corrupt.png')
        with open(path, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'garbage' * 50)
        client.get('/thumbs/characters/128/corrupt.png')
        assert len(thumbs._failed) == 1
        client.get('/thumbs/characters/128/corrupt.png')
        assert len(thumbs._failed) == 1

    def test_decompression_bomb_falls_back(self, client, monkeypatch):
        char = _make_char(client, _png(600, 900))
        monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 16)
        r = client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert r.status_code == 200
        assert r.headers['Cache-Control'] == 'no-store'

    def test_no_temp_files_left_behind(self, client):
        char = _make_char(client, _png(600, 900))
        client.get(f'/thumbs/characters/128/{char["filename"]}')
        assert [f for f in os.listdir(shared.THUMBS_DIR) if f.endswith('.tmp')] == []

    def test_concurrent_requests_generate_one_file(self, client, sample_character):
        char = _make_char(client, _png(600, 900))
        url = f'/thumbs/characters/128/{char["filename"]}'
        results = []

        def fetch():
            import app as app_module
            with app_module.app.test_client() as c:
                r = c.get(url)
                results.append((r.status_code, r.data))

        threads = [threading.Thread(target=fetch) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 8
        assert all(status == 200 for status, _ in results)
        assert len({data for _, data in results}) == 1
        assert len(_thumb_files()) == 1
        assert [f for f in os.listdir(shared.THUMBS_DIR) if f.endswith('.tmp')] == []
