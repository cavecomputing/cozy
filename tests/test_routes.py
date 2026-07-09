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

    def test_context_token_default_and_write(self, client):
        r = client.get('/api/settings')
        assert r.get_json()['context_max_tokens'] == '32768'
        client.put('/api/settings', json={'context_max_tokens': '8192'})
        r = client.get('/api/settings')
        assert r.get_json()['context_max_tokens'] == '8192'

    def test_context_token_meter_visibility_setting_persists(self, client):
        r = client.get('/api/settings')
        assert r.get_json()['show_context_token_meter'] == '1'
        client.put('/api/settings', json={'show_context_token_meter': '0'})
        r = client.get('/api/settings')
        assert r.get_json()['show_context_token_meter'] == '0'

    def test_context_token_zero_means_no_cap(self, client):
        # 0 is a valid value meaning "no cap" — round-trip preserves the literal "0"
        # so the frontend's `"0" || default` truthy-string check still picks it up.
        client.put('/api/settings', json={'context_max_tokens': '0'})
        r = client.get('/api/settings')
        assert r.get_json()['context_max_tokens'] == '0'

    def test_numeric_zero_setting_persists_as_zero(self, client):
        client.put('/api/settings', json={'context_max_tokens': 0})
        r = client.get('/api/settings')
        assert r.get_json()['context_max_tokens'] == '0'

    def test_legacy_context_message_setting_is_not_exposed(self, client):
        client.put('/api/settings', json={'context_max_messages': '64'})
        r = client.get('/api/settings')
        assert 'context_max_messages' not in r.get_json()


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
        assert prompts[0]['post_history_content'] == (
            '{{#post_history_instructions}}[Post-History Instructions]\n'
            '{{post_history_instructions}}{{/post_history_instructions}}'
        )

    def test_create_prompt(self, client):
        r = client.post('/api/system-prompts', json={
            'name': 'Custom RP',
            'content': 'You are {{char}}.',
            'post_history_content': '((OOC: Keep it moving.))',
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data['name'] == 'Custom RP'
        assert data['content'] == 'You are {{char}}.'
        assert data['post_history_content'] == '((OOC: Keep it moving.))'
        assert 'id' in data

    def test_update_prompt(self, client):
        # Get the default prompt
        prompts = client.get('/api/system-prompts').get_json()
        pid = prompts[0]['id']
        r = client.put(f'/api/system-prompts/{pid}', json={
            'content': 'Updated content.',
            'post_history_content': 'Updated post history.',
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['content'] == 'Updated content.'
        assert body['post_history_content'] == 'Updated post history.'

    def test_delete_prompt(self, client):
        # Create then delete
        created = client.post('/api/system-prompts', json={'name': 'Temp'}).get_json()
        r = client.delete(f'/api/system-prompts/{created["id"]}')
        assert r.status_code == 200
        assert r.get_json()['success'] is True

    def test_delete_prompt_404(self, client):
        r = client.delete('/api/system-prompts/99999')
        assert r.status_code == 404
        assert r.get_json()['error'] == 'System prompt not found'

    def test_create_prompt_missing_name(self, client):
        r = client.post('/api/system-prompts', json={'content': 'No name'})
        assert r.status_code == 400

    def test_export_prompt_round_trips_through_import(self, client):
        import json as _json
        created = client.post('/api/system-prompts', json={
            'name': 'Roundtrip',
            'content': 'You are {{char}}. End.',
            'post_history_content': '((OOC: End with motion.))',
        }).get_json()

        # Export
        r = client.get(f'/api/system-prompts/{created["id"]}/export')
        assert r.status_code == 200
        assert r.headers['Content-Type'].startswith('application/json')
        assert 'attachment' in r.headers['Content-Disposition']
        body = _json.loads(r.data.decode('utf-8'))
        assert body == {
            'name': 'Roundtrip',
            'content': 'You are {{char}}. End.',
            'post_history_content': '((OOC: End with motion.))',
        }

        # Re-import the same payload — name collision should suffix " (2)"
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (BytesIO(r.data), 'roundtrip.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        imported = r.get_json()
        assert imported['name'] == 'Roundtrip (2)'
        assert imported['content'] == 'You are {{char}}. End.'
        assert imported['post_history_content'] == '((OOC: End with motion.))'
        assert imported['id'] != created['id']

    def test_import_prompt_rejects_non_json(self, client):
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (BytesIO(b'not json at all'), 'bad.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_import_prompt_uses_imported_prompt_when_name_missing(self, client):
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (BytesIO(b'{"content":"hi"}'), 'noname.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Imported Prompt'
        assert body['post_history_content'] == (
            '{{#post_history_instructions}}[Post-History Instructions]\n'
            '{{post_history_instructions}}{{/post_history_instructions}}'
        )

    def test_import_prompt_accepts_legacy_system_only_json(self, client):
        r = client.post(
            '/api/system-prompts/import',
            data={'file': (BytesIO(b'{"name":"Legacy","content":"system only"}'), 'legacy.json')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Legacy'
        assert body['content'] == 'system only'
        assert body['post_history_content'].startswith('{{#post_history_instructions}}')

    def test_export_missing_prompt_returns_404(self, client):
        r = client.get('/api/system-prompts/99999/export')
        assert r.status_code == 404

    def test_default_template_endpoint(self, client):
        r = client.get('/api/system-prompts/default-template')
        assert r.status_code == 200
        data = r.get_json()
        assert 'template' in data
        assert 'post_history_template' in data
        # The default template uses conditional blocks for every assembled section
        for var in ('{{#system_prompt}}', '{{#description}}', '{{#personality}}',
                    '{{#scenario}}', '{{#persona}}', '{{#mesExamples}}', '{{#lorebook}}'):
            assert var in data['template']
        assert '{{#post_history_instructions}}' in data['post_history_template']

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

    def test_user_message_includes_persona_avatar_url(self, client, sample_chat, sample_persona):
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'avatar.png', 'image/png')},
            content_type='multipart/form-data',
        )

        chat_id = sample_chat['id']
        r = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user',
            'content': 'Hello!',
            'persona_id': sample_persona['id'],
        })
        assert r.status_code == 201
        created = r.get_json()
        assert created['persona_avatar_url'].startswith('/personas/')
        assert len(created['swipes']) == 1

        listed = client.get(f'/api/chats/{chat_id}/messages').get_json()
        assert listed[0]['persona_avatar_url'] == created['persona_avatar_url']
        assert listed[0]['swipes'][0]['content'] == 'Hello!'

    def test_list_messages_ordered(self, client, sample_chat):
        chat_id = sample_chat['id']
        first = client.post(f'/api/chats/{chat_id}/messages', json={'role': 'user', 'content': 'First'}).get_json()
        second = client.post(f'/api/chats/{chat_id}/messages', json={'role': 'character', 'content': 'Second'}).get_json()
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE messages SET created_at=? WHERE id IN (?, ?)',
                ('2026-05-15 12:00:00', first['id'], second['id'])
            )
        r = client.get(f'/api/chats/{chat_id}/messages')
        msgs = r.get_json()
        assert len(msgs) == 2
        assert msgs[0]['content'] == 'First'
        assert msgs[1]['content'] == 'Second'

    def test_list_many_messages_with_swipes(self, client, sample_chat):
        chat_id = sample_chat['id']
        with shared.get_db() as conn:
            for i in range(1200):
                cur = conn.execute(
                    'INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)',
                    (chat_id, 'user' if i % 2 == 0 else 'character', f'Message {i}')
                )
                conn.execute(
                    'INSERT INTO message_swipes (message_id, content) VALUES (?, ?)',
                    (cur.lastrowid, f'Message {i}')
                )

        r = client.get(f'/api/chats/{chat_id}/messages')
        assert r.status_code == 200
        msgs = r.get_json()
        assert len(msgs) == 1200
        assert msgs[0]['content'] == 'Message 0'
        assert msgs[-1]['content'] == 'Message 1199'
        assert msgs[-1]['swipes'][0]['content'] == 'Message 1199'

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
        assert r.get_json()['success'] is True

    def test_edit_with_update_swipe_rewrites_matching_swipe(self, client, sample_chat):
        chat_id = sample_chat['id']
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'First take',
        }).get_json()
        # addSwipe seeds 'First take' as swipe 1 and makes 'Second take' the content
        client.post(f'/api/messages/{msg["id"]}/swipes', json={'content': 'Second take'})

        r = client.put(f'/api/messages/{msg["id"]}', json={
            'content': 'Second take (edited)', 'update_swipe': True,
        })
        assert r.status_code == 200

        listed = client.get(f'/api/chats/{chat_id}/messages').get_json()
        edited = next(m for m in listed if m['id'] == msg['id'])
        assert edited['content'] == 'Second take (edited)'
        swipe_texts = [s['content'] for s in edited['swipes']]
        assert swipe_texts == ['First take', 'Second take (edited)']

    def test_swipe_selection_without_flag_leaves_swipes_alone(self, client, sample_chat):
        chat_id = sample_chat['id']
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'First take',
        }).get_json()
        client.post(f'/api/messages/{msg["id"]}/swipes', json={'content': 'Second take'})

        # Swiping back to the first swipe persists content only — both swipes survive
        r = client.put(f'/api/messages/{msg["id"]}', json={'content': 'First take'})
        assert r.status_code == 200

        listed = client.get(f'/api/chats/{chat_id}/messages').get_json()
        selected = next(m for m in listed if m['id'] == msg['id'])
        assert selected['content'] == 'First take'
        swipe_texts = [s['content'] for s in selected['swipes']]
        assert swipe_texts == ['First take', 'Second take']


