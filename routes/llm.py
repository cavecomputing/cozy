"""LLM proxy routes — forward chat requests to OpenAI-compatible endpoints."""

import json
import logging

import requests as http_requests
from flask import Blueprint, request, jsonify, Response, stream_with_context

from routes.settings import get_settings

log = logging.getLogger('cozy')

llm_bp = Blueprint('llm', __name__)


def _error_detail(e):
    """Append the upstream response body to a RequestException message.

    Providers put the actual reason for a 4xx (bad model id, rejected
    parameter, …) in the response body; raise_for_status() alone reports
    only the status line.
    """
    resp = getattr(e, 'response', None)
    if resp is not None:
        body = (resp.text or '').strip()
        if body:
            return f'{e} — {body[:500]}'
    return str(e)


def _llm_settings():
    """Return (endpoint, api_key, model) from DB."""
    s = get_settings()
    return s.get('api_endpoint', ''), s.get('api_key', ''), s.get('api_model', '')


@llm_bp.route('/api/llm/models', methods=['GET'])
def list_models():
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
        body = r.json()
        reply = body.get('choices', [{}])[0].get('message', {}).get('content', '')
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

    token_count = 0

    def generate():
        nonlocal token_count
        full_response = []
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
                    detail = (r.text or '').strip()
                    msg = f'{r.status_code} {r.reason} from {url}'
                    if detail:
                        msg += f' — {detail[:500]}'
                    log.error('  LLM error: %s', msg)
                    yield f'data: {json.dumps({"error": msg})}\n\n'
                    return
                r.encoding = 'utf-8'
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith('data: [DONE]'):
                        saw_done = True
                        yield 'data: [DONE]\n\n'
                        break
                    if line.startswith('data: '):
                        yield line + '\n\n'
                        # Collect tokens + finish_reason for response log
                        try:
                            chunk = json.loads(line[6:])
                            choice = chunk.get('choices', [{}])[0]
                            tok = choice.get('delta', {}).get('content', '')
                            if tok:
                                full_response.append(tok)
                                token_count += 1
                            if choice.get('finish_reason'):
                                finish_reason = choice['finish_reason']
                        except (json.JSONDecodeError, IndexError):
                            pass
        except http_requests.RequestException as e:
            log.error('  LLM error: %s', e)
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
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
            if log.isEnabledFor(logging.DEBUG):
                text = ''.join(full_response)
                preview = (text[:200] + '\u2026') if len(text) > 200 else text
                log.debug('  Text:   %s', preview)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
