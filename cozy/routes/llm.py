"""LLM proxy routes — forward chat requests to OpenAI-compatible endpoints."""

import json
import logging
from urllib.parse import urlsplit

import requests as http_requests
from flask import Blueprint, request, jsonify, Response, stream_with_context

from cozy.routes.settings import get_settings

log = logging.getLogger('cozy')

llm_bp = Blueprint('llm', __name__)


def _provider_message(body):
    """The reason a provider gave in an error body, or '' when there is none.

    OpenAI-style APIs nest it as {"error": {"message": ...}}; others use a bare
    "error", "message" or "detail" string. A non-JSON body is passed through
    unless it is an HTML error page, which reads as noise in a toast.
    """
    body = (body or '').strip()
    try:
        data = json.loads(body)
    except ValueError:
        return '' if body.startswith('<') else body[:300]
    if not isinstance(data, dict):
        return body[:300]
    error = data.get('error')
    if isinstance(error, dict):
        error = error.get('message')
    message = error or data.get('message') or data.get('detail')
    return message.strip()[:300] if isinstance(message, str) else body[:300]


def _status_error(status, reason, url, body):
    """An HTTP error from the model server as the sentence a user sees.

    The status a newcomer is most likely to meet gets the fix it usually
    means; the provider's own reason follows, since it names the bad model
    id or rejected parameter when there is one.
    """
    hint = {
        401: 'The server turned down the API key.',
        402: 'The provider says the account is out of credits.',
        403: 'The server refused the request. Check the API key and that it may use this model.',
        404: f'Nothing found at {url}. Check that the endpoint ends in /v1 and the model name is right.',
        429: 'The server is rate limiting requests. Wait a moment, then retry.',
    }.get(status)
    message = f'{status} {reason}.'
    if hint:
        message += f' {hint}'
    detail = _provider_message(body)
    if detail:
        message += f' It said: {detail}'
    return message


def _error_detail(e):
    """A RequestException as the message a user sees.

    A server that can't be reached at all gets a sentence naming the host
    instead of urllib3's retry chain, and an HTTP error status gets
    _status_error(). Certificate and proxy failures keep their own wording,
    since "couldn't reach" would point at the wrong fix.
    """
    if isinstance(e, http_requests.ConnectionError) and not isinstance(
            e, (http_requests.exceptions.SSLError, http_requests.exceptions.ProxyError)):
        host = urlsplit(getattr(e.request, 'url', '')).netloc or 'the model server'
        return f"Couldn't reach {host}. Is the model server running?"
    resp = getattr(e, 'response', None)
    if resp is not None:
        url = getattr(e.request, 'url', None) or resp.url
        return _status_error(resp.status_code, resp.reason, url, resp.text)
    return str(e)


def _llm_settings():
    """Return (endpoint, api_key, model) from DB."""
    s = get_settings()
    return s.get('api_endpoint', ''), s.get('api_key', ''), s.get('api_model', '')


def _summary_llm_settings():
    """Return summarizer settings, falling back to the main connection per field."""
    s = get_settings()
    return (
        s.get('summary_api_endpoint') or s.get('api_endpoint', ''),
        s.get('summary_api_key') or s.get('api_key', ''),
        s.get('summary_api_model') or s.get('api_model', ''),
    )


@llm_bp.route('/api/llm/models', methods=['GET'])
def list_models():
    profile = request.args.get('profile', 'main')
    if profile not in ('main', 'summary'):
        return jsonify({'ok': False, 'error': 'Invalid model profile'}), 400

    if profile == 'summary':
        endpoint, api_key, _ = _summary_llm_settings()
    else:
        endpoint, api_key, _ = _llm_settings()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint configured'}), 400
    url = endpoint.rstrip('/') + '/models'
    headers = {}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    try:
        r = http_requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        body = r.json()
        # OpenAI-compatible APIs use {"data": [...]}; some providers
        # (e.g. aionlabs) return {"models": [...]} instead.
        data = body.get('data') or body.get('models') or []
        models = sorted(m.get('id', '') for m in data if m.get('id'))
        model_details = {m['id']: m['context_length'] for m in data if m.get('context_length')}
        return jsonify({'ok': True, 'models': models, 'model_details': model_details})
    except http_requests.RequestException as e:
        return jsonify({'ok': False, 'error': _error_detail(e)}), 502


@llm_bp.route('/api/llm/test', methods=['POST'])
def test_llm():
    endpoint, api_key, model = _llm_settings()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint configured'}), 400
    if not model:
        return jsonify({'ok': False, 'error': 'No model selected'}), 400
    url = endpoint.rstrip('/') + '/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': 'Say "hello" and nothing else.'}],
        'max_tokens': 10,
    }
    try:
        r = http_requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        try:
            body = r.json()
        except ValueError:
            return jsonify({'ok': False, 'error': 'Upstream returned a non-JSON response'}), 502
        # Providers may return null content (reasoning-only output), an
        # empty choices list, or a non-dict body — none of those may 500.
        message = {}
        if isinstance(body, dict):
            choices = body.get('choices') or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get('message') or {}
        reply = message.get('content') or ''
        if not isinstance(reply, str):
            reply = str(reply)
        return jsonify({'ok': True, 'reply': reply.strip()})
    except http_requests.RequestException as e:
        return jsonify({'ok': False, 'error': _error_detail(e)}), 502


