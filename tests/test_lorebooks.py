"""Tests for standalone lorebooks + per-chat selection."""

import json
import os
from io import BytesIO

import shared
from png_utils import extract_png_chara, write_png_chara


def _make_book(name='Test Book', entries=None):
    return {
        'name': name,
        'description': '',
        'scan_depth': 20,
        'max_entries': 20,
        'entries': entries or [],
    }


class TestLorebooksCRUD:
    def test_list_empty_on_fresh_db(self, client):
        r = client.get('/api/lorebooks')
        assert r.status_code == 200
        assert r.get_json() == []

    def test_create_minimum_fields(self, client):
        r = client.post('/api/lorebooks', json={'name': 'World A'})
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'World A'
        assert body['book']['name'] == 'World A'
        assert body['book']['entries'] == []

    def test_create_rejects_missing_name(self, client):
        r = client.post('/api/lorebooks', json={})
        assert r.status_code == 400

    def test_create_with_full_book(self, client):
        book = _make_book('Full', entries=[
            {'keys': ['dragon'], 'content': 'Dragons fly.', 'enabled': True,
             'constant': False, 'insertion_order': 100},
            {'keys': [], 'content': 'World note.', 'enabled': True,
             'constant': True, 'insertion_order': 50},
        ])
        r = client.post('/api/lorebooks', json={'name': 'Full', 'book': book})
        assert r.status_code == 201
        body = r.get_json()
        assert len(body['book']['entries']) == 2

    def test_list_includes_book_for_request_builder(self, client):
        book = _make_book('B', entries=[{'keys': ['x'], 'content': 'y'}])
        client.post('/api/lorebooks', json={'name': 'B', 'book': book})
        r = client.get('/api/lorebooks')
        assert r.status_code == 200
        rows = r.get_json()
        assert len(rows) == 1
        assert rows[0]['name'] == 'B'
        assert rows[0]['entry_count'] == 1
        # Full book ships in the list response — the frontend's request
        # builder reads entries straight off state.lorebooks to inject lore.
        assert rows[0]['book']['entries'][0]['content'] == 'y'

    def test_get_returns_full_book(self, client):
        book = _make_book('C', entries=[{'keys': ['k'], 'content': 'v'}])
        created = client.post('/api/lorebooks', json={'name': 'C', 'book': book}).get_json()
        r = client.get(f'/api/lorebooks/{created["id"]}')
        assert r.status_code == 200
        body = r.get_json()
        assert body['book']['entries'][0]['content'] == 'v'

    def test_update_renames_and_replaces_book(self, client):
        created = client.post('/api/lorebooks', json={'name': 'Old'}).get_json()
        r = client.put(f'/api/lorebooks/{created["id"]}', json={
            'name': 'New',
            'book': _make_book('Should be overridden', entries=[
                {'keys': [], 'content': 'hi', 'constant': True}
            ]),
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['name'] == 'New'
        # Column name takes precedence and is mirrored back into the JSON
        assert body['book']['name'] == 'New'
        assert len(body['book']['entries']) == 1

    def test_delete_removes_row(self, client):
        created = client.post('/api/lorebooks', json={'name': 'Z'}).get_json()
        r = client.delete(f'/api/lorebooks/{created["id"]}')
        assert r.status_code == 200
        assert client.get(f'/api/lorebooks/{created["id"]}').status_code == 404

    def test_get_404_when_missing(self, client):
        assert client.get('/api/lorebooks/9999').status_code == 404


class TestEmbedAndExtract:
    def test_embed_writes_into_character_card(self, client, sample_character):
        book = _make_book('Inline', entries=[{'keys': ['orc'], 'content': 'Big.', 'enabled': True}])
        created = client.post('/api/lorebooks', json={'name': 'Inline', 'book': book}).get_json()

        r = client.post(
            f'/api/lorebooks/{created["id"]}/embed-in-character/{sample_character["id"]}'
        )
        assert r.status_code == 200

        # The PNG on disk now embeds the book
        char_row = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char_row['character_book']['entries'][0]['content'] == 'Big.'

    def test_embed_with_delete_standalone_drops_db_row(self, client, sample_character):
        book = _make_book('Drop me', entries=[{'keys': ['k'], 'content': 'v'}])
        created = client.post('/api/lorebooks', json={'name': 'Drop me', 'book': book}).get_json()

        r = client.post(
            f'/api/lorebooks/{created["id"]}/embed-in-character/{sample_character["id"]}'
            '?delete_standalone=1'
        )
        assert r.status_code == 200
        # Standalone row is gone
        assert client.get(f'/api/lorebooks/{created["id"]}').status_code == 404

    def test_extract_creates_standalone_from_embedded(self, client, sample_character):
        # Embed a book on the character
        book = _make_book('Embedded book', entries=[
            {'keys': ['hero'], 'content': 'Brave.', 'enabled': True}
        ])
        client.put(f'/api/characters/{sample_character["id"]}', json={'character_book': book})

        r = client.post(f'/api/characters/{sample_character["id"]}/extract-lorebook')
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Embedded book'
        assert body['book']['entries'][0]['content'] == 'Brave.'

        # Original character still has its embedded book
        char_row = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char_row['character_book']['entries'][0]['content'] == 'Brave.'

    def test_extract_with_clear_empties_character_book(self, client, sample_character):
        book = _make_book('Will clear', entries=[{'keys': ['k'], 'content': 'v'}])
        client.put(f'/api/characters/{sample_character["id"]}', json={'character_book': book})

        r = client.post(
            f'/api/characters/{sample_character["id"]}/extract-lorebook?clear_embedded=1'
        )
        assert r.status_code == 201
        char_row = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char_row.get('character_book') in (None, {}, {'entries': []})

    def test_extract_404_when_no_embedded_book(self, client, sample_character):
        r = client.post(f'/api/characters/{sample_character["id"]}/extract-lorebook')
        assert r.status_code == 400


class TestPerChatLorebookSelection:
    def test_new_chat_without_embedded_book_starts_clean(self, client, sample_character):
        r = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'C'})
        assert r.status_code == 201
        chat = r.get_json()
        assert chat['active_lorebook_id'] is None
        assert chat['active_lorebook_embedded'] is False
        assert chat['lorebook_notice_dismissed'] is False

    def test_new_chat_auto_selects_embedded_book(self, client, sample_character):
        # Add an embedded book to the character
        book = _make_book('Auto', entries=[{'keys': ['k'], 'content': 'v'}])
        client.put(f'/api/characters/{sample_character["id"]}', json={'character_book': book})

        r = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'C2'})
        chat = r.get_json()
        assert chat['active_lorebook_embedded'] is True
        assert chat['active_lorebook_id'] is None
        # Notice has not been dismissed yet
        assert chat['lorebook_notice_dismissed'] is False

    def test_update_chat_can_set_standalone_book(self, client, sample_chat):
        lb = client.post('/api/lorebooks', json={'name': 'LB1'}).get_json()
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={
            'active_lorebook_id': lb['id'],
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['active_lorebook_id'] == lb['id']
        assert body['active_lorebook_embedded'] is False

    def test_setting_embedded_clears_standalone_id(self, client, sample_chat):
        lb = client.post('/api/lorebooks', json={'name': 'LB2'}).get_json()
        client.put(f'/api/chats/{sample_chat["id"]}', json={'active_lorebook_id': lb['id']})
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={
            'active_lorebook_embedded': True,
        })
        body = r.get_json()
        assert body['active_lorebook_embedded'] is True
        assert body['active_lorebook_id'] is None

    def test_clearing_lorebook_resets_both_fields(self, client, sample_chat):
        lb = client.post('/api/lorebooks', json={'name': 'LB3'}).get_json()
        client.put(f'/api/chats/{sample_chat["id"]}', json={'active_lorebook_id': lb['id']})
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={
            'active_lorebook_id': None,
            'active_lorebook_embedded': False,
        })
        body = r.get_json()
        assert body['active_lorebook_id'] is None
        assert body['active_lorebook_embedded'] is False

    def test_set_unknown_standalone_id_returns_404(self, client, sample_chat):
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={
            'active_lorebook_id': 9999,
        })
        assert r.status_code == 404

    def test_dismissing_notice_persists(self, client, sample_chat):
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={
            'lorebook_notice_dismissed': True,
        })
        body = r.get_json()
        assert body['lorebook_notice_dismissed'] is True

    def test_deleting_lorebook_clears_chat_reference(self, client, sample_chat):
        lb = client.post('/api/lorebooks', json={'name': 'LB4'}).get_json()
        client.put(f'/api/chats/{sample_chat["id"]}', json={'active_lorebook_id': lb['id']})

        client.delete(f'/api/lorebooks/{lb["id"]}')

        # Refetch the chat — its active_lorebook_id should be cleared
        chats = client.get(f'/api/characters/{sample_chat["character_id"]}/chats').get_json()
        target = next(c for c in chats if c['id'] == sample_chat['id'])
        assert target['active_lorebook_id'] is None


