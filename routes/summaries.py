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
import uuid

import requests as http_requests
from flask import Blueprint, request, jsonify

from routes.chats import chat_to_dict
from routes.llm import _error_detail, _summary_llm_settings
from routes.settings import get_settings
from shared import get_db, not_found
from summarizer import (
    append_summary,
    build_append_messages,
    build_summarizer_messages,
    dump_summary_json,
    enforce_cap,
    estimate_tokens,
    merge_pins,
    parse_summary_json,
    parse_summarizer_output,
    pinned_texts,
    strip_thinking_content,
    summary_lines,
    summary_to_text,
)

log = logging.getLogger('cozy')

summaries_bp = Blueprint('summaries', __name__)

# One production process serves requests with gthread workers. A generation token keeps
# an old HTTP call from mistaking a newly-started run's ``running`` row for its own job.
_job_tokens = {}
_job_tokens_lock = threading.RLock()


# ── Summarizer LLM call ─────────────────────────────────────────────────────

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
        # One bounded provider call per update, but with headroom over the cap:
        # a compress-mode reply re-emits the whole summary at ~cap tokens, and
        # provider truncation chops the newest text (the BONDS tail) while the
        # local enforce_cap trims oldest-first. The floor keeps room for the
        # required headings — and for reasoning models, whose thinking spends
        # this same budget — when the configured cap is tiny.
        payload['max_tokens'] = max(512, int(cap_tokens * 1.25))
    try:
        r = http_requests.post(url, json=payload, headers=headers, timeout=180)
        r.raise_for_status()
    except http_requests.RequestException as e:
        raise RuntimeError(_error_detail(e))
    try:
        body = r.json()
    except ValueError as e:
        raise RuntimeError('Summarizer endpoint returned invalid JSON') from e
    if not isinstance(body, dict):
        raise RuntimeError('Summarizer endpoint returned a malformed response')
    choices = body.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError('Summarizer endpoint returned no completion choice')
    if choices[0].get('finish_reason') == 'length':
        # A truncated reply can still parse (the cut lands mid-bullet or right
        # after a heading) and would retire history against chopped memory.
        raise RuntimeError(
            'Summarizer response was cut off by its completion token limit; '
            'try a larger context size or summary cap'
        )
    message = choices[0].get('message')
    if not isinstance(message, dict):
        raise RuntimeError('Summarizer endpoint returned a malformed completion message')
    content = message.get('content')
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('Summarizer endpoint returned empty completion content')
    return content.strip()


# ── Config helpers ──────────────────────────────────────────────────────────

