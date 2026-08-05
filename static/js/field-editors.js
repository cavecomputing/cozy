import { icons } from './state.js';
import { sanitize } from './utils.js';

// ═══════════════════════════════════════════════════════════════════════════
// SHARED FIELD EDITORS — tag chips + alternate greetings
// ═══════════════════════════════════════════════════════════════════════════
// Used by the character modal. `onChange` fires on every mutation when a
// caller needs to observe edits; pass nothing for a plain form.

/**
 * Chip-style tag editor. Enter or comma commits the typed tag; Backspace on
 * an empty input pops the last one.
 * @returns {{ get(): string[], set(tags: string[]): void }}
 */
export function createTagEditor({ chipList, textInput, wrap, onChange = null }) {
    let tags = [];

    function render() {
        chipList.innerHTML = '';
        tags.forEach((tag, idx) => {
            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            chip.innerHTML = `${sanitize(tag)}<button type="button" class="tag-chip-remove" title="Remove tag" aria-label="Remove tag">×</button>`;
            chip.querySelector('.tag-chip-remove').addEventListener('click', () => {
                tags.splice(idx, 1);
                render();
                onChange?.();
            });
            chipList.appendChild(chip);
        });
    }

    textInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            const val = textInput.value.trim().replace(/,/g, '');
            if (val && !tags.includes(val)) {
                tags.push(val);
                render();
                onChange?.();
            }
            textInput.value = '';
        } else if (e.key === 'Backspace' && textInput.value === '' && tags.length) {
            tags.pop();
            render();
            onChange?.();
        }
    });
    wrap.addEventListener('click', () => textInput.focus());

    return {
        get: () => [...tags],
        set(next) {
            tags = Array.isArray(next) ? [...next] : [];
            render();
        },
    };
}

/**
 * Alternate-greetings editor: one textarea row per greeting with a remove
 * button; the add button appends an empty row and focuses it.
 * @returns {{ get(): string[], set(greetings: string[]): void }}
 */
export function createGreetingEditor({ listEl, addBtn, onChange = null }) {
    let greetings = [];

    function render() {
        listEl.innerHTML = '';
        greetings.forEach((text, idx) => {
            const row = document.createElement('div');
            row.className = 'alt-greeting-item';
            const ta = document.createElement('textarea');
            ta.className = 'form-textarea';
            ta.rows = 3;
            ta.value = text;
            ta.placeholder = 'Alternate greeting text…';
            ta.addEventListener('input', () => {
                greetings[idx] = ta.value;
                onChange?.();
            });
            const rm = document.createElement('button');
            rm.type = 'button';
            rm.className = 'icon-btn remove-greeting-btn';
            rm.title = 'Remove greeting';
            rm.innerHTML = icons.TRASH;
            rm.addEventListener('click', () => {
                greetings.splice(idx, 1);
                render();
                onChange?.();
            });
            row.append(ta, rm);
            listEl.appendChild(row);
        });
    }

    addBtn.addEventListener('click', () => {
        greetings.push('');
        render();
        onChange?.();
        const tas = listEl.querySelectorAll('textarea');
        if (tas.length) tas[tas.length - 1].focus();
    });

    return {
        get: () => [...greetings],
        set(next) {
            greetings = Array.isArray(next) ? [...next] : [];
            render();
        },
    };
}
