from helpers import run_node_module


BASE_NODE_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { buildChatPayload } from './static/js/request-builder.js';
    import { analyzeContext } from './static/js/context-analysis.js';

    state.apiModel = 'test-model';
    state.activeSamplers = new Set();
    state.contextMaxTokens = '0';
    el.settingsContextTokens = { value: '0' };
    state.activePersona = { name: 'Morgan', description: 'A curious user.' };

    // One preset holding the two boxes of the Prompt page.
    const usePrompt = (content, post_history_content = '') => {
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{ id: 1, name: 'Test', content, post_history_content }];
    };
    const SYSTEM_OPEN = '<system_instructions status="enabled" note="How to tell this story. Follow it throughout.">';
    const inSystemTag = text => `${SYSTEM_OPEN}\n${text}\n</system_instructions>`;
    const POST_OPEN = '<post_history_instructions status="enabled" note="For the reply you write now.">';
    const inPostTag = text => `${POST_OPEN}\n${text}\n</post_history_instructions>`;
"""


def test_chat_payload_lays_the_prompt_out_around_the_two_boxes():
    """The System box, the card, the persona, the lorebook and the memory go
    out in one fixed layout, every tag saying what it holds, and the
    Post-History box rides on the end of the latest message."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = {
            name: 'Mira',
            description: 'A careful tester.',
            personality: 'Dry.',
            mes_example: '<START>\n{{char}}: Noted.',
            scenario: 'A lab at night.',
            character_book: {
                entries: [{ enabled: true, constant: true, content: 'The lab is underground.' }],
            },
        };
        state.activeChat = { author_note: 'Keep it tense.', active_lorebook_embedded: true };
        usePrompt('Play {{char}} for {{user}}.', 'Stay with {{char}}.');
        state.messages = [{ id: 1, role: 'user', text: 'Did my prompt arrive?' }];

        const payload = buildChatPayload();
        assert.deepEqual(payload.messages.map(message => message.role), ['system', 'user']);
        assert.equal(payload.messages[0].content, [
            '<system_instructions status="enabled" note="How to tell this story. Follow it throughout.">',
            'Play Mira for Morgan.',
            '</system_instructions>',
            '',
            '<character note="Who Mira is: background, looks and personality.">',
            'A careful tester.',
            '',
            'Dry.',
            '',
            '<example_dialogue note="How Mira talks and acts. A style reference, not events in this story.">',
            '<START>',
            'Mira: Noted.',
            '</example_dialogue>',
            '</character>',
            '',
            '<user note="Morgan, the character the user plays.">',
            'A curious user.',
            '</user>',
            '',
            '<world_info note="Facts about the setting. True unless the story changes them.">',
            'The lab is underground.',
            '',
            'A lab at night.',
            '</world_info>',
            '',
            '<memory note="From earlier in this chat. For reference; not all of it applies now.">',
            '<author_note note="The user\'s running notes on the story.">',
            'Keep it tense.',
            '</author_note>',
            '</memory>',
        ].join('\n'));
        assert.equal(payload.messages[1].content, [
            'Did my prompt arrive?',
            '',
            '<post_history_instructions status="enabled" note="For the reply you write now.">',
            'Stay with Mira.',
            '</post_history_instructions>',
        ].join('\n'));

        // The summary is the other half of <memory>.
        const withSummary = analyzeContext({ summaryText: 'They crossed the bridge.' });
        assert.ok(withSummary.messages[0].content.endsWith([
            '</author_note>',
            '',
            '<story_so_far note="What happened earlier in this chat, summarized.">',
            'They crossed the bridge.',
            '</story_so_far>',
            '</memory>',
        ].join('\n')));
    """
    run_node_module(code)


def test_prompt_editor_draft_syncs_active_prompt_before_persistence():
    """The token meter should see prompt edits without waiting for autosave."""
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { syncActivePromptFromEditors } from './static/js/system-prompts.js';

        state.activeSystemPromptId = 2;
        state.systemPrompts = [{
            id: 2,
            content: 'old system',
            post_history_content: 'old user',
        }];
        el.syspromptContent = { value: 'live system draft' };
        el.postHistoryContent = { value: 'live user draft' };

        const active = syncActivePromptFromEditors();

        assert.equal(active.content, 'live system draft');
        assert.equal(active.post_history_content, 'live user draft');
        assert.equal(state.systemPrompts[0], active);
    """
    run_node_module(code)


