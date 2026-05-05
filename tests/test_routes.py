"""Tests for backend API routes."""

import os
from io import BytesIO

import shared
from png_utils import make_minimal_png


class TestSettings:
    def test_read_default_settings(self, client):
        r = client.get('/api/settings')
        assert r.status_code == 200
        data = r.get_json()
        # Fresh DB has no settings except defaults
        assert isinstance(data, dict)

    def test_write_and_read_settings(self, client):
        r = client.put('/api/settings', json={'api_endpoint': 'http://localhost:8080/v1'})
        assert r.status_code == 200
        data = r.get_json()
        assert data['api_endpoint'] == 'http://localhost:8080/v1'

    def test_sampler_settings_persist(self, client):
        client.put('/api/settings', json={
            'sampler_temperature': '0.7',
            'sampler_top_p': '0.9',
            'sampler_max_tokens': '1024',
        })
        r = client.get('/api/settings')
        data = r.get_json()
        assert data['sampler_temperature'] == '0.7'
        assert data['sampler_top_p'] == '0.9'
        assert data['sampler_max_tokens'] == '1024'

    def test_api_key_is_masked(self, client):
        client.put('/api/settings', json={'api_key': 'sk-test-secret-key-12345'})
        r = client.get('/api/settings')
        data = r.get_json()
        assert data['api_key_set'] is True
        assert data['api_key_masked'] == 'sk-…2345'
        # Raw key should not be in response
        assert 'sk-test-secret-key-12345' not in str(data)

    def test_send_thinking_setting_persists(self, client):
        client.put('/api/settings', json={'send_thinking': '1'})
        r = client.get('/api/settings')
        data = r.get_json()
        assert data['send_thinking'] == '1'


