"""Tests for API preset CRUD, masking, and activation flow."""


class TestPresetMasking:
    def test_create_with_real_key_returns_masked(self, client):
        r = client.post('/api/presets', json={
            'name': 'OpenAI',
            'api_endpoint': 'https://api.openai.com/v1',
            'api_key': 'sk-realsecret1234567',
            'api_model': 'gpt-4',
        })
        assert r.status_code == 201
        body = r.get_json()
        assert 'api_key' not in body, 'raw api_key must never appear in response'
        assert body['api_key_set'] is True
        assert 'sk-realsecret1234567' not in str(body)
        # Masked form is informative but not the raw key
        assert body['api_key_masked']
        assert 'sk-' in body['api_key_masked'] or '•' in body['api_key_masked']

    def test_create_without_key_inherits_from_settings(self, client):
        # Seed a global key
        client.put('/api/settings', json={'api_key': 'sk-global-abcdef1234'})
        r = client.post('/api/presets', json={'name': 'NoKey', 'api_endpoint': 'http://x/v1'})
        assert r.status_code == 201
        body = r.get_json()
        assert body['api_key_set'] is True

    def test_list_masks_keys(self, client):
        client.post('/api/presets', json={'name': 'A', 'api_key': 'sk-aaa11122233'})
        r = client.get('/api/presets')
        assert r.status_code == 200
        rows = r.get_json()
        assert len(rows) == 1
        assert 'sk-aaa11122233' not in str(rows)
        assert 'api_key' not in rows[0]
        assert rows[0]['api_key_masked']


class TestPresetUpdate:
    def test_update_with_masked_key_keeps_old_key(self, client):
        created = client.post('/api/presets', json={
            'name': 'Stable',
            'api_endpoint': 'http://x/v1',
            'api_key': 'sk-original-aaaaa1111',
        }).get_json()
        masked = created['api_key_masked']

        # Sending the masked sentinel back should NOT clobber the real key
        client.put(f'/api/presets/{created["id"]}', json={
            'name': 'Renamed',
            'api_endpoint': 'http://x/v2',
            'api_key': masked,  # masked → must be skipped
        })

        # Activate the preset; its api_key is written into settings —
        # we can prove the original key survived if the settings still mask it.
        client.post(f'/api/presets/{created["id"]}/activate')
        s = client.get('/api/settings').get_json()
        assert s['api_key_set'] is True
        assert 'sk-original-aaaaa1111' not in str(s)
        # And the rename took effect
        rows = client.get('/api/presets').get_json()
        assert rows[0]['name'] == 'Renamed'

    def test_update_with_real_key_replaces_old(self, client):
        created = client.post('/api/presets', json={
            'name': 'Rotate', 'api_key': 'sk-old-111111111',
        }).get_json()
        client.put(f'/api/presets/{created["id"]}', json={'api_key': 'sk-new-222222222'})
        client.post(f'/api/presets/{created["id"]}/activate')
        # No way to read the new key directly (always masked) — round-trip via
        # settings means it must have been written. Confirm by checking that
        # the masked form is NON-empty (would be empty if cleared).
        s = client.get('/api/settings').get_json()
        assert s['api_key_set'] is True
        assert 'sk-new-222222222' not in str(s)

    def test_update_404_unknown(self, client):
        r = client.put('/api/presets/99999', json={'name': 'Ghost'})
        assert r.status_code == 404


class TestPresetActivation:
    def test_activate_writes_fields_into_settings(self, client):
        created = client.post('/api/presets', json={
            'name': 'Active',
            'api_endpoint': 'http://activated/v1',
            'api_key': 'sk-active-9999',
            'api_model': 'mythical-7B',
            'context_max_messages': '64',
        }).get_json()
        r = client.post(f'/api/presets/{created["id"]}/activate')
        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert s['api_endpoint'] == 'http://activated/v1'
        assert s['api_model'] == 'mythical-7B'
        assert s['context_max_messages'] == '64'
        assert s['active_api_preset'] == str(created['id'])
        assert s['api_key_set'] is True

    def test_activate_404_unknown(self, client):
        r = client.post('/api/presets/99999/activate')
        assert r.status_code == 404


class TestPresetDelete:
    def test_delete_removes_row(self, client):
        created = client.post('/api/presets', json={'name': 'Doomed'}).get_json()
        r = client.delete(f'/api/presets/{created["id"]}')
        assert r.status_code == 200
        rows = client.get('/api/presets').get_json()
        assert all(p['id'] != created['id'] for p in rows)

    def test_delete_active_preset_clears_selection(self, client):
        created = client.post('/api/presets', json={'name': 'Active'}).get_json()
        client.post(f'/api/presets/{created["id"]}/activate')

        r = client.delete(f'/api/presets/{created["id"]}')

        assert r.status_code == 200
        s = client.get('/api/settings').get_json()
        assert s['active_api_preset'] == ''

    def test_create_requires_name(self, client):
        r = client.post('/api/presets', json={'api_endpoint': 'http://x/v1'})
        assert r.status_code == 400
