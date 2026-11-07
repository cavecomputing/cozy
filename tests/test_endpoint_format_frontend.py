import json

from helpers import run_node_module

from cozy.routes.llm import is_anthropic


ENDPOINTS = [
    'https://api.anthropic.com/v1',
    'https://API.Anthropic.com/v1/',
    'https://anthropic.com',
    'https://gateway.example.com/claude/v1/messages',
    'http://127.0.0.1:8080/v1/messages/',
    'https://openrouter.ai/api/v1',
    'http://127.0.0.1:5056/v1',
    'https://notanthropic.com/v1',
    'https://example.com/v1/messages-archive',
    'api.anthropic.com/v1',
    '',
]


def test_claude_endpoint_check_agrees_with_the_server():
    """The API page's "Claude Messages API" hint must name the format llm.py actually picks."""
    expected = [is_anthropic(e) for e in ENDPOINTS]
    out = run_node_module(f"""
        import {{ isClaudeEndpoint }} from './static/js/endpoint-format.js';
        console.log(JSON.stringify({json.dumps(ENDPOINTS)}.map(isClaudeEndpoint)));
    """)
    assert json.loads(out) == expected
    assert expected[:5] == [True] * 5 and not any(expected[5:])
