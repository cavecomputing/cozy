import os
import glob
import logging

from flask import Flask, render_template, jsonify, send_from_directory

from shared import (
    CHARACTERS_DIR, PERSONAS_DIR, THEMES_DIR, BUILTIN_THEMES_DIR, init_db,
)

log = logging.getLogger('cozy')

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB


# ── Global error handlers (always return JSON for API consumers) ──────────
@app.errorhandler(Exception)
def handle_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({'error': e.description}), e.code
    log.exception('Unhandled error: %s', e)
    return jsonify({'error': 'Internal server error'}), 500


# ── Serve files from DATA_DIR ─────────────────────────────────────────────
@app.route('/characters/<path:filename>')
def serve_character_avatar(filename):
    return send_from_directory(CHARACTERS_DIR, filename)


@app.route('/personas/<path:filename>')
def serve_persona_avatar(filename):
    return send_from_directory(PERSONAS_DIR, filename)


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/themes', methods=['GET'])
def list_themes():
    names = set()
    for d in (BUILTIN_THEMES_DIR, THEMES_DIR):
        if os.path.isdir(d):
            names.update(
                f[:-4] for f in os.listdir(d)
                if f.endswith('.css') and not f.startswith('.')
            )
    return jsonify(sorted(names))


@app.route('/themes/<path:filename>')
def serve_theme(filename):
    """Serve theme CSS — user themes in DATA_DIR take precedence over built-in."""
    user_path = os.path.join(THEMES_DIR, filename)
    if os.path.isfile(user_path):
        return send_from_directory(THEMES_DIR, filename)
    return send_from_directory(BUILTIN_THEMES_DIR, filename)


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
init_db()

# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
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
    app.run(port=5001, debug=True, extra_files=extra, threaded=True)
