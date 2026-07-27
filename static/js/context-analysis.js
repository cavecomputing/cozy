import { state } from './state.js';
import { resolveTemplateVariables } from './utils.js';
import { resolveLorebookEntries } from './lorebook.js';
import { parseThinkingContent } from './thinking.js';
import {
    estimateMessageTokens,
    estimateMessagesTokens,
    estimateTextTokens,
    selectContextMessages,
} from './tokenizer.js';
import {
    getContextTokenBudget,
    getRawHistoryMessages,
    getResponseTokenReserve,
} from './context-budget.js';

// Semantic sources are deliberately stable: CSS, tooltips, and tests all key
// off these ids while the active theme supplies their actual colors.
export const CONTEXT_SOURCES = [
    { id: 'system_prompt', label: 'System prompt' },
    { id: 'character_card', label: 'Character card' },
    { id: 'persona', label: 'Persona' },
    { id: 'lorebook', label: 'Lorebook' },
    { id: 'author_note', label: "Author's note" },
    { id: 'auto_summary', label: 'Auto summary' },
    { id: 'message_history', label: 'Message history' },
    { id: 'current_draft', label: 'Current draft' },
    { id: 'response_reserve', label: 'Reserved response' },
    { id: 'unused', label: 'Unused' },
];

const SOURCE_BY_VARIABLE = {
    user: 'persona',
    char: 'character_card',
    personality: 'character_card',
    scenario: 'character_card',
    description: 'character_card',
    persona: 'persona',
    mesExamples: 'character_card',
    lorebook: 'lorebook',
    author_note: 'author_note',
    summary: 'auto_summary',
    system_prompt: 'system_prompt',
    post_history_instructions: 'character_card',
};

const ZONE_ORDER = new Map([
    ['system', 0],
    ['history', 1],
    ['user', 2],
]);

function defaultZoneForSource(source) {
    return ['message_history', 'current_draft'].includes(source) ? 'history' : 'system';
}

function normalizeResolvedText(text) {
    return String(text || '').replace(/\n{3,}/g, '\n\n').trim();
}

/**
 * Resolve a prompt template once while surrounding dynamic values with private
 * markers. Removing the markers yields the exact outgoing content; keeping
 * their spans lets the token meter attribute repeated and conditional values
 * without maintaining a second template engine.
 */
function resolveTrackedTemplate(template, context, {
    defaultSource = 'system_prompt',
    variableSources = {},
    zone = 'system',
} = {}) {
    if (!template) return { content: '', fragments: [] };

    const markers = [];
    const trackedContext = { ...context };
    let markerSequence = 0;
    for (const [key, value] of Object.entries(context)) {
        // {{#var}} blocks treat whitespace-only values as empty (see
        // resolveTemplateVariables); wrapping them in markers would make them
        // truthy and leak the block's wrapper text into the outgoing prompt.
        if (value == null || String(value).trim() === '') continue;
        const source = variableSources[key] || SOURCE_BY_VARIABLE[key];
        if (!source) continue;
        const id = `${markerSequence++}`;
        const open = `\uE000${id}:S\uE001`;
        const close = `\uE000${id}:E\uE001`;
        markers.push({ id, open, close, source });
        trackedContext[key] = `${open}${value}${close}`;
    }

    const marked = resolveTemplateVariables(template, trackedContext);
    if (!marked) return { content: '', fragments: [] };

    const byOpen = new Map(markers.map(marker => [marker.open, marker]));
    const markerPattern = /\uE000\d+:[SE]\uE001/g;
    const fragments = [];
    const stack = [];
    let cursor = 0;
    let match;

    while ((match = markerPattern.exec(marked)) !== null) {
        if (match.index > cursor) {
            fragments.push({
                source: stack.at(-1)?.source || defaultSource,
                zone,
                text: marked.slice(cursor, match.index),
            });
        }
        const token = match[0];
        if (token.endsWith(':S\uE001')) {
            const marker = byOpen.get(token);
            if (marker) stack.push(marker);
        } else if (stack.length > 0) {
            stack.pop();
        }
        cursor = markerPattern.lastIndex;
    }
    if (cursor < marked.length) {
        fragments.push({
            source: stack.at(-1)?.source || defaultSource,
            zone,
            text: marked.slice(cursor),
        });
    }

    const content = normalizeResolvedText(fragments.map(fragment => fragment.text).join(''));
    return {
        content,
        fragments: fragments.filter(fragment => fragment.text),
    };
}

