// ═══════════════════════════════════════════════════════════════════════════
// CONFIRM DIALOG — promise-based styled replacements for window.confirm()
// and window.prompt()
//
// Both share one overlay. They differ only in whether the text field shows
// and what the promise resolves to, so `isPrompt` is the whole distinction.
// ═══════════════════════════════════════════════════════════════════════════
let overlay = null;
let resolveFn = null;
let returnFocus = null;
let isPrompt = false;

function build() {
    overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.hidden = true;
    overlay.innerHTML = `
        <div class="confirm-dialog" role="alertdialog" aria-modal="true"
             aria-labelledby="confirm-title" aria-describedby="confirm-message">
            <h3 class="confirm-title" id="confirm-title"></h3>
            <p class="confirm-message" id="confirm-message"></p>
            <input type="text" class="form-input confirm-input" id="confirm-input"
                   autocomplete="off" spellcheck="false" hidden>
            <div class="confirm-actions">
                <button type="button" class="btn btn-secondary confirm-cancel">Cancel</button>
                <button type="button" class="btn confirm-accept"></button>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    // Keep clicks from reaching document-level handlers (outside-click
    // closers for flyouts/settings would fire behind the dialog).
    overlay.addEventListener('click', e => {
        e.stopPropagation();
        if (e.target === overlay) close(false);
    });
    overlay.querySelector('.confirm-cancel').addEventListener('click', () => close(false));
    overlay.querySelector('.confirm-accept').addEventListener('click', () => close(true));
    // A nameless preset is never what the caller wants, and a button that
    // accepts a blank field and then silently does nothing is the thing this
    // dialog replaced.
    overlay.querySelector('.confirm-input').addEventListener('input', e => {
        overlay.querySelector('.confirm-accept').disabled = !e.target.value.trim();
    });
}

function onKeydown(e) {
    if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close(false);
    } else if (e.key === 'Enter' && isPrompt) {
        // Submit from the field, the way the OS box did.
        e.preventDefault();
        if (overlay.querySelector('.confirm-input').value.trim()) close(true);
    } else if (e.key === 'Tab') {
        // Focus trap over whatever the dialog is currently showing — the text
        // field joins the cycle in prompt mode and drops out of it otherwise.
        e.preventDefault();
        const input = overlay.querySelector('.confirm-input');
        const stops = [
            ...(input.hidden ? [] : [input]),
            overlay.querySelector('.confirm-cancel'),
            overlay.querySelector('.confirm-accept'),
        ];
        const at = stops.indexOf(document.activeElement);
        const step = e.shiftKey ? -1 : 1;
        stops[(at + step + stops.length) % stops.length].focus();
    }
}

function close(result) {
    // Read the field before hiding: cancel, Escape and the backdrop all land
    // here as close(false), which is how they come back as null.
    const value = overlay.querySelector('.confirm-input').value.trim();
    const answer = isPrompt ? (result ? value : null) : result;
    isPrompt = false;
    overlay.hidden = true;
    document.removeEventListener('keydown', onKeydown, true);
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
    const resolve = resolveFn;
    resolveFn = null;
    resolve?.(answer);
}

/**
 * Show a styled confirm dialog. Resolves true on confirm, false on
 * cancel / Escape / backdrop click.
 *
 * @param {object} opts
 * @param {string} opts.title         — short question ("Delete Alice?")
 * @param {string} [opts.message]     — consequence line under the title
 * @param {string} [opts.confirmLabel='Delete']
 * @param {string} [opts.cancelLabel='Cancel']
 * @param {boolean} [opts.danger=true] — red confirm button when true
 * @returns {Promise<boolean>}
 */
export function confirmDialog({
    title,
    message = '',
    confirmLabel = 'Delete',
    cancelLabel = 'Cancel',
    danger = true,
} = {}) {
    if (!overlay) build();
    if (resolveFn) close(false); // a second dialog replaces a pending one
    // The overlay is shared, so put it back into confirm shape: a text field
    // left over from a prompt would show up inside the next delete.
    isPrompt = false;
    overlay.querySelector('.confirm-input').hidden = true;

    overlay.querySelector('.confirm-title').textContent = title || 'Are you sure?';
    const msgEl = overlay.querySelector('.confirm-message');
    msgEl.textContent = message;
    msgEl.hidden = !message;
    overlay.querySelector('.confirm-cancel').textContent = cancelLabel;
    const accept = overlay.querySelector('.confirm-accept');
    accept.textContent = confirmLabel;
    accept.disabled = false;
    accept.classList.toggle('btn-danger', danger);
    accept.classList.toggle('btn-primary', !danger);

    returnFocus = document.activeElement;
    overlay.hidden = false;
    document.addEventListener('keydown', onKeydown, true);
    overlay.querySelector('.confirm-cancel').focus();

    return new Promise(resolve => { resolveFn = resolve; });
}

/**
 * Ask for one line of text — the styled replacement for window.prompt().
 * Resolves the trimmed string, or null on cancel / Escape / backdrop click,
 * so `if (!name) return;` reads the same as it did around the OS box.
 *
 * @param {object} opts
 * @param {string} opts.title           — what is being named ("Rename preset")
 * @param {string} [opts.message]       — explanatory line under the title
 * @param {string} [opts.value='']      — starting text, selected on open
 * @param {string} [opts.placeholder='']
 * @param {string} [opts.confirmLabel='Save']
 * @param {string} [opts.cancelLabel='Cancel']
 * @returns {Promise<string|null>}
 */
export function promptDialog({
    title,
    message = '',
    value = '',
    placeholder = '',
    confirmLabel = 'Save',
    cancelLabel = 'Cancel',
} = {}) {
    if (!overlay) build();
    if (resolveFn) close(false);
    isPrompt = true;

    overlay.querySelector('.confirm-title').textContent = title || 'Enter a name';
    const msgEl = overlay.querySelector('.confirm-message');
    msgEl.textContent = message;
    msgEl.hidden = !message;
    overlay.querySelector('.confirm-cancel').textContent = cancelLabel;

    const accept = overlay.querySelector('.confirm-accept');
    accept.textContent = confirmLabel;
    accept.disabled = !value.trim();
    accept.classList.remove('btn-danger');
    accept.classList.add('btn-primary');

    const input = overlay.querySelector('.confirm-input');
    input.hidden = false;
    input.value = value;
    input.placeholder = placeholder;

    returnFocus = document.activeElement;
    overlay.hidden = false;
    document.addEventListener('keydown', onKeydown, true);
    // Focus the field, not Cancel: renaming starts by replacing what is there.
    input.focus();
    input.select();

    return new Promise(resolve => { resolveFn = resolve; });
}
