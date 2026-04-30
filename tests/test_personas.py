"""Tests for persona CRUD + avatar upload."""

import os
from io import BytesIO

import shared
from png_utils import make_minimal_png


class TestPersonaList:
    def test_default_persona_is_seeded(self, client):
        r = client.get('/api/personas')
        assert r.status_code == 200
        personas = r.get_json()
        assert len(personas) >= 1
        default = next(p for p in personas if p['is_default'])
        assert default['name'] == 'Default User'
        assert default['avatar_url'] is None


class TestPersonaCRUD:
    def test_create_minimum_fields(self, client):
        r = client.post('/api/personas', json={'name': 'Alex'})
        assert r.status_code == 201
        body = r.get_json()
        assert body['name'] == 'Alex'
        assert body['is_default'] == 0

    def test_create_requires_name(self, client):
        r = client.post('/api/personas', json={'tagline': 'no name'})
        assert r.status_code == 400

    def test_update_persona(self, client, sample_persona):
        r = client.put(f'/api/personas/{sample_persona["id"]}', json={
            'name': 'Renamed',
            'tagline': 'new tagline',
            'description': 'updated desc',
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['name'] == 'Renamed'
        assert body['tagline'] == 'new tagline'
        assert body['description'] == 'updated desc'

    def test_update_404(self, client):
        r = client.put('/api/personas/99999', json={'name': 'Ghost'})
        assert r.status_code == 404

    def test_delete_persona(self, client, sample_persona):
        r = client.delete(f'/api/personas/{sample_persona["id"]}')
        assert r.status_code == 200
        rows = client.get('/api/personas').get_json()
        assert all(p['id'] != sample_persona['id'] for p in rows)

    def test_cannot_delete_default_persona(self, client):
        personas = client.get('/api/personas').get_json()
        default = next(p for p in personas if p['is_default'])
        r = client.delete(f'/api/personas/{default["id"]}')
        assert r.status_code == 400

    def test_delete_404(self, client):
        r = client.delete('/api/personas/99999')
        assert r.status_code == 404


class TestPersonaAvatar:
    def test_upload_avatar_writes_file_and_sets_url(self, client, sample_persona):
        r = client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'a.png', 'image/png')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['avatar_url']
        assert body['avatar_url'].startswith('/personas/')
        # File exists in PERSONAS_DIR
        filename = body['avatar_url'].rsplit('/', 1)[-1]
        assert os.path.exists(os.path.join(shared.PERSONAS_DIR, filename))

    def test_upload_replaces_old_avatar_file(self, client, sample_persona):
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'first.png', 'image/png')},
            content_type='multipart/form-data',
        )
        # Listing PERSONAS_DIR should yield exactly one file for this persona
        files_after_first = os.listdir(shared.PERSONAS_DIR)
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'second.jpg', 'image/jpeg')},
            content_type='multipart/form-data',
        )
        files_after_second = os.listdir(shared.PERSONAS_DIR)
        # Old .png should have been deleted before the new file landed
        assert len(files_after_second) == 1
        assert files_after_second != files_after_first

    def test_upload_rejects_disallowed_format(self, client, sample_persona):
        r = client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(b'data'), 'bad.exe', 'application/octet-stream')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_upload_no_file_rejected(self, client, sample_persona):
        r = client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={}, content_type='multipart/form-data',
        )
        assert r.status_code == 400

    def test_upload_404_for_unknown_persona(self, client):
        r = client.post(
            '/api/personas/99999/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'a.png', 'image/png')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 404

    def test_delete_persona_removes_avatar_file(self, client, sample_persona):
        # Upload an avatar
        client.post(
            f'/api/personas/{sample_persona["id"]}/avatar',
            data={'avatar': (BytesIO(make_minimal_png()), 'a.png', 'image/png')},
            content_type='multipart/form-data',
        )
        files_before = os.listdir(shared.PERSONAS_DIR)
        assert len(files_before) == 1

        client.delete(f'/api/personas/{sample_persona["id"]}')
        files_after = os.listdir(shared.PERSONAS_DIR)
        assert len(files_after) == 0
