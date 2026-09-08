import json
import os
import shutil
import subprocess

import pytest

from cozy import shared


def bundled_prompt_filenames():
    """Every bundled prompt preset file, the way the seeder sees them.

    Tests read the directory rather than naming presets, because the directory
    is the source of truth: a preset is added by dropping a file in and removed
    by deleting one, so any test naming a title breaks the next time the house
    presets change.
    """
    return sorted(
        f for f in os.listdir(shared.BUNDLED_PROMPTS_DIR)
        if f.lower().endswith('.json') and not f.startswith('.')
    )


def bundled_prompt_titles():
    """Bundled preset titles — the filename minus .json, as the seeder names them."""
    return sorted(f[:-len('.json')] for f in bundled_prompt_filenames())


def read_bundled_prompt(filename):
    path = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def run_node_module(code):
    """Run *code* as an ES module under bare node, from the repo root.

    Skips rather than fails when node isn't on PATH, so a green run on a
    machine without Node isn't quietly covering none of the frontend modules —
    check for skips before trusting a pass.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def v2_card(name='Char', **fields):
    data = {
        'name': name,
        'description': fields.get('description', ''),
        'personality': fields.get('personality', ''),
        'scenario': fields.get('scenario', ''),
        'first_mes': fields.get('first_mes', ''),
        'mes_example': fields.get('mes_example', ''),
        'creator_notes': fields.get('creator_notes', ''),
        'system_prompt': fields.get('system_prompt', ''),
        'post_history_instructions': fields.get('post_history_instructions', ''),
        'alternate_greetings': fields.get('alternate_greetings', []),
        'character_book': fields.get('character_book'),
        'tags': fields.get('tags', []),
        'creator': fields.get('creator', ''),
        'character_version': fields.get('character_version', ''),
        'extensions': fields.get('extensions', {}),
    }
    return {'spec': 'chara_card_v2', 'spec_version': '2.0', 'data': data}
