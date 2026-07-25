"""Tests for the character cards Cozy ships with and seeds on a fresh install."""

import os

import shared
from png_utils import extract_png_chara, make_minimal_png


def _seeded_flag():
    with shared.get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_characters_seeded'"
        ).fetchone()
    return row['value'] if row else None


def _bundled_filenames():
    return sorted(
        f for f in os.listdir(shared.BUNDLED_CHARACTERS_DIR)
        if f.lower().endswith('.png') and not f.startswith('.')
    )


class TestBundledCards:
    def test_bundled_dir_ships_at_least_one_card(self):
        assert os.path.isdir(shared.BUNDLED_CHARACTERS_DIR)
        assert _bundled_filenames()

    def test_bundled_cards_embed_valid_v2_data(self):
        for filename in _bundled_filenames():
            path = os.path.join(shared.BUNDLED_CHARACTERS_DIR, filename)
            with open(path, 'rb') as f:
                card = extract_png_chara(f.read())
            assert card, f'{filename} has no embedded card data'
            assert card.get('spec') == 'chara_card_v2'
            data = card.get('data', card)
            assert data.get('name')
            assert data.get('first_mes')

    def test_sasha_card_credits_its_original_author(self):
        path = os.path.join(shared.BUNDLED_CHARACTERS_DIR, 'Sasha.png')
        with open(path, 'rb') as f:
            data = extract_png_chara(f.read())['data']
        assert data['name'] == 'Sasha'
        assert data['creator'] == 'Chunchunmaru'


class TestSeeding:
    def test_fresh_install_copies_bundled_cards_into_data_dir(self, client):
        assert _seeded_flag() == '0'

        shared.seed_default_characters()

        assert _seeded_flag() == '1'
        for filename in _bundled_filenames():
            assert os.path.exists(os.path.join(shared.CHARACTERS_DIR, filename))

        # They index and behave like any other card on disk.
        names = [c['name'] for c in client.get('/api/characters').get_json()]
        assert 'Sasha' in names

    def test_seeded_card_can_be_deleted_and_stays_deleted(self, client):
        shared.seed_default_characters()
        chars = client.get('/api/characters').get_json()
        sasha = next(c for c in chars if c['name'] == 'Sasha')

        assert client.delete(f'/api/characters/{sasha["id"]}').status_code == 200
        assert not os.path.exists(os.path.join(shared.CHARACTERS_DIR, sasha['filename']))

        # A later restart must not resurrect it.
        shared.init_db()
        shared.seed_default_characters()

        assert not os.path.exists(os.path.join(shared.CHARACTERS_DIR, sasha['filename']))
        assert client.get('/api/characters').get_json() == []

    def test_seeding_runs_only_once(self, client):
        shared.seed_default_characters()
        path = os.path.join(shared.CHARACTERS_DIR, 'Sasha.png')
        os.remove(path)

        shared.seed_default_characters()

        assert not os.path.exists(path)

    def test_seeding_never_overwrites_an_existing_file(self):
        path = os.path.join(shared.CHARACTERS_DIR, 'Sasha.png')
        with open(path, 'wb') as f:
            f.write(make_minimal_png())
        before = os.path.getsize(path)

        shared.seed_default_characters()

        assert os.path.getsize(path) == before

    def test_existing_install_is_not_seeded(self, client):
        # An install predating this feature: database already on disk, no flag.
        with shared.get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key='default_characters_seeded'")

        shared.init_db()
        shared.seed_default_characters()

        assert _seeded_flag() == '1'
        assert not os.path.exists(os.path.join(shared.CHARACTERS_DIR, 'Sasha.png'))
        assert client.get('/api/characters').get_json() == []