def test_a_part_with_nothing_to_say_is_left_out_tag_and_all():
    """An empty tag is still a heading the model reads and tries to honour, so
    a part with no content takes its tag and its note with it. Imported cards
    commonly carry fields like `"scenario": " "`, which count as empty."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', description: 'A careful tester.', scenario: ' ' };
        state.activePersona = { name: 'Morgan', description: '' };
        state.activeChat = { author_note: '   ' };
        usePrompt('   ');
        state.messages = [{ id: 1, role: 'user', text: 'hi' }];

        const bare = buildChatPayload().messages;
        assert.equal(bare[0].content,
            '<character note="Who Mira is: background, looks and personality.">\n'
            + 'A careful tester.\n</character>');
        assert.equal(bare[1].content, 'hi');

        // Nothing at all to send means no system message.
        state.activeCharacter.description = '';
        assert.deepEqual(buildChatPayload().messages, [{ role: 'user', content: 'hi' }]);

        // Filling a part brings its tag back, and a tag inside another brings
        // that one with it.
        state.activePersona.description = 'A curious user.';
        state.activeCharacter.mes_example = 'Mira: Noted.';
        const filled = buildChatPayload().messages[0].content;
        assert.equal(filled, [
            '<character note="Who Mira is: background, looks and personality.">',
            '<example_dialogue note="How Mira talks and acts. A style reference, not events in this story.">',
            'Mira: Noted.',
            '</example_dialogue>',
            '</character>',
            '',
            '<user note="Morgan, the character the user plays.">',
            'A curious user.',
            '</user>',
        ].join('\n'));
        assert.doesNotMatch(filled, /<memory|<world_info|<system_instructions/);
    """
    run_node_module(code)


def test_an_empty_authors_note_leaves_no_gap_above_the_summary():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        state.activeChat = { author_note: '' };
        usePrompt('');
        state.messages = [{ id: 1, role: 'user', text: 'hi' }];

        const analysis = analyzeContext({ summaryText: 'They crossed the bridge.' });
        assert.equal(analysis.messages[0].content, [
            '<memory note="From earlier in this chat. For reference; not all of it applies now.">',
            '<story_so_far note="What happened earlier in this chat, summarized.">',
            'They crossed the bridge.',
            '</story_so_far>',
            '</memory>',
        ].join('\n'));
    """
    run_node_module(code)


def test_the_boxes_are_sent_as_written():
    """A box is the user's own words: tags and headings in it are text, not
    layout, and nothing in it is tidied away."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        usePrompt('[Rules]\n<rule></rule>\n3 < 4 > 2');
        state.messages = [{ id: 1, role: 'user', text: 'hi' }];

        assert.equal(buildChatPayload().messages[0].content,
            inSystemTag('[Rules]\n<rule></rule>\n3 < 4 > 2'));
    """
    run_node_module(code)


def test_only_a_few_variables_work_in_a_box():
    """The names, the time, the date and a random pick. A box no longer places
    the card, so {{description}} in one stands for nothing, and so does a
    card's {{original}}."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', description: 'A careful tester.' };
        state.activePersona = { name: 'Morgan', description: '' };
        usePrompt(
            '{{original}} Play {{char}} for {{USER}}. {{description}}{{#persona}}x{{/persona}} Mood: {{random:calm,calm}}.',
            '{{summary}}Year {{date}}.',
        );
        state.messages = [{ id: 1, role: 'user', text: 'hi' }];

        const [system, user] = buildChatPayload().messages;
        assert.ok(system.content.startsWith(inSystemTag('Play Mira for Morgan. x Mood: calm.')));
        assert.equal(system.content.match(/A careful tester\./g).length, 1);
        assert.equal(user.content, `hi\n\n${inPostTag(`Year ${new Date().toLocaleDateString()}.`)}`);
    """
    run_node_module(code)


def test_history_opening_on_the_character_gets_the_start_shim():
    """A window that opens on the character's turn is illegal for strict
    endpoints, so a minimal user turn goes in front of it — and nothing lands in
    the system prompt that the user did not write."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        usePrompt('SYSTEM.');
        state.messages = [
            { id: 1, role: 'character', text: 'Hello there, traveller.' },
            { id: 2, role: 'user', text: 'hi' },
        ];

        const payload = buildChatPayload();
        assert.deepEqual(payload.messages, [
            { role: 'system', content: inSystemTag('SYSTEM.') },
            { role: 'user', content: '[Start]' },
            { role: 'assistant', content: 'Hello there, traveller.' },
            { role: 'user', content: 'hi' },
        ]);
    """
    run_node_module(code)


def test_history_opening_on_the_user_gets_no_shim():
    """That request already alternates, so nothing is added to it at all."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        usePrompt('SYSTEM.');
        state.messages = [
            { id: 1, role: 'user', text: 'I speak first.' },
            { id: 2, role: 'character', text: 'And I answer.' },
        ];

        const payload = buildChatPayload();
        assert.deepEqual(payload.messages, [
            { role: 'system', content: inSystemTag('SYSTEM.') },
            { role: 'user', content: 'I speak first.' },
            { role: 'assistant', content: 'And I answer.' },
        ]);
    """
    run_node_module(code)


def test_rendered_prompts_exclude_the_alternation_shim():
    """The editor's preview shows the two boxes in their layout, not the
    assembled request.

    The [Start] turn in front of a history that opens on the character is the
    only text assembly adds outside the layout, and it has no counterpart in
    the editor. Nor has the message the Post-History box is joined to.
    """
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        usePrompt('SYSTEM for {{char}}', 'TAIL for {{char}}');
        state.messages = [
            { id: 1, role: 'character', text: 'A greeting with <div style=>markup</div>.' },
            { id: 2, role: 'user', text: 'hi' },
        ];

        const analysis = analyzeContext({});
        assert.equal(analysis.messages[1].content, '[Start]');

        const rendered = analysis.renderedPrompts;
        assert.equal(rendered.system, inSystemTag('SYSTEM for Mira'));
        assert.equal(rendered.postHistory, inPostTag('TAIL for Mira'));
    """
    run_node_module(code)