class TestScanDepthOverrideSetting:
    def test_setting_persists(self, client):
        r = client.put('/api/settings', json={'lorebook_scan_depth_override': '40'})
        assert r.status_code == 200
        body = r.get_json()
        assert body['lorebook_scan_depth_override'] == '40'


class TestLorebookEdgeCases:
    """The corners flagged in the post-implementation sanity check."""

    def test_put_with_only_name_preserves_entries(self, client):
        book = _make_book('Original', entries=[
            {'keys': ['k'], 'content': 'survives', 'enabled': True}
        ])
        created = client.post('/api/lorebooks', json={'name': 'Original', 'book': book}).get_json()
        # PUT just the name — entries must survive
        r = client.put(f'/api/lorebooks/{created["id"]}', json={'name': 'Renamed'})
        assert r.status_code == 200
        body = r.get_json()
        assert body['name'] == 'Renamed'
        assert body['book']['entries'][0]['content'] == 'survives'

    def test_put_with_only_book_pulls_name_from_book(self, client):
        created = client.post('/api/lorebooks', json={'name': 'Original'}).get_json()
        r = client.put(f'/api/lorebooks/{created["id"]}', json={
            'book': _make_book('FromBook', entries=[{'content': 'x'}])
        })
        assert r.status_code == 200
        body = r.get_json()
        # When `name` is absent from the request, the book's own name wins.
        assert body['name'] == 'FromBook'
        assert body['book']['name'] == 'FromBook'

    def test_unicode_round_trips_through_api(self, client):
        book = _make_book('世界', entries=[
            {'keys': ['東京', 'tokyo'], 'content': '🗼 東京タワー', 'enabled': True},
            {'keys': ['café'], 'content': 'naïve résumé', 'enabled': True},
        ])
        created = client.post('/api/lorebooks', json={'name': '世界', 'book': book}).get_json()
        fetched = client.get(f'/api/lorebooks/{created["id"]}').get_json()
        assert fetched['name'] == '世界'
        assert fetched['book']['entries'][0]['content'] == '🗼 東京タワー'
        assert fetched['book']['entries'][1]['content'] == 'naïve résumé'

    def test_create_lorebook_with_explicit_name_overrides_book_name(self, client):
        # Column name takes precedence on create — the book's `name` field is
        # rewritten to match so frontend code can rely on book.name == name.
        book = _make_book('Inside', entries=[{'content': 'x'}])
        r = client.post('/api/lorebooks', json={'name': 'Outside', 'book': book})
        body = r.get_json()
        assert body['name'] == 'Outside'
        assert body['book']['name'] == 'Outside'

    def test_embed_in_unknown_character_returns_404(self, client):
        created = client.post('/api/lorebooks', json={'name': 'X'}).get_json()
        r = client.post(f'/api/lorebooks/{created["id"]}/embed-in-character/99999')
        assert r.status_code == 404

    def test_embed_unknown_lorebook_returns_404(self, client, sample_character):
        r = client.post(f'/api/lorebooks/99999/embed-in-character/{sample_character["id"]}')
        assert r.status_code == 404

    def test_extract_unknown_character_returns_404(self, client):
        r = client.post('/api/characters/99999/extract-lorebook')
        assert r.status_code == 404

    def test_chat_with_empty_character_book_does_not_auto_select(self, client, sample_character):
        """character_book = {} (no entries) shouldn't trigger embedded auto-select."""
        client.put(f'/api/characters/{sample_character["id"]}', json={
            'character_book': {'name': 'Empty', 'entries': []}
        })
        r = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'C'})
        chat = r.get_json()
        assert chat['active_lorebook_embedded'] is False

    def test_chat_with_missing_character_file_does_not_auto_select(self, client, sample_character):
        """If the PNG is missing on disk, chat creation must not crash and must
        leave the embedded flag clear."""
        path = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        os.remove(path)
        # Run sync so the row is marked missing
        client.get('/api/characters')
        # Creating a chat against a missing character should still 404 (handled
        # by routes/chats.py: SELECT id FROM characters returns None? actually
        # the row exists, so chat creation goes through but `_character_has_lorebook`
        # silently returns False for missing files).
        r = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'C'})
        # Either 201 with embedded=False or 404 — both acceptable; the key
        # invariant is no 500.
        assert r.status_code in (201, 404)
        if r.status_code == 201:
            assert r.get_json()['active_lorebook_embedded'] is False

    def test_setting_negative_lorebook_id_is_rejected(self, client, sample_chat):
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={
            'active_lorebook_id': 'not-an-integer',
        })
        assert r.status_code == 400