def _cap_tokens(settings):
    """Summary size cap in tokens = summary_cap_pct% of context_max_tokens."""
    raw_pct = settings.get('summary_cap_pct')
    if raw_pct is None or raw_pct == '':
        raw_pct = 10
    try:
        pct = float(raw_pct)
    except (TypeError, ValueError, OverflowError):
        pct = 10.0
    if not math.isfinite(pct):
        pct = 10.0
    # Match the number input's documented 1%–90% range even when settings are
    # written directly rather than through the browser UI.
    pct = min(90.0, max(1.0, pct))

    raw_ctx = settings.get('context_max_tokens')
    if raw_ctx is None or raw_ctx == '':
        raw_ctx = 32768
    try:
        ctx = int(raw_ctx)
    except (TypeError, ValueError, OverflowError):
        ctx = 32768
    if ctx <= 0:
        return 0

    # Avoid converting an arbitrarily large Python integer to float during the
    # percentage calculation. Integer ratios keep this operation overflow-safe.
    pct_numerator, pct_denominator = pct.as_integer_ratio()
    return max(1, ctx * pct_numerator // (100 * pct_denominator))


def _trigger_interval(settings):
    try:
        return max(1, int(settings.get('summary_trigger_interval') or 10))
    except (TypeError, ValueError):
        return 10


# While the summary sits below this fraction of the cap it grows additively; once it
# crosses the line every batch compresses instead. The headroom (~20% of the cap) leaves
# room for one batch's new bullets so ordinary appends don't immediately trip the cap.
APPEND_CAP_FRACTION = 0.8


def _should_append(summary_obj, cap_tokens):
    """True when the running summary has room to accumulate rather than compress."""
    if cap_tokens <= 0:  # unlimited context → never need to compress
        return True
    return estimate_tokens(summary_to_text(summary_obj)) <= APPEND_CAP_FRACTION * cap_tokens


def _fit_summary_candidate(candidate, cap_tokens):
    """Enforce the configured cap locally without making a second provider call."""
    return enforce_cap(candidate, cap_tokens)


# ── Job status helpers ──────────────────────────────────────────────────────

class _SummaryPaused(Exception):
    """Internal control flow when enablement changes during a running job."""


def _require_job_generation(chat_id, job_token):
    if job_token is not None and _job_tokens.get(chat_id) != job_token:
        raise _SummaryPaused


def _release_job_generation(chat_id, job_token):
    """Drop only this worker's generation, never a replacement run's token."""
    if job_token is None:
        return
    with _job_tokens_lock:
        if _job_tokens.get(chat_id) == job_token:
            _job_tokens.pop(chat_id, None)


def _check_job_active(row, require_running):
    if not require_running:
        return
    if row['summary_status'] != 'running' or not row['summary_enabled']:
        raise _SummaryPaused


def _assert_summary_active(chat_id, require_running=False, job_token=None):
    """Re-check the per-chat gate around every potentially slow model call."""
    if not require_running:
        return
    with _job_tokens_lock:
        _require_job_generation(chat_id, job_token)
        with get_db() as conn:
            row = conn.execute(
                'SELECT c.summary_status, c.summary_enabled '
                'FROM chats c WHERE c.id=?',
                (chat_id,),
            ).fetchone()
        if not row:
            raise RuntimeError('Chat was deleted while its summary was running')
        _check_job_active(row, require_running)


def _set_status(chat_id, status=None, detail=None, require_running=False,
                job_token=None):
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
    where = 'id=?'
    if require_running:
        where += " AND summary_status='running'"
    with _job_tokens_lock:
        if job_token is not None and _job_tokens.get(chat_id) != job_token:
            return False
        with get_db() as conn:
            changed = conn.execute(
                f"UPDATE chats SET {', '.join(sets)} WHERE {where}", params
            ).rowcount
        if (job_token is not None and status in ('idle', 'error')
                and _job_tokens.get(chat_id) == job_token):
            _job_tokens.pop(chat_id, None)
        return bool(changed)


def _copy_summary(obj):
    """Copy normalized summary lines so pin reconciliation never mutates its inputs."""
    return {'lines': [dict(line) for line in summary_lines(obj)]}


def _line_key(line):
    section = 'bonds' if line.get('section') == 'bonds' else 'story'
    return section, line.get('text', '')


def _pinned_only(obj):
    return {'lines': [dict(line) for line in summary_lines(obj) if line.get('pinned')]}


def _reconcile_pin_edits(candidate, baseline, current):
    """Apply only pin-state changes from ``current`` to an in-flight candidate.

    Summary text can be rewritten by the model while a user toggles a pin. Comparing
    the last DB state seen by the worker with the latest DB state lets us preserve that
    narrow edit without replacing the newly generated summary with a stale full object.
    """
    result = _copy_summary(candidate)
    baseline_states = {_line_key(line): bool(line.get('pinned')) for line in summary_lines(baseline)}
    current_lines = {_line_key(line): line for line in summary_lines(current)}
    current_states = {key: bool(line.get('pinned')) for key, line in current_lines.items()}

    for key in baseline_states.keys() | current_states.keys():
        before = baseline_states.get(key, False)
        after = current_states.get(key, False)
        if before == after:
            continue
        matches = [line for line in result['lines'] if _line_key(line) == key]
        if matches:
            for line in matches:
                line['pinned'] = after
        elif after:
            result['lines'].append(dict(current_lines[key]))
    return result


def _refresh_pin_edits(chat_id, candidate, baseline, require_running=False,
                       job_token=None):
    """Reconcile pin changes made since ``baseline`` without writing summary state."""
    with _job_tokens_lock:
        _require_job_generation(chat_id, job_token)
        with get_db() as conn:
            row = conn.execute(
                'SELECT c.summary_json, c.summary_status, c.summary_enabled '
                'FROM chats c WHERE c.id=?',
                (chat_id,),
            ).fetchone()
        if not row:
            raise RuntimeError('Chat was deleted while its summary was running')
        _check_job_active(row, require_running)
        current = parse_summary_json(row['summary_json'])
        return _reconcile_pin_edits(candidate, baseline, current), current


def _persist_summary(chat_id, candidate, baseline, watermark, cap_tokens,
                     require_running=False, job_token=None):
    """Atomically reconcile the latest pins and publish one worker checkpoint."""
    with _job_tokens_lock:
        _require_job_generation(chat_id, job_token)
        with get_db() as conn:
            # Serialize this read/modify/write with the dedicated pin endpoint.
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'SELECT c.summary_json, c.summary_status, c.summary_enabled '
                'FROM chats c WHERE c.id=?',
                (chat_id,),
            ).fetchone()
            if not row:
                raise RuntimeError('Chat was deleted while its summary was running')
            _check_job_active(row, require_running)
            current = parse_summary_json(row['summary_json'])
            candidate = _reconcile_pin_edits(candidate, baseline, current)
            candidate, warning = enforce_cap(candidate, cap_tokens)
            serialized = dump_summary_json(candidate)
            conn.execute(
                'UPDATE chats SET summary_json=?, summary_up_to_msg_id=? WHERE id=?',
                (serialized, watermark, chat_id)
            )
        return candidate, candidate, warning


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

