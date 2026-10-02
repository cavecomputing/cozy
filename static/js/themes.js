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
        // A saved theme whose file is gone (a retired built-in, a deleted
        // user theme) would leave the page unstyled, so show the default,
        // keeping a light theme light.
        if (!state.themes.includes(state.theme)) applyTheme(state.theme.endsWith('-light') ? 'cozy-light' : 'cozy');
    } catch { state.themes = ['cozy']; }
}

export function renderThemePicker() {
    if (!el.settingsThemeSelect) return;
    el.settingsThemeSelect.innerHTML = '';
    state.themes.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name.split('-').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
        opt.selected = state.theme === name;
        el.settingsThemeSelect.appendChild(opt);
    });
}
