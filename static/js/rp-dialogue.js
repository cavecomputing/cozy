// ═══════════════════════════════════════════════════════════════════════════
// RP DIALOGUE — recognising quoted speech inside a reply
//
// Split out of main.js so the matching can be exercised under bare node:
// `marked` is a CDN global and can't be imported there, but deciding "is this
// quoted speech, and where does it end" is plain string work.
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Opening mark → the closing marks allowed to end it.
 *
 * A model writing German or French quotes the way that language does, not the
 * way English does, so recognising only the English pair leaves those replies
 * rendered as flat narration:
 *
 *   "…"   straight — most English output
 *   “…”   English curly
 *   „…“   German: opens low (U+201E) and closes with the very mark English
 *         *opens* with (U+201C), which is why it looks broken to English eyes.
 *         U+201D is accepted too, since models often emit it as the closer.
 *   »…«   German/Danish guillemets, pointing inward
 *   «…»   French/Swiss/Russian guillemets, pointing outward
 *   「…」  Japanese corner brackets — the standard marks for speech
 *   『…』  Japanese white corner brackets, for quotes within quotes
 *
 * Single quotes are deliberately absent. ' and U+2019 double as apostrophes,
 * so "don't" would open a quotation that never closes and swallow the rest of
 * the line.
 */
export const RP_DIALOGUE_PAIRS = [
    ['"', '"'],
    ['“', '”'],
    ['„', '“”'],
    ['»', '«'],
    ['«', '»'],
    ['「', '」'],
    ['『', '』'],
];

export const RP_DIALOGUE_OPENERS = RP_DIALOGUE_PAIRS.map(([open]) => open);

// None of the marks above are regex metacharacters, but the pattern is built
// from data — escaping keeps that true if a pair is ever added.
const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\\-]/g, '\\$&');

const RP_DIALOGUE_PATTERN = new RegExp(
    '^(?:' + RP_DIALOGUE_PAIRS
        .map(([open, close]) => `${escapeRe(open)}([^${escapeRe(close)}\\n]+)[${escapeRe(close)}]`)
        .join('|') + ')'
);

/** Index of the earliest opening mark in `src`, or undefined if there is none. */
export function dialogueStart(src) {
    const starts = RP_DIALOGUE_OPENERS
        .map(ch => src.indexOf(ch))
        .filter(idx => idx !== -1);
    return starts.length ? Math.min(...starts) : undefined;
}

/**
 * Match quoted speech at the very start of `src`.
 *
 * Returns the marks that were actually used alongside the inner text, so the
 * renderer can put them back rather than substituting English ones.
 *
 * @returns {{raw: string, text: string, open: string, close: string}|null}
 */
export function matchDialogue(src) {
    const match = RP_DIALOGUE_PATTERN.exec(src);
    if (!match) return null;
    const raw = match[0];
    return {
        raw,
        // Exactly one alternation captured; the rest are undefined.
        text: match.slice(1).find(group => group !== undefined),
        open: raw[0],
        close: raw[raw.length - 1],
    };
}
