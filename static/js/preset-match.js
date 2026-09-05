// ═══════════════════════════════════════════════════════════════════════════
// PRESET MATCH — name lookup for the /prompt and /api slash commands
// ═══════════════════════════════════════════════════════════════════════════
// Pure logic, free of DOM and app state so it stays importable under bare
// `node` (see Testing gotchas in CLAUDE.md).

/**
 * Resolve a typed preset name against [{id, name}, …].
 *
 * Case-insensitive exact match wins; otherwise a unique case-insensitive
 * prefix match wins. Anything else resolves to an error carrying the names
 * the caller should offer: the ambiguous subset, or the whole list.
 */
export function matchPresetByName(presets, query) {
    const list = Array.isArray(presets) ? presets.filter(p => typeof p?.name === 'string') : [];
    const names = list.map(p => p.name);
    const q = String(query ?? '').trim().toLowerCase();
    if (!q) return { error: 'missing', candidates: names };
    const exact = list.find(p => p.name.toLowerCase() === q);
    if (exact) return { preset: exact };
    const prefixed = list.filter(p => p.name.toLowerCase().startsWith(q));
    if (prefixed.length === 1) return { preset: prefixed[0] };
    if (prefixed.length > 1) return { error: 'ambiguous', candidates: prefixed.map(p => p.name) };
    return { error: 'unknown', candidates: names };
}
