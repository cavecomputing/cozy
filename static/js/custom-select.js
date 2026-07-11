// ═══════════════════════════════════════════════════════════════════════════
// CUSTOM SELECT — themed dropdown that decorates a native <select>
//
// Native <option> popups can't be themed in Safari (WebKit ignores option
// background/colour), so we render our own listbox. The native <select> stays
// in the DOM as the hidden source of truth + event source: selecting an item
// sets `select.value` and dispatches a bubbling `change`, so every existing
// listener keeps working. The custom UI re-syncs automatically when the
// options are repopulated (MutationObserver) or the value is set
// programmatically (an instance-level `value` setter override) — so the
// modules that populate these selects need no changes.
//
// Follows the W3C ARIA APG "select-only combobox" pattern.
// ═══════════════════════════════════════════════════════════════════════════

let idCounter = 0;
let closeOpenSelect = null;  // close() of the currently-open custom select, if any

/** Enhance every native `.settings-select` on the page (idempotent). */
export function enhanceSettingsSelects() {
    document.querySelectorAll('select.settings-select').forEach(enhanceSelect);
}

export function enhanceSelect(select) {
    if (!select || select.dataset.cozyEnhanced) return;
    select.dataset.cozyEnhanced = 'true';

    const uid = `cozy-select-${++idCounter}`;
    const menuId = `${uid}-listbox`;

    // ── Structure ──────────────────────────────────────────────────────────
    const wrap = document.createElement('div');
    wrap.className = 'cozy-select';
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add('cozy-select__native');
    select.setAttribute('tabindex', '-1');
    select.setAttribute('aria-hidden', 'true');

    const label = select.id ? document.querySelector(`label[for="${select.id}"]`) : null;
    if (label && !label.id) label.id = `${uid}-label`;

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = `${uid}-trigger`;
    trigger.className = 'settings-select cozy-select-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', menuId);
    if (label) trigger.setAttribute('aria-labelledby', `${label.id} ${trigger.id}`);
    const valueSpan = document.createElement('span');
    valueSpan.className = 'cozy-select-value';
    trigger.appendChild(valueSpan);
    wrap.appendChild(trigger);

    const menu = document.createElement('ul');
    menu.id = menuId;
    menu.className = 'cozy-select-menu';
    menu.hidden = true;
    menu.setAttribute('role', 'listbox');
    if (label) menu.setAttribute('aria-labelledby', label.id);
    wrap.appendChild(menu);

    // ── State ──────────────────────────────────────────────────────────────
    let items = [];        // interactive options: [{ value, text, li }]
    let activeIndex = -1;  // index into `items`
    let typeahead = '';
    let typeaheadTimer = null;

    const isOpen = () => !menu.hidden;

    // ── Render ─────────────────────────────────────────────────────────────
    function render() {
        const opts = Array.from(select.options);
        menu.innerHTML = '';
        items = [];
        let selectedText = '';
        opts.forEach(opt => {
            if (opt.selected) selectedText = opt.textContent;
            if (opt.hidden) return;  // e.g. the api-preset placeholder once presets load
            const li = document.createElement('li');
            li.id = `${uid}-opt-${menu.children.length}`;
            li.className = 'cozy-select-option';
            li.setAttribute('role', 'option');
            li.dataset.value = opt.value;
            li.textContent = opt.textContent;
            li.setAttribute('aria-selected', String(opt.selected));
            if (opt.selected) li.classList.add('selected');
            if (opt.disabled) {
                li.setAttribute('aria-disabled', 'true');
                li.classList.add('disabled');
            } else {
                items.push({ value: opt.value, text: opt.textContent, li });
            }
            menu.appendChild(li);
        });
        if (!selectedText && select.selectedIndex >= 0) {
            selectedText = opts[select.selectedIndex]?.textContent || '';
        }
        valueSpan.textContent = selectedText;
        if (isOpen()) {
            const clamped = Math.max(0, Math.min(activeIndex, items.length - 1));
            setActive(items.length ? clamped : -1, false);
        }
    }

    function setActive(idx, scroll = true) {
        activeIndex = idx;
        items.forEach((it, i) => it.li.classList.toggle('active', i === idx));
        if (idx >= 0 && items[idx]) {
            trigger.setAttribute('aria-activedescendant', items[idx].li.id);
            if (scroll) items[idx].li.scrollIntoView({ block: 'nearest' });
        } else {
            trigger.removeAttribute('aria-activedescendant');
        }
    }

    // ── Open / close / select ──────────────────────────────────────────────
    function open() {
        if (isOpen()) return;
        if (closeOpenSelect && closeOpenSelect !== close) closeOpenSelect();  // close any other open select
        render();
        menu.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        closeOpenSelect = close;
        const selIdx = items.findIndex(it => it.value === select.value);
        setActive(items.length ? Math.max(selIdx, 0) : -1);
    }

    function close({ focusTrigger = false } = {}) {
        if (!isOpen()) {
            if (focusTrigger) trigger.focus();
            return;
        }
        menu.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        trigger.removeAttribute('aria-activedescendant');
        items.forEach(it => it.li.classList.remove('active'));
        if (closeOpenSelect === close) closeOpenSelect = null;
        if (focusTrigger) trigger.focus();
    }

    function commit(value, { keepFocus = true } = {}) {
        if (select.value !== value) {
            select.value = value;  // triggers the value-setter override → render()
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        render();
        close({ focusTrigger: keepFocus });
    }

    function typeaheadMatch(ch) {
        clearTimeout(typeaheadTimer);
        typeahead += ch.toLowerCase();
        typeaheadTimer = setTimeout(() => { typeahead = ''; }, 500);
        const idx = items.findIndex(it => it.text.toLowerCase().startsWith(typeahead));
        if (idx >= 0) setActive(idx);
    }

    // ── Events ─────────────────────────────────────────────────────────────
    trigger.addEventListener('click', () => (isOpen() ? close() : open()));

    trigger.addEventListener('keydown', e => {
        const key = e.key;
        const printable = key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey;
        if (!isOpen()) {
            if (['ArrowDown', 'ArrowUp', 'Enter', ' ', 'Home', 'End'].includes(key)) {
                e.preventDefault();
                open();
            } else if (printable) {
                open();
                typeaheadMatch(key);
            }
            return;
        }
        switch (key) {
            case 'ArrowDown': e.preventDefault(); setActive(Math.min(activeIndex + 1, items.length - 1)); break;
            case 'ArrowUp':   e.preventDefault(); setActive(Math.max(activeIndex - 1, 0)); break;
            case 'Home':      e.preventDefault(); setActive(0); break;
            case 'End':       e.preventDefault(); setActive(items.length - 1); break;
            case 'Enter':
            case ' ':
                e.preventDefault();
                if (items[activeIndex]) commit(items[activeIndex].value);
                break;
            case 'Escape':
                e.preventDefault();
                e.stopPropagation();
                close({ focusTrigger: true });
                break;
            case 'Tab':
                if (items[activeIndex]) commit(items[activeIndex].value, { keepFocus: false });
                break;
            default:
                if (printable) { e.preventDefault(); typeaheadMatch(key); }
        }
    });

    // mousedown (not click) so the trigger doesn't blur/close before we select
    menu.addEventListener('mousedown', e => {
        const li = e.target.closest('.cozy-select-option');
        if (!li || li.classList.contains('disabled')) return;
        e.preventDefault();
        commit(li.dataset.value);
    });
    menu.addEventListener('mousemove', e => {
        const li = e.target.closest('.cozy-select-option');
        if (!li || li.classList.contains('disabled')) return;
        const idx = items.findIndex(it => it.li === li);
        if (idx >= 0 && idx !== activeIndex) setActive(idx, false);
    });

    document.addEventListener('click', e => {
        if (isOpen() && !wrap.contains(e.target)) close();
    });

    // ── Auto-sync with the native select (no changes needed in callers) ─────
    new MutationObserver(() => render()).observe(select, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['selected', 'disabled', 'hidden', 'value'],
    });

    const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    if (valueDesc?.configurable && valueDesc.get && valueDesc.set) {
        Object.defineProperty(select, 'value', {
            configurable: true,
            enumerable: valueDesc.enumerable,
            get() { return valueDesc.get.call(this); },
            set(v) { valueDesc.set.call(this, v); render(); },
        });
    }

    render();
}