def test_names_inside_card_text_and_history_reach_the_model_resolved():
    """{{char}}/{{user}} written inside a card, persona or message are names too.

    The screen shows them resolved, so the model must not be the one reader
    left looking at the placeholders.
    """
    code = BASE_NODE_SETUP + r"""
        state.activePersona = { name: 'Morgan', description: '{{user}} is new in town.' };
        state.activeCharacter = {
            name: 'Mira',
            description: '{{char}} guards {{USER}}.',
            scenario: '{{char}} meets {{user}} at the gate.',
            mes_example: '{{user}}: Hi\n{{char}}: Hello.',
        };
        state.activeChat = { author_note: 'Go easy on {{user}}.' };
        usePrompt('');
        state.messages = [
            { id: 1, role: 'character', text: '*{{char}} salutes {{user}}.*' },
            { id: 2, role: 'user', text: 'Hi, {{char}}.' },
        ];

        const payload = buildChatPayload();
        const everything = payload.messages.map(m => m.content).join('\n');
        assert.doesNotMatch(everything, /\{\{(char|user)\}\}/i);
        for (const line of [
            'Mira guards Morgan.', 'Mira meets Morgan at the gate.', 'Morgan: Hi\nMira: Hello.',
            'Morgan is new in town.', 'Go easy on Morgan.',
        ]) assert.ok(payload.messages[0].content.includes(`\n${line}\n`), line);
        assert.ok(payload.messages.some(m => m.role === 'assistant' && m.content === '*Mira salutes Morgan.*'));
        assert.equal(payload.messages.at(-1).content, 'Hi, Mira.');
    """
    run_node_module(code)


def test_post_history_follows_the_chat_as_its_own_turn_after_the_character():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        usePrompt('System for {{char}}', '((OOC: Write only {{char}}. Do not narrate for {{user}}.))');
        state.messages = [
            { role: 'user', text: 'Hello' },
            { role: 'character', text: 'Hi there.' },
        ];

        const payload = buildChatPayload();
        assert.deepEqual(payload.messages.map(m => m.role), ['system', 'user', 'assistant', 'user']);
        assert.equal(payload.messages.at(-1).content,
            inPostTag('((OOC: Write only Mira. Do not narrate for Morgan.))'));
    """
    run_node_module(code)


def test_an_empty_post_history_box_appends_nothing():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        usePrompt('System for {{char}}', '  ');
        state.messages = [{ role: 'user', text: 'Hello' }];

        const payload = buildChatPayload();
        assert.deepEqual(payload.messages.map(m => m.role), ['system', 'user']);
        assert.equal(payload.messages[1].content, 'Hello');
    """
    run_node_module(code)


