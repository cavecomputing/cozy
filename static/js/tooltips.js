// ═══════════════════════════════════════════════════════════════════════════
// TOOLTIPS — single shared bubble portal, escapes overflow:hidden ancestors
// ═══════════════════════════════════════════════════════════════════════════
// Triggers are help badges and context-meter segments with a `data-tip`
// attribute containing the tooltip's HTML. Placement is picked from {above,
// below, right, left} based on viewport space.

let portal = null;
let activeTrigger = null;
const TRIGGER_SELECTOR = '.help-tip, .context-meter-segment';

function ensurePortal() {
    if (portal) return portal;
    portal = document.createElement('div');
    portal.className = 'help-tip-portal';
    portal.setAttribute('role', 'tooltip');
    portal.hidden = true;
    document.body.appendChild(portal);
    return portal;
}

function show(trigger) {
    const tip = trigger.dataset.tip;
    if (!tip) return;
    const p = ensurePortal();
    p.innerHTML = tip;
    p.hidden = false;
    p.style.visibility = 'hidden';
    p.style.left = '0px';
    p.style.top = '0px';
    p.classList.remove('help-tip-portal--down', 'help-tip-portal--right', 'help-tip-portal--left');

    const r = trigger.getBoundingClientRect();
    const b = p.getBoundingClientRect();
    const margin = 6;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let placement = 'above';
    if (r.top - b.height - margin < 4) {
        if (r.bottom + b.height + margin < vh - 4) placement = 'below';
        else if (r.right + b.width + margin < vw - 4) placement = 'right';
        else placement = 'left';
    }

    let top, left;
    if (placement === 'above') {
        top = r.top - b.height - margin;
        left = r.left + r.width / 2 - b.width / 2;
    } else if (placement === 'below') {
        top = r.bottom + margin;
        left = r.left + r.width / 2 - b.width / 2;
    } else if (placement === 'right') {
        top = r.top + r.height / 2 - b.height / 2;
        left = r.right + margin;
    } else {
        top = r.top + r.height / 2 - b.height / 2;
        left = r.left - b.width - margin;
    }

    left = Math.max(8, Math.min(left, vw - b.width - 8));
    top = Math.max(8, Math.min(top, vh - b.height - 8));

    p.style.left = `${left}px`;
    p.style.top = `${top}px`;
    p.style.visibility = 'visible';
    p.classList.add('help-tip-portal--visible');
    if (placement !== 'above') p.classList.add(`help-tip-portal--${placement}`);
}

function hide() {
    if (!portal) return;
    portal.classList.remove('help-tip-portal--visible');
    portal.hidden = true;
    activeTrigger = null;
}

export function initTooltips() {
    document.addEventListener('pointerover', e => {
        const t = e.target.closest(TRIGGER_SELECTOR);
        if (t && t !== activeTrigger) {
            activeTrigger = t;
            show(t);
        }
    });
    document.addEventListener('pointerout', e => {
        const t = e.target.closest(TRIGGER_SELECTOR);
        if (!t) return;
        const next = e.relatedTarget?.closest?.(TRIGGER_SELECTOR);
        if (next !== t) hide();
    });
    document.addEventListener('focusin', e => {
        const t = e.target.closest(TRIGGER_SELECTOR);
        if (t) {
            activeTrigger = t;
            show(t);
        }
    });
    document.addEventListener('focusout', e => {
        if (e.target.closest(TRIGGER_SELECTOR)) hide();
    });
    // Touch devices do not have a durable hover state. Tapping a segment
    // focuses its button and keeps the shared tooltip visible until the next
    // outside tap.
    document.addEventListener('click', e => {
        const t = e.target.closest(TRIGGER_SELECTOR);
        if (t) {
            activeTrigger = t;
            show(t);
        } else if (activeTrigger?.matches('.context-meter-segment')) {
            hide();
        }
    });
    window.addEventListener('scroll', () => {
        if (activeTrigger) show(activeTrigger);
    }, true);
    window.addEventListener('resize', () => {
        if (activeTrigger) show(activeTrigger);
    });
}
