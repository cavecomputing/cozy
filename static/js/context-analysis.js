import { resolveTemplateVariables } from './utils.js';
import { resolveLorebookEntries } from './lorebook.js';
import { parseThinkingContent } from './thinking.js';
import {
    estimateMessageTokens,
    estimateMessagesTokens,
    estimateTextTokens,
    selectContextMessages,
} from './tokenizer.js';
import { contextInputs } from './context-budget.js';

// Semantic sources are deliberately stable: CSS, tooltips, and tests all key
// off these ids while the active theme supplies their actual colors.
export const CONTEXT_SOURCES = [
    { id: 'system_prompt', label: 'System' },
    { id: 'character_card', label: 'Character card' },
    { id: 'persona', label: 'Persona' },
    { id: 'lorebook', label: 'Lorebook' },
    { id: 'author_note', label: "Author's note" },
    { id: 'auto_summary', label: 'Auto summary' },
    { id: 'message_history', label: 'Message history' },
    { id: 'current_draft', label: 'Current draft' },
    { id: 'post_history', label: 'Post-History' },
    { id: 'response_reserve', label: 'Reserved response' },
    { id: 'unused', label: 'Unused' },
];

// What Cozy sends around the two boxes of the Prompt page, and the only
// layout there is. A tag holds values by name and tags of its own, each tag
// says what it is in a note, and one with nothing to hold is not sent.
const tag = (name, attributes, ...parts) => ({ name, attributes, parts });
const SYSTEM_LAYOUT = [
    tag('system_instructions', 'status="enabled" note="How to tell this story. Follow it throughout."',
        'system'),
    tag('character', 'note="Who {{char}} is: background, looks and personality."',
        'description',
        'personality',
        tag('example_dialogue', 'note="How {{char}} talks and acts. A style reference, not events in this story."',
            'mesExamples')),
    tag('user', 'note="{{user}}, the character the user plays."',
        'persona'),
    tag('world_info', 'note="Facts about the setting. True unless the story changes them."',
        'lorebook',
        'scenario'),
    tag('memory', 'note="From earlier in this chat. For reference; not all of it applies now."',
        tag('author_note', 'note="The user\'s running notes on the story."',
            'authorNote'),
        tag('story_so_far', 'note="What happened earlier in this chat, summarized."',
            'summary')),
];
const POST_HISTORY_LAYOUT = [
    tag('post_history_instructions', 'status="enabled" note="For the reply you write now."',
        'postHistory'),
];

const SOURCE_BY_VALUE = {
    system: 'system_prompt',
    description: 'character_card',
    personality: 'character_card',
    mesExamples: 'character_card',
    persona: 'persona',
    lorebook: 'lorebook',
    scenario: 'character_card',
    authorNote: 'author_note',
    summary: 'auto_summary',
    postHistory: 'post_history',
};

/**
 * Lay the values out as the fragments the meter counts. Joined, they are the
 * exact outgoing text. A tag and its note count toward the value they sit
 * against, so none of Cozy's own words make a segment of their own.
 */
function layOut(parts, values, names) {
    const fragments = [];
    for (const part of parts) {
        let inner;
        if (typeof part === 'string') {
            inner = values[part] ? [{ source: SOURCE_BY_VALUE[part], text: values[part] }] : [];
        } else {
            inner = layOut(part.parts, values, names);
            if (inner.length > 0) {
                const open = `<${part.name} ${resolveNames(part.attributes, names)}>\n`;
                inner.unshift({ source: inner[0].source, text: open });
                inner.push({ source: inner.at(-1).source, text: `\n</${part.name}>` });
            }
        }
        if (inner.length === 0) continue;
        if (fragments.length > 0) fragments.push({ source: inner[0].source, text: '\n\n' });
        fragments.push(...inner);
    }
    return fragments;
}

// A message's own overhead (its role and framing) counts toward what it opens with.
function trackedMessage(role, fragments) {
    return {
        role,
        content: fragments.map(fragment => fragment.text).join(''),
        fragments,
        overheadSource: fragments[0]?.source,
    };
}

