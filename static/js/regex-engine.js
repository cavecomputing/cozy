// ═══════════════════════════════════════════════════════════════════════════
// REGEX ENGINE — pure find/replace over a character reply
//
// The settings preview, the two save points and the renderer all run this, so
// "what you test is what you get" depends on there being exactly one copy of
// it. Deliberately free of imports, DOM and app state: regex-filters.js owns
// all of that, which leaves this importable under bare node for the tests.
// ═══════════════════════════════════════════════════════════════════════════

/** Every flag `new RegExp()` accepts — anything else throws. */
export const ALLOWED_FLAGS = 'dgimsuvy';

/** The four worth surfacing in the UI, with the plain-English gloss shown next to each. */
export const FLAG_LABELS = [
    ['g', 'all matches'],
    ['i', 'ignore case'],
    ['m', '^ $ per line'],
    ['s', '. matches newline'],
];

const UI_FLAGS = new Set(FLAG_LABELS.map(([flag]) => flag));

function normalizeFlags(flags) {
    const present = new Set(String(flags ?? ''));
    return [...ALLOWED_FLAGS].filter(flag => present.has(flag)).join('');
}

/**
 * Separate the four flags exposed as checkboxes from valid advanced flags that
 * still need to survive an edit or an imported slash-form pattern.
 */
export function splitFilterFlags(flags) {
    const normalized = normalizeFlags(flags);
    return {
        visible: [...normalized].filter(flag => UI_FLAGS.has(flag)).join(''),
        hidden: [...normalized].filter(flag => !UI_FLAGS.has(flag)).join(''),
    };
}

/** Recombine checked UI flags with the advanced flags preserved on the row. */
export function combineFilterFlags(visible, hidden = '') {
    return normalizeFlags(`${visible ?? ''}${hidden ?? ''}`);
}

/**
 * Split SillyTavern's `/pattern/flags` form so scripts copied from ST paste in
 * without hand-translation.
 *
 * Returns null when the input isn't that shape — including a pattern that
 * merely happens to start with a slash, which stays a literal pattern.
 */
export function splitSlashForm(raw) {
    const s = typeof raw === 'string' ? raw.trim() : '';
    if (!s.startsWith('/')) return null;
    const end = s.lastIndexOf('/');
    if (end <= 0) return null;
    const flags = s.slice(end + 1);
    if ([...flags].some(c => !ALLOWED_FLAGS.includes(c))) return null;
    // `/x/gg` is a SyntaxError, so a repeated flag means this wasn't the slash
    // form to begin with — leave it alone rather than silently repairing it.
    if (new Set(flags).size !== flags.length) return null;
    return { find: s.slice(1, end), flags };
}

/**
 * Compile one filter, or null if it can't run. An uncompilable pattern is a
 * normal state, not an error: the settings UI shows the message and the send
 * path skips the row, so a half-typed regex never blocks a reply.
 */
export function compileFilter(filter) {
    if (!filter || typeof filter.find !== 'string' || !filter.find) return null;
    try {
        // Exactly the flags given, with no hidden default: unticking every box
        // has to mean "no flags", not "g anyway". New rows get `g` pre-ticked
        // in the UI instead, where it's visible.
        return new RegExp(filter.find, String(filter.flags ?? ''));
    } catch {
        return null;
    }
}

/**
 * The engine's own message for a pattern that won't compile, or '' if it does.
 * These read well enough to show verbatim ("Invalid regular expression:
 * /„([^"]*"/g: Unterminated character class").
 */
export function filterError(filter) {
    if (!filter || !filter.find) return '';
    try {
        new RegExp(filter.find, String(filter.flags ?? ''));
        return '';
    } catch (e) {
        return e.message;
    }
}

/**
 * Expand `\n`, `\r`, `\t` and `\\` in a replacement string.
 *
 * The Replace field is a single-line input, so without this there is no way to
 * type a newline and rules like "collapse runs of blank lines" are impossible.
 * Only the replacement gets this treatment — the Find field is a regex, where
 * `\n` already means a newline. Write `\\n` for a literal backslash-n.
 */
function expandEscapes(s) {
    return s.replace(/\\([\\nrt])/g, (_, c) => (
        c === 'n' ? '\n' : c === 'r' ? '\r' : c === 't' ? '\t' : '\\'
    ));
}

/**
 * Render a stored Find/Replace value for a single-line `<input>`.
 *
 * `<input type="text">` silently drops CR and LF from its value, so a pattern
 * holding a real newline — every bundled preset does, to keep a quote from
 * swallowing the next paragraph — came back from the DOM with that newline
 * gone, and the next edit wrote the broken version back to the DB. Showing the
 * escape instead means the round trip is lossless.
 *
 * Only the control characters are escaped, never backslashes: in a Find field
 * `\n` already *is* the newline, and a stored Replace value is escape-form text
 * that `expandEscapes` reads back, so both survive unchanged either way.
 */
export function escapeForInput(value) {
    return String(value ?? '')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r')
        .replace(/\t/g, '\\t');
}

/**
 * Split a preset into the two classes that run in different places: filters
 * that rewrite the saved reply, and display-only ones that rewrite the bubble.
 * A filter belongs to exactly one, and a missing `display` key means the
 * saved-reply class, so a preset written before the option existed is unchanged.
 */
export function selectFilters(filters, display) {
    if (!Array.isArray(filters)) return [];
    return filters.filter(f => Boolean(f?.display) === display);
}

/**
 * Run every filter over `text` in order — each one sees the previous one's
 * output, which is why the settings preview runs the whole list rather than
 * one row at a time.
 */
export function runFilters(text, filters) {
    if (typeof text !== 'string' || !text) return text;
    if (!Array.isArray(filters) || filters.length === 0) return text;

    let out = text;
    for (const filter of filters) {
        const re = compileFilter(filter);
        if (!re) continue;  // invalid or empty — skip, never throw mid-send
        // `{{match}}` is SillyTavern's spelling of the whole match; JS spells it
        // `$&`. The replacement here is itself a replacement string, so a
        // literal `$&` has to be written `$$&` — plain `'$&'` would substitute
        // the text we just matched (`{{match}}`) straight back in.
        // Native `$1`, `$<name>` and `$$` need no translation.
        const replacement = expandEscapes(String(filter.replace ?? ''))
            .replace(/\{\{match\}\}/g, '$$&');
        try {
            const candidate = out.replace(re, replacement);
            // Reject only the filter that erased the current reply. Returning
            // the original input here would also undo every successful filter
            // that ran before it.
            if (out.trim() && !candidate.trim()) continue;
            out = candidate;
        } catch {
            // A pattern can compile and still throw at replace time. Keep the
            // last good text rather than losing the reply.
        }
    }
    return out;
}
