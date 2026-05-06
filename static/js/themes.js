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
}

export async function loadThemeList() {
    try {
        const r = await fetch('/api/themes');
        state.themes = await r.json();
    } catch { state.themes = ['cozy']; }
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
