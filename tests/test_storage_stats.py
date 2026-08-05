import os

import shared
from routes import settings as settings_module


def _write_bytes(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)


def test_storage_stats_reports_durable_categories_and_separate_cache(client, tmp_path):
    database = tmp_path / 'test.db'
    _write_bytes(database, 11)
    _write_bytes(tmp_path / 'test.db-wal', 7)
    _write_bytes(tmp_path / 'test.db-shm', 5)
    _write_bytes(tmp_path / 'test.db-journal', 3)
    _write_bytes(tmp_path / 'characters' / 'nested' / 'card.png', 13)
    _write_bytes(tmp_path / 'personas' / 'avatar.webp', 17)
    _write_bytes(tmp_path / 'themes' / 'custom.css', 19)
    _write_bytes(tmp_path / 'thumbs' / 'avatar.webp', 23)
    _write_bytes(tmp_path / 'notes.txt', 29)
    _write_bytes(tmp_path / 'extras' / 'kept.bin', 31)

    response = client.get('/api/storage-stats')

    assert response.status_code == 200
    body = response.get_json()
    assert body['categories'] == {
        'database': {'bytes': 26, 'files': 4},
        'characters': {'bytes': 13, 'files': 1},
        'personas': {'bytes': 17, 'files': 1},
        'themes': {'bytes': 19, 'files': 1},
        'other': {'bytes': 60, 'files': 2},
    }
    assert body['user_data_bytes'] == 135
    assert body['cache'] == {'bytes': 23, 'files': 1}


def test_storage_stats_tolerates_missing_files_and_directories(client):
    os.remove(shared.DATABASE)
    for directory in (
        shared.CHARACTERS_DIR,
        shared.PERSONAS_DIR,
        shared.THEMES_DIR,
        shared.THUMBS_DIR,
    ):
        os.rmdir(directory)

    response = client.get('/api/storage-stats')

    assert response.status_code == 200
    assert response.get_json() == {
        'user_data_bytes': 0,
        'categories': {
            'database': {'bytes': 0, 'files': 0},
            'characters': {'bytes': 0, 'files': 0},
            'personas': {'bytes': 0, 'files': 0},
            'themes': {'bytes': 0, 'files': 0},
            'other': {'bytes': 0, 'files': 0},
        },
        'cache': {'bytes': 0, 'files': 0},
    }


def test_directory_stats_skips_file_that_disappears_during_stat(monkeypatch, tmp_path):
    class VanishedEntry:
        path = str(tmp_path / 'vanished.bin')

        def is_dir(self, *, follow_symlinks):
            return False

        def is_file(self, *, follow_symlinks):
            return True

        def stat(self, *, follow_symlinks):
            raise FileNotFoundError(self.path)

    class Entries:
        def __enter__(self):
            return iter([VanishedEntry()])

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(settings_module.os, 'scandir', lambda _path: Entries())

    assert settings_module._directory_stats(str(tmp_path)) == {'bytes': 0, 'files': 0}


def test_about_storage_card_is_between_acknowledgements_and_source(client):
    body = client.get('/').get_data(as_text=True)

    acknowledgements = body.index('<h4>Acknowledgements</h4>')
    storage = body.index('<h4>Storage</h4>')
    source = body.index('<h4>Source &amp; documentation</h4>')
    assert acknowledgements < storage < source
    assert 'id="about-storage-content"' in body
    assert 'aria-live="polite"' in body