def test_a_cards_own_prompts_are_not_sent():
    """The card keeps its system prompt and post-history instructions, but the
    two boxes are what the model is told."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = {
            name: 'Mira',
            system_prompt: 'CARD SYSTEM',
            post_history_instructions: 'CARD POST',
        };
        usePrompt('Mine.', 'Mine too.');
        state.messages = [{ role: 'user', text: 'Hello' }];

        const sent = JSON.stringify(buildChatPayload().messages);
        assert.doesNotMatch(sent, /CARD SYSTEM|CARD POST/);
        assert.match(sent, /Mine\./);
        assert.match(sent, /Mine too\./);
    """
    run_node_module(code)


def test_summary_tokens_reduce_the_raw_message_budget():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        usePrompt('');
        state.contextMaxTokens = '150';
        el.settingsContextTokens = { value: '150' };
        el.samplerMaxTokens = { value: '20' };
        state.messages = Array.from({ length: 12 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `message-${i}-abcdefghijklmno`,
        }));

        state.activeChat = { summary_enabled: false, summary: { lines: [] } };
        const withoutSummary = buildChatPayload();

        state.activeChat = {
            summary_enabled: true,
            summary: { lines: [{ section: 'story', text: 'x'.repeat(120), pinned: false }] },
        };
        const withSummary = buildChatPayload();

        const rawWithout = withoutSummary.messages.filter(m => /message-\d+-/.test(m.content));
        const rawWith = withSummary.messages.filter(m => /message-\d+-/.test(m.content));
        // Full-budget accounting includes role framing and the rendered system
        // message, tags and notes included.
                assert.equal(rawWithout.length, 11);
        assert.equal(rawWith.length, 3);
    """
    run_node_module(code)


def test_the_summary_is_sent_once_in_its_own_tag():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activeChat = {
            summary_enabled: true,
            summary: {
                lines: [{ section: 'story', text: 'Morgan carries the brass key.', pinned: false }],
            },
        };
        usePrompt('CUSTOM');
        state.messages = [{ id: 1, role: 'user', text: 'Open the door.' }];

        const payload = buildChatPayload();
        assert.equal(
            (JSON.stringify(payload.messages).match(/Morgan carries the brass key\./g) || []).length,
            1,
        );
        assert.match(payload.messages[0].content,
            /<story_so_far note="[^"]+">\n[^<]*Morgan carries the brass key\.[^<]*\n<\/story_so_far>\n<\/memory>$/);
    """
    run_node_module(code)


def test_regen_payload_excludes_summarized_turn_but_keeps_first_live_turn():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        usePrompt('');
        state.activeChat = {
            summary_enabled: true,
            summary_up_to_msg_id: 1,
            summary: {
                lines: [{ section: 'story', text: 'Old history retained.', pinned: false }],
            },
        };
        state.messages = [
            { id: 1, role: 'character', text: 'SUMMARIZED TURN' },
            { id: 2, role: 'user', text: 'FIRST LIVE TURN' },
            { id: 3, role: 'character', text: 'ASSISTANT BEING REGENERATED' },
        ];

        const payload = buildChatPayload(1);
        const serialized = JSON.stringify(payload.messages);

        assert.doesNotMatch(serialized, /SUMMARIZED TURN/);
        assert.match(serialized, /Old history retained\./);
        assert.match(serialized, /FIRST LIVE TURN/);
        assert.doesNotMatch(serialized, /ASSISTANT BEING REGENERATED/);
    """
    run_node_module(code)


# A chat with something in every part of the layout.
EVERY_SOURCE_SETUP = BASE_NODE_SETUP + r"""
    el.samplerMaxTokens = { value: '100' };
    state.activeCharacter = {
        name: 'Mira',
        description: 'A careful cartographer.',
        personality: 'Patient and observant.',
        scenario: 'A storm is coming.',
        character_book: {
            entries: [{
                enabled: true,
                constant: true,
                insertion_order: 1,
                content: 'The observatory stands above the sea.',
            }],
        },
    };
    state.activePersona = { name: 'Morgan', description: 'An inquisitive sailor.' };
    state.activeChat = {
        summary_enabled: true,
        active_lorebook_embedded: true,
        author_note: 'Keep the brass key important.',
    };
    usePrompt('Never break character.', 'Write only {{char}}. Do not narrate for {{user}}.');
    state.messages = [
        { id: 1, role: 'user', text: 'Where are we?' },
        { id: 2, role: 'character', text: 'At the cliff path.' },
    ];
    const analyzeEverySource = () => analyzeContext({
        summaryText: 'The lantern was lost in the storm.',
        draftText: 'Search the observatory.',
    });
