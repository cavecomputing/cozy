"""PNG Character Card utilities — read/write 'chara' tEXt chunks in PNG files."""

import json
import base64
import binascii
import struct
import zlib


def make_minimal_png() -> bytes:
    """Return a minimal valid 1x1 black RGB PNG (used when no avatar exists)."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    sig  = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    # 1-row scanline: filter byte (0 = None) + R G B
    idat = _chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00'))
    iend = _chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


def write_png_chara(png_bytes: bytes, card: dict) -> bytes:
    """
    Inject (or replace) a 'chara' tEXt chunk into a PNG byte string.
    The card dict is serialised to JSON, base64-encoded, and stored as the
    chunk value — exactly the format readers like SillyTavern expect.
    """
    if png_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a valid PNG')

    json_str  = json.dumps(card, ensure_ascii=False)
    b64_val   = base64.b64encode(json_str.encode('utf-8')).decode('ascii')
    raw_data  = b'chara\x00' + b64_val.encode('latin-1')
    crc_val   = struct.pack('>I', zlib.crc32(b'tEXt' + raw_data) & 0xFFFFFFFF)
    chara_chunk = struct.pack('>I', len(raw_data)) + b'tEXt' + raw_data + crc_val

    out = bytearray(png_bytes[:8])   # PNG signature
    pos = 8
    while pos < len(png_bytes) - 8:
        try:
            clen  = struct.unpack('>I', png_bytes[pos:pos + 4])[0]
            ctype = png_bytes[pos + 4:pos + 8]
        except struct.error:
            break

        # Skip any existing 'chara' tEXt chunk so we don't duplicate
        if ctype == b'tEXt':
            chunk_data = png_bytes[pos + 8:pos + 8 + clen]
            try:
                nul = chunk_data.index(b'\x00')
                if chunk_data[:nul].decode('latin-1').lower() == 'chara':
                    pos += 12 + clen
                    continue
            except (ValueError, UnicodeDecodeError):
                pass

        # Insert our chunk immediately before IEND
        if ctype == b'IEND':
            out += chara_chunk

        out += png_bytes[pos:pos + 12 + clen]
        pos += 12 + clen
        if ctype == b'IEND':
            break

    return bytes(out)


def extract_png_chara(file_bytes: bytes):
    """
    Pull Character Card V2 JSON out of a PNG's tEXt or iTXt 'chara' chunk.
    Returns a parsed dict, or None if nothing found.
    """
    if file_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        return None

    pos = 8
    while pos < len(file_bytes) - 12:
        try:
            chunk_len  = struct.unpack('>I', file_bytes[pos:pos + 4])[0]
            chunk_type = file_bytes[pos + 4:pos + 8].decode('ascii', errors='replace')
            chunk_data = file_bytes[pos + 8:pos + 8 + chunk_len]
        except (struct.error, UnicodeDecodeError):
            break

        if chunk_type == 'tEXt':
            try:
                nul  = chunk_data.index(b'\x00')
                key  = chunk_data[:nul].decode('latin-1')
                val  = chunk_data[nul + 1:].decode('latin-1')
                if key.lower() == 'chara':          # case-insensitive: older TavernAI used "Chara"
                    padded = val.rstrip('=')
                    padded += '=' * (-len(padded) % 4)
                    return json.loads(base64.b64decode(padded))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
                pass

        elif chunk_type == 'iTXt':
            try:
                nul  = chunk_data.index(b'\x00')
                key  = chunk_data[:nul].decode('utf-8')
                rest = chunk_data[nul + 1:]
                comp_flag   = rest[0]
                rest        = rest[2:]                          # skip flag + method
                nul2        = rest.index(b'\x00')
                rest        = rest[nul2 + 1:]                  # skip language tag
                nul3        = rest.index(b'\x00')
                val_bytes   = rest[nul3 + 1:]
                if comp_flag:
                    val_bytes = zlib.decompress(val_bytes)
                val = val_bytes.decode('utf-8')
                if key.lower() == 'chara':          # case-insensitive match
                    padded = val.rstrip('=')
                    padded += '=' * (-len(padded) % 4)
                    return json.loads(base64.b64decode(padded))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error, zlib.error):
                pass

        pos += 12 + chunk_len
        if chunk_type == 'IEND':
            break

    return None
