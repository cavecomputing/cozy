"""COZY_PASSWORD: the sign-in page, and the guard in front of every other route.

app.py installs the guard while it is imported, and only when the password is
set, so these tests load app.py again as a fresh module with the password in
place (the wiring a real start runs) instead of using the password-less app the
rest of the suite shares.
"""

import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
import time
import types
import zipfile

import pytest
from flask import Flask
from flask.sessions import SecureCookieSessionInterface

import app as app_module
from cozy import auth, shared
from cozy.png_utils import make_minimal_png

PASSWORD = 'correct horse battery staple'


def load_app(monkeypatch, password=PASSWORD):
    """app.py imported again with COZY_PASSWORD set to *password*."""
    monkeypatch.setattr(auth, 'PASSWORD', password)
    spec = importlib.util.spec_from_file_location('app_with_password', app_module.__file__)
    module = importlib.util.module_from_spec(spec)
    # Flask finds templates/ and static/ through the module's file.
    monkeypatch.setitem(sys.modules, 'app_with_password', module)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    return module.app


@pytest.fixture(autouse=True)
def no_wrong_password_delay(monkeypatch):
    # A stand-in for the time module, so the tests that stop the clock stop
    # only auth's.
    monkeypatch.setattr(auth, 'time', types.SimpleNamespace(sleep=lambda seconds: None, monotonic=time.monotonic))
    monkeypatch.setattr(auth, 'next_try', 0.0)


@pytest.fixture
def locked(monkeypatch):
    return load_app(monkeypatch)


@pytest.fixture
def anon(locked):
    """A client that has not signed in."""
    return locked.test_client()


@pytest.fixture
def signed_in(locked):
    client = locked.test_client()
    assert client.post('/login', data={'password': PASSWORD}).status_code == 302
    return client


def session_cookie(response):
    return next(h for h in response.headers.getlist('Set-Cookie') if h.startswith('cozy_session='))


def forge(key, data=None):
    """A session cookie signed with *key*, the way Flask signs its own."""
    fake = Flask('forge')
    fake.secret_key = key
    return SecureCookieSessionInterface().get_signing_serializer(fake).dumps(data or {'signed_in': True})


def stored_secret():
    with sqlite3.connect(shared.DATABASE) as conn:
        return conn.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()[0]


def example_path(rule):
    """The rule's URL with every variable filled in."""
    return re.sub(r'<(?:(\w+):)?\w+>', lambda m: '1' if m.group(1) == 'int' else 'x.png', rule.rule)


def assert_refused(response, path):
    if path.startswith('/api/'):
        assert response.status_code == 401, path
        if response.data:  # HEAD has no body
            assert response.get_json() == {'error': 'Sign in first'}, path
    else:
        assert response.status_code == 302, path
        assert response.headers['Location'].startswith('/login?next='), path


# ── Without a password ──────────────────────────────────────────────────────

def test_without_a_password_nothing_changes(client):
    page = client.get('/')
    assert page.status_code == 200
    assert b'Sign out' not in page.data and b'pageshow' not in page.data
    assert 'Content-Security-Policy' not in page.headers
    assert client.get('/api/settings').status_code == 200
    assert client.get('/login').status_code == 404
    assert 'session' not in page.headers.get('Set-Cookie', '')


# ── What a stranger can reach ───────────────────────────────────────────────

def test_every_route_needs_a_session(locked, anon):
    routes = locked.url_map.bind('localhost')
    swept = set()
    for rule in locked.url_map.iter_rules():
        if rule.endpoint in auth.OPEN_ENDPOINTS:
            continue
        path = example_path(rule)
        for method in sorted(rule.methods):
            # The example has to reach this rule, or the sweep is only testing a 404.
            assert routes.match(path, method=method, return_rule=True)[0].rule == rule.rule, (method, path)
            assert_refused(anon.open(path, method=method), path)
        swept.add(rule.rule)
    # So a sweep that found nothing can't pass.
    assert {
        '/', '/logout', '/api/settings', '/api/backup', '/api/backup/restore', '/api/llm/chat',
        '/characters/<path:filename>', '/thumbs/characters/<int:size>/<path:filename>',
    } <= swept


