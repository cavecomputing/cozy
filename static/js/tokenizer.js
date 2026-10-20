// ═══════════════════════════════════════════════════════════════════════════
// TOKEN ESTIMATION — heuristic, not exact BPE
// ═══════════════════════════════════════════════════════════════════════════
// Real BPE tokenizers ship ~1MB of merge tables per encoding, which we don't
// want to load on every page view. This heuristic combines a word-count and
// character-count estimate (taking the max) and is within ~10–15% of cl100k
// for natural-language chat. It over-counts on dense code and structured
// text, which is the safe direction for a budget meter — the UI labels every
// number with `≈` so users know it's an estimate.

import { parseThinkingContent } from './thinking.js';

const MESSAGE_OVERHEAD = 4; // ChatML-ish per-message framing tokens

// Counts of recent texts. One analysis counts each message several times over,
// and the next one, a keystroke later, counts the same messages again.
const counted = new Map();

export function estimateTextTokens(text) {
    if (!text) return 0;
    const s = String(text);
    let tokens = counted.get(s);
    if (tokens === undefined) {
        if (counted.size >= 5000) counted.clear();
        tokens = countTokens(s);
        counted.set(s, tokens);
    }
    return tokens;
}

function countTokens(s) {
    let words = 0;
    let inWord = false;
    for (let i = 0; i < s.length; i += 1) {
        const c = s.charCodeAt(i);
        const space = c === 32 || (c >= 9 && c <= 13) || (c > 127 && /\s/.test(s[i]));
        if (space) inWord = false;
        else if (!inWord) { inWord = true; words += 1; }
    }
    // Word-based estimate (~1.3 tokens/word for English) vs character-based
    // (~4 chars/token). Take the max — code and dense punctuation push the
    // char estimate up; ordinary prose tracks the word estimate.
    return Math.max(1, Math.ceil(Math.max(words * 1.3, s.length / 4)));
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
    maxTokens = 0,
    stripThinking = false,
} = {}) {
    const candidates = Array.isArray(messages) ? messages : [];
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
