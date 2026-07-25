"""Tests for character routes — V1/V2 import, export, avatar upload, sync."""

import json
import os
from io import BytesIO

import card_store
import shared
import routes.characters as characters_module
from helpers import v2_card
from png_utils import extract_png_chara, write_png_chara, make_minimal_png


# ── Helpers ────────────────────────────────────────────────────────────────

def _png_with_card(card):
    """Return PNG bytes with the given V2 card embedded."""
    return write_png_chara(make_minimal_png(), card)


# ── Create endpoint validation ────────────────────────────────────────────

class TestCreate:
    def test_create_invalid_form_json_rejected(self, client):
        r = client.post('/api/characters', data={
            'data': '{not json',
            'image': (BytesIO(make_minimal_png()), 'avatar.png', 'image/png'),
        }, content_type='multipart/form-data')

        assert r.status_code == 400
        assert r.get_json() == {'error': 'Invalid character data'}

    def test_create_non_utf8_form_json_rejected(self, client):
        boundary = b'CozyBoundary'
        png = make_minimal_png()
        body = b''.join([
            b'--', boundary, b'\r\n',
            b'Content-Disposition: form-data; name="data"\r\n\r\n',
            b'\xff\xfe\r\n',
            b'--', boundary, b'\r\n',
            b'Content-Disposition: form-data; name="image"; filename="avatar.png"\r\n',
            b'Content-Type: image/png\r\n\r\n',
            png, b'\r\n',
            b'--', boundary, b'--\r\n',
        ])

        r = client.post(
            '/api/characters',
            data=body,
            content_type='multipart/form-data; boundary=CozyBoundary',
        )

        assert r.status_code == 400
        assert r.get_json() == {'error': 'Invalid character data'}


# ── PNG round-trip with realistic content ─────────────────────────────────

class TestPngRoundtrip:
    def test_basic_roundtrip(self):
        card = v2_card(name='Basic', description='A simple character.')
        png = _png_with_card(card)
        extracted = extract_png_chara(png)
        assert extracted['data']['name'] == 'Basic'
        assert extracted['data']['description'] == 'A simple character.'

    def test_unicode_roundtrip(self):
        """Emoji + CJK + accented latin must survive PNG embed/extract."""
        card = v2_card(
            name='ユウキ',
            description='Brave 🗡️ adventurer with a café past — naïve but résilient.',
            personality='热情 and 好奇',
        )
        card['data']['character_book'] = {
            'name': '世界',
            'entries': [
                {'keys': ['東京', 'tokyo'], 'content': '東京は大都市です。🏙️',
                 'enabled': True, 'constant': False, 'insertion_order': 100},
            ],
        }
        png = _png_with_card(card)
        extracted = extract_png_chara(png)
        assert extracted['data']['name'] == 'ユウキ'
        assert '🗡️' in extracted['data']['description']
        assert extracted['data']['character_book']['entries'][0]['content'] == '東京は大都市です。🏙️'

    def test_large_lorebook_roundtrip(self):
        """A book with many large entries should round-trip without truncation."""
        long_text = 'lore line ' * 500  # ~5KB per entry
        entries = [
            {'keys': [f'k{i}'], 'content': f'#{i} {long_text}',
             'enabled': True, 'constant': False, 'insertion_order': i}
            for i in range(50)
        ]
        card = v2_card(name='Big')
        card['data']['character_book'] = {'name': 'Big', 'entries': entries}
        png = _png_with_card(card)
        extracted = extract_png_chara(png)
        out_entries = extracted['data']['character_book']['entries']
        assert len(out_entries) == 50
        assert out_entries[49]['content'].startswith('#49')
        assert long_text in out_entries[0]['content']

    def test_replacing_existing_chara_chunk_does_not_duplicate(self):
        """Writing the same PNG twice must not stack two chara chunks."""
        card1 = v2_card(name='First')
        png1 = _png_with_card(card1)
        card2 = v2_card(name='Second')
        png2 = write_png_chara(png1, card2)
        # Only the second card should be readable
        extracted = extract_png_chara(png2)
        assert extracted['data']['name'] == 'Second'
        # And it must remain a valid PNG (signature + IEND survive)
        assert png2[:8] == b'\x89PNG\r\n\x1a\n'
        assert b'IEND' in png2

    def test_extract_returns_none_on_non_png(self):
        assert extract_png_chara(b'not a png') is None

    def test_extract_returns_none_on_png_without_chara(self):
        png = make_minimal_png()
        assert extract_png_chara(png) is None