def test_only_sign_in_static_and_theme_stylesheets_are_open(locked, anon, signed_in):
    open_rules = {rule.rule for rule in locked.url_map.iter_rules() if rule.endpoint in auth.OPEN_ENDPOINTS}
    assert open_rules == {'/login', '/static/<path:filename>'}
    for name in ('notes.txt', 'page.html', 'picture.svg', 'mine.CSS', 'mine.css.bak'):
        with open(os.path.join(shared.THEMES_DIR, name), 'w', encoding='utf-8') as handle:
            handle.write('private')
        assert_refused(anon.get(f'/themes/{name}'), f'/themes/{name}')
        assert signed_in.get(f'/themes/{name}').data == b'private', name


@pytest.mark.parametrize('method, path', [
    ('GET', '/api/no-such-route'),
    ('PATCH', '/api/characters'),  # a real path, a method it doesn't take
    ('GET', '/no-such-page'),
    ('GET', '//api/characters'),
    ('GET', '/API/characters'),
    ('GET', '/api/characters/'),
    ('GET', '/api/../api/characters'),
    ('POST', '/api/backup/restore'),
])
def test_a_stranger_learns_nothing_about_routes(anon, method, path):
    assert_refused(anon.open(path, method=method), path)


@pytest.mark.parametrize('headers', [
    {'X-Forwarded-For': '127.0.0.1'},
    {'X-Real-IP': '127.0.0.1'},
    {'X-Original-URL': '/login'},
    {'X-Rewrite-URL': '/static/css/style.css'},
    {'X-Forwarded-Host': 'localhost'},
    {'X-Forwarded-Prefix': '/static'},
    {'Authorization': f'Basic {PASSWORD}'},
    {'Cookie': 'session=1; signed_in=1'},
])
def test_proxy_style_headers_do_not_open_anything(locked, headers):
    # Without its own cookie jar, the client sends the Cookie header as given.
    client = locked.test_client(use_cookies=False)
    assert_refused(client.get('/api/settings', headers=headers), '/api/settings')


def test_open_routes_serve_without_a_session(anon):
    with open(os.path.join(shared.THEMES_DIR, 'mine.css'), 'w', encoding='utf-8') as handle:
        handle.write(':root { --app-bg: #123456; }')
    assert anon.get('/login').status_code == 200
    for path in ('/static/css/style.css', '/themes/cozy.css', '/themes/mine.css'):
        response = anon.get(path)
        assert response.status_code == 200, path
        # They never read the session, so they stay cacheable for everyone.
        assert 'Cookie' not in response.headers.get('Vary', ''), path
        assert not response.headers.getlist('Set-Cookie'), path


@pytest.mark.parametrize('path', [
    '/themes/../private.css',
    '/themes/..%2fprivate.css',
    '/themes/%2e%2e/private.css',
    '/themes/%2e%2e%2fprivate.css',
    '/themes/..%5cprivate.css',
    '/themes/characters/../../private.css',
    '/themes/' + '/'.join(['..'] * 12) + '/etc/hostname.css',
    '/static/../app.py',
    '/static/..%2f..%2fapp.py',
    '/static/%2e%2e/%2e%2e/app.py',
])
def test_open_routes_serve_nothing_outside_their_folders(anon, path):
    with open(os.path.join(shared.DATA_DIR, 'private.css'), 'w', encoding='utf-8') as handle:
        handle.write('/* private */')
    response = anon.get(path)
    assert response.status_code in (302, 400, 404), path
    assert b'private' not in response.data
    assert b'Flask(__name__)' not in response.data


