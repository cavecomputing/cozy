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
 * @param {object}  card              the collected form values
 * @param {boolean} card.isNew        true when creating rather than editing
 * @param {boolean} card.hasImage     true when an avatar is present or pending
 * @returns {Array<{field: string, message: string}>} empty when the card is valid
 */
export function validateCharacter({ name, isNew, hasImage } = {}) {
    const errors = [];

    if (!String(name ?? '').trim()) {
        errors.push({ field: 'name', message: 'Give the character a name.' });
    }

    // Only new cards need an image. An existing card already has one, and an
    // edit that does not touch the avatar must not be blocked by it.
    if (isNew && !hasImage) {
        errors.push({ field: 'avatar', message: 'Choose an image for the character.' });
    }

    return errors;
}
