"""LLM proxy routes — forward chat requests to OpenAI-compatible endpoints.

An endpoint on anthropic.com, or one whose path ends in /messages, speaks
Claude's Messages API instead. The browser never knows: requests are
translated on the way out and replies back into the Chat Completions shape
api.js already reads.
"""

import json
import logging
import time
from urllib.parse import urlsplit

import requests as http_requests
from flask import Blueprint, request, jsonify, Response, stream_with_context

from cozy.routes.settings import get_settings
from cozy.shared import json_body

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


# ── Claude's Messages API ───────────────────────────────────────────────────

ANTHROPIC_VERSION = '2026-10-21'

# The Chat Completions fields Claude also takes. Everything else Cozy sends is a
# sampler it doesn't have, and the Messages API rejects fields it doesn't know.
# temperature, top_p and top_k are among them on this API version.
ANTHROPIC_FIELDS = {'max_tokens', 'stop_sequences', 'metadata', 'thinking', 'output_config',
                    'service_tier', 'inference_geo', 'cache_control', 'stream'}

CACHED = {'type': 'ephemeral'}


def is_anthropic(endpoint):
    """Whether an endpoint speaks Claude's Messages API rather than Chat Completions."""
    parts = urlsplit((endpoint or '').strip())
    host = (parts.hostname or '').lower()
    return (host == 'anthropic.com' or host.endswith('.anthropic.com')
            or parts.path.rstrip('/').endswith('/messages'))


def _upstream(endpoint, api_key, path):
    """The URL and headers for one call to the model server.

    ``path`` is the Chat Completions name, 'chat/completions' or 'models'. A
    Claude endpoint may be typed as the bare host, the /v1 base, or the full
    /v1/messages URL; all three reach the same two paths.
    """
    base = endpoint.strip().rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if not is_anthropic(endpoint):
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return f'{base}/{path}', headers
    base = base.removesuffix('/messages')
    if not urlsplit(base).path:
        base += '/v1'
    headers['anthropic-version'] = ANTHROPIC_VERSION
    if api_key:
        headers['x-api-key'] = api_key
    return f'{base}/{"messages" if path == "chat/completions" else path}', headers


def to_anthropic(payload):
    """A Chat Completions payload as a Messages API request.

    System turns move to the top-level ``system`` field, and two cache
    breakpoints go in: one on the system prompt, which outlives many turns, and
    one on the latest reply, the end of the history the next request repeats.
    The final user turn is left out of the cache because the post-history
    template rewrites it every turn, so a breakpoint there would never be read.
    """
    body = {key: value for key, value in payload.items() if key in ANTHROPIC_FIELDS}
    body['model'] = payload.get('model')
    body.setdefault('max_tokens', 8192)
    stop = payload.get('stop')
    if stop and 'stop_sequences' not in body:
        body['stop_sequences'] = [stop] if isinstance(stop, str) else list(stop)
    turns = payload.get('messages') or []
    system = '\n\n'.join(m['content'] for m in turns if m.get('role') == 'system' and m.get('content'))
    if system:
        body['system'] = [{'type': 'text', 'text': system, 'cache_control': CACHED}]
    messages = [{'role': m['role'], 'content': m['content']} for m in turns
                if m.get('role') in ('user', 'assistant') and m.get('content')]
    for message in reversed(messages):
        if message['role'] == 'assistant':
            message['content'] = [{'type': 'text', 'text': message['content'], 'cache_control': CACHED}]
            break
    body['messages'] = messages
    return body


def _finish_reason(stop_reason):
    return 'length' if stop_reason == 'max_tokens' else 'stop'


def from_anthropic_message(body):
    """A non-streaming Messages response in the Chat Completions shape.

    Anything that isn't a Messages response passes through untouched, so the
    caller's own checks for a malformed body still apply.
    """
    if not isinstance(body, dict) or not isinstance(body.get('content'), list):
        return body
    text = ''.join(block.get('text', '') for block in body['content']
                   if isinstance(block, dict) and block.get('type') == 'text')
    return {'choices': [{'message': {'role': 'assistant', 'content': text},
                         'finish_reason': _finish_reason(body.get('stop_reason'))}]}


