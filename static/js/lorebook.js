// ═══════════════════════════════════════════════════════════════════════════
// LOREBOOK
// ═══════════════════════════════════════════════════════════════════════════
import { parseThinkingContent } from './thinking.js';

// A key compiles to the same pattern every time, and one context analysis asks
// for every key of the book once per trial assembly, several times a send.
const keyPatterns = new Map();

// A key is a literal, so it is escaped and must not touch a word character on
// either side. Not \b: that needs a word character at the key's own edge, so
// "Öl", "café", "東京" or "C++" never matched. Non-ASCII counts as a
// separator, as in SillyTavern.
function keyPattern(key, flags) {
    const cacheKey = `${flags}:${key}`;
    let pattern = keyPatterns.get(cacheKey);
    if (!pattern) {
        if (keyPatterns.size >= 5000) keyPatterns.clear();
        pattern = new RegExp('(?<!\\w)' + key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?!\\w)', flags);
        keyPatterns.set(cacheKey, pattern);
    }
    return pattern;
}

/**
 * Resolve which lorebook entries apply to the current conversation.
 * @param {object} book — character_book object from the character card
 * @param {Array}  messages — current state.messages array
 * @param {Map}    [resolved] — results by scanned text, kept by a caller that
 *   resolves the same book over and over. Only the last `scan_depth` messages
 *   are scanned, so trial assemblies that differ at the old end of the window
 *   all scan the same text. The caller must drop the map once the book can
 *   have changed.
 * @returns {string[]} ordered, deduplicated content strings to inject
 */
export function resolveLorebookEntries(book, messages, resolved = null) {
    if (!book || !book.entries || book.entries.length === 0) return [];
    const maxEntries = book.max_entries || 20;
    const enabled = book.entries.filter(e => e.enabled !== false);

    const scanDepth = book.scan_depth || 20;
    const constants = enabled.filter(e => e.constant === true);
    const keywords  = enabled.filter(e => e.constant !== true);
    // Reasoning is never sent, so a key it mentions has nothing to answer.
    const scanText  = messages.slice(-scanDepth)
        .map(m => parseThinkingContent(m.text || '').response || '').join('\n');
    if (resolved?.has(scanText)) return resolved.get(scanText);

    const matched = keywords.filter(entry => {
        if (!entry.keys || entry.keys.length === 0) return false;
        const flags = entry.case_sensitive ? '' : 'i';
        // A key that won't compile falls back to a substring test rather
        // than dropping the entry.
        const hits = k => {
            try {
                return keyPattern(k, flags).test(scanText);
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
    const contents = combined
        .filter(e => e.content && !seen.has(e.content) && seen.add(e.content))
        .slice(0, maxEntries)
        .map(e => e.content);
    resolved?.set(scanText, contents);
    return contents;
}
