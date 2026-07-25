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
    if (!text) return { thinking: null, response: text, hasThinking: false };

    // Find the first matching open tag
    let bestOpen = -1, matched = null;
    for (const pair of THINKING_TAG_PAIRS) {
        const idx = text.indexOf(pair.open);
        if (idx !== -1 && (bestOpen === -1 || idx < bestOpen)) {
            bestOpen = idx;
            matched = pair;
        }
    }
    if (!matched) return { thinking: null, response: text, hasThinking: false };

    const closeIdx = text.indexOf(matched.close, bestOpen + matched.open.length);
    if (closeIdx === -1) {
        // Tag opened but not closed (still streaming)
        return {
            thinking: text.slice(bestOpen + matched.open.length),
            response: text.slice(0, bestOpen),
            incomplete: true,
            hasThinking: true,
            // Edits need a complete segment to reattach. Closing an interrupted
            // block here prevents the edited response from becoming reasoning.
            thinkingSegment: text.slice(bestOpen) + matched.close,
        };
    }
    const thinking = text.slice(bestOpen + matched.open.length, closeIdx);
    const response = text.slice(0, bestOpen) + text.slice(closeIdx + matched.close.length);
    // thinkingSegment keeps the tags so callers can reassemble the full text
    const thinkingSegment = text.slice(bestOpen, closeIdx + matched.close.length);
    return {
        thinking: thinking.trim(),
        response: response.trim(),
        thinkingSegment,
        hasThinking: true,
    };
}

/** True when text carries body content outside of any thinking block. */
export function hasVisibleResponse(text) {
    return Boolean(text && parseThinkingContent(text).response.trim());
}

/**
 * Close a thinking block left hanging by an interrupted stream. api.js only
 * appends the close tag once content tokens arrive, so text captured mid-stream
 * can carry an open tag; persisting that would make a later edit's text read
 * back as reasoning.
 */
export function closeIncompleteThinking(text) {
    if (!text) return '';
    const parsed = parseThinkingContent(text);
    if (!parsed.incomplete) return text;
    // Thinking first, matching how finishEditing reassembles an edited message.
    return parsed.thinkingSegment + parsed.response;
}

/** Render or update the thinking block above message content. */
export function renderThinkingBlock(msgBody, parsed, { collapse = false } = {}) {
    let block = msgBody.querySelector('.thinking-block');
    if (!parsed.hasThinking) {
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
    if (collapse) {
        block.querySelector('.thinking-toggle').classList.remove('open');
        thinkContent.classList.remove('open');
    }
}