"""


def test_context_analysis_attributes_every_semantic_source_and_reconciles_totals():
    code = EVERY_SOURCE_SETUP + r"""
        state.contextMaxTokens = '1000';
        el.settingsContextTokens = { value: '1000' };

        const analysis = analyzeEverySource();
        const byId = new Map(analysis.segments.map(segment => [segment.id, segment.tokens]));
        for (const id of [
            'system_prompt', 'character_card', 'persona', 'lorebook',
            'author_note', 'auto_summary', 'message_history', 'current_draft',
            'post_history', 'response_reserve', 'unused',
        ]) assert.ok(byId.get(id) > 0, `${id} should have tokens`);

        const promptTotal = analysis.segments
            .filter(segment => !['response_reserve', 'unused'].includes(segment.id))
            .reduce((sum, segment) => sum + segment.tokens, 0);
        assert.equal(promptTotal, analysis.promptTokens);
        assert.equal(
            analysis.segments.reduce((sum, segment) => sum + segment.tokens, 0),
            analysis.maxTokens,
        );
        assert.ok(analysis.summaryTokens > 0);
        assert.deepEqual(analysis.selectedMessageIds, [1, 2]);
        assert.match(JSON.stringify(analysis.messages), /Search the observatory\./);
    """
    run_node_module(code)


def test_context_segments_follow_the_order_of_the_request():
    """One segment a source, in the order the request reads: the system
    message's parts, the chat, the draft, then Post-History."""
    code = EVERY_SOURCE_SETUP + r"""
        const analysis = analyzeEverySource();
        assert.deepEqual(
            analysis.segments.map(segment => [segment.id, segment.label]),
            [
                ['system_prompt', 'System'],
                ['character_card', 'Character card'],
                ['persona', 'Persona'],
                ['lorebook', 'Lorebook'],
                ['author_note', "Author's note"],
                ['auto_summary', 'Auto summary'],
                ['message_history', 'Message history'],
                ['current_draft', 'Current draft'],
                ['post_history', 'Post-History'],
                ['response_reserve', 'Reserved response'],
            ],
        );
    """
    run_node_module(code)


def test_a_tag_and_its_note_count_toward_the_part_they_wrap():
    """Cozy's own words are never a segment of their own. With only a persona
    to send, the whole system message is Persona, and System is exactly the
    System box in its tag."""
    code = BASE_NODE_SETUP + r"""
        import { estimateMessagesTokens } from './static/js/tokenizer.js';

        el.samplerMaxTokens = { value: '0' };
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: 'An inquisitive sailor.' };
        usePrompt('');
        state.messages = [{ id: 1, role: 'user', text: 'Hello' }];

        const personaOnly = analyzeContext();
        assert.deepEqual(personaOnly.segments.map(segment => segment.id), ['persona', 'message_history']);
        assert.equal(personaOnly.segments[0].tokens, estimateMessagesTokens([personaOnly.messages[0]]));

        state.activePersona.description = '';
        usePrompt('Never break character.');
        const systemOnly = analyzeContext();
        assert.deepEqual(systemOnly.segments.map(segment => segment.id), ['system_prompt', 'message_history']);
        assert.equal(systemOnly.segments[0].tokens, estimateMessagesTokens([
            { role: 'system', content: inSystemTag('Never break character.') },
        ]));

        // A tag around two parts opens with the first and closes with the last.
        state.activeCharacter.scenario = 'A storm is coming.';
        usePrompt('');
        const worldInfo = analyzeContext();
        assert.deepEqual(worldInfo.segments.map(segment => segment.id), ['character_card', 'message_history']);
    """
    run_node_module(code)


def test_context_names_count_toward_the_text_they_sit_in():
    """A {{user}} or {{char}} in a box is not the persona or the card."""
    code = BASE_NODE_SETUP + r"""
        el.samplerMaxTokens = { value: '40' };
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan', description: '' };
        state.activeChat = {};
        usePrompt('You are {{char}}.', '((Write only {{char}}. Do not narrate for {{user}}.))');
        state.messages = [{ id: 1, role: 'user', text: 'Hello' }];

        const analysis = analyzeContext();
        assert.deepEqual(
            analysis.segments.map(segment => segment.id),
            ['system_prompt', 'message_history', 'post_history', 'response_reserve'],
        );
        assert.match(JSON.stringify(analysis.messages), /Do not narrate for Morgan\./);
    """
    run_node_module(code)


