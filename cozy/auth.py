"""The optional password (COZY_PASSWORD): the sign-in page, the guard in front
of every other route, and signing out.

Ported from imgy, which took it from binny. app.py installs all of it only when
the password is set, so without one Cozy runs exactly as it always has.
"""

import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature

from cozy.shared import get_db

# The password that signs a device in. Unset or empty means no sign-in at all.
PASSWORD = os.environ.get('COZY_PASSWORD', '')

bp = Blueprint('auth', __name__)

# "Keep this device signed in" lasts as long as browsers allow a cookie to
# (Chrome caps it at 400 days).
REMEMBER_FOR = timedelta(days=400)

# Everything else needs a signed-in session, so a new route is protected without
# opting in. /static holds only Cozy's own files. Theme stylesheets are open too
# (see require_login()), so the sign-in page wears the theme the device last
# chose, a user theme included; anything else kept in data/themes is not.
OPEN_ENDPOINTS = {'auth.login', 'static'}

# A wrong password from any device holds off every sign-in for a second, the
# right password included, so guessing goes at one try a second however many
# requests run side by side. Kept in memory because Cozy is one process (gunicorn
# runs a single worker). Signed-in devices aren't affected, but anything that
# keeps guessing keeps new devices out until it stops. Holding off each address
# on its own would let many addresses guess side by side, and behind a reverse
# proxy every device has the proxy's address anyway.
WRONG_PASSWORD_WAIT = 1  # seconds
next_try = 0.0  # time.monotonic() before which a sign-in is refused unchecked
next_try_lock = threading.Lock()


class SessionInterface(SecureCookieSessionInterface):
    def open_session(self, app, request):
        """Flask's, but trying every cookie of this name rather than the first.

        Browsers send the one with the longest path first, and a page on another
        port of this host can set one under /api, since cookies ignore the port.
        Read alone, that planted cookie would send every API call to the sign-in
        page, and the sign-in page, seeing the real one, back to the app.
        """
        serializer = self.get_signing_serializer(app)
        max_age = int(app.permanent_session_lifetime.total_seconds())
        for value in request.cookies.getlist(self.get_cookie_name(app)):
            try:
                return self.session_class(serializer.loads(value, max_age=max_age))
            except BadSignature:
                pass
        return self.session_class()

    def get_cookie_secure(self, app):
        """Mark the cookie Secure whenever the browser reached Cozy over HTTPS.

        Behind a reverse proxy that comes from X-Forwarded-Proto (ProxyFix in
        app.py). Over plain HTTP on the LAN a Secure cookie would never come
        back, so it isn't set there.
        """
        return request.is_secure


def signing_key():
    """The key that signs session cookies: a random secret kept in the
    database, mixed with the password.

    Changing COZY_PASSWORD signs every device out. So does deleting the
    `secret_key` setting and restarting. Restoring a backup keeps this
    install's secret (see restore_backup()).
    """
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)",
            (secrets.token_hex(32),),
        )
        secret = conn.execute(
            "SELECT value FROM settings WHERE key = 'secret_key'"
        ).fetchone()['value']
    return hmac.new(secret.encode(), PASSWORD.encode(), hashlib.sha256).digest()


def require_login():
    """before_request guard: API calls get a 401, page loads go to sign-in.

    The open routes are checked before the session is read, so stylesheets
    and scripts don't pick up a Vary: Cookie that would defeat their caching.
    """
    theme_css = request.endpoint == 'serve_theme' and request.path.endswith('.css')
    if request.endpoint in OPEN_ENDPOINTS or theme_css or session.get('signed_in'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Sign in first'}), 401
    return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))


def refuse_framing_and_caching(response):
    """after_request: no page may show Cozy in a frame, where a page on another
    port of this host could get it clicked through, and API responses, chats
    among them, stay out of the browser's cache, where they'd outlive signing
    out."""
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    if request.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


def local_target(target):
    """target if it is a path on this site, else '/', so ?next= can't send the
    browser elsewhere.

    Browsers drop tabs and newlines from a URL and read a backslash as a slash,
    so any of them could turn it into "//elsewhere", another site.
    """
    if target and target.startswith('/') and not target.startswith('//') \
            and not any(c == '\\' or c <= ' ' for c in target):
        return target
    return '/'


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('signed_in'):
        return redirect(local_target(request.args.get('next')))
    if request.method == 'GET':
        return render_template('login.html', error=None)
    global next_try
    password = request.form.get('password', '')
    with next_try_lock:
        if time.monotonic() < next_try:
            return render_template(
                'login.html',
                error='Too many wrong passwords just now. Try again in a moment.',
            ), 429
        # Comparing digests keeps the time taken from telling anything about
        # the password, its length included.
        right = hmac.compare_digest(
            hashlib.sha256(password.encode()).digest(),
            hashlib.sha256(PASSWORD.encode()).digest(),
        )
        if not right:
            next_try = time.monotonic() + WRONG_PASSWORD_WAIT
    if not right:
        # So whoever mistyped can try again as soon as they see this.
        time.sleep(WRONG_PASSWORD_WAIT)
        return render_template('login.html', error='That password is wrong.'), 401
    session.clear()
    session['signed_in'] = True
    session.permanent = request.form.get('remember') == 'on'
    return redirect(local_target(request.args.get('next')))


@bp.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
