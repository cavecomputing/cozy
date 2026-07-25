"""Tests for derived avatar thumbnails — content keying, generation, serving."""

from io import BytesIO

from PIL import Image

from helpers import v2_card
from png_utils import make_minimal_png, png_pixel_key, write_png_chara


# ── Helpers ────────────────────────────────────────────────────────────────

def _png(width, height, colour=(200, 60, 60)):
    """Return PNG bytes for a solid-colour image of the given size."""
    buf = BytesIO()
    Image.new('RGB', (width, height), colour).save(buf, format='PNG')
    return buf.getvalue()


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