# Enough fixed prompt that a 10-message chat cannot all fit beside it.
TIGHT_WINDOW_SETUP = BASE_NODE_SETUP + r"""
    state.contextMaxTokens = '200';
    el.settingsContextTokens = { value: '200' };
    el.samplerMaxTokens = { value: '20' };
    state.activeCharacter = { name: 'Mira', description: 'x'.repeat(100) };
    state.activePersona = { name: 'Morgan', description: '' };
    state.activeChat = { summary_enabled: false, author_note: 'z'.repeat(60) };
    usePrompt('y'.repeat(80));
    state.messages = Array.from({ length: 10 }, (_, i) => ({
        id: i + 1,
        role: i % 2 ? 'character' : 'user',
        text: `turn-${i}-${'a'.repeat(20)}`,
    }));
"""


def test_full_context_budget_trims_history_after_fixed_prompt_sources():
    code = TIGHT_WINDOW_SETUP + r"""
        const analysis = analyzeContext();
        assert.ok(analysis.selectedMessageIds.length > 0);
        assert.ok(analysis.selectedMessageIds.length < state.messages.length);
        assert.deepEqual(
            analysis.selectedMessageIds,
            state.messages.slice(-analysis.selectedMessageIds.length).map(message => message.id),
        );
        assert.ok(analysis.allocatedTokens <= analysis.maxTokens);
        assert.equal(analysis.overflowTokens, 0);
    """
    run_node_module(code)


def test_message_history_tooltip_reports_how_far_context_reaches():
    """The message-history segment tooltip states how many messages are in the
    live window, and how many total, when older turns are trimmed/summarized."""
    code = TIGHT_WINDOW_SETUP + r"""
        import { tooltipForSegment } from './static/js/context-meter.js';

        const analysis = analyzeContext();
        const inWindow = analysis.selectedMessageIds.length;
        assert.ok(inWindow > 0 && inWindow < state.messages.length);
        const segment = analysis.segments.find(s => s.id === 'message_history');
        const tip = tooltipForSegment(segment, analysis);
        const plural = inWindow === 1 ? '' : 's';
        assert.match(tip, new RegExp(`Reaches back ${inWindow} message${plural} \\(of 10\\)`));
    """
    run_node_module(code)


def test_message_history_tooltip_when_every_message_fits():
    """With no context limit the tooltip reports that every message is in range."""
    code = BASE_NODE_SETUP + r"""
        import { tooltipForSegment } from './static/js/context-meter.js';

        el.samplerMaxTokens = { value: '30' };
        state.activeCharacter = { name: 'Mira' };
        state.activeChat = { summary_enabled: false };
        usePrompt('CUSTOM INSTRUCTIONS');
        state.messages = [
            { id: 1, role: 'user', text: 'first' },
            { id: 2, role: 'character', text: 'second' },
            { id: 3, role: 'user', text: 'third' },
        ];

        const analysis = analyzeContext();
        assert.equal(analysis.selectedMessageIds.length, state.messages.length);
        const segment = analysis.segments.find(s => s.id === 'message_history');
        const tip = tooltipForSegment(segment, analysis);
        assert.match(tip, /Reaches back through all 3 messages\./);
    """
    run_node_module(code)


def test_the_two_prompt_segments_are_named_for_their_boxes():
    code = BASE_NODE_SETUP + r"""
        import { tooltipForSegment } from './static/js/context-meter.js';

        state.activeCharacter = { name: 'Mira' };
        usePrompt('Mine.', 'Mine too.');
        state.messages = [{ id: 1, role: 'user', text: 'first' }];

        const analysis = analyzeContext();
        const tip = id => tooltipForSegment(analysis.segments.find(s => s.id === id), analysis);
        assert.match(tip('system_prompt'), /^<strong>System<\/strong>.*Your System prompt/);
        assert.match(tip('post_history'), /^<strong>Post-History<\/strong>.*Your Post-History prompt/);
    """
    run_node_module(code)


def test_context_analysis_keeps_latest_turn_and_reports_unavoidable_overflow():
    code = BASE_NODE_SETUP + r"""
        state.contextMaxTokens = '20';
        el.settingsContextTokens = { value: '20' };
        el.samplerMaxTokens = { value: '10' };
        state.activeCharacter = { name: 'Mira' };
        state.activeChat = { summary_enabled: false };
        usePrompt('x'.repeat(400));
        state.messages = [
            { id: 1, role: 'user', text: 'old turn' },
            { id: 2, role: 'character', text: 'older reply' },
            { id: 3, role: 'user', text: 'latest request' },
        ];

        const analysis = analyzeContext();
        assert.deepEqual(analysis.selectedMessageIds, [3]);
        assert.match(JSON.stringify(analysis.messages), /latest request/);
        assert.ok(analysis.overflowTokens > 0);
        assert.equal(analysis.unusedTokens, 0);
    """
    run_node_module(code)


