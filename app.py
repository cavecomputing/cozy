import os
import glob
import socket
import hashlib
import logging
import argparse
from urllib.parse import urlsplit

import flask.cli
from flask import Flask, render_template, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import get_interface_ip, is_running_from_reloader

from cozy import auth
from cozy import banner
from cozy import shared
from cozy import defaults
from cozy import schema
from cozy import thumbs

# Here rather than under __main__, so gunicorn's workers log the way
# `uv run app.py` does; set up under __main__ alone, `docker logs` showed
# nothing below a warning. COZY_LOG_LEVEL takes a level name, and DEBUG adds
# the opening words of each prompt and reply.
logging.basicConfig(format='%(asctime)s %(levelname)-7s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger('cozy')
log.setLevel(os.environ.get('COZY_LOG_LEVEL', 'INFO').upper())

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB


# ── Global error handlers (always return JSON for API consumers) ──────────
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({'error': e.description}), e.code
    log.exception('Unhandled error: %s', e)
    return jsonify({'error': 'Internal server error'}), 500


# ── Same-origin writes only ───────────────────────────────────────────────
# Without COZY_PASSWORD Cozy has no login, so any other page open in the same
# browser can send it requests; with it, a page served from another port on the
# same host still gets the cookie. A file upload needs no CORS preflight, which
# put every upload route in reach — /api/backup/restore among them, which
# replaces the whole data directory. Browsers say where a request came from;
# writes that did not come from Cozy's own page are refused.
@app.before_request
def refuse_cross_site_writes():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    site = request.headers.get('Sec-Fetch-Site')
    if site is not None:
        allowed = site in ('same-origin', 'none')
    else:
        # Browsers too old for Sec-Fetch-Site still send Origin cross-site.
        origin = request.headers.get('Origin')
        allowed = origin is None or urlsplit(origin).netloc == request.host
    if not allowed:
        return jsonify({'error': 'Cross-site request refused'}), 403
    return None


# ── Serve files from DATA_DIR ─────────────────────────────────────────────
@app.route('/characters/<path:filename>')
def serve_character_avatar(filename):
    return send_from_directory(shared.CHARACTERS_DIR, filename)


@app.route('/personas/<path:filename>')
def serve_persona_avatar(filename):
    return send_from_directory(shared.PERSONAS_DIR, filename)


# Downscaled copies of the above, generated on demand. The URL names the source
# file and the wanted size; cozy/thumbs.py maps that to a content-addressed cache
# entry, so callers never need to know the key. See cozy/thumbs.py.
@app.route('/thumbs/characters/<int:size>/<path:filename>')
def serve_character_thumb(size, filename):
    return thumbs.serve(shared.CHARACTERS_DIR, size, filename)


@app.route('/thumbs/personas/<int:size>/<path:filename>')
def serve_persona_thumb(size, filename):
    return thumbs.serve(shared.PERSONAS_DIR, size, filename)


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    # Every module, named up front so the browser fetches them side by side
    # instead of finding each one only after the module importing it arrives.
    js_modules = sorted(
        os.path.basename(path)
        for path in glob.glob(os.path.join(app.static_folder, 'js', '*.js'))
    )
    # no-cache: an installed web app otherwise reopens from its saved copy of
    # this page and keeps running the modules it named before an update.
    return render_template(
        'index.html', build_info=shared.BUILD_INFO, js_modules=js_modules,
        has_password=bool(auth.PASSWORD),
        theme_versions={name: _file_version(path) for name, path in _theme_files().items()},
    ), {'Cache-Control': 'no-cache'}


# The page's scripts and stylesheet, under the build they belong to. Each build
# names them by new URLs, so a browser can't run modules it kept from before an
# update inside the updated page, as Safari did despite no-cache. Imports
# between modules are relative, so they stay under the same prefix.
@app.route('/assets/<build>/<path:filename>')
def build_asset(build, filename):
    resp = app.send_static_file(filename)
    # A file under a committed build's URL never changes, so the browser can
    # keep it for a year instead of asking after every script on each load. A
    # dirty checkout or an unknown build keeps the same label from one edit to
    # the next, so those stay on the default no-cache and revalidate.
    info = shared.BUILD_INFO
    if build == info.display and info.commit and not info.dirty:
        resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp


def _theme_files():
    """Each theme's name and the file that serves it — a user theme in DATA_DIR
    takes precedence over a built-in of the same name."""
    files = {}
    for d in (shared.BUILTIN_THEMES_DIR, shared.THEMES_DIR):
        if os.path.isdir(d):
            files.update(
                (f[:-4], os.path.join(d, f)) for f in os.listdir(d)
                if f.endswith('.css') and not f.startswith('.')
            )
    return files


def _file_version(path):
    """A short name for a file's contents, which changes when they do."""
    with open(path, 'rb') as f:
        return hashlib.blake2b(f.read(), digest_size=6).hexdigest()


@app.route('/api/themes', methods=['GET'])
def list_themes():
    return jsonify(sorted(_theme_files()))


@app.route('/themes/<path:filename>')
def serve_theme(filename):
    """Serve theme CSS — user themes in DATA_DIR take precedence over built-in."""
    user_path = os.path.join(shared.THEMES_DIR, filename)
    directory = shared.THEMES_DIR if os.path.isfile(user_path) else shared.BUILTIN_THEMES_DIR
    resp = send_from_directory(directory, filename)
    # The page asks for a theme under the version of the file it was rendered
    # with. The sheet blocks the first paint, and asking after it on every
    # launch cost a round trip before anything was drawn, so a request naming
    # the file as it is now may be kept for good. An edited theme has a new
    # version, which the next page load names. Any other request, a stale
    # version included, gets the current file and revalidates as before.
    if request.args.get('v') == _file_version(os.path.join(directory, filename)):
        resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp


# ── Register blueprints ────────────────────────────────────────────────────
from cozy.routes.characters import characters_bp
from cozy.routes.chats import chats_bp
from cozy.routes.messages import messages_bp
from cozy.routes.personas import personas_bp
from cozy.routes.settings import settings_bp
from cozy.routes.llm import llm_bp
from cozy.routes.lorebooks import lorebooks_bp
from cozy.routes.summaries import summaries_bp

for blueprint in (
    characters_bp,
    chats_bp,
    messages_bp,
    personas_bp,
    settings_bp,
    llm_bp,
    lorebooks_bp,
    summaries_bp,
):
    app.register_blueprint(blueprint)


# ── Initialise DB (runs under both gunicorn and `python app.py`) ────────────
schema.init_db()
defaults.seed_default_characters()
defaults.seed_default_prompts()


# ── Optional password (COZY_PASSWORD) ──────────────────────────────────────
# Installed only when the password is set; see cozy/auth.py. After the DB
# exists, since the signing key lives in it, and after refuse_cross_site_writes,
# so another site can't post a sign-in either.
if auth.PASSWORD:
    # A reverse proxy says whether the browser used HTTPS, which decides
    # whether the cookie is Secure.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)
    app.session_interface = auth.SessionInterface()
    app.config.update(
        # Cookies ignore the port, so a plain "session" would collide with
        # another app on the same host.
        SESSION_COOKIE_NAME='cozy_session',
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=auth.REMEMBER_FOR,
        # Re-signing a remembered cookie on every response would change it on
        # every response, and its Vary: Cookie would then turn each cached
        # avatar into a fresh download.
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.secret_key = auth.signing_key()
    app.before_request(auth.require_login)
    app.after_request(auth.refuse_framing_and_caching)
    app.register_blueprint(auth.bp)

# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="A cozy roleplay frontend.")
    parser.add_argument("--host", help="Change host binding", default="127.0.0.1", type=str)
    parser.add_argument("--port", help="Change port binding", default=5000, type=int)
    parser.add_argument("--debug", action="store_true",
                        help="Development: reload on file changes and serve Flask's debugger")
    args = parser.parse_args()
    BIND_HOST = args.host
    BIND_PORT = args.port

    # The banner says where Cozy is, which is all Flask's and werkzeug's own
    # start-up lines said, and a line per request (avatars and scripts
    # included) is for development. werkzeug's warnings and errors still show.
    if not args.debug:
        flask.cli.show_server_banner = lambda *_: None
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
    # Under --debug the reloader runs this file again in a child process.
    if not is_running_from_reloader():
        hosts = [BIND_HOST]
        if BIND_HOST == '0.0.0.0':
            hosts = ['127.0.0.1', get_interface_ip(socket.AF_INET)]
        banner.print_banner([f'http://{host}:{BIND_PORT}' for host in hosts])

    # Flask's built-in server, not livereload.Server: that one wraps WSGI and
    # buffers responses, which breaks SSE streaming.  Flask's dev server
    # streams natively, and under --debug the ``extra_files`` watcher gives
    # auto-reload on template/static changes.
    #
    # Debug is opt-in because this is also how the README tells everyone to
    # run Cozy: it serves Werkzeug's PIN-locked Python console at /console,
    # outside COZY_PASSWORD's reach, and --host 0.0.0.0 would put that on the
    # network.
    extra = [
        *glob.glob('static/**/*', recursive=True),
        *glob.glob('templates/**/*', recursive=True),
    ]
    app.run(port=BIND_PORT, debug=args.debug, extra_files=extra, threaded=True, host=BIND_HOST)
