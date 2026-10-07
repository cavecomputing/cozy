import { state, el } from './state.js';

// ═══════════════════════════════════════════════════════════════════════════
// THEMES
// ═══════════════════════════════════════════════════════════════════════════
export function applyTheme(name) {
    const link = document.getElementById('theme-stylesheet');
    if (link) {
        link.addEventListener('load', syncThemeColor, { once: true });
        link.href = `/themes/${name}.css`;
    }
    syncThemeColor();
    state.theme = name;
}

// The browser bar and an installed app's status or title bar take theme-color,
// so match it to the theme's page background — on the spot for the sheet already
// loaded, and again once a newly chosen sheet arrives.
function syncThemeColor() {
    const appBg = getComputedStyle(document.documentElement).getPropertyValue('--app-bg').trim();
    if (appBg) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', appBg);
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