def test_summary_is_attributed_and_unlimited_context_has_no_unused_segment():
    code = BASE_NODE_SETUP + r"""
        el.samplerMaxTokens = { value: '30' };
        state.activeCharacter = { name: 'Mira' };
        state.activeChat = { summary_enabled: true };
        usePrompt('CUSTOM INSTRUCTIONS');
        state.messages = [{ id: 1, role: 'user', text: 'Where to?' }];

        const analysis = analyzeContext({ summaryText: 'The bridge is broken.' });
        const byId = new Map(analysis.segments.map(segment => [segment.id, segment.tokens]));
        assert.ok(byId.get('auto_summary') > 0);
        assert.ok(byId.get('response_reserve') > 0);
        assert.equal(byId.has('unused'), false);
        assert.equal(analysis.overflowTokens, 0);
        assert.match(analysis.messages[0].content, /The bridge is broken\./);
    """
    run_node_module(code)


def test_dollar_signs_in_values_reach_the_prompt_as_written():
    """A replacement string reads $&, $' and $$ as patterns; values are text."""
    code = BASE_NODE_SETUP + r"""
        state.activePersona = { name: 'Rich $$ Guy', description: "Says $' and $` and $& a lot." };
        state.activeCharacter = { name: 'Mi$&ra', description: 'Charges $$5, $&.' };
        state.activeChat = { author_note: 'Costs $$$.' };
        usePrompt("Pay {{user}} $' now.", 'Tip {{char}} $$.');
        state.messages = [{ role: 'user', text: 'Give me $$$ and $& now' }];

        const [system, user] = buildChatPayload().messages;
        for (const line of [
            "Pay Rich $$ Guy $' now.", 'Charges $$5, $&.', "Says $' and $` and $& a lot.", 'Costs $$$.',
        ]) assert.ok(system.content.includes(`\n${line}\n`), line);
        assert.match(system.content, /<character note="Who Mi\$&ra is:/);
        assert.match(system.content, /<user note="Rich \$\$ Guy, the character/);
        assert.equal(user.content, `Give me $$$ and $& now\n\n${inPostTag('Tip Mi$&ra $$.')}`);
    """
    run_node_module(code)


# A chat that exercises every input the analysis is built from.
REUSE_SETUP = BASE_NODE_SETUP + r"""
    el.settingsContextTokens = { value: '4000' };
    el.samplerMaxTokens = { value: '200' };
    state.activeCharacter = {
        name: 'Mira',
        description: 'A careful tester.',
        character_book: { entries: [{ keys: ['dragon'], content: 'Dragons are extinct.' }] },
    };
    state.activeChat = { id: 1, author_note: 'Keep it short.', active_lorebook_embedded: true };
    state.activeSystemPromptId = 1;
    state.systemPrompts = [
        { id: 1, content: 'RULES', post_history_content: 'REMINDER' },
        { id: 2, content: 'OTHER RULES', post_history_content: '' },
    ];
    state.messages = [
        { id: 1, role: 'character', text: 'Hello there.', swipes: [{ content: 'Hello there.' }] },
        { id: 2, role: 'user', text: 'Tell me about the dragon.' },
        { id: 3, role: 'character', text: 'It sleeps under the hill.' },
    ];
    const sent = analysis => JSON.stringify(analysis.messages);
"""


def test_an_unchanged_chat_is_analysed_once():
    """The meter, the separator and the request ask one after another about the
    same chat. They get one analysis between them, not one each."""
    run_node_module(REUSE_SETUP + r"""
        const first = analyzeContext();
        assert.equal(analyzeContext(), first);
        // What the meter measured is what the request sends.
        assert.equal(buildChatPayload().messages, first.messages);
        // A draft of nothing but spaces is no draft.
        assert.equal(analyzeContext({ draftText: '   ' }), first);
    """)