def test_private_files_stay_private(locked, anon, signed_in):
    r = signed_in.post('/api/characters', data={
        'data': json.dumps({'name': 'Secret Agent'}),
        'image': (io.BytesIO(make_minimal_png()), 'agent.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert r.status_code == 201
    character = r.get_json()
    filename = character['filename']
    for path in (f'/characters/{filename}', f'/thumbs/characters/128/{filename}'):
        assert signed_in.get(path).status_code == 200, path
        response = anon.get(path)
        assert response.status_code == 302, path
        assert b'PNG' not in response.data and b'RIFF' not in response.data, path
    for path in ('/api/backup', '/api/characters', f'/api/characters/{character["id"]}',
                 f'/api/characters/{character["id"]}/export?fmt=png'):
        response = anon.get(path)
        assert response.status_code == 401, path
        assert b'Secret Agent' not in response.data, path
        assert not response.data.startswith(b'PK'), path


def test_a_stranger_cannot_restore_over_the_data(anon):
    marker = os.path.join(shared.CHARACTERS_DIR, 'keep.png')
    with open(marker, 'wb') as handle:
        handle.write(make_minimal_png())
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('cozy-backup.json', json.dumps({'app': 'cozy', 'schema_version': 1}))
        z.writestr(os.path.basename(shared.DATABASE), b'')
    response = anon.post('/api/backup/restore', data={'file': (io.BytesIO(archive.getvalue()), 'b.zip')},
                         content_type='multipart/form-data')
    assert response.status_code == 401
    assert os.path.exists(marker)


def test_a_stranger_cannot_reach_the_model(anon, signed_in, monkeypatch):
    from cozy.routes import llm
    calls = []

    def upstream(url, **kwargs):
        calls.append(url)
        raise llm.http_requests.ConnectionError('no model here')
    monkeypatch.setattr(llm.http_requests, 'post', upstream)
    monkeypatch.setattr(llm.http_requests, 'get', upstream)
    signed_in.put('/api/settings', json={'api_endpoint': 'http://model.invalid/v1'})
    chat = {'model': 'm', 'messages': [{'role': 'user', 'content': 'hi'}]}
    response = anon.post('/api/llm/chat', json=chat)
    assert response.status_code == 401
    assert response.mimetype == 'application/json'
    assert anon.get('/api/llm/models').status_code == 401
    assert anon.post('/api/llm/test', json={}).status_code == 401
    assert calls == []
    # The same request does go out once signed in, so the stranger was stopped.
    signed_in.post('/api/llm/chat', json=chat).get_data()
    assert calls == ['http://model.invalid/v1/chat/completions']


def test_the_redirect_ignores_the_host_header(anon):
    response = anon.get('/', headers={'Host': 'evil.example'})
    assert response.headers['Location'] == '/login?next=/'


# ── Signing in ──────────────────────────────────────────────────────────────

def test_pages_send_you_to_sign_in(anon):
    response = anon.get('/')
    assert response.status_code == 302
    assert response.headers['Location'] == '/login?next=/'


@pytest.mark.parametrize('form', [
    {'password': 'nope'},
    {'password': ''},
    {},
    {'password': PASSWORD + ' '},
    {'password': PASSWORD.upper()},
    {'password': PASSWORD[:-1]},
    {'Password': PASSWORD},
])
def test_wrong_password_is_refused(anon, form):
    response = anon.post('/login', data=form)
    assert response.status_code == 401
    assert b'That password is wrong.' in response.data
    assert not any(h.startswith('cozy_session=') for h in response.headers.getlist('Set-Cookie'))
    assert anon.get('/').status_code == 302


def test_the_password_only_counts_in_the_form(anon):
    assert anon.get('/login', query_string={'password': PASSWORD}).status_code == 200
    response = anon.post('/login', query_string={'password': PASSWORD})
    assert response.status_code == 401
    assert anon.get('/').status_code == 302


def test_wrong_password_holds_off_every_sign_in_for_a_second(anon, monkeypatch):
    now = 1000.0
    monkeypatch.setattr(auth.time, 'monotonic', lambda: now)
    assert anon.post('/login', data={'password': 'nope'}).status_code == 401
    response = anon.post('/login', data={'password': PASSWORD})  # even the right one, from anywhere
    assert response.status_code == 429
    assert b'Too many wrong passwords' in response.data
    assert anon.get('/').status_code == 302
    now += auth.WRONG_PASSWORD_WAIT
    assert anon.post('/login', data={'password': PASSWORD}).status_code == 302


def test_signed_in_devices_keep_working_while_someone_guesses(signed_in, monkeypatch):
    monkeypatch.setattr(auth.time, 'monotonic', lambda: 1000.0)
    stranger = signed_in.application.test_client()
    assert stranger.post('/login', data={'password': 'nope'}).status_code == 401
    assert signed_in.get('/api/settings').status_code == 200


def test_remembered_device_gets_a_lasting_cookie(anon):
    response = anon.post('/login', data={'password': PASSWORD, 'remember': 'on'})
    assert response.status_code == 302
    cookie = session_cookie(response)
    assert 'Expires=' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie and 'Path=/' in cookie
    assert anon.get('/').status_code == 200


def test_unremembered_device_gets_a_browser_session_cookie(anon):
    response = anon.post('/login', data={'password': PASSWORD})
    assert 'Expires=' not in session_cookie(response)
    assert anon.get('/').status_code == 200


def test_the_cookie_is_not_signed_again_on_every_response(anon):
    anon.post('/login', data={'password': PASSWORD, 'remember': 'on'})
    for path in ('/', '/api/settings', '/api/characters'):
        assert not anon.get(path).headers.getlist('Set-Cookie'), path


@pytest.mark.parametrize('target, expected', [
    ('/api/characters?x=1', '/api/characters?x=1'),
    ('//evil.example/', '/'),
    ('///evil.example/', '/'),
    ('https://evil.example/', '/'),
    ('javascript:alert(1)', '/'),
    ('/\\evil.example', '/'),
    ('\\\\evil.example', '/'),
    ('/\t/evil.example', '/'),
    ('/\n/evil.example', '/'),
    ('/\x00/evil.example', '/'),
    ('/\r\nSet-Cookie: x=1', '/'),
    ('evil.example', '/'),
    ('', '/'),
])
def test_sign_in_returns_only_to_this_site(anon, target, expected):
    response = anon.post('/login', query_string={'next': target}, data={'password': PASSWORD})
    assert response.headers['Location'] == expected


def test_the_sign_in_page_sends_a_signed_in_device_on(signed_in):
    response = signed_in.get('/login', query_string={'next': '//evil.example'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/'


def test_signed_in_device_reaches_everything(locked, signed_in):
    page = signed_in.get('/')
    assert page.status_code == 200
    assert b'Sign out' in page.data
    for rule in locked.url_map.iter_rules():
        if 'GET' in rule.methods:
            path = example_path(rule)
            response = signed_in.get(path)
            assert response.status_code != 401, path
            assert not response.headers.get('Location', '').startswith('/login'), path


def test_sign_out(signed_in):
    assert signed_in.get('/').status_code == 200
    response = signed_in.post('/logout')
    assert response.headers['Location'] == '/login'
    assert signed_in.get('/').status_code == 302
    assert signed_in.get('/api/settings').status_code == 401


def test_back_after_sign_out_asks_the_server(signed_in):
    # The browser's back/forward cache would otherwise show the last chat again
    # without a request; the page reloads itself instead (index.html).
    assert b"addEventListener('pageshow'" in signed_in.get('/').data


def test_no_page_can_be_framed(anon, signed_in):
    for client, path in ((anon, '/login'), (anon, '/'), (anon, '/api/chats'), (anon, '/static/css/style.css'),
                         (signed_in, '/'), (signed_in, '/api/settings')):
        assert client.get(path).headers['Content-Security-Policy'] == "frame-ancestors 'none'", path


def test_api_replies_stay_out_of_the_browser_cache(anon, signed_in):
    for client, path in ((signed_in, '/api/characters'), (signed_in, '/api/settings'),
                         (signed_in, '/api/backup'), (anon, '/api/characters')):
        assert client.get(path).headers['Cache-Control'] == 'no-store', path
    # Avatars keep their own caching; signing out changes the Cookie they vary on.
    assert signed_in.get('/static/css/style.css').headers.get('Cache-Control') != 'no-store'


def test_a_cookie_planted_beside_the_real_one_changes_nothing(locked, signed_in):
    cookie = signed_in.get_cookie('cozy_session').value
    client = locked.test_client(use_cookies=False)
    # What a page on another port of the same host can set: cookies ignore the
    # port, and the one with the longer path comes first.
    for planted in ('cozy_session=planted', f'cozy_session={forge("guess")}', 'cozy_session='):
        headers = {'Cookie': f'{planted}; cozy_session={cookie}'}
        assert client.get('/api/settings', headers=headers).status_code == 200, planted
        assert client.get('/', headers=headers).status_code == 200, planted
        alone = client.get('/api/settings', headers={'Cookie': planted})
        assert alone.status_code == 401, planted


def test_cookie_is_secure_when_the_proxy_says_https(locked):
    response = locked.test_client().post('/login', data={'password': PASSWORD},
                                         headers={'X-Forwarded-Proto': 'https'})
    assert 'Secure' in session_cookie(response)
    response = locked.test_client().post('/login', data={'password': PASSWORD})
    assert 'Secure' not in session_cookie(response)


@pytest.mark.parametrize('headers', [
    {'Sec-Fetch-Site': 'cross-site'},
    {'Sec-Fetch-Site': 'same-site'},  # another port on the same host
    {'Origin': 'http://evil.example'},
])
def test_another_site_cannot_sign_in_or_out(locked, signed_in, headers):
    response = locked.test_client().post('/login', data={'password': PASSWORD}, headers=headers)
    assert response.status_code == 403
    assert not response.headers.getlist('Set-Cookie')
    assert signed_in.post('/logout', headers=headers).status_code == 403
    assert signed_in.get('/').status_code == 200


def test_another_site_cannot_lock_sign_in_out(locked, monkeypatch):
    monkeypatch.setattr(auth.time, 'monotonic', lambda: 1000.0)
    stranger = locked.test_client()
    response = stranger.post('/login', data={'password': 'nope'}, headers={'Sec-Fetch-Site': 'cross-site'})
    assert response.status_code == 403
    assert locked.test_client().post('/login', data={'password': PASSWORD}).status_code == 302


# ── The cookie ──────────────────────────────────────────────────────────────

def test_cookie_survives_a_restart(monkeypatch, signed_in):
    cookie = signed_in.get_cookie('cozy_session').value
    restarted = load_app(monkeypatch).test_client()
    restarted.set_cookie('cozy_session', cookie)
    assert restarted.get('/').status_code == 200


def test_new_password_signs_every_device_out(monkeypatch, signed_in):
    cookie = signed_in.get_cookie('cozy_session').value
    restarted = load_app(monkeypatch, 'a new password').test_client()
    restarted.set_cookie('cozy_session', cookie)
    assert restarted.get('/').status_code == 302


def test_forged_cookies_are_refused(locked, anon):
    secret = stored_secret()
    real_key = auth.hmac.new(secret.encode(), PASSWORD.encode(), auth.hashlib.sha256).digest()
    # The forger works: the real key does sign a device in.
    anon.set_cookie('cozy_session', forge(real_key))
    assert anon.get('/').status_code == 200

    forgeries = [
        forge(PASSWORD),                     # the password alone
        forge(secret),                       # the secret alone, from a stolen backup
        forge(auth.hmac.new(secret.encode(), b'guess', auth.hashlib.sha256).digest()),
        forge('dev'), forge('secret'), forge(bytes(32)),
        forge(real_key, {'signed_in': False}),
        forge(real_key)[:-2] + 'xx',         # tampered signature
        'eyJzaWduZWRfaW4iOnRydWV9',          # {"signed_in":true}, unsigned
        '.eJyrVipKLS7JzM-LLy1OLYpXslIqLU4tUqoFAG6jCRY.x.y',
    ]
    for forged in forgeries:
        client = locked.test_client()
        client.set_cookie('cozy_session', forged)
        assert client.get('/').status_code == 302, forged
        assert client.get('/api/settings').status_code == 401, forged


def test_a_cookie_from_another_install_is_refused(monkeypatch, signed_in):
    cookie = signed_in.get_cookie('cozy_session').value
    with sqlite3.connect(shared.DATABASE) as conn:
        conn.execute("UPDATE settings SET value = 'another install' WHERE key = 'secret_key'")
    restarted = load_app(monkeypatch).test_client()
    restarted.set_cookie('cozy_session', cookie)
    assert restarted.get('/').status_code == 302


def test_the_secret_never_leaves(signed_in):
    secret = stored_secret()
    assert secret
    settings = signed_in.get('/api/settings').get_json()
    assert 'secret_key' not in settings
    assert secret not in json.dumps(settings)
    # Nor in a backup, not even in the free space a deleted row leaves behind.
    backup = zipfile.ZipFile(io.BytesIO(signed_in.get('/api/backup').data))
    for name in backup.namelist():
        assert secret.encode() not in backup.read(name), name


def test_restore_keeps_devices_signed_in(monkeypatch, signed_in):
    secret = stored_secret()
    backup = signed_in.get('/api/backup').data  # carries no secret, as above
    response = signed_in.post('/api/backup/restore', data={'file': (io.BytesIO(backup), 'b.zip')},
                              content_type='multipart/form-data')
    assert response.status_code == 200, response.get_json()
    assert stored_secret() == secret
    cookie = signed_in.get_cookie('cozy_session').value
    restarted = load_app(monkeypatch).test_client()
    restarted.set_cookie('cozy_session', cookie)
    assert restarted.get('/').status_code == 200