class TestSystemPrompts:
    def test_list_includes_default_seed(self, client):
        r = client.get('/api/system-prompts')
        assert r.status_code == 200
        prompts = r.get_json()
        assert len(prompts) >= 1
        assert prompts[0]['name'] == 'Default'
        # Seed is the default Prompt Builder template — should contain the
        # builder variables and conditional blocks.
        assert '{{description}}' in prompts[0]['content']
        assert '{{#system_prompt}}' in prompts[0]['content']

    def test_create_prompt(self, client):
        r = client.post('/api/system-prompts', json={
            'name': 'Custom RP',
            'content': 'You are {{char}}.',
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data['name'] == 'Custom RP'
        assert data['content'] == 'You are {{char}}.'
        assert 'id' in data

    def test_update_prompt(self, client):
        # Get the default prompt
        prompts = client.get('/api/system-prompts').get_json()
        pid = prompts[0]['id']
        r = client.put(f'/api/system-prompts/{pid}', json={
            'content': 'Updated content.',
        })
        assert r.status_code == 200
        assert r.get_json()['content'] == 'Updated content.'

    def test_delete_prompt(self, client):
        # Create then delete
        created = client.post('/api/system-prompts', json={'name': 'Temp'}).get_json()
        r = client.delete(f'/api/system-prompts/{created["id"]}')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

    def test_create_prompt_missing_name(self, client):
        r = client.post('/api/system-prompts', json={'content': 'No name'})
        assert r.status_code == 400

    def test_export_prompt_round_trips_through_import(self, client):
        import io, json as _json
        created = client.post('/api/system-prompts', json={
            'name': 'Roundtrip', 'content': 'You are {{char}}. End.'
        }).get_json()

        # Export
        r = client.get(f'/api/system-prompts/{created["id"]}/export')
        assert r.status_code == 200
        assert r.headers['Content-Type'].startswith('application/json')
        assert 'attachment' in r.headers['Content-Disposition']
        body = _json.loads(r.data.decode('utf-8'))
        assert body == {'name': 'Roundtrip', 'content': 'You are {{char}}. End.'}

        # Re-import the same payload — name collision should suffix " (2)"
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (io.BytesIO(r.data), 'roundtrip.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        imported = r.get_json()
        assert imported['name'] == 'Roundtrip (2)'
        assert imported['content'] == 'You are {{char}}. End.'
        assert imported['id'] != created['id']

    def test_import_prompt_rejects_non_json(self, client):
        import io
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (io.BytesIO(b'not json at all'), 'bad.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_import_prompt_uses_imported_prompt_when_name_missing(self, client):
        import io
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (io.BytesIO(b'{"content":"hi"}'), 'noname.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        assert r.get_json()['name'] == 'Imported Prompt'

    def test_export_missing_prompt_returns_404(self, client):
        r = client.get('/api/system-prompts/99999/export')
        assert r.status_code == 404

    def test_default_template_endpoint(self, client):
        r = client.get('/api/system-prompts/default-template')
        assert r.status_code == 200
        data = r.get_json()
        assert 'template' in data
        # The default template uses conditional blocks for every assembled section
        for var in ('{{#system_prompt}}', '{{#description}}', '{{#personality}}',
                    '{{#scenario}}', '{{#persona}}', '{{#mesExamples}}', '{{#lorebook}}'):
            assert var in data['template']

    def test_legacy_prompt_migration_wraps_plain_content(self, client, monkeypatch, tmp_path):
        """Legacy plain-text system_prompts rows should be wrapped in the
        default template at the {{system_prompt}} slot, and the migration
        sentinel should make subsequent init_db() calls a no-op."""
        import shared
        # New temp DB so we control the seed → migration sequence
        db_path = tmp_path / 'legacy.db'
        monkeypatch.setattr(shared, 'DATABASE', str(db_path))
        shared.init_db()

        # Replace the seeded template content with legacy plain text and clear
        # the migration sentinel so the next init_db() runs the migration.
        with shared.get_db() as conn:
            conn.execute('DELETE FROM settings WHERE key=?', ('prompt_template_migration',))
            conn.execute('UPDATE system_prompts SET content=? WHERE id=1',
                         ('Plain legacy instructions for {{char}}.',))

        shared.init_db()

        with shared.get_db() as conn:
            row = conn.execute('SELECT content FROM system_prompts WHERE id=1').fetchone()
            sentinel = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                ('prompt_template_migration',)
            ).fetchone()

        assert sentinel is not None
        assert 'Plain legacy instructions for {{char}}.' in row['content']
        assert '{{#description}}' in row['content']

        # Idempotency: running init_db() again should not re-wrap.
        before = row['content']
        shared.init_db()
        with shared.get_db() as conn:
            row2 = conn.execute('SELECT content FROM system_prompts WHERE id=1').fetchone()
        assert row2['content'] == before


class TestMessages:
    def test_add_message_creates_swipe(self, client, sample_chat):
        chat_id = sample_chat['id']
        r = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user',
            'content': 'Hello!',
        })
        assert r.status_code == 201
        msg = r.get_json()
        assert msg['role'] == 'user'
        assert msg['content'] == 'Hello!'
        assert len(msg['swipes']) == 1
        assert msg['swipes'][0]['content'] == 'Hello!'

    def test_list_messages_ordered(self, client, sample_chat):
        chat_id = sample_chat['id']
        client.post(f'/api/chats/{chat_id}/messages', json={'role': 'user', 'content': 'First'})
        client.post(f'/api/chats/{chat_id}/messages', json={'role': 'character', 'content': 'Second'})
        r = client.get(f'/api/chats/{chat_id}/messages')
        msgs = r.get_json()
        assert len(msgs) == 2
        assert msgs[0]['content'] == 'First'
        assert msgs[1]['content'] == 'Second'

    def test_add_swipe(self, client, sample_chat):
        chat_id = sample_chat['id']
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'Original',
        }).get_json()
        r = client.post(f'/api/messages/{msg["id"]}/swipes', json={
            'content': 'Alternative response',
        })
        assert r.status_code == 201
        swipe = r.get_json()
        assert swipe['content'] == 'Alternative response'

    def test_update_message(self, client, sample_chat):
        chat_id = sample_chat['id']
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user', 'content': 'Original text',
        }).get_json()
        r = client.put(f'/api/messages/{msg["id"]}', json={
            'content': 'Edited text',
        })
        assert r.status_code == 200
        assert r.get_json()['ok'] is True


class TestCharacters:
    def test_create_character_requires_image(self, client):
        r = client.post('/api/characters', data={
            'data': '{"name": "Alice"}',
        }, content_type='multipart/form-data')
        assert r.status_code == 400

    def test_create_character(self, client):
        from io import BytesIO
        png = make_minimal_png()
        r = client.post('/api/characters', data={
            'data': '{"name": "Alice"}',
            'image': (BytesIO(png), 'alice.png', 'image/png'),
        }, content_type='multipart/form-data')
        assert r.status_code == 201
        assert r.get_json()['name'] == 'Alice'

    def test_character_has_card_fields(self, client, sample_character):
        """Verify character API returns all fields the request builder needs."""
        r = client.get('/api/characters')
        chars = r.get_json()
        char = next(c for c in chars if c['id'] == sample_character['id'])
        for field in ['description', 'personality', 'scenario', 'first_mes',
                      'mes_example', 'system_prompt', 'post_history_instructions']:
            assert field in char


