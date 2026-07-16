"""Auto Summaries — background summarization of aged-out chat history.

The run endpoint claims the job in the DB and spawns a **daemon thread**, returning
202 immediately; the worker folds the aged-out messages into the chat's running summary
in batches, persisting after each batch so it survives the browser closing (and leaves
consistent partial progress across a server restart). Status lives on the chat row, so
any worker process can report it — see ``GET …/summary/status``.
"""

import logging
import math
import threading

import requests as http_requests
from flask import Blueprint, request, jsonify

from routes.chats import chat_to_dict
from routes.llm import _error_detail
from routes.settings import get_settings
from shared import get_db, not_found
from summarizer import (
    build_summarizer_messages,
    dump_summary_json,
    enforce_cap,
    merge_pins,
    parse_summary,
    parse_summary_json,
    pinned_texts,
    summary_to_text,
)

log = logging.getLogger('cozy')

summaries_bp = Blueprint('summaries', __name__)


# ── Summarizer LLM call ─────────────────────────────────────────────────────

def _summary_llm_settings():
    """(endpoint, api_key, model) for the summarizer, each falling back to the main
    LLM setting when its ``summary_*`` counterpart is blank."""
    s = get_settings()
    endpoint = s.get('summary_api_endpoint') or s.get('api_endpoint', '')
    api_key = s.get('summary_api_key') or s.get('api_key', '')
    model = s.get('summary_api_model') or s.get('api_model', '')
    return endpoint, api_key, model


def call_summarizer(messages, cap_tokens=0):
    """One non-streaming summarizer completion. Mirrors ``test_llm`` in routes/llm.py."""
    endpoint, api_key, model = _summary_llm_settings()
    if not endpoint:
        raise RuntimeError('No summarizer endpoint configured (Settings → Auto Summaries)')
    if not model:
        raise RuntimeError('No summarizer model configured (Settings → Auto Summaries)')
    url = endpoint.rstrip('/') + '/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    payload = {'model': model, 'messages': messages, 'stream': False}
    if cap_tokens and cap_tokens > 0:
        # Give the model room to reach the cap without the endpoint's low default
        # max_tokens truncating it; enforce_cap trims anything over afterward.
        payload['max_tokens'] = max(512, int(cap_tokens * 1.5))
    try:
        r = http_requests.post(url, json=payload, headers=headers, timeout=180)
        r.raise_for_status()
    except http_requests.RequestException as e:
        raise RuntimeError(_error_detail(e))
    body = r.json()
    return (body.get('choices', [{}])[0].get('message', {}).get('content', '') or '').strip()


# ── Config helpers ──────────────────────────────────────────────────────────

def _cap_tokens(settings):
    """Summary size cap in tokens = summary_cap_pct% of context_max_tokens."""
    try:
        pct = float(settings.get('summary_cap_pct') or 10)
    except (TypeError, ValueError):
        pct = 10.0
    try:
        ctx = int(settings.get('context_max_tokens') or 32768)
    except (TypeError, ValueError):
        ctx = 32768
    return max(1, int(ctx * pct / 100.0))


def _trigger_interval(settings):
    try:
        return max(1, int(settings.get('summary_trigger_interval') or 20))
    except (TypeError, ValueError):
        return 20


# ── Job status helpers ──────────────────────────────────────────────────────

def _set_status(chat_id, status=None, detail=None):
    sets, params = [], []
    if status is not None:
        sets.append('summary_status=?')
        params.append(status)
    if detail is not None:
        sets.append('summary_status_detail=?')
        params.append(detail)
    if not sets:
        return
    params.append(chat_id)
    with get_db() as conn:
        conn.execute(f"UPDATE chats SET {', '.join(sets)} WHERE id=?", params)


def _summary_state(row):
    """The summary-related slice of a chat row, for run/status responses."""
    d = chat_to_dict(row)
    return {
        'id': d['id'],
        'summary_enabled': d.get('summary_enabled'),
        'summary': d.get('summary'),
        'summary_up_to_msg_id': d.get('summary_up_to_msg_id'),
        'summary_status': d.get('summary_status'),
        'summary_status_detail': d.get('summary_status_detail'),
    }


# ── The worker ──────────────────────────────────────────────────────────────