function resolveActiveLorebook(chat, character, lorebooks) {
    if (!chat) return character?.character_book || character?.data?.character_book || null;
    if (chat.active_lorebook_embedded) {
        return character?.character_book || character?.data?.character_book || null;
    }
    if (chat.active_lorebook_id != null) {
        const entry = lorebooks?.find(book => book.id === chat.active_lorebook_id);
        return entry?.book || null;
    }
    return null;
}

function trackedMessage(
    role,
    content,
    source,
    overheadSource = source,
    zone = defaultZoneForSource(source),
) {
    return {
        role,
        content,
        fragments: content ? [{ source, zone, text: content }] : [],
        overheadSource,
        overheadZone: zone,
    };
}

function mergeTrackedMessages(left, right, separator = '\n\n') {
    return {
        role: left.role,
        content: `${left.content}${separator}${right.content}`,
        fragments: [
            ...left.fragments,
            {
                source: right.overheadSource || right.fragments[0]?.source || 'system_prompt',
                zone: right.overheadZone || right.fragments[0]?.zone || 'system',
                text: separator,
            },
            ...right.fragments,
        ],
        overheadSource: left.overheadSource || right.overheadSource,
        overheadZone: left.overheadZone || right.overheadZone,
    };
}

function enforceTrackedAlternation(messages) {
    const out = [];
    for (const message of messages) {
        if (out.length > 0 && out.at(-1).role === message.role) {
            out[out.length - 1] = mergeTrackedMessages(out.at(-1), message);
        } else {
            out.push({ ...message, fragments: [...message.fragments] });
        }
    }

    const firstNonSystem = out.findIndex(message => message.role !== 'system');
    if (firstNonSystem !== -1 && out[firstNonSystem].role === 'assistant') {
        if (firstNonSystem > 0 && out[firstNonSystem - 1].role === 'system') {
            const greeting = out[firstNonSystem];
            const header = trackedMessage('system', '[Character Greeting]', 'system_prompt');
            out[firstNonSystem - 1] = mergeTrackedMessages(out[firstNonSystem - 1], header);
            out[firstNonSystem - 1] = mergeTrackedMessages(out[firstNonSystem - 1], greeting, '\n');
            out.splice(firstNonSystem, 1);
        } else {
            out.splice(firstNonSystem, 0, trackedMessage('user', '[Start]', 'system_prompt'));
        }
    }

    const final = [];
    for (const message of out) {
        if (final.length > 0 && final.at(-1).role === message.role) {
            final[final.length - 1] = mergeTrackedMessages(final.at(-1), message);
        } else {
            final.push(message);
        }
    }
    return final;
}

function allocateInteger(total, weights) {
    const positive = [...weights.entries()].filter(([, weight]) => weight > 0);
    if (total <= 0 || positive.length === 0) return new Map();
    const weightTotal = positive.reduce((sum, [, weight]) => sum + weight, 0);
    const allocations = positive.map(([source, weight]) => {
        const exact = total * weight / weightTotal;
        return { source, tokens: Math.floor(exact), remainder: exact - Math.floor(exact) };
    });
    let remaining = total - allocations.reduce((sum, item) => sum + item.tokens, 0);
    allocations.sort((a, b) => b.remainder - a.remainder);
    for (let i = 0; i < allocations.length && remaining > 0; i += 1, remaining -= 1) {
        allocations[i].tokens += 1;
    }
    return new Map(allocations.map(item => [item.source, item.tokens]));
}

