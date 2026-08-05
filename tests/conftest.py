import json
import os
import sys
import tempfile
from io import BytesIO

import pytest

# Make test helpers importable (e.g. `from helpers import v2_card`)
sys.path.insert(0, os.path.dirname(__file__))

# Importing app.py initializes the database. Force that import-time work into
# a temp data directory so pytest never touches a production checkout's data/.
_IMPORT_DATA_DIR = tempfile.TemporaryDirectory(prefix='cozy-test-import-')
os.environ['COZY_DATA_DIR'] = _IMPORT_DATA_DIR.name

import app as app_module
import card_store
import shared
from png_utils import make_minimal_png


@pytest.fixture(scope='session', autouse=True)
def _cleanup_import_data_dir():
    yield
    _IMPORT_DATA_DIR.cleanup()


@pytest.fixture(autouse=True)
def _test_db(tmp_path, monkeypatch):
    """Use fresh temporary database and data directories for every test."""
    data_dir = str(tmp_path)
    db_path = str(tmp_path / 'test.db')
    chars_dir = str(tmp_path / 'characters')
    personas_dir = str(tmp_path / 'personas')
    themes_dir = str(tmp_path / 'themes')
    # Thumbnails are keyed by image content, and nearly every test uses the same
    # 1x1 make_minimal_png(). Without a per-test cache dir they would all share
    # one filename in the session-scoped import dir, making any "was a thumbnail
    # generated?" assertion depend on test order.
    thumbs_dir = str(tmp_path / 'thumbs')
    for d in (chars_dir, personas_dir, themes_dir, thumbs_dir):
        os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(shared, 'DATA_DIR', data_dir)
    monkeypatch.setattr(shared, 'DATABASE', db_path)
    monkeypatch.setattr(shared, 'CHARACTERS_DIR', chars_dir)
    monkeypatch.setattr(shared, 'PERSONAS_DIR', personas_dir)
    monkeypatch.setattr(shared, 'THEMES_DIR', themes_dir)
    monkeypatch.setattr(shared, 'THUMBS_DIR', thumbs_dir)
    # The CRC and card memos are module-level and outlive a single test. Each
    # test gets its own tmp_path so the keys never actually collide, but the
    # tests should not quietly depend on that.
    card_store._crc_memo.clear()
    card_store._card_memo.clear()
    shared.init_db()
    yield db_path


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def sample_character(client):
    """Create and return a test character with embedded card data."""
    png = make_minimal_png()
    char_data = json.dumps({
        'name': 'TestChar',
        'description': 'A brave test character.',
        'personality': 'Cheerful and helpful.',
        'scenario': 'A testing scenario.',
        'first_mes': 'Hello, I am {{char}}!',
        'mes_example': '<START>\n{{user}}: Hi\n{{char}}: Hello there!',
    })
    r = client.post('/api/characters', data={
        'data': char_data,
        'image': (BytesIO(png), 'test.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert r.status_code == 201
    return r.get_json()


@pytest.fixture
def sample_chat(client, sample_character):
    """Create and return a test chat for the sample character."""
    r = client.post(f'/api/characters/{sample_character["id"]}/chats', json={
        'name': 'Test Chat',
    })
    assert r.status_code == 201
    return r.get_json()


@pytest.fixture
def sample_persona(client):
    """Create and return a test persona."""
    r = client.post('/api/personas', json={
        'name': 'TestUser',
        'tagline': 'Adventurer',
        'description': 'A brave adventurer.',
    })
    assert r.status_code == 201
    return r.get_json()
