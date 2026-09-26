// ═══════════════════════════════════════════════════════════════════════════
// FIELD VALIDATION
// ═══════════════════════════════════════════════════════════════════════════
// What counts as a valid card, expressed as data rather than as DOM effects.
// Deliberately free of imports, DOM and app state so it runs under bare node —
// the same reason regex-engine.js and rp-dialogue.js are separate modules. The
// caller decides how an error looks; this only decides that there is one.
//
// Each error names the field it belongs to, so the caller can put the message
// beside the control that caused it rather than in a toast at the other end of
// the screen.

/**
 * Validate a character card before it is saved.
 *
 * No image is required: a card saved without one is stored on a placeholder
 * PNG and drawn with its initials.
 *
 * @param {object}  card       the collected form values
 * @returns {Array<{field: string, message: string}>} empty when the card is valid
 */
export function validateCharacter({ name } = {}) {
    const errors = [];

    if (!String(name ?? '').trim()) {
        errors.push({ field: 'name', message: 'Give the character a name.' });
    }

    return errors;
}