class TestChatJsonlImportExport:
    def test_export_chat_as_sillytavern_jsonl(self, client, sample_chat):
        import json
        chat_id = sample_chat['id']
        user_msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user', 'content': 'Hello!',
        }).get_json()
        msg = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'Original',
        }).get_json()
        client.post(f'/api/messages/{msg["id"]}/swipes', json={'content': 'Alternative'})
        with shared.get_db() as conn:
            conn.execute(
                'UPDATE messages SET created_at=? WHERE id IN (?, ?)',
                ('2026-05-15 12:00:00', user_msg['id'], msg['id'])
            )

        r = client.get(f'/api/chats/{chat_id}/export')
        assert r.status_code == 200
        assert r.headers['Content-Type'].startswith('application/jsonl')
        assert 'attachment' in r.headers['Content-Disposition']

        lines = [json.loads(line) for line in r.data.decode('utf-8').splitlines()]
        assert lines[0]['character_name'] == 'TestChar'
        assert lines[1]['is_user'] is True
        assert lines[1]['mes'] == 'Hello!'
        assert lines[2]['is_user'] is False
        assert lines[2]['mes'] == 'Alternative'
        assert lines[2]['swipes'] == ['Original', 'Alternative']
        assert lines[2]['swipe_id'] == 1

    def test_import_sillytavern_jsonl_with_swipes(self, client, sample_character):
        import json
        payload = '\n'.join(json.dumps(line) for line in [
            {'user_name': 'Alice', 'character_name': 'TestChar', 'create_date': '2026-05-07T12:00:00Z', 'chat_metadata': {}},
            {'name': 'Alice', 'is_user': True, 'send_date': '2026-05-07T12:00:01Z', 'mes': 'Hi'},
            {'name': 'TestChar', 'is_user': False, 'send_date': '2026-05-07T12:00:02Z', 'mes': 'Alt B',
             'swipe_id': 1, 'swipes': ['Alt A', 'Alt B'], 'extra': {'source': 'test'}},
        ]).encode('utf-8')

        r = client.post(
            f'/api/chats/import?character_id={sample_character["id"]}',
            data={'file': (BytesIO(payload), 'st-chat.jsonl')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        imported = r.get_json()
        assert imported['id']
        assert imported['warnings'] == ['Line 3: ignored extra metadata']

        messages = client.get(f'/api/chats/{imported["id"]}/messages').get_json()
        assert [m['role'] for m in messages] == ['user', 'character']
        assert messages[0]['content'] == 'Hi'
        assert messages[1]['content'] == 'Alt B'
        assert [s['content'] for s in messages[1]['swipes']] == ['Alt A', 'Alt B']

    def test_import_rejects_invalid_jsonl(self, client, sample_character):
        r = client.post(
            f'/api/chats/import?character_id={sample_character["id"]}',
            data={'file': (BytesIO(b'{"ok": true}\nnot json'), 'bad.jsonl')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400
        assert 'Line 2' in r.get_json()['error']


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

    def test_chat_forwards_system_prompt_unchanged(self, client, monkeypatch):
        """The proxy should not replace the frontend-built system message."""
        import routes.llm as llm_module

        captured = {}

        class UpstreamResponse:
            encoding = 'utf-8'
            ok = True
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
                yield 'data: [DONE]'

        def fake_post(url, json, headers, timeout, stream=False):
            captured['url'] = url
            captured['json'] = json
            captured['headers'] = headers
            captured['stream'] = stream
            return UpstreamResponse()

        monkeypatch.setattr(llm_module.http_requests, 'post', fake_post)
        client.put('/api/settings', json={
            'api_endpoint': 'http://upstream.test/v1',
            'api_model': 'test-model',
            'api_key': 'sk-test-key',
        })
        custom_system = 'CUSTOM SYSTEM PROMPT: stay in character.'

        r = client.post('/api/llm/chat', json={
            'model': 'test-model',
            'messages': [
                {'role': 'system', 'content': custom_system},
                {'role': 'user', 'content': 'hi'},
            ],
        })

        assert r.content_type.startswith('text/event-stream')
        assert 'data: [DONE]' in r.get_data(as_text=True)
        assert captured['url'] == 'http://upstream.test/v1/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer sk-test-key'
        assert captured['stream'] is True
        assert captured['json']['stream'] is True
        assert captured['json']['messages'][0] == {
            'role': 'system',
            'content': custom_system,
        }

    def test_chat_upstream_error_body_reaches_client(self, client, monkeypatch):
        """A 4xx from the provider must surface its response body, not just the status."""
        import routes.llm as llm_module

        class ErrorResponse:
            encoding = 'utf-8'
            ok = False
            status_code = 400
            reason = 'Bad Request'
            text = '{"error": {"message": "model not found: aion-2.0"}}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(llm_module.http_requests, 'post',
                            lambda *a, **kw: ErrorResponse())
        client.put('/api/settings', json={
            'api_endpoint': 'http://upstream.test/v1',
            'api_model': 'test-model',
        })
        r = client.post('/api/llm/chat', json={
            'model': 'test-model',
            'messages': [{'role': 'user', 'content': 'hi'}],
        })
        body = r.get_data(as_text=True)
        assert '400' in body
        assert 'model not found: aion-2.0' in body

    def test_models_endpoint_requires_endpoint(self, client):
        r = client.get('/api/llm/models')
        assert r.status_code == 400
        assert 'endpoint' in r.get_json()['error'].lower()

    def test_models_endpoint_accepts_models_key(self, client, monkeypatch):
        """Some providers (aionlabs) return {"models": [...]} instead of {"data": [...]}."""
        import routes.llm as llm_module

        class ModelsResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {'models': [
                    {'id': 'aion-labs/aion-2.0', 'context_length': 131072},
                    {'id': 'aion-labs/aion-rp-llama-3.1-8b', 'context_length': 32768},
                ]}

        monkeypatch.setattr(llm_module.http_requests, 'get',
                            lambda *a, **kw: ModelsResponse())
        client.put('/api/settings', json={'api_endpoint': 'http://upstream.test/v1'})
        r = client.get('/api/llm/models')
        body = r.get_json()
        assert body['ok'] is True
        assert body['models'] == ['aion-labs/aion-2.0', 'aion-labs/aion-rp-llama-3.1-8b']
        assert body['model_details']['aion-labs/aion-2.0'] == 131072

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


class TestFork:
    def test_fork_copies_messages_and_swipes(self, client, sample_chat):
        chat_id = sample_chat['id']
        m1 = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user', 'content': 'Hello',
        }).get_json()
        m2 = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'Hi there!',
        }).get_json()
        m3 = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user', 'content': 'How are you?',
        }).get_json()
        client.post(f'/api/messages/{m2["id"]}/swipes', json={'content': 'Hey!'})

        r = client.post(f'/api/chats/{chat_id}/fork?message_id={m2["id"]}')
        assert r.status_code == 201
        new_chat = r.get_json()
        assert new_chat['character_id'] == sample_chat['character_id']
        assert new_chat['active_lorebook_embedded'] == sample_chat['active_lorebook_embedded']

        msgs = client.get(f'/api/chats/{new_chat["id"]}/messages').get_json()
        assert len(msgs) == 2
        assert msgs[0]['content'] == 'Hello'
        assert msgs[1]['content'] == 'Hey!'
        assert len(msgs[1]['swipes']) == 2

    def test_fork_on_last_message(self, client, sample_chat):
        chat_id = sample_chat['id']
        m1 = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'user', 'content': 'A',
        }).get_json()
        m2 = client.post(f'/api/chats/{chat_id}/messages', json={
            'role': 'character', 'content': 'B',
        }).get_json()

        r = client.post(f'/api/chats/{chat_id}/fork?message_id={m2["id"]}')
        assert r.status_code == 201
        new_chat = r.get_json()
        msgs = client.get(f'/api/chats/{new_chat["id"]}/messages').get_json()
        assert len(msgs) == 2

    def test_fork_requires_message_id(self, client, sample_chat):
        r = client.post(f'/api/chats/{sample_chat["id"]}/fork')
        assert r.status_code == 400

    def test_fork_unknown_chat_404(self, client):
        r = client.post('/api/chats/99999/fork?message_id=1')
        assert r.status_code == 404

    def test_fork_unknown_message_404(self, client, sample_chat):
        r = client.post(f'/api/chats/{sample_chat["id"]}/fork?message_id=99999')
        assert r.status_code == 404

    def test_fork_message_from_wrong_chat_404(self, client, sample_character):
        chat_a = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'A'}).get_json()
        chat_b = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'B'}).get_json()
        msg_in_b = client.post(f'/api/chats/{chat_b["id"]}/messages', json={
            'role': 'user', 'content': 'in B',
        }).get_json()

        r = client.post(f'/api/chats/{chat_a["id"]}/fork?message_id={msg_in_b["id"]}')
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


