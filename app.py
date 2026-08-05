import os
import glob
import logging
import argparse

from flask import Flask, render_template, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

import shared
import thumbs

log = logging.getLogger('cozy')

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


# ── Serve files from DATA_DIR ─────────────────────────────────────────────
@app.route('/characters/<path:filename>')
def serve_character_avatar(filename):
    return send_from_directory(shared.CHARACTERS_DIR, filename)


@app.route('/personas/<path:filename>')
def serve_persona_avatar(filename):
    return send_from_directory(shared.PERSONAS_DIR, filename)


# Downscaled copies of the above, generated on demand. The URL names the source
# file and the wanted size; thumbs.py maps that to a content-addressed cache
# entry, so callers never need to know the key. See thumbs.py.
@app.route('/thumbs/characters/<int:size>/<path:filename>')
def serve_character_thumb(size, filename):
    return thumbs.serve(shared.CHARACTERS_DIR, size, filename)


@app.route('/thumbs/personas/<int:size>/<path:filename>')
def serve_persona_thumb(size, filename):
    return thumbs.serve(shared.PERSONAS_DIR, size, filename)


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html', build_info=shared.BUILD_INFO)


@app.route('/api/themes', methods=['GET'])
def list_themes():
    names = set()
    for d in (shared.BUILTIN_THEMES_DIR, shared.THEMES_DIR):
        if os.path.isdir(d):
            names.update(
                f[:-4] for f in os.listdir(d)
                if f.endswith('.css') and not f.startswith('.')
            )
    return jsonify(sorted(names))


@app.route('/themes/<path:filename>')
def serve_theme(filename):
    """Serve theme CSS — user themes in DATA_DIR take precedence over built-in."""
    user_path = os.path.join(shared.THEMES_DIR, filename)
    if os.path.isfile(user_path):
        return send_from_directory(shared.THEMES_DIR, filename)
    return send_from_directory(shared.BUILTIN_THEMES_DIR, filename)


# ── Register blueprints ────────────────────────────────────────────────────
from routes.characters import characters_bp
from routes.chats import chats_bp
from routes.messages import messages_bp
from routes.personas import personas_bp
from routes.settings import settings_bp
from routes.llm import llm_bp
from routes.lorebooks import lorebooks_bp
from routes.summaries import summaries_bp

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
shared.init_db()
shared.seed_default_characters()
shared.seed_default_prompts()
shared.seed_default_regex_presets()

# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="A cozy roleplay frontend.")
    parser.add_argument("--host", help="Change host binding", default="127.0.0.1", type=str)
    parser.add_argument("--port", help="Change port binding", default=5001, type=int)
    args = parser.parse_args()
    BIND_HOST = args.host
    BIND_PORT = args.port

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(message)s',
        datefmt='%H:%M:%S',
    )

    # Use Flask's built-in server with livereload extras.
    # livereload.Server wraps WSGI and buffers responses, which breaks SSE
    # streaming.  Flask's dev server supports streaming natively and the
    # ``extra_files`` watcher gives us auto-reload on template/static changes.
    extra = [
        *glob.glob('static/**/*', recursive=True),
        *glob.glob('templates/**/*', recursive=True),
    ]
    app.run(port=BIND_PORT, debug=True, extra_files=extra, threaded=True, host=BIND_HOST)
