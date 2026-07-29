import json
from io import BytesIO

import shared


def make_preset(client, name='Filters', filters=None):
    r = client.post('/api/regex-presets', json={
        'name': name,
        'filters': filters if filters is not None else [],
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def upload(client, payload, filename='preset.json'):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return client.post('/api/regex-presets/import', data={
        'file': (BytesIO(body.encode('utf-8')), filename),
    }, content_type='multipart/form-data')


# ── CRUD ──────────────────────────────────────────────────────────────────

def test_create_and_list_preset(client):
    created = make_preset(client, 'German punctuation', [
        {'name': 'Quotes', 'find': '„([^"]*)"', 'replace': '"$1"', 'flags': 'g'},
    ])
    assert created['name'] == 'German punctuation'
    assert created['filters'] == [
        {'name': 'Quotes', 'find': '„([^"]*)"', 'replace': '"$1"', 'flags': 'g'},
    ]

    listed = client.get('/api/regex-presets').get_json()
    assert [p['id'] for p in listed] == [created['id']]


def test_create_requires_name(client):
    assert client.post('/api/regex-presets', json={'filters': []}).status_code == 400


def test_duplicate_name_conflicts(client):
    make_preset(client, 'Dupe')
    assert client.post('/api/regex-presets', json={'name': 'Dupe'}).status_code == 409


def test_update_replaces_filter_list(client):
    preset = make_preset(client, 'P', [{'name': 'a', 'find': 'x', 'replace': 'y', 'flags': 'g'}])
    r = client.put(f'/api/regex-presets/{preset["id"]}', json={
        'filters': [{'name': 'b', 'find': 'q', 'replace': 'r', 'flags': 'gi'}],
    })
    assert r.status_code == 200
    assert r.get_json()['filters'] == [
        {'name': 'b', 'find': 'q', 'replace': 'r', 'flags': 'gi'},
    ]


def test_update_without_filters_key_leaves_them_alone(client):
    preset = make_preset(client, 'P', [{'name': 'a', 'find': 'x', 'replace': 'y', 'flags': 'g'}])
    r = client.put(f'/api/regex-presets/{preset["id"]}', json={'name': 'Renamed'})
    assert r.status_code == 200
    assert r.get_json()['name'] == 'Renamed'
    assert len(r.get_json()['filters']) == 1


def test_update_with_empty_list_clears_them(client):
    preset = make_preset(client, 'P', [{'name': 'a', 'find': 'x', 'replace': 'y', 'flags': 'g'}])
    r = client.put(f'/api/regex-presets/{preset["id"]}', json={'filters': []})
    assert r.get_json()['filters'] == []


def test_delete_clears_the_active_setting(client):
    preset = make_preset(client, 'P')
    client.put('/api/settings', json={'active_regex_preset': str(preset['id'])})
    assert client.get('/api/settings').get_json()['active_regex_preset'] == str(preset['id'])

    assert client.delete(f'/api/regex-presets/{preset["id"]}').status_code == 200
    # Otherwise the frontend would keep pointing at a preset that no longer exists.
    assert client.get('/api/settings').get_json()['active_regex_preset'] == ''


def test_delete_leaves_a_different_active_preset_alone(client):
    keep = make_preset(client, 'Keep')
    drop = make_preset(client, 'Drop')
    client.put('/api/settings', json={'active_regex_preset': str(keep['id'])})
    client.delete(f'/api/regex-presets/{drop["id"]}')
    assert client.get('/api/settings').get_json()['active_regex_preset'] == str(keep['id'])


def test_missing_preset_is_404(client):
    assert client.put('/api/regex-presets/999', json={}).status_code == 404
    assert client.delete('/api/regex-presets/999').status_code == 404
    assert client.get('/api/regex-presets/999/export').status_code == 404


# ── Normalisation ─────────────────────────────────────────────────────────

def test_whitespace_in_find_and_replace_is_preserved(client):
    """A rule collapsing double spaces is only expressible if a lone space survives."""
    preset = make_preset(client, 'Spaces', [
        {'name': ' Trim ', 'find': ' {2,}', 'replace': ' ', 'flags': 'g'},
    ])
    only = preset['filters'][0]
    assert only['find'] == ' {2,}'
    assert only['replace'] == ' '
    assert only['name'] == 'Trim'  # the name is the one field that is trimmed


def test_unknown_flags_are_dropped_and_duplicates_collapsed(client):
    preset = make_preset(client, 'Flags', [
        {'name': 'f', 'find': 'x', 'replace': '', 'flags': 'ggiZ!'},
    ])
    # 'Z' and '!' would make new RegExp() throw in the browser.
    assert preset['filters'][0]['flags'] == 'gi'


def test_non_dict_filter_entries_are_discarded(client):
    preset = make_preset(client, 'Junk', ['nope', 42, None,
                                          {'name': 'ok', 'find': 'x', 'replace': '', 'flags': ''}])
    assert [f['name'] for f in preset['filters']] == ['ok']


def test_unknown_keys_are_stripped(client):
    preset = make_preset(client, 'Extra', [
        {'name': 'f', 'find': 'x', 'replace': 'y', 'flags': 'g', 'placement': [1], 'disabled': True},
    ])
    assert set(preset['filters'][0]) == {'name', 'find', 'replace', 'flags'}


def test_corrupt_scripts_json_degrades_to_empty(client):
    preset = make_preset(client, 'Corrupt')
    with shared.get_db() as conn:
        conn.execute('UPDATE regex_presets SET scripts_json = ? WHERE id = ?',
                     ('{not json at all', preset['id']))

    r = client.get('/api/regex-presets')
    assert r.status_code == 200
    assert r.get_json()[0]['filters'] == []


# ── Import / export ───────────────────────────────────────────────────────

def test_export_round_trips_through_import(client):
    filters = [
        {'name': 'Quotes', 'find': '„([^"]*)"', 'replace': '"$1"', 'flags': 'g'},
        {'name': 'Blank lines', 'find': '\\n{3,}', 'replace': '\\n\\n', 'flags': 'g'},
    ]
    preset = make_preset(client, 'Round trip', filters)

    exported = client.get(f'/api/regex-presets/{preset["id"]}/export')
    assert exported.status_code == 200
    assert 'attachment' in exported.headers['Content-Disposition']
    body = json.loads(exported.get_data(as_text=True))
    assert body == {'name': 'Round trip', 'filters': filters}

    r = upload(client, body)
    assert r.status_code == 201
    reimported = r.get_json()
    assert reimported['filters'] == filters
    # The original is still there, so the copy gets a free name.
    assert reimported['name'] == 'Round trip (2)'


def test_import_rejects_invalid_json(client):
    r = upload(client, 'definitely { not json')
    assert r.status_code == 400
    assert 'Invalid JSON' in r.get_json()['error']


def test_import_requires_a_file(client):
    assert client.post('/api/regex-presets/import').status_code == 400


def test_import_rejects_a_payload_with_no_filters(client):
    assert upload(client, {'name': 'Empty', 'filters': []}).status_code == 400


# ── SillyTavern interop ───────────────────────────────────────────────────

def test_import_sillytavern_script(client):
    """ST stores the pattern as /…/flags and names the fields differently."""
    r = upload(client, {
        'id': 'b2c1-uuid',
        'scriptName': 'Straighten quotes',
        'findRegex': '/„([^"]*)"/g',
        'replaceString': '"$1"',
        'trimStrings': [],
        'placement': [2],
        'disabled': False,
        'markdownOnly': False,
        'promptOnly': False,
        'runOnEdit': True,
        'substituteRegex': 0,
        'minDepth': None,
        'maxDepth': None,
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body['name'] == 'Straighten quotes'
    assert body['filters'] == [
        {'name': 'Straighten quotes', 'find': '„([^"]*)"', 'replace': '"$1"', 'flags': 'g'},
    ]
    # placement [2] is AI output, which is what Cozy does anyway.
    assert body['warnings'] == []


def test_import_converts_a_multiline_sillytavern_replacement(client):
    """ST writes a real line break; Cozy's single-line Replace box spells it \\n.

    Left as-is the newline reached an `<input>`, which drops it, so the first
    edit to the imported preset silently collapsed the replacement onto one line.
    """
    r = upload(client, {
        'scriptName': 'Block quote',
        'findRegex': '/^(OOC:.*)$/gm',
        'replaceString': '\n> $1\n',
        'placement': [2],
    })
    assert r.status_code == 201
    assert r.get_json()['filters'] == [
        {'name': 'Block quote', 'find': '^(OOC:.*)$', 'replace': '\\n> $1\\n', 'flags': 'gm'},
    ]


def test_import_keeps_a_literal_backslash_in_a_replacement(client):
    """Escaping the newline must not turn an ST `\\d` into a control character."""
    r = upload(client, {
        'scriptName': 'Literal',
        'findRegex': '/x/g',
        'replaceString': 'C:\\new',
        'placement': [2],
    })
    assert r.status_code == 201
    # Doubled, so the Replace box's own expansion gives back `C:\new` unchanged
    # rather than reading `\n` as a line break.
    assert r.get_json()['filters'][0]['replace'] == 'C:\\\\new'


def test_import_sillytavern_script_targeting_user_input_warns(client):
    r = upload(client, {
        'scriptName': 'User only',
        'findRegex': '/foo/g',
        'replaceString': 'bar',
        'placement': [1],
    })
    assert r.status_code == 201
    warnings = r.get_json()['warnings']
    assert len(warnings) == 1
    assert 'User only' in warnings[0]


def test_import_skips_disabled_sillytavern_scripts(client):
    r = upload(client, [
        {
            'scriptName': 'Disabled',
            'findRegex': '/secret/g',
            'replaceString': 'visible',
            'disabled': True,
        },
        {
            'scriptName': 'Enabled',
            'findRegex': '/cat/g',
            'replaceString': 'dog',
            'disabled': False,
        },
    ])
    assert r.status_code == 201
    body = r.get_json()
    assert [f['name'] for f in body['filters']] == ['Enabled']
    assert len(body['warnings']) == 1
    assert 'Disabled' in body['warnings'][0]
    assert 'skipped' in body['warnings'][0]


def test_import_rejects_an_all_disabled_sillytavern_payload(client):
    r = upload(client, {
        'scriptName': 'Off',
        'findRegex': '/cat/g',
        'replaceString': 'dog',
        'disabled': True,
    })
    assert r.status_code == 400
    body = r.get_json()
    assert 'No enabled regex filters' in body['error']
    assert 'Off' in body['warnings'][0]
    assert client.get('/api/regex-presets').get_json() == []


def test_import_a_list_of_sillytavern_scripts(client):
    r = upload(client, [
        {'scriptName': 'One', 'findRegex': '/a/g', 'replaceString': 'b'},
        {'scriptName': 'Two', 'findRegex': '/c/gi', 'replaceString': 'd'},
    ])
    assert r.status_code == 201
    body = r.get_json()
    assert [f['name'] for f in body['filters']] == ['One', 'Two']
    assert [f['flags'] for f in body['filters']] == ['g', 'gi']
    assert body['name'] == 'Imported Filters'


def test_import_sillytavern_pattern_without_slash_form(client):
    """A bare pattern must stay literal rather than losing its first character."""
    r = upload(client, {'scriptName': 'Bare', 'findRegex': 'plain', 'replaceString': 'x'})
    assert r.get_json()['filters'][0] == {
        'name': 'Bare', 'find': 'plain', 'replace': 'x', 'flags': '',
    }


def test_import_pattern_that_merely_starts_with_a_slash(client):
    """`/me waves` is a literal pattern, not the /…/flags form."""
    r = upload(client, {'scriptName': 'Emote', 'findRegex': '/me waves', 'replaceString': ''})
    assert r.get_json()['filters'][0]['find'] == '/me waves'