def _run_summary_job(chat_id, up_to_msg_id, rebuild=False):
    """Fold messages ``(watermark, up_to_msg_id]`` into the chat's running summary,
    in batches. Persists after each batch. Never raises out — terminal state is written
    to ``summary_status`` (``idle`` on success, ``error`` on failure)."""
    warning = ''
    try:
        settings = get_settings()
        interval = _trigger_interval(settings)
        cap_tokens = _cap_tokens(settings)

        with get_db() as conn:
            row = conn.execute(
                'SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?', (chat_id,)
            ).fetchone()
            if not row:
                return
            if rebuild:
                summary_obj = {'lines': []}
                watermark = 0
                conn.execute(
                    "UPDATE chats SET summary_json='', summary_up_to_msg_id=NULL WHERE id=?",
                    (chat_id,)
                )
            else:
                summary_obj = parse_summary_json(row['summary_json'])
                watermark = row['summary_up_to_msg_id'] or 0

        if up_to_msg_id is None:
            _set_status(chat_id, status='idle', detail='')
            return

        with get_db() as conn:
            msgs = conn.execute(
                'SELECT id, role, content FROM messages '
                'WHERE chat_id=? AND id>? AND id<=? ORDER BY id ASC',
                (chat_id, watermark, up_to_msg_id)
            ).fetchall()

        batch = [{'role': m['role'], 'content': m['content']} for m in msgs]
        ids = [m['id'] for m in msgs]
        if not batch:
            _set_status(chat_id, status='idle', detail='')
            return

        total = math.ceil(len(batch) / interval)
        for bi in range(total):
            chunk = batch[bi * interval:(bi + 1) * interval]
            chunk_ids = ids[bi * interval:(bi + 1) * interval]
            _set_status(chat_id, detail=f'Summarizing… (batch {bi + 1}/{total})')

            messages = build_summarizer_messages(
                summary_to_text(summary_obj), chunk, pinned_texts(summary_obj), cap_tokens
            )
            reply = call_summarizer(messages, cap_tokens)
            folded = merge_pins(parse_summary(reply), summary_obj)
            summary_obj, warning = enforce_cap(folded, cap_tokens)

            with get_db() as conn:
                conn.execute(
                    'UPDATE chats SET summary_json=?, summary_up_to_msg_id=? WHERE id=?',
                    (dump_summary_json(summary_obj), chunk_ids[-1], chat_id)
                )

        _set_status(chat_id, status='idle', detail=warning or '')
    except Exception as e:  # noqa: BLE001 — terminal state must always be recorded
        log.exception('Summary job failed for chat %s', chat_id)
        _set_status(chat_id, status='error', detail=str(e)[:300])


def _spawn_job(chat_id, up_to_msg_id, rebuild):
    """Start the worker in a daemon thread (indirection so tests can stub it out)."""
    threading.Thread(
        target=_run_summary_job,
        args=(chat_id, up_to_msg_id, rebuild),
        daemon=True,
    ).start()


# ── Routes ──────────────────────────────────────────────────────────────────

@summaries_bp.route('/api/chats/<int:chat_id>/summary/run', methods=['POST'])
def run_summary(chat_id):
    """Kick off a background summary run and return 202 immediately.

    Body: ``{ "up_to_msg_id": <int>, "rebuild": <bool?> }`` — fold everything after the
    watermark up to that id. A run already in flight is a no-op (409)."""
    data = request.get_json(silent=True) or {}
    up_to_msg_id = data.get('up_to_msg_id')
    rebuild = bool(data.get('rebuild'))

    with get_db() as conn:
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not row:
            return not_found('Chat')
        # Atomic claim: only start when not already running.
        claimed = conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            "WHERE id=? AND summary_status!='running'",
            (chat_id,)
        ).rowcount

    if not claimed:
        with get_db() as conn:
            fresh = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return jsonify({'already_running': True, **_summary_state(fresh)}), 409

    _spawn_job(chat_id, up_to_msg_id, rebuild)

    with get_db() as conn:
        fresh = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
    return jsonify(_summary_state(fresh)), 202


@summaries_bp.route('/api/chats/<int:chat_id>/summary/reset', methods=['POST'])
def reset_summary(chat_id):
    """Clear the summary, pins, and watermark — a clean slate, no LLM call.

    Useful after changing the context size: growing it can leave a now-unneeded
    summary lingering, and shrinking it may need a fresh start over a different set
    of aged-out messages (follow with a run/rebuild to regenerate)."""
    with get_db() as conn:
        row = conn.execute('SELECT id FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not row:
            return not_found('Chat')
        conn.execute(
            "UPDATE chats SET summary_json='', summary_up_to_msg_id=NULL, "
            "summary_status='idle', summary_status_detail='' WHERE id=?",
            (chat_id,)
        )
        fresh = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
    return jsonify(_summary_state(fresh))


@summaries_bp.route('/api/chats/<int:chat_id>/summary/status', methods=['GET'])
def summary_status(chat_id):
    """Current summary state for polling: status, progress detail, and the summary."""
    with get_db() as conn:
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
    if not row:
        return not_found('Chat')
    return jsonify(_summary_state(row))
