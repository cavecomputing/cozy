import { API } from './api.js';

const CATEGORY_META = [
    {
        key: 'database',
        label: 'Database',
        description: 'Chats, messages, presets, and settings',
    },
    {
        key: 'characters',
        label: 'Character cards',
        description: 'Card data and full-size artwork',
    },
    {
        key: 'personas',
        label: 'Persona avatars',
        description: 'Images attached to your personas',
    },
    {
        key: 'themes',
        label: 'Custom themes',
        description: 'Themes added to your data folder',
    },
    {
        key: 'other',
        label: 'Other',
        description: 'Other files in Cozy\u2019s data folder',
    },
];

export function formatBytes(bytes) {
    const amount = Number(bytes);
    if (!Number.isFinite(amount) || amount <= 0) return '0 B';

    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const unitIndex = Math.min(
        Math.floor(Math.log(amount) / Math.log(1024)),
        units.length - 1,
    );
    const scaled = amount / (1024 ** unitIndex);
    const maximumFractionDigits = unitIndex === 0 || scaled >= 100 ? 0 : 1;
    return `${scaled.toLocaleString(undefined, { maximumFractionDigits })} ${units[unitIndex]}`;
}

function fileCountLabel(files) {
    const count = Number.isFinite(Number(files)) ? Math.max(0, Math.trunc(Number(files))) : 0;
    return `${count.toLocaleString()} ${count === 1 ? 'file' : 'files'}`;
}

export function buildStorageViewModel(stats) {
    const categories = stats?.categories || {};
    const tiles = CATEGORY_META
        .map(meta => ({
            ...meta,
            bytes: Math.max(0, Number(categories[meta.key]?.bytes) || 0),
            files: Math.max(0, Number(categories[meta.key]?.files) || 0),
        }))
        .filter(item => item.key !== 'other' || item.bytes > 0 || item.files > 0);

    const totalBytes = Math.max(0, Number(stats?.user_data_bytes) || 0);
    return {
        totalBytes,
        totalLabel: formatBytes(totalBytes),
        tiles: tiles.map(item => ({
            ...item,
            sizeLabel: formatBytes(item.bytes),
            fileLabel: fileCountLabel(item.files),
            weight: totalBytes > 0 ? item.bytes / totalBytes : 0,
        })),
        cache: {
            bytes: Math.max(0, Number(stats?.cache?.bytes) || 0),
            files: Math.max(0, Number(stats?.cache?.files) || 0),
            sizeLabel: formatBytes(stats?.cache?.bytes),
            fileLabel: fileCountLabel(stats?.cache?.files),
        },
    };
}

function storageLoadingMarkup() {
    return `
        <div class="about-storage-status">
            <span class="about-storage-spinner" aria-hidden="true"></span>
            <span>Calculating storage use&hellip;</span>
        </div>`;
}

function storageErrorMarkup() {
    return `
        <div class="about-storage-status about-storage-status--error">
            <span>Storage details aren\u2019t available right now.</span>
            <button class="btn btn-secondary btn-sm" type="button" data-storage-retry>Retry</button>
        </div>`;
}

export function renderStorageStats(root, stats) {
    const view = buildStorageViewModel(stats);
    const breakdownLabel = view.tiles
        .map(item => `${item.label} ${item.sizeLabel}`)
        .join(', ');
    const segments = view.tiles
        .filter(item => item.bytes > 0)
        .map(item => `
            <span class="about-storage-segment about-storage-segment--${item.key}"
                  style="--storage-weight: ${item.weight}"
                  title="${item.label}: ${item.sizeLabel}"></span>`)
        .join('');
    const tiles = view.tiles.map(item => `
        <div class="about-storage-tile">
            <span class="about-storage-dot about-storage-dot--${item.key}" aria-hidden="true"></span>
            <div class="about-storage-tile-body">
                <span class="about-storage-tile-title">${item.label}</span>
                <span class="about-storage-tile-desc">${item.description}</span>
            </div>
            <div class="about-storage-tile-value">
                <strong>${item.sizeLabel}</strong>
                <span>${item.fileLabel}</span>
            </div>
        </div>`).join('');

    root.setAttribute('aria-busy', 'false');
    root.innerHTML = `
        <div class="about-storage-summary">
            <div>
                <span class="about-storage-eyebrow">Your data</span>
                <strong class="about-storage-total">${view.totalLabel}</strong>
            </div>
            <span class="about-storage-summary-note">stored locally</span>
        </div>
        <div class="about-storage-bar" role="img" aria-label="Storage breakdown: ${breakdownLabel || 'no data'}">
            ${segments}
        </div>
        <div class="about-storage-grid">${tiles}</div>
        <div class="about-storage-cache">
            <span class="about-storage-cache-icon" aria-hidden="true">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.34-5.66"></path><polyline points="20 4 20 10 14 10"></polyline></svg>
            </span>
            <div class="about-storage-cache-body">
                <strong>Thumbnail cache</strong>
                <span>Rebuildable and not included in your data total</span>
            </div>
            <div class="about-storage-tile-value">
                <strong>${view.cache.sizeLabel}</strong>
                <span>${view.cache.fileLabel}</span>
            </div>
        </div>`;
}

let requestSequence = 0;

export async function loadStorageStats(root = document.getElementById('about-storage-content')) {
    if (!root) return;
    const requestId = ++requestSequence;
    root.setAttribute('aria-busy', 'true');
    root.innerHTML = storageLoadingMarkup();
    try {
        const stats = await API.getStorageStats();
        if (requestId === requestSequence) renderStorageStats(root, stats);
    } catch {
        if (requestId === requestSequence) {
            root.setAttribute('aria-busy', 'false');
            root.innerHTML = storageErrorMarkup();
        }
    }
}

export function initStorageStats(root = document.getElementById('about-storage-content')) {
    if (!root || root.dataset.storageBound) return;
    root.dataset.storageBound = 'true';
    root.addEventListener('click', event => {
        if (event.target.closest('[data-storage-retry]')) loadStorageStats(root);
    });
}
