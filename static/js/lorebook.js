// ═══════════════════════════════════════════════════════════════════════════
// LOREBOOK
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Resolve which lorebook entries apply to the current conversation.
 * @param {object} book — character_book object from the character card
 * @param {Array}  messages — current state.messages array
 * @param {object} [options]
 * @param {boolean} [options.alwaysInjectAll] — when true, skip the keyword
 *        scan and treat every enabled entry as a hit (still respects
 *        insertion_order, max_entries, dedup).
 * @returns {string[]} ordered, deduplicated content strings to inject
 */
export function resolveLorebookEntries(book, messages, options = {}) {
    if (!book || !book.entries || book.entries.length === 0) return [];
    const maxEntries = book.max_entries || 20;
    const enabled = book.entries.filter(e => e.enabled !== false);

    let combined;
    if (options.alwaysInjectAll) {
        combined = enabled.slice();
    } else {
        const scanDepth = book.scan_depth || 20;
        const constants = enabled.filter(e => e.constant === true);
        const keywords  = enabled.filter(e => e.constant !== true);
        const scanText  = messages.slice(-scanDepth).map(m => m.text || '').join('\n');

        const matched = keywords.filter(entry => {
            if (!entry.keys || entry.keys.length === 0) return false;
            const flags = entry.case_sensitive ? '' : 'i';
            const hitPrimary = entry.keys.some(k => {
                try {
                    return new RegExp('\\b' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', flags).test(scanText);
                } catch {
                    return scanText.toLowerCase().includes(k.toLowerCase());
                }
            });
            if (!hitPrimary) return false;
            if (entry.selective && entry.secondary_keys?.length > 0) {
                return entry.secondary_keys.some(k => {
                    try {
                        return new RegExp('\\b' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', flags).test(scanText);
                    } catch {
                        return scanText.toLowerCase().includes(k.toLowerCase());
                    }
                });
            }
            return true;
        });

        combined = [...constants, ...matched];
    }

    combined.sort((a, b) => (a.insertion_order ?? 100) - (b.insertion_order ?? 100));

    const seen = new Set();
    return combined
        .filter(e => e.content && !seen.has(e.content) && seen.add(e.content))
        .slice(0, maxEntries)
        .map(e => e.content);
}