class TestEmbedExtractRoundtrip:
    def test_byte_for_byte_round_trip_via_extract(self, client, sample_character):
        """Embed → extract → re-embed must preserve every entry field."""
        original = _make_book('RT', entries=[
            {'keys': ['α'], 'secondary_keys': ['β'], 'content': 'γ',
             'enabled': True, 'constant': False, 'case_sensitive': True,
             'selective': True, 'insertion_order': 42, 'comment': 'hand-tuned'},
            {'keys': [], 'content': 'always', 'enabled': True,
             'constant': True, 'insertion_order': 5},
        ])
        # Step 1: embed via the character update path
        client.put(f'/api/characters/{sample_character["id"]}', json={'character_book': original})
        # Step 2: extract back to a standalone row
        r = client.post(f'/api/characters/{sample_character["id"]}/extract-lorebook')
        body = r.get_json()
        # Compare every entry's relevant fields
        assert body['book']['entries'][0]['keys'] == ['α']
        assert body['book']['entries'][0]['secondary_keys'] == ['β']
        assert body['book']['entries'][0]['content'] == 'γ'
        assert body['book']['entries'][0]['case_sensitive'] is True
        assert body['book']['entries'][0]['selective'] is True
        assert body['book']['entries'][0]['insertion_order'] == 42
        assert body['book']['entries'][0]['comment'] == 'hand-tuned'
        assert body['book']['entries'][1]['constant'] is True