class TestLLMProxy:
    def test_chat_no_endpoint_returns_400(self, client):
        r = client.post('/api/llm/chat', json={
            'model': 'test',
            'messages': [{'role': 'user', 'content': 'hi'}],
        })
        assert r.status_code == 400
        assert r.get_json()['ok'] is False

    def test_chat_no_model_returns_400(self, client):
        client.put('/api/settings', json={'api_endpoint': 'http://localhost:9999/v1'})
        r = client.post('/api/llm/chat', json={
            'messages': [{'role': 'user', 'content': 'hi'}],
        })
        assert r.status_code == 400
        assert 'model' in r.get_json()['error'].lower()

    def test_chat_streams_sse_content_type(self, client):
        """When endpoint+model are set, response should be SSE (even if upstream fails)."""
        client.put('/api/settings', json={
            'api_endpoint': 'http://127.0.0.1:1/v1',
            'api_model': 'test-model',
        })
        r = client.post('/api/llm/chat', json={
            'model': 'test-model',
            'messages': [{'role': 'user', 'content': 'hi'}],
        })
        assert r.content_type.startswith('text/event-stream')

    def test_models_endpoint_requires_endpoint(self, client):
        r = client.get('/api/llm/models')
        assert r.status_code == 400
        assert 'endpoint' in r.get_json()['error'].lower()

    def test_test_endpoint_requires_endpoint(self, client):
        r = client.post('/api/llm/test')
        assert r.status_code == 400
        body = r.get_json()
        assert body['ok'] is False


# ── Messages: delete + cascade ─────────────────────────────────────────────

class TestMessageDelete:
    def test_delete_message_removes_swipes(self, client, sample_chat):
        chat_id = sample_chat['id']
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'Original',
        }).get_json()
        client.post(f'/api/messages/{msg["id"]}/swipes', json={'content': 'Alt 1'})
        client.post(f'/api/messages/{msg["id"]}/swipes', json={'content': 'Alt 2'})
        # Confirm swipes exist
        swipes = client.get(f'/api/messages/{msg["id"]}/swipes').get_json()
        assert len(swipes) >= 2

        r = client.delete(f'/api/messages/{msg["id"]}')
        assert r.status_code == 200
        # Swipes should cascade
        with shared.get_db() as conn:
            rows = conn.execute(
                'SELECT COUNT(*) FROM message_swipes WHERE message_id=?', (msg['id'],)
            ).fetchone()[0]
        assert rows == 0

    def test_delete_message_404(self, client):
        r = client.delete('/api/messages/99999')
        assert r.status_code == 404

    def test_update_message_404(self, client):
        r = client.put('/api/messages/99999', json={'content': 'x'})
        assert r.status_code == 404

    def test_update_message_requires_content(self, client, sample_chat):
        msg = client.post(f'/api/chats/{sample_chat["id"]}/messages', json={
            'role': 'user', 'content': 'orig',
        }).get_json()
        r = client.put(f'/api/messages/{msg["id"]}', json={'content': '   '})
        assert r.status_code == 400

    def test_add_message_validates_role(self, client, sample_chat):
        r = client.post(f'/api/chats/{sample_chat["id"]}/messages', json={
            'role': 'wizard', 'content': 'hi',
        })
        assert r.status_code == 400

    def test_add_message_requires_content(self, client, sample_chat):
        r = client.post(f'/api/chats/{sample_chat["id"]}/messages', json={
            'role': 'user', 'content': '',
        })
        assert r.status_code == 400

    def test_add_message_to_unknown_chat_404(self, client):
        r = client.post('/api/chats/99999/messages', json={
            'role': 'user', 'content': 'hi',
        })
        assert r.status_code == 404


