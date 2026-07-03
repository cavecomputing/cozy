// ═══════════════════════════════════════════════════════════════════════════
// CONFIRM DIALOG — promise-based styled replacement for window.confirm()
// ═══════════════════════════════════════════════════════════════════════════
let overlay = null;
let resolveFn = null;
let returnFocus = null;

function build() {
    overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.hidden = true;
    overlay.innerHTML = `
        <div class="confirm-dialog" role="alertdialog" aria-modal="true"
             aria-labelledby="confirm-title" aria-describedby="confirm-message">
            <h3 class="confirm-title" id="confirm-title"></h3>
            <p class="confirm-message" id="confirm-message"></p>
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
}

function onKeydown(e) {
    if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close(false);
    } else if (e.key === 'Tab') {
        // Two-button focus trap
        e.preventDefault();
        const cancel = overlay.querySelector('.confirm-cancel');
        const accept = overlay.querySelector('.confirm-accept');
        (document.activeElement === cancel ? accept : cancel).focus();
    }
}

function close(result) {
    overlay.hidden = true;
    document.removeEventListener('keydown', onKeydown, true);
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
    const resolve = resolveFn;
    resolveFn = null;
    resolve?.(result);
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

    overlay.querySelector('.confirm-title').textContent = title || 'Are you sure?';
    const msgEl = overlay.querySelector('.confirm-message');
    msgEl.textContent = message;
    msgEl.hidden = !message;
    overlay.querySelector('.confirm-cancel').textContent = cancelLabel;
    const accept = overlay.querySelector('.confirm-accept');
    accept.textContent = confirmLabel;
    accept.classList.toggle('btn-danger', danger);
    accept.classList.toggle('btn-primary', !danger);

    returnFocus = document.activeElement;
    overlay.hidden = false;
    document.addEventListener('keydown', onKeydown, true);
    overlay.querySelector('.confirm-cancel').focus();

    return new Promise(resolve => { resolveFn = resolve; });
}