def _run_summary_job(chat_id, up_to_msg_id, rebuild=False, require_running=False,
                     job_token=None):
    """Fold messages ``(watermark, up_to_msg_id]`` into the chat's running summary,
    in batches. Persists after each batch. Never raises out — terminal state is written
    to ``summary_status`` (``idle`` on success, ``error`` on failure)."""
    warning = ''
    try:
        _assert_summary_active(
            chat_id, require_running=require_running, job_token=job_token
        )
        settings = get_settings()
        interval = _trigger_interval(settings)
        cap_tokens = _cap_tokens(settings)

        with get_db() as conn:
            row = conn.execute(
                'SELECT summary_json, summary_up_to_msg_id, summary_status '
                'FROM chats WHERE id=?', (chat_id,)
            ).fetchone()
            if not row:
                if require_running:
                    raise RuntimeError('Chat was deleted while its summary was running')
                return
            if require_running and row['summary_status'] != 'running':
                raise _SummaryPaused
            stored_obj = parse_summary_json(row['summary_json'])
            if rebuild:
                # Rebuild entirely in memory. Keep the old DB summary/watermark usable
                # until every batch succeeds, and seed the replacement with all pins.
                summary_obj = _pinned_only(stored_obj)
                watermark = 0
            else:
                summary_obj = stored_obj
                watermark = row['summary_up_to_msg_id'] or 0
            pin_baseline = stored_obj

        if up_to_msg_id is None:
            _set_status(
                chat_id, status='idle', detail='', require_running=require_running,
                job_token=job_token,
            )
            return

        with get_db() as conn:
            msgs = conn.execute(
                'SELECT id, role, content FROM messages '
                'WHERE chat_id=? AND id>? AND id<=? ORDER BY id ASC',
                (chat_id, watermark, up_to_msg_id)
            ).fetchall()

        hide_thinking = settings.get('send_thinking') != '1'
        batch = []
        for msg in msgs:
            content = msg['content']
            if hide_thinking:
                content = strip_thinking_content(content)
            batch.append({'role': msg['role'], 'content': content})
        ids = [m['id'] for m in msgs]
        if not batch:
            _set_status(
                chat_id, status='idle', detail='', require_running=require_running,
                job_token=job_token,
            )
            return

        total = math.ceil(len(batch) / interval)
        for bi in range(total):
            chunk = batch[bi * interval:(bi + 1) * interval]
            chunk_ids = ids[bi * interval:(bi + 1) * interval]
            _set_status(
                chat_id,
                detail=f'Summarizing… (batch {bi + 1}/{total})',
                require_running=require_running,
                job_token=job_token,
            )

            # A pin may have changed while the previous LLM call was running. Feed the
            # latest pin state into this batch as well as reconciling once more at write.
            summary_obj, pin_baseline = _refresh_pin_edits(
                chat_id, summary_obj, pin_baseline,
                require_running=require_running, job_token=job_token,
            )

            # A stored turn may consist entirely of hidden reasoning. Retire those
            # message ids without asking the model to invent visible memory for them.
            visible_chunk = [
                message for message in chunk
                if str(message.get('content') or '').strip()
            ]
            if not visible_chunk:
                if not rebuild:
                    summary_obj, pin_baseline, write_warning = _persist_summary(
                        chat_id,
                        summary_obj,
                        pin_baseline,
                        chunk_ids[-1],
                        cap_tokens,
                        require_running=require_running,
                        job_token=job_token,
                    )
                    if write_warning:
                        warning = write_warning
                continue

            # Accumulate detail while there is headroom; only rewrite/compress the whole
            # summary once it approaches the cap. This keeps the memory additive instead
            # of boiling the same few lines down on every batch.
            append_mode = _should_append(summary_obj, cap_tokens)
            build = build_append_messages if append_mode else build_summarizer_messages
            messages = build(
                summary_to_text(summary_obj), visible_chunk,
                pinned_texts(summary_obj), cap_tokens
            )
            _assert_summary_active(
                chat_id, require_running=require_running, job_token=job_token
            )
            reply = call_summarizer(messages, cap_tokens)
            # A disable may have committed while the HTTP request was in flight. Do not
            # parse, tighten, or publish that now-stale result.
            _assert_summary_active(
                chat_id, require_running=require_running, job_token=job_token
            )
            parsed = parse_summarizer_output(reply)
            candidate = append_summary(summary_obj, parsed) if append_mode else parsed
            folded = merge_pins(candidate, summary_obj)
            summary_obj, batch_warning = _fit_summary_candidate(folded, cap_tokens)
            if batch_warning:
                warning = batch_warning

            if not rebuild:
                summary_obj, pin_baseline, write_warning = _persist_summary(
                    chat_id,
                    summary_obj,
                    pin_baseline,
                    chunk_ids[-1],
                    cap_tokens,
                    require_running=require_running,
                    job_token=job_token,
                )
                if write_warning:
                    warning = write_warning

        if rebuild:
            # Publish a rebuild exactly once so a failed later batch cannot replace a
            # complete prior summary with partial work. The final transaction also
            # folds in a pin edit made during the last model call.
            summary_obj, pin_baseline, write_warning = _persist_summary(
                chat_id,
                summary_obj,
                pin_baseline,
                ids[-1],
                cap_tokens,
                require_running=require_running,
                job_token=job_token,
            )
            if write_warning:
                warning = write_warning

        _set_status(
            chat_id,
            status='idle',
            detail=warning or '',
            require_running=require_running,
            job_token=job_token,
        )
    except _SummaryPaused:
        # Pausing is an expected control path, not a failed summary. Keep the last
        # committed checkpoint (or the complete prior state for rebuilds) and clear
        # progress so the UI can resume cleanly when re-enabled.
        _set_status(
            chat_id,
            status='idle',
            detail='',
            require_running=require_running,
            job_token=job_token,
        )
    except Exception as e:  # noqa: BLE001 — terminal state must always be recorded
        log.exception('Summary job failed for chat %s', chat_id)
        _set_status(
            chat_id,
            status='error',
            detail=str(e)[:300],
            require_running=require_running,
            job_token=job_token,
        )
    finally:
        # A pause may already have moved the row to idle, and deletion leaves no row to
        # update at all. Token ownership, rather than UPDATE rowcount, defines cleanup.
        _release_job_generation(chat_id, job_token)