# ── V1 → V2 normalisation via the import endpoint ─────────────────────────

class TestImport:
    def test_import_v1_flat_json(self, client):
        """Bare V1 card (no `data` nesting, no `spec` field) imports as V2."""
        v1 = {
            'name': 'V1Char',
            'description': 'V1 description',
            'personality': 'V1 personality',
            'first_mes': 'Hi from V1.',
        }
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(json.dumps(v1).encode('utf-8')), 'v1.json', 'application/json'),
        }, content_type='multipart/form-data')
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'V1Char'
        assert body['description'] == 'V1 description'
        # The PNG on disk should embed a normalised V2 card
        path = os.path.join(shared.CHARACTERS_DIR, body['filename'])
        with open(path, 'rb') as f:
            card = extract_png_chara(f.read())
        assert card['spec'] == 'chara_card_v2'
        assert card['data']['name'] == 'V1Char'

    def test_import_v2_json(self, client):
        v2 = v2_card(name='V2Char', description='Already V2')
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(json.dumps(v2).encode('utf-8')), 'v2.json', 'application/json'),
        }, content_type='multipart/form-data')
        assert r.status_code == 201
        assert r.get_json()['name'] == 'V2Char'

    def test_import_png_with_embedded_card(self, client):
        card = v2_card(name='PngCard', description='From PNG.',
                        character_book={'name': 'BK', 'entries': [
                            {'keys': ['k'], 'content': 'v', 'enabled': True}
                        ]})
        png = _png_with_card(card)
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(png), 'card.png', 'image/png'),
        }, content_type='multipart/form-data')
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'PngCard'
        assert body['character_book']['entries'][0]['content'] == 'v'

    def test_import_embedded_lorebook_with_object_entries_is_api_visible(self, client):
        card = v2_card(name='ObjectBook', character_book={
            'name': 'Embedded Object Book',
            'entries': {
                '2': {'key': ['later'], 'content': 'Second', 'order': 20},
                '1': {'key': 'first, alpha', 'keysecondary': ['beta'], 'content': 'First', 'disable': True},
            },
        })
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(json.dumps(card).encode('utf-8')), 'object-book.json', 'application/json'),
        }, content_type='multipart/form-data')

        assert r.status_code == 201
        book = r.get_json()['character_book']
        assert isinstance(book['entries'], list)
        assert [e['content'] for e in book['entries']] == ['First', 'Second']
        assert book['entries'][0]['keys'] == ['first', 'alpha']
        assert book['entries'][0]['secondary_keys'] == ['beta']
        assert book['entries'][0]['enabled'] is False
        assert book['entries'][1]['insertion_order'] == 20

    def test_import_png_without_embedded_card_rejected(self, client):
        png = make_minimal_png()
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(png), 'plain.png', 'image/png'),
        }, content_type='multipart/form-data')
        assert r.status_code == 400

    def test_import_invalid_json_rejected(self, client):
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(b'{not json'), 'bad.json', 'application/json'),
        }, content_type='multipart/form-data')
        assert r.status_code == 400

    def test_import_unsupported_extension_rejected(self, client):
        r = client.post('/api/characters/import', data={
            'file': (BytesIO(b'data'), 'card.txt', 'text/plain'),
        }, content_type='multipart/form-data')
        assert r.status_code == 400

    def test_import_missing_file_rejected(self, client):
        r = client.post('/api/characters/import', data={}, content_type='multipart/form-data')
        assert r.status_code == 400


# ── Export endpoint ────────────────────────────────────────────────────────

