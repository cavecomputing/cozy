// ═══════════════════════════════════════════════════════════════════════════
// THINKING TAG PARSER
// ═══════════════════════════════════════════════════════════════════════════
// Known thinking tag pairs — checked in order, first match wins
export const THINKING_TAG_PAIRS = [
    { open: '<think>',       close: '</think>' },
    { open: '<thinking>',    close: '</thinking>' },
    { open: '<|thinking|>',  close: '<|/thinking|>' },
];

export function parseThinkingContent(text) {
    if (!text) return { thinking: null, response: text };

    // Find the first matching open tag
    let bestOpen = -1, matched = null;
    for (const pair of THINKING_TAG_PAIRS) {
        const idx = text.indexOf(pair.open);
        if (idx !== -1 && (bestOpen === -1 || idx < bestOpen)) {
            bestOpen = idx;
            matched = pair;
        }
    }
    if (!matched) return { thinking: null, response: text };

    const closeIdx = text.indexOf(matched.close, bestOpen + matched.open.length);
    if (closeIdx === -1) {
        // Tag opened but not closed (still streaming)
        return {
            thinking: text.slice(bestOpen + matched.open.length),
            response: text.slice(0, bestOpen),
            incomplete: true,
        };
    }
    const thinking = text.slice(bestOpen + matched.open.length, closeIdx);
    const response = text.slice(0, bestOpen) + text.slice(closeIdx + matched.close.length);
    return { thinking: thinking.trim(), response: response.trim() };
}

/** Render or update the thinking block above message content. */
export function renderThinkingBlock(msgBody, parsed) {
    let block = msgBody.querySelector('.thinking-block');
    if (!parsed.thinking && !parsed.incomplete) {
        if (block) block.remove();
        return;
    }
    if (!block) {
        block = document.createElement('div');
        block.className = 'thinking-block';
        block.innerHTML = `
            <button class="thinking-toggle">
                <span class="chevron">&#9654;</span>
                <span class="thinking-label">Thinking\u2026</span>
            </button>
            <div class="thinking-content"></div>`;
        block.querySelector('.thinking-toggle').addEventListener('click', () => {
            block.querySelector('.thinking-toggle').classList.toggle('open');
            block.querySelector('.thinking-content').classList.toggle('open');
        });
        const contentEl = msgBody.querySelector('.message-content');
        msgBody.insertBefore(block, contentEl);
    }
    const label = block.querySelector('.thinking-label');
    label.textContent = parsed.incomplete ? 'Thinking\u2026' : 'Thinking';
    const thinkContent = block.querySelector('.thinking-content');
    thinkContent.innerHTML = DOMPurify.sanitize(marked.parse(parsed.thinking || ''));
}