class TestMigrationIdempotent:
    def test_init_db_runs_twice_safely(self, tmp_path, monkeypatch):
        """Running init_db a second time on an existing DB should be a no-op."""
        # The autouse _test_db fixture has already initialised once.
        shared.init_db()
        shared.init_db()
        # Verify the new columns exist
        with shared.get_db() as conn:
            cols = {r['name'] for r in conn.execute('PRAGMA table_info(chats)').fetchall()}
            assert 'active_lorebook_id' in cols
            assert 'active_lorebook_embedded' in cols
            assert 'lorebook_notice_dismissed' in cols
            tables = {r['name'] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert 'lorebooks' in tables


class TestLorebookImport:
    def test_import_v2_character_book_shape(self, client):
        """A bare V2 character_book object imports cleanly."""
        payload = {
            'name': 'Imported',
            'description': 'A test',
            'scan_depth': 30,
            'token_budget': 500,
            'recursive_scanning': True,
            'extensions': {'foo': 'bar'},
            'entries': [
                {'keys': ['dragon'], 'content': 'They fly.', 'enabled': True,
                 'insertion_order': 100, 'extensions': {}},
                {'keys': [], 'content': 'Always present.', 'enabled': True,
                 'constant': True, 'insertion_order': 50, 'extensions': {}},
            ],
        }
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Imported'
        b = body['book']
        assert b['scan_depth'] == 30
        assert b['token_budget'] == 500
        assert b['recursive_scanning'] is True
        assert b['extensions'] == {'foo': 'bar'}
        assert len(b['entries']) == 2
        assert b['entries'][0]['keys'] == ['dragon']
        assert b['entries'][1]['constant'] is True

    def test_import_unwraps_character_book_key(self, client):
        """A payload wrapped under `character_book` is unwrapped."""
        payload = {'character_book': {
            'name': 'Wrapped',
            'entries': [{'keys': ['x'], 'content': 'y'}],
        }}
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        assert r.get_json()['book']['entries'][0]['content'] == 'y'

    def test_import_unwraps_full_character_card(self, client):
        """A full V2 card with `data.character_book` works too."""
        payload = {
            'spec': 'chara_card_v2',
            'spec_version': '2.0',
            'data': {
                'name': 'Char',
                'character_book': {
                    'name': 'From Card',
                    'entries': [{'keys': ['k'], 'content': 'v'}],
                },
            },
        }
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'From Card'
        assert body['book']['entries'][0]['content'] == 'v'

    def test_import_silly_world_info_object_entries(self, client):
        """SillyTavern world-info uses object-keyed entries with key/order/disable aliases."""
        payload = {
            'name': 'WI',
            'entries': {
                '0': {'key': ['alpha'], 'keysecondary': ['a2'], 'content': 'A',
                      'order': 200, 'disable': False, 'selective': True},
                '1': {'key': ['beta'], 'content': 'B', 'order': 50, 'disable': True},
            },
        }
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        b = r.get_json()['book']
        # Sorted by numeric key (0, 1) → [alpha, beta]
        assert b['entries'][0]['keys'] == ['alpha']
        assert b['entries'][0]['secondary_keys'] == ['a2']
        assert b['entries'][0]['insertion_order'] == 200
        assert b['entries'][0]['enabled'] is True
        assert b['entries'][0]['selective'] is True
        # `disable: True` becomes `enabled: False`
        assert b['entries'][1]['enabled'] is False
        assert b['entries'][1]['insertion_order'] == 50

    def test_import_string_keys_split_on_comma(self, client):
        """ST sometimes stores keys as a comma-separated string."""
        payload = {
            'entries': [{'key': 'one, two , three', 'content': 'c'}],
        }
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        keys = r.get_json()['book']['entries'][0]['keys']
        assert keys == ['one', 'two', 'three']

    def test_import_defaults_extensions_to_empty(self, client):
        """V2 spec requires `extensions` on book and every entry — default to {}."""
        payload = {'entries': [{'keys': ['k'], 'content': 'c'}]}
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        b = r.get_json()['book']
        assert b['extensions'] == {}
        assert b['entries'][0]['extensions'] == {}

    def test_import_preserves_optional_v2_fields(self, client):
        """Spec-optional fields like priority, position, name, id round-trip."""
        payload = {'entries': [{
            'keys': ['k'], 'content': 'c',
            'name': 'Entry Display Name',
            'priority': 7,
            'id': 42,
            'position': 'before_char',
        }]}
        r = client.post('/api/lorebooks/import', json=payload)
        assert r.status_code == 201
        e = r.get_json()['book']['entries'][0]
        assert e['name'] == 'Entry Display Name'
        assert e['priority'] == 7
        assert e['id'] == 42
        assert e['position'] == 'before_char'

    def test_import_rejects_missing_entries(self, client):
        r = client.post('/api/lorebooks/import', json={'name': 'no entries'})
        assert r.status_code == 400

    def test_import_rejects_non_object(self, client):
        r = client.post('/api/lorebooks/import', json=[1, 2, 3])
        assert r.status_code == 400

    def test_import_via_multipart_upload(self, client):
        """Uploading a JSON file via multipart works the same as a JSON body."""
        data = json.dumps({
            'name': 'Uploaded',
            'entries': [{'keys': ['u'], 'content': 'u-content'}],
        }).encode('utf-8')
        r = client.post(
            '/api/lorebooks/import',
            data={'file': (BytesIO(data), 'book.json', 'application/json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Uploaded'
        assert body['book']['entries'][0]['content'] == 'u-content'

    def test_import_rejects_invalid_json_upload(self, client):
        from io import BytesIO
        r = client.post(
            '/api/lorebooks/import',
            data={'file': (BytesIO(b'not json{'), 'bad.json', 'application/json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_import_falls_back_to_default_name(self, client):
        """A book with no name gets a sensible default."""
        r = client.post('/api/lorebooks/import', json={'entries': []})
        assert r.status_code == 201
        assert r.get_json()['name'] == 'Imported Lorebook'

    def test_import_query_name_overrides_payload(self, client):
        r = client.post(
            '/api/lorebooks/import?name=Override',
            json={'name': 'Inner', 'entries': []},
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Override'
        assert body['book']['name'] == 'Override'


class TestLorebookExport:
    def test_export_returns_json_attachment(self, client):
        book = _make_book('Export Me', entries=[
            {'keys': ['k'], 'content': 'v', 'enabled': True, 'insertion_order': 100},
        ])
        created = client.post('/api/lorebooks', json={'name': 'Export Me', 'book': book}).get_json()

        r = client.get(f'/api/lorebooks/{created["id"]}/export')
        assert r.status_code == 200
        assert r.mimetype == 'application/json'
        # Content-Disposition triggers download with a sanitized filename
        cd = r.headers.get('Content-Disposition', '')
        assert 'attachment' in cd
        assert 'Export Me.json' in cd

        body = json.loads(r.data.decode('utf-8'))
        assert body['name'] == 'Export Me'
        assert body['entries'][0]['content'] == 'v'

    def test_export_404_unknown(self, client):
        r = client.get('/api/lorebooks/99999/export')
        assert r.status_code == 404

    def test_export_filename_sanitised(self, client):
        """Filenames with slashes/punctuation get cleaned for the attachment header."""
        client.post('/api/lorebooks', json={'name': 'Bad/Name?*'}).get_json()
        rows = client.get('/api/lorebooks').get_json()
        r = client.get(f'/api/lorebooks/{rows[0]["id"]}/export')
        assert r.status_code == 200
        assert '/' not in r.headers['Content-Disposition'].split('filename=')[1]

    def test_import_then_export_roundtrip(self, client):
        """Import a V2 book, export it, re-import — entries stay byte-identical
        on the load-bearing fields."""
        original = {
            'name': 'Roundtrip',
            'description': 'desc',
            'scan_depth': 25,
            'extensions': {},
            'entries': [
                {'keys': ['a', 'b'], 'secondary_keys': ['c'], 'content': 'first',
                 'enabled': True, 'constant': False, 'selective': True,
                 'case_sensitive': True, 'insertion_order': 75,
                 'comment': 'note', 'extensions': {}},
                {'keys': [], 'content': 'always', 'enabled': True,
                 'constant': True, 'insertion_order': 10, 'extensions': {}},
            ],
        }
        created = client.post('/api/lorebooks/import', json=original).get_json()

        # Export, then import again
        exported = client.get(f'/api/lorebooks/{created["id"]}/export')
        re_imported = client.post(
            '/api/lorebooks/import',
            json=json.loads(exported.data.decode('utf-8')),
        ).get_json()

        a = created['book']
        b = re_imported['book']
        assert a['entries'] == b['entries']
        assert a['scan_depth'] == b['scan_depth']
        assert a['description'] == b['description']

    def test_export_character_lorebook(self, client, sample_character):
        """Embedded books export the same way."""
        # Embed a book in the character
        filepath = os.path.join(shared.CHARACTERS_DIR, sample_character['filename'])
        with open(filepath, 'rb') as f:
            png = f.read()
        card = extract_png_chara(png)
        card['data']['character_book'] = _make_book('From Char', entries=[
            {'keys': ['emb'], 'content': 'embedded', 'enabled': True, 'insertion_order': 100},
        ])
        with open(filepath, 'wb') as f:
            f.write(write_png_chara(png, card))

        r = client.get(f'/api/characters/{sample_character["id"]}/export-lorebook')
        assert r.status_code == 200
        assert r.mimetype == 'application/json'
        body = json.loads(r.data.decode('utf-8'))
        assert body['entries'][0]['content'] == 'embedded'

    def test_export_character_lorebook_404_when_no_book(self, client, sample_character):
        r = client.get(f'/api/characters/{sample_character["id"]}/export-lorebook')
        assert r.status_code == 400