class TestSettingsWhitelist:
    def test_unknown_key_is_ignored(self, client):
        r = client.put('/api/settings', json={'evil_injection': 'hacked'})
        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert 'evil_injection' not in s

    def test_legacy_context_max_messages_not_reintroduced(self, client):
        r = client.put('/api/settings', json={'context_max_messages': '50'})
        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert 'context_max_messages' not in s


class TestChatRename:
    def test_rename_chat(self, client, sample_chat):
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={'name': 'Renamed Chat'})
        assert r.status_code == 200
        assert r.get_json()['name'] == 'Renamed Chat'
        r2 = client.get(f'/api/characters/{sample_chat["character_id"]}/chats')
        chat = next(c for c in r2.get_json() if c['id'] == sample_chat['id'])
        assert chat['name'] == 'Renamed Chat'

    def test_rename_empty_name_keeps_existing(self, client, sample_chat):
        r = client.put(f'/api/chats/{sample_chat["id"]}', json={'name': ''})
        assert r.status_code == 200
        assert r.get_json()['name'] == sample_chat['name']


class TestGetSingleCharacter:
    def test_get_character_by_id(self, client, sample_character):
        r = client.get(f'/api/characters/{sample_character["id"]}')
        assert r.status_code == 200
        data = r.get_json()
        assert data['id'] == sample_character['id']
        assert data['name'] == 'TestChar'
        assert data['description'] == 'A brave test character.'

    def test_get_character_404(self, client):
        r = client.get('/api/characters/99999')
        assert r.status_code == 404
        assert r.get_json()['error'] == 'Character not found'