def from_anthropic_event(event):
    """One Messages stream event as the chunk the browser reads.

    Returns a Chat Completions chunk, an ``{"error": ...}`` the browser shows,
    '[DONE]' at the end of the message, or None for the bookkeeping events
    (message_start, ping, block starts and stops) that carry nothing to show.
    """
    kind = event.get('type')
    if kind == 'content_block_delta':
        delta = event.get('delta') or {}
        if delta.get('type') == 'text_delta':
            return {'choices': [{'delta': {'content': delta.get('text', '')}}]}
        if delta.get('type') == 'thinking_delta':
            return {'choices': [{'delta': {'reasoning_content': delta.get('thinking', '')}}]}
    elif kind == 'message_delta':
        stop_reason = (event.get('delta') or {}).get('stop_reason')
        if stop_reason == 'refusal':
            return {'error': 'Claude declined to continue this reply.'}
        if stop_reason:
            return {'choices': [{'delta': {}, 'finish_reason': _finish_reason(stop_reason)}]}
    elif kind == 'message_stop':
        return '[DONE]'
    elif kind == 'error':
        error = event.get('error') or {}
        return {'error': f"Claude's API reported an error: {error.get('message') or error.get('type') or 'unknown'}"}
    return None


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


def _listed_models(body):
    """The models in a /models response, each a dict with a text ``id``.

    OpenAI-compatible servers answer ``{"data": [...]}``. Some providers
    (aionlabs) use ``{"models": [...]}``, some send the list with nothing
    around it, and some list bare names instead of objects.
    """
    if isinstance(body, dict):
        body = body.get('data') or body.get('models')
    if not isinstance(body, list):
        return []
    models = ({'id': m} if isinstance(m, str) else m for m in body)
    return [m for m in models if isinstance(m, dict) and m.get('id') and isinstance(m['id'], str)]


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
    url, headers = _upstream(endpoint, api_key, 'models')
    # Claude's list pages at 20 by default.
    params = {'limit': 1000} if is_anthropic(endpoint) else None
    try:
        r = http_requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = _listed_models(r.json())
        models = sorted(m['id'] for m in data)
        # Claude names the context window max_input_tokens.
        model_details = {m['id']: m.get('context_length') or m['max_input_tokens']
                         for m in data if m.get('context_length') or m.get('max_input_tokens')}
        return jsonify({'ok': True, 'models': models, 'model_details': model_details})
    except http_requests.RequestException as e:
        error = _error_detail(e)
        log.warning('Model list failed: %s', error)
        return jsonify({'ok': False, 'error': error}), 502


@llm_bp.route('/api/llm/test', methods=['POST'])
def test_llm():
    endpoint, api_key, model = _llm_settings()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint configured'}), 400
    if not model:
        return jsonify({'ok': False, 'error': 'No model selected'}), 400
    url, headers = _upstream(endpoint, api_key, 'chat/completions')
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': 'Say "hello" and nothing else.'}],
        'max_tokens': 10,
    }
    anthropic = is_anthropic(endpoint)
    if anthropic:
        # Current Claude models always think, and ten tokens can run out
        # before the hello does.
        payload = to_anthropic({**payload, 'max_tokens': 1024})
    try:
        r = http_requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        try:
            body = r.json()
        except ValueError:
            return jsonify({'ok': False, 'error': 'Upstream returned a non-JSON response'}), 502
        if anthropic:
            body = from_anthropic_message(body)
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
        error = _error_detail(e)
        log.warning('Connection test failed: %s', error)
        return jsonify({'ok': False, 'error': error}), 502


