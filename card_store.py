"""Character card file helpers shared by route modules."""

import io
import os
import zlib
from copy import deepcopy

from PIL import Image

import shared
from shared import get_db
from png_utils import extract_png_chara, write_png_chara

CARD_DATA_DEFAULTS = {
    'name': 'Unnamed',
    'description': '',
    'personality': '',
    'scenario': '',
    'first_mes': '',
    'mes_example': '',
    'creator_notes': '',
    'system_prompt': '',
    'post_history_instructions': '',
    'alternate_greetings': [],
    'character_book': None,
    'tags': [],
    'creator': '',
    'character_version': '',
    'extensions': {},
}


def file_crc(path):
    """Compute CRC32 of a file, returned as an 8-character hex string."""
    with open(path, 'rb') as f:
        return format(zlib.crc32(f.read()) & 0xFFFFFFFF, '08x')


def read_character_card(path):
    """Parse embedded card data from a PNG file. Returns dict or None."""
    try:
        with open(path, 'rb') as f:
            return extract_png_chara(f.read())
    except (OSError, IOError):
        return None


def get_character_card(conn, char_id):
    """Return (row, full_card_dict) for *char_id*, or (None, None) if absent/missing."""
    row = conn.execute('SELECT * FROM characters WHERE id=?', (char_id,)).fetchone()
    if not row or row['missing']:
        return None, None
    path = os.path.join(shared.CHARACTERS_DIR, row['filename'])
    return row, read_character_card(path)


def get_character_card_data(conn, char_id):
    """Return the character card's inner `data` dict, or `{}` if absent/missing."""
    _, card = get_character_card(conn, char_id)
    return card.get('data', card) if card else {}


def normalize_to_v2(card_data):
    """Normalize V1/V2/flat card data into a proper V2 card dict."""
    if isinstance(card_data, dict) and card_data.get('spec') == 'chara_card_v2':
        return card_data
    data = card_data.get('data', card_data)
    return {
        'spec': 'chara_card_v2',
        'spec_version': '2.0',
        'data': card_data_fields(data),
    }


def card_data_fields(card_data):
    """Return API-facing character data fields with V2 defaults applied."""
    data = card_data.get('data', card_data) if isinstance(card_data, dict) else {}
    return {
        key: data[key] if key in data else deepcopy(default)
        for key, default in CARD_DATA_DEFAULTS.items()
    }


def ensure_png(image_bytes):
    """Convert any supported image format to PNG bytes."""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return image_bytes
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def write_character_card(path, card):
    """Embed card data in an existing PNG file and return (png_bytes, crc)."""
    with open(path, 'rb') as f:
        png_bytes = f.read()
    png_bytes = write_png_chara(png_bytes, card)
    with open(path, 'wb') as f:
        f.write(png_bytes)
    return png_bytes, file_crc(path)


def set_character_book(char_id, new_book):
    """Set a character PNG's embedded character_book and refresh its DB CRC."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT * FROM characters WHERE id=?', (char_id,)
        ).fetchone()
        if not row or row['missing']:
            return None, 'Character not found'

        path = os.path.join(shared.CHARACTERS_DIR, row['filename'])
        with open(path, 'rb') as f:
            png_bytes = f.read()

        existing_card = extract_png_chara(png_bytes) or {'data': {}}
        existing_data = existing_card.get('data', existing_card)
        existing_data['character_book'] = new_book

        card = {
            'spec': 'chara_card_v2',
            'spec_version': '2.0',
            'data': existing_data,
        }
        png_bytes = write_png_chara(png_bytes, card)
        with open(path, 'wb') as f:
            f.write(png_bytes)

        conn.execute('UPDATE characters SET crc=? WHERE id=?', (file_crc(path), char_id))
        return card, None