function mergeTrackedMessages(left, right, separator = '\n\n') {
    return {
        role: left.role,
        content: `${left.content}${separator}${right.content}`,
        fragments: [
            ...left.fragments,
            { source: right.overheadSource, text: separator },
            ...right.fragments,
        ],
        overheadSource: left.overheadSource,
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

    // Strict endpoints reject a conversation that opens on the character's
    // turn, so a window that starts on one gets a minimal user turn in front of
    // it. This is protocol rather than content: it is the only text assembly
    // adds outside the layout, and it never touches the system prompt.
    const firstNonSystem = out.findIndex(message => message.role !== 'system');
    if (firstNonSystem !== -1 && out[firstNonSystem].role === 'assistant') {
        const shim = [{ source: 'message_history', text: '[Start]' }];
        out.splice(firstNonSystem, 0, trackedMessage('user', shim));
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

/** Tokens by source, in the order the sources first appear in the request. */
function countTrackedTokens(messages) {
    const counts = new Map();
    for (const message of messages) {
        const contentTokens = estimateTextTokens(message.content);
        const weights = new Map();
        for (const { source, text } of message.fragments) {
            weights.set(source, (weights.get(source) || 0) + estimateTextTokens(text));
            if (!counts.has(source)) counts.set(source, 0);
        }
        const overhead = estimateMessageTokens({ content: message.content }) - contentTokens;
        counts.set(message.overheadSource, (counts.get(message.overheadSource) || 0) + overhead);
        for (const [source, tokens] of allocateInteger(contentTokens, weights)) {
            counts.set(source, counts.get(source) + tokens);
        }
    }
    return counts;
}

/** {{char}} and {{user}} replaced by the names they stand for. */
function resolveNames(text, names) {
    return String(text || '')
        .replace(/\{\{char\}\}/gi, () => names.char)
        .replace(/\{\{user\}\}/gi, () => names.user);
}

/** The text for each slot of the layout, '' for one with nothing to say. */
function layoutValues(inputs, lorebookText, names) {
    const { character, persona } = inputs;
    // Cards, personas and lorebooks write the two names as {{char}} and
    // {{user}}, and some write {{time}} or a {{random}} pick.
    const filled = text => resolveTemplateVariables(text, names);
    // A box of the Prompt page is prose. The same few variables work in it and
    // any other stands for nothing, a card's {{original}} above all.
    const box = text => filled(text).replace(/\{\{[^{}\n]*\}\}/g, '').trim();
    return {
        system: box(inputs.systemText),
        description: filled(character.description),
        personality: filled(character.personality),
        mesExamples: filled(character.mes_example),
        persona: filled(persona.description),
        lorebook: filled(lorebookText),
        scenario: filled(character.scenario),
        authorNote: filled(inputs.authorNote),
        summary: filled(inputs.summaryText),
        postHistory: box(inputs.postHistoryText),
    };
}

function assembleMessages(selectedMessages, inputs, resolvedLore) {
    const lorebookContents = resolveLorebookEntries(inputs.lorebook, selectedMessages, resolvedLore);
    const names = { user: inputs.persona.name || 'User', char: inputs.character.name || '' };
    const values = layoutValues(inputs, lorebookContents.join('\n---\n'), names);

    const messages = [];
    const system = trackedMessage('system', layOut(SYSTEM_LAYOUT, values, names));
    if (system.content) messages.push(system);

    for (const message of selectedMessages) {
        // Reasoning blocks never go back into the prompt — they burn context
        // without adding anything the model needs to continue the scene. The
        // names are resolved here as the renderer resolves them on screen, so
        // a greeting copied from a card reads the same to the model as to you.
        const content = resolveNames(parseThinkingContent(message.text || '').response, names);
        if (!content) continue;
        messages.push(trackedMessage(
            message.role === 'user' ? 'user' : 'assistant',
            [{ source: message._contextSource || 'message_history', text: content }],
        ));
    }

    // Its own user turn after the chat, which the alternation pass below joins
    // to your latest message when that is the turn before it.
    const postHistory = trackedMessage('user', layOut(POST_HISTORY_LAYOUT, values, names));
    if (postHistory.content) messages.push(postHistory);

    const trackedMessages = enforceTrackedAlternation(messages);
    return {
        messages: trackedMessages.map(message => ({ role: message.role, content: message.content })),
        // Held for the editor's preview: the layout alone, before the
        // alternation shims.
        renderedPrompts: { system: system.content, postHistory: postHistory.content },
        tokenCounts: countTrackedTokens(trackedMessages),
        lorebookContents,
    };
}

function sameSelection(left, right) {
    if (left.length !== right.length) return false;
    return left.every((message, index) => message === right[index]);
}

function deepFreeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {
        Object.freeze(value);
        Object.values(value).forEach(deepFreeze);
    }
    return value;
}

function sameCandidates(left, right) {
    if (left.length !== right.length) return false;
    return left.every((message, index) => {
        const other = right[index];
        return message.text === other.text && message.role === other.role
            && message.id === other.id && message._contextSource === other._contextSource;
    });
}

// The analysis made last, and what it was made from.
let latest = null;
let latestExpiry = 0;

/**
 * Build the exact context messages plus the source-aware accounting used by
 * the meter, transcript boundary, summary aging, and outgoing request.
 *
 * Those four ask one after another about the same chat: a send used to run
 * this three times before the request left, and each run grows with the chat
 * and the context window. So a call whose inputs equal the last call's gets
 * the last analysis back.
 *
 * That is safe because of how the work is split. contextInputs() is the only
 * place anything is read from the app, and analyze() sees nothing else, so
 * equal inputs cannot give a different answer. The messages are compared
 * field by field against private copies and the rest as JSON, so an edit made
 * in place to a card, a lorebook entry or a message counts as a change. Read
 * something new from `state` in this file and that guarantee is gone: add it
 * to contextInputs() instead.
 *
 * The analysis is shared between its callers, so it is frozen. And it is only
 * kept until the browser next takes a turn: {{time}}, {{date}} and {{random}}
 * are not inputs, and an analysis a moment old is still this same send.
 */
export function analyzeContext(options) {
    const inputs = contextInputs(options);
    const { candidates, ...rest } = inputs;
    const fixed = JSON.stringify(rest);
    if (latest?.fixed === fixed && sameCandidates(latest.candidates, candidates)) {
        return latest.analysis;
    }
    latest = { fixed, candidates, analysis: deepFreeze(analyze(inputs)) };
    latestExpiry ||= setTimeout(() => {
        latest = null;
        latestExpiry = 0;
    });
    return latest.analysis;
}

function analyze(inputs) {
    const { candidates, maxTokens, responseTokens } = inputs;
    // Only a tail that fits the whole window can survive the trimming below,
    // so the first, rough assembly needn't walk the rest of a long chat.
    let selected = maxTokens > 0
        ? selectContextMessages(candidates, { maxTokens, stripThinking: true })
        : candidates;
    // Every trial below matches the lorebook's keys against the newest
    // messages, which the trials all share, so one match serves them all. The
    // map lives for this one call, during which the book cannot change.
    const resolvedLore = new Map();
    const assemble = selection => assembleMessages(selection, inputs, resolvedLore);
    let assembled = assemble(selected);

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
            assembled = assemble(selected);
        }

        // Exact correction: the prompt layout and role alternation can add or
        // remove overhead that a raw-message estimate cannot see.
        while (selected.length > 1
            && estimateMessagesTokens(assembled.messages) + responseTokens > maxTokens) {
            selected = selected.slice(1);
            assembled = assemble(selected);
        }

        // If the rough first pass was conservative, grow back one oldest turn
        // at a time. Each trial re-resolves lore and alternation.
        let selectedStart = candidates.length - selected.length;
        while (selectedStart > 0) {
            const trialSelection = candidates.slice(selectedStart - 1);
            const trial = assemble(trialSelection);
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
    const segment = (id, tokens) => ({ id, label: labels.get(id), tokens });
    const segments = [...assembled.tokenCounts]
        .filter(([, tokens]) => tokens > 0)
        .map(([id, tokens]) => segment(id, tokens));
    if (responseTokens > 0) segments.push(segment('response_reserve', responseTokens));
    if (unusedTokens > 0) segments.push(segment('unused', unusedTokens));

    const selectedMessageIds = selected
        .filter(message => !message._isDraft && message.id != null)
        .map(message => message.id);

    return {
        messages: assembled.messages,
        renderedPrompts: assembled.renderedPrompts,
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
