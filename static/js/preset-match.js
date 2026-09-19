// ═══════════════════════════════════════════════════════════════════════════
// PRESET MATCH — name lookup for the /prompt and /api slash commands
// ═══════════════════════════════════════════════════════════════════════════
// Pure logic, free of DOM and app state so it stays importable under bare
// `node` (see Testing gotchas in CLAUDE.md).

const VERSION_RE = /^\d+(?:\.\d+)?$/;

/**
 * Order a preset's version the way cozy/defaults.py's version_key() does:
 * the two halves as integers, anything unparseable lowest. API presets carry
 * no version at all, so they all tie here and keep their listed order.
 */
function versionKey(preset) {
    const [major, minor = '0'] = String(preset?.version ?? '').split('.');
    if (!/^\d+$/.test(major) || !/^\d+$/.test(minor)) return [-1, 0];
    return [Number(major), Number(minor)];
}

/** The newest of *list*, keeping the first entry when versions tie. */
export function newestPreset(list) {
    return list.reduce((best, p) => {
        const [bMajor, bMinor] = versionKey(best);
        const [pMajor, pMinor] = versionKey(p);
        return pMajor > bMajor || (pMajor === bMajor && pMinor > bMinor) ? p : best;
    });
}

/**
 * Display labels for *list*, qualifying a name only where it repeats —
 * "NanoBear 2.2" and "NanoBear 2.1" once two editions are installed, plain
 * "NanoBear" while there is only one. A label is also a valid query, so the
 * slash-command menu can offer it straight back.
 */
export function labelPresets(list) {
    const counts = new Map();
    for (const p of list) counts.set(p.name, (counts.get(p.name) || 0) + 1);
    return list.map(p => (
        counts.get(p.name) > 1 && p.version ? `${p.name} ${p.version}` : p.name
    ));
}

function resolve(list, name, version) {
    const q = name.toLowerCase();
    let pool = list.filter(p => p.name.toLowerCase() === q);
    if (!pool.length) {
        pool = list.filter(p => p.name.toLowerCase().startsWith(q));
        // A prefix reaching several *different* names is ambiguous; several
        // editions of one name is not — that is what the version picks between.
        if (new Set(pool.map(p => p.name.toLowerCase())).size > 1) {
            return { error: 'ambiguous', candidates: labelPresets(pool) };
        }
    }
    if (!pool.length) return { error: 'unknown', candidates: labelPresets(list) };
    if (version !== null) {
        const wanted = pool.filter(p => String(p.version ?? '') === version);
        // Offer this name's editions rather than the whole library: the name
        // was right and only the version missed.
        if (!wanted.length) return { error: 'unknown', candidates: labelPresets(pool) };
        pool = wanted;
    }
    return { preset: newestPreset(pool) };
}

/**
 * Resolve a typed preset name against [{id, name, version?}, …].
 *
 * The query is `<name>` or `<name> <version>`. Case-insensitive exact match
 * wins; otherwise a unique case-insensitive prefix match wins. With no version
 * given, the newest edition of the matched name wins. Anything else resolves
 * to an error carrying the labels the caller should offer.
 */
export function matchPresetByName(presets, query) {
    const list = Array.isArray(presets) ? presets.filter(p => typeof p?.name === 'string') : [];
    const raw = String(query ?? '').trim();
    if (!raw) return { error: 'missing', candidates: labelPresets(list) };

    const words = raw.split(/\s+/);
    const tail = words.length > 1 && VERSION_RE.test(words[words.length - 1])
        ? { name: words.slice(0, -1).join(' '), version: words[words.length - 1] }
        : null;

    // Read a trailing number as a version first, then fall back to the whole
    // string as a name — so a preset actually called "NanoBear 2.1" is still
    // reachable by typing exactly that.
    const versioned = tail ? resolve(list, tail.name, tail.version) : null;
    const whole = versioned?.preset ? null : resolve(list, raw, null);
    const hit = versioned?.preset ? versioned : (whole?.preset ? whole : null);
    if (hit) {
        // The label the caller should name it by, qualified the same way the
        // slash-command menu qualifies it.
        return { preset: hit.preset, label: labelPresets(list)[list.indexOf(hit.preset)] };
    }
    // The versioned read found the name and missed only the version, which
    // says more than "no preset called 'NanoBear 2.9'".
    return versioned || whole;
}
