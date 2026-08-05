"""Derived avatar thumbnails — lazy, content-addressed, cached on disk.

Character cards are full-size PNGs (often over a megabyte) but are displayed as
avatars 30–50 px wide, so serving the card itself costs roughly 200x more bytes
than the pixels actually shown. This module serves downscaled WebP copies from
shared.THUMBS_DIR, generating them on first request.

Cache entries are keyed by image *content* (png_pixel_key), not by the card's
CRC: rewriting a card's embedded text changes its CRC while leaving the artwork
identical, and keying on that would discard a card's thumbnails every time its
description was edited. Content keying also means cards with identical artwork
share one entry, and renaming a file on disk needs no regeneration.

The directory is a pure cache — deleting it is always safe.
"""

import hashlib
import logging
import os
import tempfile
import threading

from flask import abort, send_from_directory
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import safe_join

import shared
from png_utils import png_pixel_key

Image.MAX_IMAGE_PIXELS = shared.MAX_IMAGE_PIXELS

log = logging.getLogger('cozy')

# Tiers, chosen against the sizes these images actually render at:
#   SM  every circular/rounded avatar (sidebar 30px, message 46px, modal 52px)
#   LG  the expanded message avatar
SM, LG = 128, 1024
ALLOWED_SIZES = (SM, LG)
# SM renders into square containers with `background-size: cover`, which
# is itself a centre crop — cropping server-side shows the same pixels for far
# fewer bytes. LG must keep the true aspect ratio: the hero lets the user drag
# background-position through a tall portrait, and the expand handler derives
# its box from the image's natural dimensions.
CROP_SIZES = (SM,)

WEBP_QUALITY = 80
WEBP_METHOD = 4
IMMUTABLE = 'public, max-age=31536000, immutable'

# (path, mtime_ns, size) -> content key. Saves re-reading the source on every
# request; a changed file misses the memo because its stat differs.
_key_memo = {}
# Content keys that Pillow could not decode, so a corrupt file is not decoded
# again on every request. Keyed by content, so replacing the file retries.
_failed = set()
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(name):
    """Return the generation lock for a cache filename, creating it once."""
    with _locks_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = _locks[name] = threading.Lock()
        return lock


def _key_for(src_path):
    """Content key for *src_path*, or None if it cannot be stat'd."""
    try:
        st = os.stat(src_path)
    except OSError:
        return None
    memo_key = (src_path, st.st_mtime_ns, st.st_size)
    key = _key_memo.get(memo_key)
    if key is not None:
        return key

    key = None
    try:
        with open(src_path, 'rb') as f:
            key = png_pixel_key(f.read())
    except OSError:
        return None
    if key is None:
        # Not a PNG (persona uploads may be jpg/gif/webp) or not walkable.
        # Persona avatars are rewritten in place at the same filename, so the
        # stat is a faithful change signal.
        key = hashlib.blake2b(
            f'{src_path}|{st.st_mtime_ns}|{st.st_size}'.encode('utf-8', 'replace'),
            digest_size=8,
        ).hexdigest()

    _key_memo[memo_key] = key
    return key


def _normalize_mode(im):
    """Coerce any Pillow mode to RGB, or RGBA where transparency is present."""
    if im.mode in ('RGB', 'RGBA'):
        return im
    if im.mode in ('P', 'PA'):
        return im.convert('RGBA' if 'transparency' in im.info else 'RGB')
    if im.mode in ('LA', 'La'):
        return im.convert('RGBA')
    return im.convert('RGB')


def _generate(src_path, size, dest_path):
    """Write a *size* thumbnail of *src_path* to *dest_path*, atomically."""
    with Image.open(src_path) as im:
        im.load()                            # raises on truncated/corrupt data
        im = ImageOps.exif_transpose(im)     # no-op for PNG; matters for phone JPEGs
        im = _normalize_mode(im)
        if size in CROP_SIZES:
            # Clamping to the smaller edge keeps this a downscale only. Without
            # it ImageOps.fit would happily blow a 1x1 card up to 128x128.
            edge = min(size, im.width, im.height)
            out = ImageOps.fit(im, (edge, edge), method=Image.LANCZOS, centering=(0.5, 0.5))
        else:
            out = im.copy()
            out.thumbnail((size, size), Image.LANCZOS)   # never upscales

        fd, tmp_path = tempfile.mkstemp(dir=shared.THUMBS_DIR, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as f:
                out.save(f, format='WEBP', quality=WEBP_QUALITY, method=WEBP_METHOD)
            # Same-directory replace is atomic on POSIX and Windows alike, so a
            # concurrent reader never sees a half-written file.
            os.replace(tmp_path, dest_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def ensure_thumb(src_path, size):
    """Return the cache filename for *src_path* at *size*, or None on failure."""
    key = _key_for(src_path)
    if key is None:
        return None
    if (key, size) in _failed:
        return None

    name = f'{key}_{size}.webp'
    dest_path = os.path.join(shared.THUMBS_DIR, name)
    if os.path.exists(dest_path):
        return name

    with _lock_for(name):
        if os.path.exists(dest_path):        # another thread just built it
            return name
        try:
            os.makedirs(shared.THUMBS_DIR, exist_ok=True)
            _generate(src_path, size, dest_path)
        except (UnidentifiedImageError, Image.DecompressionBombError,
                OSError, ValueError, MemoryError) as e:
            # Log once per content key; callers fall back to the original file.
            log.warning('Thumbnail generation failed for %s at %d: %s', src_path, size, e)
            _failed.add((key, size))
            return None
    return name


def serve(root_dir, size, filename):
    """Serve a thumbnail of *filename* from *root_dir*, falling back to the original."""
    if size not in ALLOWED_SIZES:
        # Unbounded sizes would let a request loop fill the disk with entries.
        abort(404)
    # <path:filename> accepts slashes, so resolve before touching the filesystem.
    src_path = safe_join(root_dir, filename)
    if not src_path or not os.path.isfile(src_path):
        abort(404)

    name = ensure_thumb(src_path, size)
    if name is None:
        # Undecodable source. Serving the original keeps this no worse than not
        # having thumbnails at all, rather than turning a bad card into a
        # broken image. no-store so a later fix is picked up.
        resp = send_from_directory(root_dir, filename)
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    # Declare the type rather than letting Werkzeug guess from the extension:
    # the stdlib mimetypes module reads the Windows registry, where .webp is
    # often absent, and guessing wrong yields application/octet-stream.
    resp = send_from_directory(
        shared.THUMBS_DIR, name, max_age=31536000, mimetype='image/webp',
    )
    # Flask has no parameter for `immutable`; the URL is content-addressed, so
    # a cached copy can never be wrong for this key.
    resp.headers['Cache-Control'] = IMMUTABLE
    return resp
