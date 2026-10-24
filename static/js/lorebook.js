// ═══════════════════════════════════════════════════════════════════════════
// LOREBOOK
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Resolve which lorebook entries apply to the current conversation.
 * @param {object} book — character_book object from the character card
 * @param {Array}  messages — current state.messages array
 * @returns {string[]} ordered, deduplicated content strings to inject
 */
export function resolveLorebookEntries(book, messages) {
    if (!book || !book.entries || book.entries.length === 0) return [];
    const maxEntries = book.max_entries || 20;
    const enabled = book.entries.filter(e => e.enabled !== false);

    const scanDepth = book.scan_depth || 20;
    const constants = enabled.filter(e => e.constant === true);
    const keywords  = enabled.filter(e => e.constant !== true);
    const scanText  = messages.slice(-scanDepth).map(m => m.text || '').join('\n');

    const matched = keywords.filter(entry => {
        if (!entry.keys || entry.keys.length === 0) return false;
        const flags = entry.case_sensitive ? '' : 'i';
        // A key is a literal, so it is escaped and must not touch a word
        // character on either side. Not \b: that needs a word character at
        // the key's own edge, so "Öl", "café", "東京" or "C++" never matched.
        // Non-ASCII counts as a separator, as in SillyTavern.
        // A key that still won't compile falls back to a substring test
        // rather than dropping the entry.
        const hits = k => {
            try {
                return new RegExp('(?<!\\w)' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?!\\w)', flags).test(scanText);
            } catch {
                return scanText.toLowerCase().includes(k.toLowerCase());
            }
        };
        if (!entry.keys.some(hits)) return false;
        if (entry.selective && entry.secondary_keys?.length > 0) {
            return entry.secondary_keys.some(hits);
        }
        return true;
    });

    const combined = [...constants, ...matched];
    combined.sort((a, b) => (a.insertion_order ?? 100) - (b.insertion_order ?? 100));

    const seen = new Set();
    return combined
        .filter(e => e.content && !seen.has(e.content) && seen.add(e.content))
        .slice(0, maxEntries)
        .map(e => e.content);
}