@llm_bp.route('/api/llm/chat', methods=['POST'])
def llm_chat():
    endpoint, api_key, _ = _llm_settings()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint configured'}), 400
    data = json_body()
    if not data:
        return jsonify({'ok': False, 'error': 'Invalid or missing JSON body'}), 400
    if not data.get('model'):
        return jsonify({'ok': False, 'error': 'No model specified'}), 400

    data['stream'] = True
    url, headers = _upstream(endpoint, api_key, 'chat/completions')
    anthropic = is_anthropic(endpoint)

    # One line at INFO says what was asked of which server. Chat text is
    # private, so previews and the samplers (Extra request parameters can
    # carry anything) wait for DEBUG, and credentials typed into the endpoint
    # never reach the log.
    parts = urlsplit(url)
    logged_url = parts._replace(netloc=parts.netloc.rpartition('@')[2], query='').geturl()
    log.info('Chat request: model %s, %d messages, to %s',
             data.get('model'), len(data.get('messages', [])), logged_url)
    if log.isEnabledFor(logging.DEBUG):
        for msg in data.get('messages', []):
            role = msg.get('role', '?')
            content = msg.get('content', '')
            preview = (content[:120] + '\u2026') if len(content) > 120 else content
            log.debug('  [%s] %s', role, preview)
        samplers = {k: v for k, v in data.items()
                    if k not in ('model', 'messages', 'stream')}
        if samplers:
            log.debug('  Samplers: %s', json.dumps(samplers))

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
        error = None
        stopped = False
        started = time.monotonic()
        first_chunk_after = None
        try:
            # (connect, read) tuple: bound the connection attempt at 10s, but
            # never time out waiting for tokens. Slow local backends (llama.cpp
            # on weak hardware) can take minutes on prompt prefill before the
            # first token; the user stops a runaway generation with the Stop
            # button instead. See issue #6.
            body = to_anthropic(data) if anthropic else data
            with http_requests.post(url, json=body, headers=headers, timeout=(10, None), stream=True) as r:
                if not r.ok:
                    # Read the body before raising — the provider's actual
                    # complaint (bad model id, rejected parameter, …) lives
                    # there, and raise_for_status() discards it.
                    r.encoding = 'utf-8'
                    error = (f'{r.status_code} {r.reason} from {logged_url}: '
                             f'{(r.text or "").strip()[:500]}')
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
                    if anthropic:
                        try:
                            chunk = from_anthropic_event(json.loads(payload))
                        except (ValueError, AttributeError):
                            continue
                        if chunk is None:
                            continue
                        payload = chunk if chunk == '[DONE]' else json.dumps(chunk)
                    if payload.startswith('[DONE]'):
                        saw_done = True
                        yield 'data: [DONE]\n\n'
                        break
                    if first_chunk_after is None:
                        first_chunk_after = time.monotonic() - started
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
            error = e
            yield f'data: {json.dumps({"error": _error_detail(e)})}\n\n'
        except GeneratorExit:
            # The browser went away: the Stop button, or a closed tab.
            stopped = True
            raise
        finally:
            took = time.monotonic() - started
            first = '' if first_chunk_after is None else f', first after {first_chunk_after:.1f}s'
            if error:
                log.error('Chat request failed after %.1fs: %s', took, error)
            elif stopped:
                log.info('Chat reply stopped after %.1fs%s: %d chunks', took, first, token_count)
            elif not saw_done and finish_reason is None:
                # No [DONE] and no finish_reason: the upstream connection ended
                # mid-stream. Usually a reverse proxy / load balancer timeout or
                # response buffering in front of the endpoint. Surfaces to the
                # user as a silently truncated reply.
                log.warning('Chat reply ended without [DONE] or a finish reason after '
                            '%.1fs%s: %d chunks (a proxy closed or buffered the stream?)',
                            took, first, token_count)
            else:
                # 'length' means max_tokens ran out, often to reasoning output.
                log.info('Chat reply in %.1fs%s: %d chunks, finish %s',
                         took, first, token_count, finish_reason or '(none)')
            if full_response is not None:
                text = ''.join(full_response)
                preview = (text[:200] + '\u2026') if len(text) > 200 else text
                log.debug('  Text:   %s', preview)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