class TestChatCascade:
    def test_delete_chat_cascades_to_messages_and_swipes(self, client, sample_chat):
        chat_id = sample_chat['id']
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user', 'content': 'a',
        }).get_json()
        client.post(f'/api/messages/{msg["id"]}/swipes', json={'content': 'b'})

        client.delete(f'/api/chats/{chat_id}')

        with shared.get_db() as conn:
            msgs = conn.execute(
                'SELECT COUNT(*) FROM messages WHERE chat_id=?', (chat_id,)
            ).fetchone()[0]
            swipes = conn.execute(
                'SELECT COUNT(*) FROM message_swipes WHERE message_id=?', (msg['id'],)
            ).fetchone()[0]
        assert msgs == 0
        assert swipes == 0

    def test_delete_character_cascades_to_chats(self, client, sample_character):
        # Create a chat for the character
        chat = client.post(
            f'/api/characters/{sample_character["id"]}/chats', json={'name': 'C'}
        ).get_json()
        client.delete(f'/api/characters/{sample_character["id"]}')

        with shared.get_db() as conn:
            rows = conn.execute(
                'SELECT COUNT(*) FROM chats WHERE id=?', (chat['id'],)
            ).fetchone()[0]
        assert rows == 0

    def test_delete_chat_404(self, client):
        r = client.delete('/api/chats/99999')
        assert r.status_code == 404


# ── Static-file routes ─────────────────────────────────────────────────────

class TestStaticRoutes:
    def test_character_avatar_route_serves_file(self, client, sample_character):
        r = client.get(f'/characters/{sample_character["filename"]}')
        assert r.status_code == 200
        # Bytes start with the PNG signature
        assert r.data[:8] == b'\x89PNG\r\n\x1a\n'

    def test_character_avatar_404_for_missing_file(self, client):
        r = client.get('/characters/does-not-exist.png')
        assert r.status_code == 404

    def test_persona_avatar_route_serves_file(self, client, sample_persona):
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'p.png', 'image/png')},
            content_type='multipart/form-data',
        )
        files = os.listdir(shared.PERSONAS_DIR)
        assert len(files) == 1
        r = client.get(f'/personas/{files[0]}')
        assert r.status_code == 200
        assert r.data[:8] == b'\x89PNG\r\n\x1a\n'

    def test_persona_avatar_404(self, client):
        r = client.get('/personas/missing.png')
        assert r.status_code == 404

    def test_themes_list_endpoint_returns_list(self, client):
        r = client.get('/api/themes')
        assert r.status_code == 200
        body = r.get_json()
        assert isinstance(body, list)
        # The built-in themes dir always has at least one theme
        assert len(body) > 0

    def test_user_theme_overrides_builtin(self, client):
        """A theme CSS file in user THEMES_DIR should be served for /themes/<name>."""
        path = os.path.join(shared.THEMES_DIR, 'my-custom.css')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(':root { --probe: 1; }')
        r = client.get('/themes/my-custom.css')
        assert r.status_code == 200
        assert b'--probe' in r.data


# ── Global JSON error handler ──────────────────────────────────────────────

class TestErrorHandler:
    def test_404_returns_json(self, client):
        r = client.get('/api/this-route-does-not-exist')
        assert r.status_code == 404
        # Response shape: {"error": "..."} via the global handler
        body = r.get_json()
        assert body is not None
        assert 'error' in body

    def test_unhandled_exception_returns_500_json(self, client, monkeypatch):
        """Force a route to raise; the global handler should return JSON."""
        from routes import settings as settings_mod

        def boom():
            raise RuntimeError('intentional test crash')

        monkeypatch.setattr(settings_mod, 'get_settings', boom)
        r = client.get('/api/settings')
        assert r.status_code == 500
        body = r.get_json()
        assert body is not None
        assert 'error' in body
        # The raw exception text must NOT leak into the response
        assert 'intentional test crash' not in str(body)


# ── Settings: lorebook_scan_depth_override (added to whitelist) ───────────

class TestLorebookOverrideInSettings:
    def test_override_appears_in_settings_payload_after_write(self, client):
        r = client.put('/api/settings', json={'lorebook_scan_depth_override': '15'})
        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert s['lorebook_scan_depth_override'] == '15'

    def test_always_inject_all_round_trips(self, client):
        r = client.put('/api/settings', json={'lorebook_always_inject_all': '1'})
        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert s['lorebook_always_inject_all'] == '1'
        r = client.put('/api/settings', json={'lorebook_always_inject_all': '0'})
        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert s['lorebook_always_inject_all'] == '0'
