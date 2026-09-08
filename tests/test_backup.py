import io
import json
import os
import tempfile
import zipfile

import app as app_module
from cozy import shared
from cozy.png_utils import make_minimal_png
from cozy.routes.settings import BACKUP_MANIFEST
from cozy.schema import MIGRATIONS


def create_character(client, name):
    r = client.post('/api/characters', data={
        'data': json.dumps({'name': name}),
        'image': (io.BytesIO(make_minimal_png()), f'{name}.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def download_backup(client):
    r = client.get('/api/backup')
    assert r.status_code == 200
    return zipfile.ZipFile(io.BytesIO(r.data))


def restore(client, data, filename='backup.zip'):
    return client.post(
        '/api/backup/restore',
        data={'file': (io.BytesIO(data), filename)},
        content_type='multipart/form-data',
    )


def make_backup_bytes(members, manifest=None):
    """Build an archive by hand, so a test can bend one part of a real one."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        if manifest is not None:
            archive.writestr(BACKUP_MANIFEST, json.dumps(manifest))
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TestBackupDownload:
    def test_backup_holds_manifest_database_and_data_files(self, client):
        create_character(client, 'Backed Up')
        with open(os.path.join(shared.THEMES_DIR, 'mine.css'), 'w', encoding='utf-8') as handle:
            handle.write('body { color: red; }')

        with download_backup(client) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read(BACKUP_MANIFEST))
            database = archive.read(os.path.basename(shared.DATABASE))

        assert manifest['app'] == 'cozy'
        assert manifest['schema_version'] == MIGRATIONS[-1][0]
        assert database.startswith(b'SQLite format 3')
        assert 'themes/mine.css' in names
        assert any(name.startswith('characters/') for name in names)

    def test_the_download_is_not_resumable(self, client):
        """Every request builds a different archive, so a client must not be
        invited to fetch it in pieces: Chrome splits a download of a few
        megabytes across parallel range requests, and stitching two archives
        together produces a zip that fails its own CRCs."""
        r = client.get('/api/backup')
        assert r.headers['Accept-Ranges'] == 'none'
        assert 'ETag' not in r.headers
        assert r.headers['Cache-Control'] == 'no-store'

        ranged = client.get('/api/backup', headers={'Range': 'bytes=0-99'})
        assert ranged.status_code == 200
        assert 'Content-Range' not in ranged.headers
        assert len(ranged.data) == int(ranged.headers['Content-Length'])

    def test_the_temporary_archive_does_not_outlive_the_response(self, client):
        """The zip is built on disk before it is streamed. If the copy is left
        behind, every backup of a large library strands another one in the
        system temp directory."""
        temp_root = tempfile.gettempdir()
        before = set(os.listdir(temp_root))
        client.get('/api/backup')
        leaked = [
            name for name in set(os.listdir(temp_root)) - before
            if name.startswith('cozy-backup-')
        ]
        assert leaked == []

    def test_thumbnail_cache_is_left_out(self, client):
        with open(os.path.join(shared.THUMBS_DIR, 'cached.webp'), 'wb') as handle:
            handle.write(b'not really a thumbnail')
        with download_backup(client) as archive:
            assert not [n for n in archive.namelist() if n.startswith('thumbs/')]


class TestBackupRestore:
    def test_restore_replaces_the_data_directory(self, client):
        create_character(client, 'In The Backup')
        backup = client.get('/api/backup').data

        # Everything below is created after the snapshot, so a restore must
        # take it all away again.
        create_character(client, 'Added Later')
        stray = os.path.join(shared.THEMES_DIR, 'later.css')
        with open(stray, 'w', encoding='utf-8') as handle:
            handle.write('body {}')

        r = restore(client, backup)
        assert r.status_code == 200

        names = [c['name'] for c in client.get('/api/characters').get_json()]
        assert names == ['In The Backup']
        assert not os.path.exists(stray)
        # The standard directories exist again even though nothing filled them.
        for directory in (shared.CHARACTERS_DIR, shared.PERSONAS_DIR,
                          shared.THEMES_DIR, shared.THUMBS_DIR):
            assert os.path.isdir(directory)

    def test_restore_rejects_a_newer_schema_version(self, client):
        body = make_backup_bytes(
            {os.path.basename(shared.DATABASE): b'SQLite format 3\x00'},
            manifest={'app': 'cozy', 'schema_version': MIGRATIONS[-1][0] + 1},
        )
        r = restore(client, body)
        assert r.status_code == 409
        assert 'newer version of Cozy' in r.get_json()['error']

    def test_restore_rejects_files_that_are_not_backups(self, client):
        assert restore(client, b'this is not a zip').status_code == 400
        no_manifest = make_backup_bytes({'cozy_chat.db': b'SQLite format 3\x00'})
        assert restore(client, no_manifest).status_code == 400
        no_database = make_backup_bytes(
            {'themes/mine.css': b'body {}'},
            manifest={'app': 'cozy', 'schema_version': 1},
        )
        assert restore(client, no_database).status_code == 400

    def test_restore_refuses_paths_that_escape_the_data_directory(self, client):
        body = make_backup_bytes(
            {
                os.path.basename(shared.DATABASE): b'SQLite format 3\x00',
                '../escaped.txt': b'nope',
            },
            manifest={'app': 'cozy', 'schema_version': 1},
        )
        r = restore(client, body)
        assert r.status_code == 400
        assert not os.path.exists(os.path.join(os.path.dirname(shared.DATA_DIR), 'escaped.txt'))

    def test_an_unreadable_database_is_refused_before_anything_is_deleted(self, client):
        """The check that matters: a broken archive must not cost the user the
        data they already have."""
        create_character(client, 'Still Here')
        body = make_backup_bytes(
            {os.path.basename(shared.DATABASE): b'not a database at all'},
            manifest={'app': 'cozy', 'schema_version': 1},
        )
        r = restore(client, body)
        assert r.status_code == 400
        names = [c['name'] for c in client.get('/api/characters').get_json()]
        assert names == ['Still Here']

    def test_a_backup_larger_than_the_upload_cap_is_accepted(self, client):
        """A backup is the whole library, so the app-wide upload cap — sized
        for one character card — must not apply to it."""
        cap = app_module.app.config['MAX_CONTENT_LENGTH']
        with zipfile.ZipFile(io.BytesIO(client.get('/api/backup').data)) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        # Random bytes so the archive on the wire really is over the cap.
        members['characters/big.png'] = os.urandom(cap + 1024)
        oversized = make_backup_bytes(
            {k: v for k, v in members.items() if k != BACKUP_MANIFEST},
            manifest=json.loads(members[BACKUP_MANIFEST]),
        )
        assert len(oversized) > cap
        assert restore(client, oversized).status_code == 200

    def test_an_older_backup_is_migrated_and_reseeded(self, client):
        """A restore runs init_db(), so a database taken at an older schema
        version comes back recorded at this build's version."""
        backup = client.get('/api/backup').data
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            members = {
                name: archive.read(name)
                for name in archive.namelist() if name != BACKUP_MANIFEST
            }
        older = make_backup_bytes(members, manifest={'app': 'cozy', 'schema_version': 1})

        assert restore(client, older).status_code == 200
        with shared.get_db() as conn:
            version = conn.execute(
                'SELECT MAX(version) AS version FROM schema_migrations'
            ).fetchone()['version']
        assert version == MIGRATIONS[-1][0]
        assert client.get('/api/system-prompts').get_json()