class TestExport:
    def test_export_json(self, client, sample_character):
        r = client.get(f'/api/characters/{sample_character["id"]}/export?fmt=json')
        assert r.status_code == 200
        assert r.content_type.startswith('application/json')
        body = json.loads(r.data)
        assert body['spec'] == 'chara_card_v2'
        assert body['data']['name'] == 'TestChar'
        # The Content-Disposition header should suggest a sane filename
        cd = r.headers.get('Content-Disposition', '')
        assert 'TestChar' in cd

    def test_export_png_round_trips(self, client, sample_character):
        r = client.get(f'/api/characters/{sample_character["id"]}/export?fmt=png')
        assert r.status_code == 200
        assert r.content_type == 'image/png'
        # The PNG bytes must contain readable card data
        card = extract_png_chara(r.data)
        assert card is not None
        assert card['data']['name'] == 'TestChar'

    def test_export_default_format_is_json(self, client, sample_character):
        r = client.get(f'/api/characters/{sample_character["id"]}/export')
        assert r.status_code == 200
        assert r.content_type.startswith('application/json')

    def test_export_missing_character_404(self, client):
        r = client.get('/api/characters/99999/export?fmt=json')
        assert r.status_code == 404

    def test_export_then_reimport_preserves_lorebook(self, client, sample_character):
        # Embed a lorebook on the character
        book = {'name': 'Trip', 'entries': [
            {'keys': ['hero'], 'content': 'Brave.', 'enabled': True}
        ]}
        client.put(f'/api/characters/{sample_character["id"]}', json={'character_book': book})

        # Export as PNG
        r = client.get(f'/api/characters/{sample_character["id"]}/export?fmt=png')
        png_bytes = r.data

        # Re-import — should land as a fresh character with the same book
        r2 = client.post('/api/characters/import', data={
            'file': (BytesIO(png_bytes), 'reimport.png', 'image/png'),
        }, content_type='multipart/form-data')
        assert r2.status_code == 201
        new_char = r2.get_json()
        assert new_char['character_book']['entries'][0]['content'] == 'Brave.'


# ── Avatar upload ──────────────────────────────────────────────────────────

