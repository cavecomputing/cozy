import { state, el } from './state.js';

// ═══════════════════════════════════════════════════════════════════════════
// THEMES
// ═══════════════════════════════════════════════════════════════════════════
export function applyTheme(name) {
    const link = document.getElementById('theme-stylesheet');
    if (link) {
        link.href = `/themes/${name}.css`;
    }
    state.theme = name;
    // Update favicon after new stylesheet loads so CSS vars are available
    if (link) {
        link.addEventListener('load', () => updateFavicon(), { once: true });
    }
}

export function updateFavicon() {
    const s = getComputedStyle(document.documentElement);
    const bg   = s.getPropertyValue('--favicon-bg').trim()        || '#2d353b';
    const pad  = s.getPropertyValue('--favicon-paw-pad').trim()  || '#a7c080';
    const toes = s.getPropertyValue('--favicon-paw-toes').trim() || '#7fbbb3';
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="${bg}"/>
  <ellipse cx="16" cy="20" rx="5.5" ry="4.5" fill="${pad}"/>
  <ellipse cx="9.5" cy="14" rx="2.5" ry="3" transform="rotate(-10 9.5 14)" fill="${toes}"/>
  <ellipse cx="14" cy="11" rx="2.5" ry="3" transform="rotate(-5 14 11)" fill="${toes}"/>
  <ellipse cx="18" cy="11" rx="2.5" ry="3" transform="rotate(5 18 11)" fill="${toes}"/>
  <ellipse cx="22.5" cy="14" rx="2.5" ry="3" transform="rotate(10 22.5 14)" fill="${toes}"/>
</svg>`;
    const favicon = document.getElementById('favicon');
    if (favicon) {
        favicon.href = 'data:image/svg+xml,' + encodeURIComponent(svg);
    }
}

export async function loadThemeList() {
    try {
        const r = await fetch('/api/themes');
        state.themes = await r.json();
    } catch { state.themes = ['everforest-dark']; }
}

export function renderThemePicker() {
    if (!el.settingsThemeSelect) return;
    el.settingsThemeSelect.innerHTML = '';
    state.themes.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name.charAt(0).toUpperCase() + name.slice(1);
        opt.selected = state.theme === name;
        el.settingsThemeSelect.appendChild(opt);
    });
}
