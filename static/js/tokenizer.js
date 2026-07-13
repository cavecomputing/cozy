// ═══════════════════════════════════════════════════════════════════════════
// TOKEN ESTIMATION — heuristic, not exact BPE
// ═══════════════════════════════════════════════════════════════════════════
// Real BPE tokenizers ship ~1MB of merge tables per encoding, which we don't
// want to load on every page view. This heuristic combines a word-count and
// character-count estimate (taking the max) and is within ~10–15% of cl100k
// for natural-language chat. It over-counts on dense code and structured
// text, which is the safe direction for a budget meter — the UI labels every
// number with `≈` so users know it's an estimate.
//
// Public surface kept identical to the previous implementation so callers in
// request-builder.js and context-meter.js don't need to change.

import { parseThinkingContent } from './thinking.js';

const MESSAGE_OVERHEAD = 4; // ChatML-ish per-message framing tokens

export function estimateTextTokens(text) {
    if (!text) return 0;
    const s = String(text);
    const words = s.trim().split(/\s+/).filter(Boolean).length;
    // Word-based estimate (~1.3 tokens/word for English) vs character-based
    // (~4 chars/token). Take the max — code and dense punctuation push the
    // char estimate up; ordinary prose tracks the word estimate.
    return Math.max(1, Math.ceil(Math.max(words * 1.3, s.length / 4)));
}

/**
 * Trim `text` so its estimated token count is at most `maxTokens`. Returns the
 * text unchanged when it already fits, or when `maxTokens` is falsy/≤ 0 (no
 * cap). Cuts on a whitespace boundary where possible to avoid slicing a word
 * in half. Uses the same heuristic as the meters, so it's approximate.
 */
export function truncateTextToTokens(text, maxTokens) {
    if (!text) return text || '';
    if (!maxTokens || maxTokens <= 0) return String(text);
    const s = String(text);
    if (estimateTextTokens(s) <= maxTokens) return s;

    // Binary-search the longest prefix that still fits the budget.
    let lo = 0;
    let hi = s.length;
    while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        if (estimateTextTokens(s.slice(0, mid)) <= maxTokens) lo = mid;
        else hi = mid - 1;
    }
    let cut = s.slice(0, lo);
    // Prefer to end on a word boundary if we're not discarding too much.
    const lastSpace = cut.lastIndexOf(' ');
    if (lastSpace > lo * 0.6) cut = cut.slice(0, lastSpace);
    return cut.trimEnd();
}

export function estimateMessageTokens(message, { stripThinking = false } = {}) {
    let content = message?.content ?? message?.text ?? '';
    if (stripThinking) content = parseThinkingContent(content).response;
    return MESSAGE_OVERHEAD + estimateTextTokens(content);
}

export function estimateMessagesTokens(messages, options = {}) {
    return (messages || []).reduce((sum, msg) => sum + estimateMessageTokens(msg, options), 0);
}

export function selectContextMessages(messages, {
    maxMessages = 0,
    maxTokens = 0,
    stripThinking = false,
} = {}) {
    let candidates = Array.isArray(messages) ? messages : [];
    if (maxMessages > 0) candidates = candidates.slice(-maxMessages);
    if (!maxTokens || maxTokens <= 0) return candidates;

    const selected = [];
    let total = 0;
    for (let i = candidates.length - 1; i >= 0; i -= 1) {
        const msg = candidates[i];
        const cost = estimateMessageTokens(msg, { stripThinking });
        if (selected.length > 0 && total + cost > maxTokens) break;
        selected.unshift(msg);
        total += cost;
        if (total >= maxTokens) break;
    }
    return selected;
}

/**
 * Return the `id` of the first (oldest) message that fits within the token
 * budget, or `null` if all messages fit or no limit is set.  Callers use this
 * to position the context-boundary indicator in the UI.
 */
export function getContextBoundaryMsgId(messages, {
    maxTokens = 0,
    stripThinking = false,
} = {}) {
    if (!maxTokens || maxTokens <= 0) return null;
    if (!Array.isArray(messages) || messages.length === 0) return null;

    const selected = selectContextMessages(messages, { maxTokens, stripThinking });
    if (selected.length === 0) return null;

    const firstInContext = selected[0];
    // If every message fits, the boundary sits at the top (return null so the
    // caller inserts the indicator before the first message).
    if (selected.length >= messages.length) return null;

    return firstInContext.id ?? null;
}
