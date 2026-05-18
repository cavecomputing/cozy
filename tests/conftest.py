import os
import sys
import tempfile
import pytest

# Make test helpers importable (e.g. `from helpers import v2_card`)
sys.path.insert(0, os.path.dirname(__file__))

# Importing app.py initializes the database. Force that import-time work into
# a temp data directory so pytest never touches a production checkout's data/.
os.environ.setdefault('COZY_DATA_DIR', tempfile.mkdtemp(prefix='cozy-test-import-'))

import app as app_module
import shared
from png_utils import make_minimal_png


@pytest.fixture(autouse=True)
def _test_db(tmp_path, monkeypatch):
    """Use a fresh temp database, characters dir, and personas dir for every test.

    `app.py` does `from shared import CHARACTERS_DIR, PERSONAS_DIR` at import
    time, so its static-file routes capture the original paths in their closure.
    Monkeypatching `shared.X` doesn't reach those — we have to patch
    `app_module.X` too. Routes under `routes/*.py` use `shared.X` directly and
    only need the `shared` patch.
    """
    db_path = str(tmp_path / 'test.db')
    chars_dir = str(tmp_path / 'characters')
    personas_dir = str(tmp_path / 'personas')
    themes_dir = str(tmp_path / 'themes')
    for d in (chars_dir, personas_dir, themes_dir):
        os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(shared, 'DATABASE', db_path)
    monkeypatch.setattr(shared, 'CHARACTERS_DIR', chars_dir)
    monkeypatch.setattr(shared, 'PERSONAS_DIR', personas_dir)
    monkeypatch.setattr(shared, 'THEMES_DIR', themes_dir)
    monkeypatch.setattr(app_module, 'CHARACTERS_DIR', chars_dir)
    monkeypatch.setattr(app_module, 'PERSONAS_DIR', personas_dir)
    monkeypatch.setattr(app_module, 'THEMES_DIR', themes_dir)
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
    import json
    from io import BytesIO
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
