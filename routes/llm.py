"""LLM proxy routes — forward chat requests to OpenAI-compatible endpoints."""

import json
import logging

import requests as http_requests
from flask import Blueprint, request, jsonify, Response, stream_with_context

from routes.settings import get_settings

log = logging.getLogger('cozy')

llm_bp = Blueprint('llm', __name__)


def _llm_settings():
    """Return (endpoint, api_key, model) from DB."""
    s = get_settings()
    return s.get('api_endpoint', ''), s.get('api_key', ''), s.get('api_model', '')


@llm_bp.route('/api/llm/models', methods=['GET'])
def list_models():
    endpoint, api_key, _ = _llm_settings()
    if not endpoint:
        return jsonify({'error': 'No endpoint configured'}), 400
    url = endpoint.rstrip('/') + '/models'
    headers = {}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    try:
        r = http_requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        body = r.json()
        data = body.get('data', [])
        models = sorted(m['id'] for m in data)
        model_details = {m['id']: m['context_length'] for m in data if m.get('context_length')}
        return jsonify({'models': models, 'model_details': model_details})
    except http_requests.RequestException as e:
        return jsonify({'error': str(e)}), 502


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
        return jsonify({'ok': False, 'error': str(e)}), 502


@llm_bp.route('/api/llm/chat', methods=['POST'])
def llm_chat():
    endpoint, api_key, _ = _llm_settings()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint configured'}), 400
    data = request.get_json(force=True) or {}
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
        try:
            with http_requests.post(url, json=data, headers=headers, timeout=120, stream=True) as r:
                r.raise_for_status()
                r.encoding = 'utf-8'
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith('data: [DONE]'):
                        yield 'data: [DONE]\n\n'
                        break
                    if line.startswith('data: '):
                        yield line + '\n\n'
                        # Collect tokens for response log
                        try:
                            chunk = json.loads(line[6:])
                            tok = chunk.get('choices', [{}])[0].get('delta', {}).get('content', '')
                            if tok:
                                full_response.append(tok)
                                token_count += 1
                        except (json.JSONDecodeError, IndexError):
                            pass
        except http_requests.RequestException as e:
            log.error('  LLM error: %s', e)
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
        finally:
            log.info('── LLM RESPONSE ──')
            log.info('  Tokens: %d chunks', token_count)
            if log.isEnabledFor(logging.DEBUG):
                text = ''.join(full_response)
                preview = (text[:200] + '\u2026') if len(text) > 200 else text
                log.debug('  Text:   %s', preview)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