function countTrackedTokens(messages) {
    const counts = new Map();
    const buckets = new Map();
    let nextOrder = 0;
    const ensureBucket = (source, zone) => {
        const normalizedZone = ['message_history', 'current_draft'].includes(source)
            ? 'history'
            : (zone || defaultZoneForSource(source));
        const key = `${normalizedZone}:${source}`;
        if (!buckets.has(key)) {
            buckets.set(key, {
                key,
                id: source,
                zone: normalizedZone,
                order: nextOrder++,
                tokens: 0,
            });
        }
        return buckets.get(key);
    };
    const addSource = (source, tokens) => {
        if (tokens <= 0) return;
        counts.set(source, (counts.get(source) || 0) + tokens);
    };
    const addBucket = (source, zone, tokens) => {
        if (tokens <= 0) return;
        ensureBucket(source, zone).tokens += tokens;
    };

    for (const message of messages) {
        const contentTokens = estimateTextTokens(message.content);
        const sourceWeights = new Map();
        const bucketWeights = new Map();
        for (const fragment of message.fragments) {
            const weight = estimateTextTokens(fragment.text);
            const bucket = ensureBucket(fragment.source, fragment.zone);
            sourceWeights.set(
                fragment.source,
                (sourceWeights.get(fragment.source) || 0) + weight,
            );
            bucketWeights.set(bucket.key, (bucketWeights.get(bucket.key) || 0) + weight);
        }
        for (const [source, tokens] of allocateInteger(contentTokens, sourceWeights)) {
            addSource(source, tokens);
            const sourceBucketWeights = new Map(
                [...bucketWeights].filter(([key]) => buckets.get(key).id === source),
            );
            for (const [key, bucketTokens] of allocateInteger(tokens, sourceBucketWeights)) {
                const bucket = buckets.get(key);
                addBucket(bucket.id, bucket.zone, bucketTokens);
            }
        }

        const overhead = estimateMessageTokens({ content: message.content }) - contentTokens;
        const overheadSource = message.overheadSource
            || [...sourceWeights.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
            || 'system_prompt';
        const overheadZone = message.overheadZone || defaultZoneForSource(overheadSource);
        addSource(overheadSource, overhead);
        addBucket(overheadSource, overheadZone, overhead);
    }
    return {
        bySource: counts,
        buckets: [...buckets.values()]
            .filter(bucket => bucket.tokens > 0)
            .sort((left, right) => {
                const zoneDelta = ZONE_ORDER.get(left.zone) - ZONE_ORDER.get(right.zone);
                return zoneDelta || left.order - right.order;
            }),
    };
}

function makeTemplateContext(character, persona, lorebookText, summaryText) {
    const context = {
        user: persona.name || 'User',
        char: character.name || '',
        personality: character.personality || '',
        scenario: character.scenario || '',
        description: character.description || '',
        persona: persona.description || '',
        mesExamples: character.mes_example || '',
        lorebook: lorebookText,
        author_note: state.activeChat?.author_note || '',
        summary: summaryText,
        system_prompt: character.system_prompt || '',
        post_history_instructions: character.post_history_instructions || '',
        user_message: '',
    };
    // Preserve the request builder's existing behavior: card post-history text
    // resolves its own {{char}}/{{user}} variables before entering the User
    // template, and the whole result remains attributed to the card.
    context.post_history_instructions = resolveTemplateVariables(
        context.post_history_instructions,
        context,
    );
    return context;
}

function assembleMessages(selectedMessages, { summaryText = '', nudge = null } = {}) {
    const character = state.activeCharacter || {};
    const persona = state.activePersona || {};
    const prompt = state.systemPrompts.find(item => item.id === state.activeSystemPromptId);

    const activeBook = resolveActiveLorebook(state.activeChat, character, state.lorebooks);
    const override = parseInt(state.lorebookScanDepthOverride, 10) || 0;
    const bookForResolver = activeBook && override > 0
        ? { ...activeBook, scan_depth: override }
        : activeBook;
    const lorebookContents = resolveLorebookEntries(bookForResolver, selectedMessages, {
        alwaysInjectAll: state.lorebookAlwaysInjectAll,
    });
    const lorebookText = lorebookContents.join('\n---\n');
    const context = makeTemplateContext(character, persona, lorebookText, summaryText);

    const systemTemplate = prompt?.content || '';
    const userTemplate = prompt?.post_history_content || '';
    const system = resolveTrackedTemplate(systemTemplate, context, { zone: 'system' });
    const messages = [];
    if (system.content) {
        messages.push({
            role: 'system',
            content: system.content,
            fragments: system.fragments,
            overheadSource: 'system_prompt',
            overheadZone: 'system',
        });
    }

    const wrapsUserMessage = /\{\{user_message\}\}/i.test(userTemplate);
    let lastUserIndex = -1;
    if (wrapsUserMessage) {
        for (let i = selectedMessages.length - 1; i >= 0; i -= 1) {
            if (selectedMessages[i].role === 'user') {
                lastUserIndex = i;
                break;
            }
        }
    }

    for (let i = 0; i < selectedMessages.length; i += 1) {
        const message = selectedMessages[i];
        const source = message._contextSource || 'message_history';
        // Reasoning blocks never go back into the prompt — they burn context
        // without adding anything the model needs to continue the scene.
        const content = parseThinkingContent(message.text || '').response;
        if (!content) continue;

        if (wrapsUserMessage && i === lastUserIndex) {
            context.user_message = content;
            const rendered = resolveTrackedTemplate(userTemplate, context, {
                variableSources: { user_message: source },
                zone: 'user',
            });
            messages.push({
                role: 'user',
                content: rendered.content,
                fragments: rendered.fragments,
                overheadSource: source,
                overheadZone: 'history',
            });
        } else {
            messages.push(trackedMessage(
                message.role === 'user' ? 'user' : 'assistant',
                content,
                source,
            ));
        }
    }

    if (!wrapsUserMessage || lastUserIndex === -1) {
        const rendered = resolveTrackedTemplate(userTemplate, context, { zone: 'user' });
        if (rendered.content) {
            messages.push({
                role: 'user',
                content: rendered.content,
                fragments: rendered.fragments,
                overheadSource: 'system_prompt',
                overheadZone: 'user',
            });
        }
    }

    // A summary placeholder inside a false conditional does not inject memory.
    // Keep the existing fallback behavior while retaining source provenance.
    if (summaryText && !messages.some(message => message.content.includes(summaryText))) {
        const header = '[MEMORY — STORY SO FAR]\n';
        const systemMessage = messages.find(message => message.role === 'system');
        if (systemMessage) {
            systemMessage.content += `\n\n${header}${summaryText}`;
            systemMessage.fragments.push(
                { source: 'system_prompt', zone: 'system', text: `\n\n${header}` },
                { source: 'auto_summary', zone: 'system', text: summaryText },
            );
        } else {
            messages.unshift({
                role: 'system',
                content: `${header}${summaryText}`,
                fragments: [
                    { source: 'system_prompt', zone: 'system', text: header },
                    { source: 'auto_summary', zone: 'system', text: summaryText },
                ],
                overheadSource: 'system_prompt',
                overheadZone: 'system',
            });
        }
    }

    if (nudge) messages.push(trackedMessage('user', nudge, 'current_draft'));

    const trackedMessages = enforceTrackedAlternation(messages);
    const tokenAccounting = countTrackedTokens(trackedMessages);
    return {
        trackedMessages,
        messages: trackedMessages.map(message => ({ role: message.role, content: message.content })),
        tokenCounts: tokenAccounting.bySource,
        segmentBuckets: tokenAccounting.buckets,
        lorebookContents,
    };
}

function sameSelection(left, right) {
    if (left.length !== right.length) return false;
    return left.every((message, index) => message === right[index]);
}

/**
 * Build the exact context messages plus the source-aware accounting used by
 * the meter, transcript boundary, summary aging, and outgoing request.
 */
export function analyzeContext({
    excludeLastN = 0,
    nudge = null,
    draftText = '',
    summaryText = '',
    includeSummarized = false,
    messages = state.messages,
} = {}) {
    let candidates = getRawHistoryMessages(messages, { excludeLastN, includeSummarized })
        .map(message => ({ ...message, _contextSource: message._contextSource || 'message_history' }));
    const draft = String(draftText || '').trim();
    if (draft) {
        candidates.push({ role: 'user', text: draft, _contextSource: 'current_draft', _isDraft: true });
    }

    const maxTokens = getContextTokenBudget();
    const responseTokens = getResponseTokenReserve();
    let selected = candidates;
    let assembled = assembleMessages(selected, { summaryText, nudge });

    if (maxTokens > 0 && candidates.length > 0) {
        const selectableTokens = (assembled.tokenCounts.get('message_history') || 0)
            + (assembled.tokenCounts.get('current_draft') || 0);
        const fixedTokens = Math.max(0, estimateMessagesTokens(assembled.messages) - selectableTokens);
        const available = Math.max(1, maxTokens - responseTokens - fixedTokens);
        const approximate = selectContextMessages(candidates, {
            maxTokens: available,
            stripThinking: true,
        });
        if (!sameSelection(selected, approximate)) {
            selected = approximate;
            assembled = assembleMessages(selected, { summaryText, nudge });
        }

        // Exact correction: prompt templates and role alternation can add or
        // remove overhead that a raw-message estimate cannot see.
        while (selected.length > 1
            && estimateMessagesTokens(assembled.messages) + responseTokens > maxTokens) {
            selected = selected.slice(1);
            assembled = assembleMessages(selected, { summaryText, nudge });
        }

        // If the rough first pass was conservative, grow back one oldest turn
        // at a time. Each trial re-resolves lore, conditionals, and alternation.
        let selectedStart = candidates.length - selected.length;
        while (selectedStart > 0) {
            const trialSelection = candidates.slice(selectedStart - 1);
            const trial = assembleMessages(trialSelection, { summaryText, nudge });
            if (estimateMessagesTokens(trial.messages) + responseTokens > maxTokens) break;
            selectedStart -= 1;
            selected = trialSelection;
            assembled = trial;
        }
    }

    const promptTokens = estimateMessagesTokens(assembled.messages);
    const allocatedTokens = promptTokens + responseTokens;
    const overflowTokens = maxTokens > 0 ? Math.max(0, allocatedTokens - maxTokens) : 0;
    const unusedTokens = maxTokens > 0 ? Math.max(0, maxTokens - allocatedTokens) : 0;
    const labels = new Map(CONTEXT_SOURCES.map(source => [source.id, source.label]));
    const segments = assembled.segmentBuckets.map(bucket => ({
        key: bucket.key,
        id: bucket.id,
        zone: bucket.zone,
        label: labels.get(bucket.id),
        tokens: bucket.tokens,
    }));
    if (responseTokens > 0) {
        segments.push({
            key: 'capacity:response_reserve',
            id: 'response_reserve',
            zone: 'capacity',
            label: labels.get('response_reserve'),
            tokens: responseTokens,
        });
    }
    if (unusedTokens > 0) {
        segments.push({
            key: 'capacity:unused',
            id: 'unused',
            zone: 'capacity',
            label: labels.get('unused'),
            tokens: unusedTokens,
        });
    }

    const selectedMessageIds = selected
        .filter(message => !message._isDraft && message.id != null)
        .map(message => message.id);

    return {
        messages: assembled.messages,
        selectedMessages: selected,
        selectedMessageIds,
        firstSelectedMessageId: selectedMessageIds[0] ?? null,
        lorebookContents: assembled.lorebookContents,
        segments,
        promptTokens,
        responseTokens,
        allocatedTokens,
        maxTokens,
        unusedTokens,
        overflowTokens,
        summaryTokens: assembled.tokenCounts.get('auto_summary') || 0,
    };
}