@llm_bp.route('/api/llm/chat', methods=['POST'])
def llm_chat():
    endpoint, api_key, _ = _llm_settings()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint configured'}), 400
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'ok': False, 'error': 'Invalid or missing JSON body'}), 400
    if not data.get('model'):
        return jsonify({'ok': False, 'error': 'No model specified'}), 400

    data['stream'] = True
    url = endpoint.rstrip('/') + '/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    # Log safe request metadata at INFO. Full prompt content can contain
    # private chat text, so keep previews out of production logs.
    log.info('── LLM REQUEST ──')
    log.info('  URL:      %s', url)
    log.info('  Model:    %s', data.get('model'))
    log.info('  Messages: %d', len(data.get('messages', [])))
    if log.isEnabledFor(logging.DEBUG):
        for msg in data.get('messages', []):
            role = msg.get('role', '?')
            content = msg.get('content', '')
            preview = (content[:120] + '\u2026') if len(content) > 120 else content
            log.debug('    [%s] %s', role, preview)
    samplers = {k: v for k, v in data.items()
                if k not in ('model', 'messages', 'stream')}
    if samplers:
        log.info('  Samplers: %s', json.dumps(samplers))

    def generate():
        token_count = 0
        debug_enabled = log.isEnabledFor(logging.DEBUG)
        full_response = [] if debug_enabled else None
        # Diagnostics for truncation reports: why did the stream stop?
        #   finish_reason 'length' -> hit max_tokens (raise it / reasoning ate it)
        #   finish_reason 'stop'   -> model decided it was done
        #   saw_done False         -> upstream/proxy severed the stream early
        finish_reason = None
        saw_done = False
        try:
            # (connect, read) tuple: bound the connection attempt at 10s, but
            # never time out waiting for tokens. Slow local backends (llama.cpp
            # on weak hardware) can take minutes on prompt prefill before the
            # first token; the user stops a runaway generation with the Stop
            # button instead. See issue #6.
            with http_requests.post(url, json=data, headers=headers, timeout=(10, None), stream=True) as r:
                if not r.ok:
                    # Read the body before raising — the provider's actual
                    # complaint (bad model id, rejected parameter, …) lives
                    # there, and raise_for_status() discards it.
                    r.encoding = 'utf-8'
                    log.error('  LLM error: %s %s from %s — %s',
                              r.status_code, r.reason, url, (r.text or '').strip()[:500])
                    msg = _status_error(r.status_code, r.reason, url, r.text)
                    yield f'data: {json.dumps({"error": msg})}\n\n'
                    return
                # Split on \n alone. Decoded lines go through str.splitlines(),
                # which also breaks at U+2028, U+2029 and U+0085 — characters
                # JSON may carry unescaped — and the halves of that event were
                # dropped as unparseable.
                for raw in r.iter_lines(delimiter=b'\n'):
                    line = raw.decode('utf-8', 'replace').rstrip('\r')
                    # SSE allows "data:" with or without the space after it;
                    # the browser reads this proxy's own "data: " form.
                    if not line.startswith('data:'):
                        continue
                    payload = line[5:].removeprefix(' ')
                    if payload.startswith('[DONE]'):
                        saw_done = True
                        yield 'data: [DONE]\n\n'
                        break
                    yield f'data: {payload}\n\n'
                    # Collect tokens + finish_reason for response log
                    try:
                        choice = json.loads(payload).get('choices', [{}])[0]
                        tok = (choice.get('delta') or {}).get('content', '')
                        if tok:
                            if full_response is not None:
                                full_response.append(tok)
                            token_count += 1
                        if choice.get('finish_reason'):
                            finish_reason = choice['finish_reason']
                    except (ValueError, IndexError, AttributeError, TypeError):
                        # Logging only: a chunk shaped unlike the rest must not
                        # end the reply it belongs to.
                        pass
        except http_requests.RequestException as e:
            # The raw exception for the log, the readable one for the toast.
            log.error('  LLM error: %s', e)
            yield f'data: {json.dumps({"error": _error_detail(e)})}\n\n'
        finally:
            log.info('── LLM RESPONSE ──')
            log.info('  Tokens: %d chunks', token_count)
            log.info('  Finish: %s', finish_reason or '(none)')
            if not saw_done and finish_reason is None:
                # No [DONE] and no finish_reason: the upstream connection ended
                # mid-stream. Usually a reverse proxy / load balancer timeout or
                # response buffering in front of the endpoint. Surfaces to the
                # user as a silently truncated reply.
                log.warning('  Stream ended without [DONE] or finish_reason '
                            '(upstream proxy closed/buffered the connection early?)')
            elif finish_reason == 'length':
                log.info('  Note: hit max_tokens — raise sampler_max_tokens, or '
                         'reasoning output is consuming the token budget.')
            if full_response is not None:
                text = ''.join(full_response)
                preview = (text[:200] + '\u2026') if len(text) > 200 else text
                log.debug('  Text:   %s', preview)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