def _spawn_job(chat_id, up_to_msg_id, rebuild, job_token):
    """Start the worker in a daemon thread (indirection so tests can stub it out)."""
    threading.Thread(
        target=_run_summary_job,
        args=(chat_id, up_to_msg_id, rebuild, True, job_token),
        daemon=True,
    ).start()


# ── Routes ──────────────────────────────────────────────────────────────────

@summaries_bp.route('/api/chats/<int:chat_id>/summary/run', methods=['POST'])
def run_summary(chat_id):
    """Kick off a background summary run and return 202 immediately.

    Body: ``{ "up_to_msg_id": <int>, "rebuild": <bool?> }`` — fold everything after the
    watermark up to that id. A run already in flight is a no-op (409)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object body is required'}), 400
    up_to_msg_id = data.get('up_to_msg_id')
    rebuild = data.get('rebuild', False)
    if isinstance(up_to_msg_id, bool) or not isinstance(up_to_msg_id, int) or up_to_msg_id <= 0:
        return jsonify({'error': 'up_to_msg_id must be a positive integer'}), 400
    if not isinstance(rebuild, bool):
        return jsonify({'error': 'rebuild must be a boolean'}), 400

    job_token = uuid.uuid4().hex
    with _job_tokens_lock:
        with get_db() as conn:
            # Serialize validation + claim with reset and generation registration so
            # an older worker can never borrow this new run's ``running`` state.
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
            if not row:
                return not_found('Chat')
            boundary = conn.execute(
                'SELECT 1 FROM messages WHERE id=? AND chat_id=?',
                (up_to_msg_id, chat_id),
            ).fetchone()
            if not boundary:
                return jsonify({'error': 'up_to_msg_id must identify a message in this chat'}), 400
            if not row['summary_enabled']:
                return jsonify({'error': 'Auto Summaries are disabled for this chat'}), 409
            # Atomic claim: only start when not already running.
            claimed = conn.execute(
                "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
                "WHERE id=? AND summary_status!='running'",
                (chat_id,)
            ).rowcount
            if claimed:
                _job_tokens[chat_id] = job_token

    if not claimed:
        with get_db() as conn:
            fresh = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return jsonify({'already_running': True, **_summary_state(fresh)}), 409

    try:
        _spawn_job(chat_id, up_to_msg_id, rebuild, job_token)
    except Exception as e:  # thread creation failure must not leave a permanent claim
        log.exception('Could not start summary job for chat %s', chat_id)
        _set_status(
            chat_id, status='error', detail=str(e)[:300], require_running=True,
            job_token=job_token,
        )
        return jsonify({'error': 'Could not start summary job'}), 500

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
        # Serialize with the run claim so a reset can never invalidate a worker after
        # observing an outdated idle status.
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not row:
            return not_found('Chat')
        if row['summary_status'] == 'running':
            return jsonify({
                'error': 'Cannot reset a summary while it is running',
                **_summary_state(row),
            }), 409
        conn.execute(
            "UPDATE chats SET summary_json='', summary_up_to_msg_id=NULL, "
            "summary_status='idle', summary_status_detail='' WHERE id=?",
            (chat_id,)
        )
        fresh = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
    return jsonify(_summary_state(fresh))


@summaries_bp.route('/api/chats/<int:chat_id>/summary/pins', methods=['PUT'])
def update_summary_pin(chat_id):
    """Atomically update one summary line's pin state.

    The narrow ``text`` + ``section`` identity avoids sending a stale copy of the whole
    summary back while a background rewrite is publishing new content.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object body is required'}), 400
    text = data.get('text')
    section = data.get('section')
    pinned = data.get('pinned')
    if not isinstance(text, str) or not text.strip():
        return jsonify({'error': 'text must be a non-empty string'}), 400
    text = text.strip()
    if section not in ('story', 'bonds'):
        return jsonify({'error': 'section must be "story" or "bonds"'}), 400
    if not isinstance(pinned, bool):
        return jsonify({'error': 'pinned must be a boolean'}), 400

    with get_db() as conn:
        # Serialize with worker checkpoints so neither side can read stale pin state and
        # then overwrite the other side's committed update.
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not row:
            return not_found('Chat')
        summary_obj = parse_summary_json(row['summary_json'])
        matches = [
            line for line in summary_obj['lines']
            if _line_key(line) == (section, text)
        ]
        if not matches:
            return jsonify({
                'error': 'That summary line no longer exists; refresh and try again',
                **_summary_state(row),
            }), 409
        for line in matches:
            line['pinned'] = pinned
        conn.execute(
            'UPDATE chats SET summary_json=? WHERE id=?',
            (dump_summary_json(summary_obj), chat_id),
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