def test_any_changed_input_gets_a_fresh_analysis():
    """Reuse must never hand back an analysis of a chat that has since changed,
    including when the change was made in place to an object it already saw."""
    run_node_module(REUSE_SETUP + r"""
        const changes = [
            ['a new message', () => state.messages.push({ id: 4, role: 'user', text: 'NEWEST' }), /NEWEST/],
            ['a message edited in place', () => { state.messages[0].text = 'EDITED'; }, /EDITED/],
            ['the card', () => { state.activeCharacter.description = 'CARD2'; }, /CARD2/],
            ['the persona', () => { state.activePersona.description = 'PERSONA2'; }, /PERSONA2/],
            ['the System box', () => { state.systemPrompts[0].content += '\nSYSTEM2'; }, /SYSTEM2/],
            ['the Post-History box', () => { state.systemPrompts[0].post_history_content = 'POST2'; }, /POST2/],
            ["the author's note", () => { state.activeChat.author_note = 'NOTE2'; }, /NOTE2/],
            ['a lorebook entry edited in place',
                () => { state.activeCharacter.character_book.entries[0].content = 'LORE2'; }, /LORE2/],
            ['a lorebook key edited in place',
                () => { state.activeCharacter.character_book.entries[0].keys[0] = 'hill'; }, /LORE2/],
            ['the lorebook switched off',
                () => { state.activeChat.active_lorebook_embedded = false; }, /^(?!.*LORE2)/],
            ['another prompt picked', () => { state.activeSystemPromptId = 2; }, /OTHER RULES/],
        ];
        for (const [name, change, expected] of changes) {
            const before = analyzeContext({ summaryText: 'MEMORY' });
            change();
            const after = analyzeContext({ summaryText: 'MEMORY' });
            assert.notEqual(after, before, name);
            assert.match(sent(after), expected, name);
            assert.equal(analyzeContext({ summaryText: 'MEMORY' }), after, name);
        }

        // Arguments and budgets are inputs too.
        state.activeSystemPromptId = 1;
        const base = analyzeContext();
        assert.match(sent(analyzeContext({ summaryText: 'MEMORY2' })), /MEMORY2/);
        assert.match(sent(analyzeContext({ draftText: 'DRAFT' })), /DRAFT/);
        assert.doesNotMatch(sent(analyzeContext({ excludeLastN: 1 })), /NEWEST/);
        el.settingsContextTokens.value = '5000';
        assert.equal(analyzeContext().maxTokens, 5000);
        el.samplerMaxTokens.value = '300';
        assert.equal(analyzeContext().responseTokens, 300);
        state.extraRequestParams = '{"max_tokens": 400}';
        assert.equal(analyzeContext().responseTokens, 400);
        // A summary watermark takes the messages it covers out of raw history.
        state.activeChat.summary_enabled = 1;
        state.activeChat.summary_up_to_msg_id = 1;
        assert.doesNotMatch(sent(analyzeContext()), /EDITED/);
        assert.notEqual(analyzeContext(), base);
    """)


def test_a_kept_analysis_does_not_outlive_the_moment():
    """{{time}} and {{random}} are not inputs, so an analysis is only reused
    within the turn that made it. A retry minutes later is worked out again."""
    run_node_module(REUSE_SETUP + r"""
        const first = analyzeContext();
        await new Promise(resolve => setTimeout(resolve, 5));
        const later = analyzeContext();
        assert.notEqual(later, first);
        assert.deepEqual(later, first);
    """)


def test_a_shared_analysis_cannot_be_altered():
    """One caller changing the analysis would change what the next one sends,
    so it is frozen. The chat it was made from is not."""
    run_node_module(REUSE_SETUP + r"""
        const analysis = analyzeContext();
        assert.throws(() => analysis.messages.push({ role: 'user', content: 'x' }), TypeError);
        assert.throws(() => { analysis.messages[0].content = 'x'; }, TypeError);
        assert.throws(() => { analysis.selectedMessages[0].text = 'x'; }, TypeError);
        assert.ok(!Object.isFrozen(state.messages[0]));
        assert.ok(!Object.isFrozen(state.messages[0].swipes));
        assert.ok(!Object.isFrozen(state.activeCharacter.character_book.entries[0]));
        // The payload around it is the request's own, and extras still apply.
        state.extraRequestParams = '{"messages_note": "x"}';
        assert.equal(buildChatPayload().messages_note, 'x');
    """)


def test_the_analysis_cannot_read_app_state_behind_its_inputs():
    """Reuse is only safe while contextInputs() is the single place the analysis
    reads the app from. Importing state.js here would let a read slip past the
    comparison and send a stale context without any sign."""
    with open('static/js/context-analysis.js', encoding='utf-8') as handle:
        imports = [line for line in handle if line.startswith(('import', '} from'))]
    assert imports
    assert not any('state.js' in line for line in imports)