class TestListChatsForCharacter:
    def test_list_chats(self, client, sample_character, sample_chat):
        r = client.get(f'/api/characters/{sample_character["id"]}/chats')
        assert r.status_code == 200
        chats = r.get_json()
        assert len(chats) >= 1
        assert any(c['id'] == sample_chat['id'] for c in chats)

    def test_list_chats_unknown_character_404(self, client):
        r = client.get('/api/characters/99999/chats')
        assert r.status_code == 404


class TestCharacterBasicFieldUpdate:
    def test_update_multiple_fields(self, client, sample_character):
        r = client.put(f'/api/characters/{sample_character["id"]}', json={
            'name': 'Updated',
            'description': 'New description',
            'personality': 'Revised personality',
            'scenario': 'New scenario',
            'first_mes': 'New first message',
        })
        assert r.status_code == 200
        char = client.get(f'/api/characters/{sample_character["id"]}').get_json()
        assert char['name'] == 'Updated'
        assert char['description'] == 'New description'
        assert char['personality'] == 'Revised personality'
        assert char['scenario'] == 'New scenario'
        assert char['first_mes'] == 'New first message'


class TestThemePrecedence:
    def test_user_theme_shadows_builtin(self, client, monkeypatch):
        builtin_dir = os.path.join(os.path.dirname(__file__), '..', 'static', 'themes')
        if not os.path.isdir(builtin_dir):
            return
        builtin_files = [f for f in os.listdir(builtin_dir) if f.endswith('.css') and not f.startswith('.')]
        if not builtin_files:
            return
        shadow_name = builtin_files[0]
        user_content = '/* user override */ :root { --shadow-test: 1; }'
        path = os.path.join(shared.THEMES_DIR, shadow_name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(user_content)
        r = client.get(f'/themes/{shadow_name}')
        assert r.status_code == 200
        assert b'shadow-test' in r.data

    def test_builtin_fallback_when_no_user_theme(self, client):
        builtin_dir = shared.BUILTIN_THEMES_DIR
        if not os.path.isdir(builtin_dir):
            return
        builtin_files = [f for f in os.listdir(builtin_dir) if f.endswith('.css') and not f.startswith('.')]
        if not builtin_files:
            return
        r = client.get(f'/themes/{builtin_files[0]}')
        assert r.status_code == 200