class TestAvatarUpload:
    def test_avatar_upload_preserves_card_data(self, client, sample_character):
        # Establish baseline card data and embed a lorebook
        book = {'name': 'AvBook', 'entries': [
            {'keys': ['x'], 'content': 'survives', 'enabled': True}
        ]}
        client.put(f'/api/characters/{sample_character["id"]}', json={
            'character_book': book,
            'description': 'Pre-upload description',
        })

        # Upload a new avatar (a different PNG)
        new_png = make_minimal_png()
        r = client.post(
            f'/api/characters/{sample_character["id"]}/avatar',
            data={'avatar': (BytesIO(new_png), 'new.png', 'image/png')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 200

        # Card data must survive the avatar swap
        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char['description'] == 'Pre-upload description'
        assert char['character_book']['entries'][0]['content'] == 'survives'

    def test_avatar_upload_updates_crc(self, client, sample_character):
        # Force a different file content so CRC changes
        new_png = make_minimal_png() + b''  # same bytes, but write_png_chara replays card
        r = client.post(
            f'/api/characters/{sample_character["id"]}/avatar',
            data={'avatar': (BytesIO(new_png), 'a.png', 'image/png')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 200
        # File must still be a valid PNG and still embed card data
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(path, 'rb') as f:
            data = f.read()
        assert data[:8] == b'\x89PNG\r\n\x1a\n'
        assert extract_png_chara(data) is not None

    def test_avatar_upload_rejects_disallowed_format(self, client, sample_character):
        r = client.post(
            f'/api/characters/{sample_character["id"]}/avatar',
            data={'avatar': (BytesIO(b'not an image'), 'bad.exe', 'application/octet-stream')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_avatar_upload_no_file_rejected(self, client, sample_character):
        r = client.post(
            f'/api/characters/{sample_character["id"]}/avatar',
            data={}, content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_avatar_upload_404_for_unknown_character(self, client):
        png = make_minimal_png()
        r = client.post(
            '/api/characters/99999/avatar',
            data={'avatar': (BytesIO(png), 'a.png', 'image/png')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 404


# ── Delete behavior ────────────────────────────────────────────────────────

class TestCharacterDelete:
    def test_delete_succeeds_when_file_is_already_missing(self, client, sample_character):
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        os.remove(path)

        r = client.delete(f'/api/characters/{sample_character["id"]}')

        assert r.status_code == 200
        with shared.get_db() as conn:
            row = conn.execute(
                'SELECT id FROM characters WHERE id=?',
                (sample_character['id'],),
            ).fetchone()
        assert row is None

    def test_delete_failure_preserves_database_row(self, client, sample_character, monkeypatch):
        def deny_remove(_path):
            raise PermissionError('read-only filesystem')

        monkeypatch.setattr(characters_module.os, 'remove', deny_remove)

        r = client.delete(f'/api/characters/{sample_character["id"]}')

        assert r.status_code == 500
        with shared.get_db() as conn:
            row = conn.execute(
                'SELECT id FROM characters WHERE id=?',
                (sample_character['id'],),
            ).fetchone()
        assert row is not None


# ── Sync logic — files renamed/missing on disk ─────────────────────────────

class TestSync:
    def test_rename_on_disk_is_detected_via_crc(self, client, sample_character):
        old_path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        new_filename = 'renamed.png'
        new_path = os.path.join(shared.CHARACTERS_DIR, new_filename)
        os.rename(old_path, new_path)

        # Listing characters runs _sync — the row should now point at the new filename
        chars = client.get('/api/characters').get_json()
        target = next(c for c in chars if c['id'] == sample_character['id'])
        assert target['filename'] == new_filename
        assert target['missing'] is False

    def test_missing_file_marks_row_missing(self, client, sample_character):
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        os.remove(path)
        chars = client.get('/api/characters').get_json()
        target = next(c for c in chars if c['id'] == sample_character['id'])
        assert target['missing'] is True

    def test_new_file_on_disk_creates_row(self, client):
        # Drop a fresh card directly into the dir — list endpoint should pick it up
        card = v2_card(name='WalkIn')
        png = _png_with_card(card)
        path = os.path.join(shared.CHARACTERS_DIR, 'walkin.png')
        with open(path, 'wb') as f:
            f.write(png)
        chars = client.get('/api/characters').get_json()
        names = [c['name'] for c in chars]
        assert 'WalkIn' in names

    def test_identical_copy_of_indexed_card_gets_its_own_row(self, client, sample_character):
        # Copying a card file gives two files with one CRC. Matching by CRC first
        # would hand the copy the original's row, so the original would vanish
        # from the listing and the two files would swap names request to request.
        src = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(src, 'rb') as f:
            content = f.read()
        with open(os.path.join(shared.CHARACTERS_DIR, 'copy.png'), 'wb') as f:
            f.write(content)

        chars = client.get('/api/characters').get_json()
        filenames = sorted(c['filename'] for c in chars)
        assert filenames == sorted([sample_character['filename'], 'copy.png'])

    def test_card_overwritten_by_a_copy_of_another_card(self, client, sample_character):
        # Two indexed cards, then one file is replaced by a byte-copy of the
        # other. Both rows now share a CRC; matching by CRC first tries to rename
        # the first row onto the second's filename and trips the UNIQUE index.
        other = client.post('/api/characters', data={
            'data': json.dumps({'name': 'Other'}),
            'image': (BytesIO(make_minimal_png()), 'other.png', 'image/png'),
        }, content_type='multipart/form-data').get_json()

        src = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(src, 'rb') as f:
            content = f.read()
        with open(os.path.join(shared.CHARACTERS_DIR, other['filename']), 'wb') as f:
            f.write(content)

        r = client.get('/api/characters')
        assert r.status_code == 200
        # Both files still on disk, so both rows stay present and neither is missing.
        chars = r.get_json()
        assert sorted(c['filename'] for c in chars) == sorted(
            [sample_character['filename'], other['filename']]
        )
        assert all(c['missing'] is False for c in chars)

    def test_unchanged_files_are_not_reread(self, client, sample_character, monkeypatch):
        client.get('/api/characters')      # prime the memos

        crc_reads, card_reads = [], []
        real_crc = card_store.file_crc
        real_card = card_store.read_character_card
        monkeypatch.setattr(card_store, 'file_crc',
                            lambda p: (crc_reads.append(p), real_crc(p))[1])
        monkeypatch.setattr(card_store, 'read_character_card',
                            lambda p: (card_reads.append(p), real_card(p))[1])

        assert client.get('/api/characters').status_code == 200
        assert crc_reads == []
        assert card_reads == []

        # Touch the file and repeat, so a spy that silently never fires cannot
        # make the assertions above pass for the wrong reason.
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        assert client.get('/api/characters').status_code == 200
        assert crc_reads == [path]
        assert card_reads == [path]

    def test_edited_card_is_reread(self, client, sample_character):
        before = client.get('/api/characters').get_json()[0]
        client.put(f'/api/characters/{sample_character["id"]}',
                   json={'description': 'Rewritten on disk.'})

        after = client.get('/api/characters').get_json()[0]
        assert after['description'] == 'Rewritten on disk.'
        # avatar_url carries ?v={crc} as the browser cache-buster, so a stale
        # CRC here would leave clients showing the old image.
        assert after['avatar_url'] != before['avatar_url']

    def test_new_file_appears_without_a_restart(self, client, sample_character):
        client.get('/api/characters')      # prime the memos

        path = os.path.join(shared.CHARACTERS_DIR, 'latecomer.png')
        with open(path, 'wb') as f:
            f.write(_png_with_card(v2_card(name='Latecomer')))

        chars = client.get('/api/characters').get_json()
        assert 'Latecomer' in [c['name'] for c in chars]

    def test_sync_after_restoring_file_clears_missing_flag(self, client, sample_character):
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(path, 'rb') as f:
            content = f.read()
        os.remove(path)
        client.get('/api/characters')  # marks missing

        # Restore — listing again should clear the flag
        with open(path, 'wb') as f:
            f.write(content)
        chars = client.get('/api/characters').get_json()
        target = next(c for c in chars if c['id'] == sample_character['id'])
        assert target['missing'] is False


# ── Update behaviour for character_book null/empty ─────────────────────────

class TestCharacterBookUpdate:
    def test_clearing_character_book_with_null(self, client, sample_character):
        book = {'name': 'B', 'entries': [{'keys': ['k'], 'content': 'v'}]}
        client.put(f'/api/characters/{sample_character["id"]}', json={'character_book': book})
        # Clear it
        r = client.put(f'/api/characters/{sample_character["id"]}',
                       json={'character_book': None})
        assert r.status_code == 200
        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char.get('character_book') in (None, {}, {'entries': []})

    def test_partial_field_update_preserves_others(self, client, sample_character):
        client.put(f'/api/characters/{sample_character["id"]}', json={
            'character_book': {'name': 'KeepMe', 'entries': [{'content': 'persist'}]}
        })
        client.put(f'/api/characters/{sample_character["id"]}', json={
            'description': 'Changed description'
        })
        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char['description'] == 'Changed description'
        # character_book must survive an unrelated field update
        assert char['character_book']['entries'][0]['content'] == 'persist'

    def test_update_normalizes_object_keyed_character_book(self, client, sample_character):
        r = client.put(f'/api/characters/{sample_character["id"]}', json={
            'character_book': {
                'name': 'Object keyed',
                'entries': {
                    '10': {'key': ['ten'], 'content': 'Ten', 'order': 10},
                    '2': {'key': 'two', 'content': 'Two', 'disable': True},
                },
            }
        })

        assert r.status_code == 200
        book = r.get_json()['character_book']
        assert [e['content'] for e in book['entries']] == ['Two', 'Ten']
        assert book['entries'][0]['keys'] == ['two']
        assert book['entries'][0]['enabled'] is False
        assert book['entries'][1]['insertion_order'] == 10

    def test_update_ignores_disallowed_keys(self, client, sample_character):
        r = client.put(f'/api/characters/{sample_character["id"]}', json={
            'name': 'Allowed',
            'spec': 'injected_spec',
            'spec_version': '99.0',
        })
        assert r.status_code == 200
        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char['name'] == 'Allowed'
        # spec/spec_version are not in ALLOWED_UPDATE_KEYS and must be ignored
        r2 = client.get(f'/api/characters/{sample_character["id"]}/export?fmt=json')
        card = r2.get_json()
        assert card.get('spec') == 'chara_card_v2'
        assert card.get('spec_version') == '2.0'

    def test_update_persists_extensions(self, client, sample_character):
        r = client.put(f'/api/characters/{sample_character["id"]}', json={
            'extensions': {'cozy_hero_position': 60, 'foo': 'bar'},
        })
        assert r.status_code == 200
        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char['extensions']['cozy_hero_position'] == 60
        assert char['extensions']['foo'] == 'bar'


# ── Pin / favourite ────────────────────────────────────────────────────────

class TestPin:
    def test_pin_toggle(self, client, sample_character):
        # Pin
        r = client.post(f'/api/characters/{sample_character["id"]}/pin')
        assert r.status_code == 200
        body = r.get_json()
        assert body['pinned'] is True
        assert body['pinned_at'] is not None

        # Unpin
        r2 = client.post(f'/api/characters/{sample_character["id"]}/pin')
        assert r2.status_code == 200
        body2 = r2.get_json()
        assert body2['pinned'] is False
        assert body2['pinned_at'] is None

    def test_list_orders_pinned_first(self, client):
        # Create three characters
        png = make_minimal_png()
        chars = []
        for name in ('Alpha', 'Beta', 'Gamma'):
            r = client.post('/api/characters', data={
                'data': json.dumps({'name': name}),
                'image': (BytesIO(png), f'{name}.png', 'image/png'),
            }, content_type='multipart/form-data')
            assert r.status_code == 201
            chars.append(r.get_json())

        # Pin Beta, then Alpha (Alpha should be first among pinned because most recent)
        client.post(f'/api/characters/{chars[1]["id"]}/pin')
        client.post(f'/api/characters/{chars[0]["id"]}/pin')

        listing = client.get('/api/characters').get_json()
        names = [c['name'] for c in listing]
        assert names[0] == 'Alpha'
        assert names[1] == 'Beta'
        assert names[2] == 'Gamma'
        assert listing[0]['pinned'] is True
        assert listing[1]['pinned'] is True
        assert listing[2]['pinned'] is False

    def test_pin_404_for_missing_character(self, client):
        r = client.post('/api/characters/99999/pin')
        assert r.status_code == 404


# ── Gallery organization: archive + collections ───────────────────────────

class TestCharacterOrganization:
    def test_archive_excluded_by_default_and_included_when_requested(self, client, sample_character):
        r = client.post(f'/api/characters/{sample_character["id"]}/archive', json={'archived': True})
        assert r.status_code == 200
        assert r.get_json()['archived_at'] is not None

        default_listing = client.get('/api/characters').get_json()
        assert [c['id'] for c in default_listing] == []

        all_listing = client.get('/api/characters?include_archived=1').get_json()
        assert [c['id'] for c in all_listing] == [sample_character['id']]

        archived_listing = client.get('/api/characters?archived=1').get_json()
        assert [c['id'] for c in archived_listing] == [sample_character['id']]

        r2 = client.post(f'/api/characters/{sample_character["id"]}/archive', json={'archived': False})
        assert r2.status_code == 200
        assert r2.get_json()['archived_at'] is None

    def test_default_listing_empty_when_all_characters_are_archived(self, client, sample_character):
        png = make_minimal_png()
        r = client.post('/api/characters', data={
            'data': json.dumps({'name': 'SecondChar'}),
            'image': (BytesIO(png), 'second.png', 'image/png'),
        }, content_type='multipart/form-data')
        assert r.status_code == 201
        second_character = r.get_json()

        for char in (sample_character, second_character):
            archived = client.post(f'/api/characters/{char["id"]}/archive', json={'archived': True})
            assert archived.status_code == 200

        default_listing = client.get('/api/characters').get_json()
        assert default_listing == []

        archived_listing = client.get('/api/characters?archived=1').get_json()
        assert sorted(c['id'] for c in archived_listing) == sorted([
            sample_character['id'],
            second_character['id'],
        ])

    def test_collection_crud_and_membership(self, client, sample_character):
        created = client.post('/api/character-collections', json={'name': 'Story Crew'})
        assert created.status_code == 201
        collection = created.get_json()
        assert collection['name'] == 'Story Crew'
        assert collection['character_count'] == 0

        added = client.post(
            f'/api/character-collections/{collection["id"]}/characters/{sample_character["id"]}'
        )
        assert added.status_code == 200
        assert added.get_json()['collections'][0]['name'] == 'Story Crew'

        collections = client.get('/api/character-collections').get_json()
        assert collections[0]['character_count'] == 1

        renamed = client.put(
            f'/api/character-collections/{collection["id"]}',
            json={'name': 'Bridge Crew'},
        )
        assert renamed.status_code == 200
        assert renamed.get_json()['name'] == 'Bridge Crew'

        removed = client.delete(
            f'/api/character-collections/{collection["id"]}/characters/{sample_character["id"]}'
        )
        assert removed.status_code == 200
        assert removed.get_json()['collections'] == []

    def test_delete_collection_clears_membership(self, client, sample_character):
        collection = client.post('/api/character-collections', json={'name': 'Doctors'}).get_json()
        client.post(f'/api/character-collections/{collection["id"]}/characters/{sample_character["id"]}')

        r = client.delete(f'/api/character-collections/{collection["id"]}')
        assert r.status_code == 200

        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char['collections'] == []

    def test_delete_character_cascades_membership(self, client, sample_character):
        collection = client.post('/api/character-collections', json={'name': 'Spaceport'}).get_json()
        client.post(f'/api/character-collections/{collection["id"]}/characters/{sample_character["id"]}')

        r = client.delete(f'/api/characters/{sample_character["id"]}')
        assert r.status_code == 200

        collections = client.get('/api/character-collections').get_json()
        assert collections[0]['character_count'] == 0

    def test_collection_and_archive_do_not_rewrite_png_card(self, client, sample_character):
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(path, 'rb') as f:
            before = f.read()

        collection = client.post('/api/character-collections', json={'name': 'Local Only'}).get_json()
        client.post(f'/api/character-collections/{collection["id"]}/characters/{sample_character["id"]}')
        client.post(f'/api/characters/{sample_character["id"]}/archive', json={'archived': True})
        client.post(f'/api/characters/{sample_character["id"]}/archive', json={'archived': False})
        client.delete(f'/api/character-collections/{collection["id"]}/characters/{sample_character["id"]}')

        with open(path, 'rb') as f:
            after = f.read()
        assert after == before


# ── Duplicate endpoint ────────────────────────────────────────────────────

class TestDuplicate:
    def test_duplicate_creates_independent_copy(self, client, sample_character):
        r = client.post(f'/api/characters/{sample_character["id"]}/duplicate')
        assert r.status_code == 201
        copy = r.get_json()

        # New row, new file, name suffixed with " Copy".
        assert copy['id'] != sample_character['id']
        assert copy['filename'] != sample_character['filename']
        assert copy['name'] == 'TestChar Copy'

        # Both characters now show up in the listing.
        ids = {c['id'] for c in client.get('/api/characters').get_json()}
        assert {sample_character['id'], copy['id']} <= ids

        # The copy's PNG embeds the renamed card.
        path = os.path.join(shared.CHARACTERS_DIR, copy['filename'])
        with open(path, 'rb') as f:
            card = extract_png_chara(f.read())
        assert card['data']['name'] == 'TestChar Copy'

    def test_duplicate_leaves_original_untouched(self, client, sample_character):
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(path, 'rb') as f:
            before = f.read()

        client.post(f'/api/characters/{sample_character["id"]}/duplicate')

        original = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert original['name'] == 'TestChar'
        with open(path, 'rb') as f:
            assert f.read() == before

    def test_duplicate_unknown_character_404s(self, client):
        r = client.post('/api/characters/99999/duplicate')
        assert r.status_code == 404
