// ═══════════════════════════════════════════════════════════════════════════
// ENDPOINT FORMAT — which wire format an endpoint gets
// ═══════════════════════════════════════════════════════════════════════════
// Pure logic, free of DOM and app state so it stays importable under bare
// `node` (see Testing gotchas in CLAUDE.md).

/**
 * Whether *endpoint* speaks Claude's Messages API rather than Chat
 * Completions: an anthropic.com address, or a path ending in /messages.
 * Mirrors is_anthropic() in cozy/routes/llm.py, which makes the real choice.
 */
export function isClaudeEndpoint(endpoint) {
    const text = String(endpoint ?? '').trim();
    let host = '';
    let path = text.split(/[?#]/)[0];
    if (URL.canParse(text)) ({ hostname: host, pathname: path } = new URL(text));
    return host === 'anthropic.com' || host.endsWith('.anthropic.com')
        || path.replace(/\/+$/, '').endsWith('/messages');
}
