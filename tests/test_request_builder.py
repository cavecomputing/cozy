import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend request-builder tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


BASE_NODE_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { buildChatPayload } from './static/js/request-builder.js';
    import { analyzeContext } from './static/js/context-analysis.js';

    state.apiModel = 'test-model';
    state.activeSamplers = new Set();
    state.contextMaxTokens = '0';
    el.sendThinking = { checked: false };
    el.settingsContextTokens = { value: '0' };
    state.activePersona = { name: 'Morgan', description: 'A curious user.' };
"""


def test_chat_payload_uses_active_custom_system_prompt_template():
    """Message sends should use the selected custom template, not the default."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = {
            name: 'Mira',
            description: 'A careful tester.',
            system_prompt: 'Always answer as the custom character.',
        };
        state.activeSystemPromptId = 2;
        state.systemPrompts = [
            {
                id: 1,
                name: 'Default',
                content: 'DEFAULT TEMPLATE {{system_prompt}}',
                post_history_content: '',
            },
            {
                id: 2,
                name: 'Custom',
                content: 'CUSTOM TEMPLATE\n{{#system_prompt}}Character rules: {{system_prompt}}{{/system_prompt}}\nUser={{user}}\nChar={{char}}',
                post_history_content: '',
            },
        ];
        state.messages = [{ role: 'user', text: 'Did my custom prompt arrive?' }];

        const payload = buildChatPayload();
        const systemMessage = payload.messages[0];

        assert.equal(systemMessage.role, 'system');
        assert.match(systemMessage.content, /CUSTOM TEMPLATE/);
        assert.match(systemMessage.content, /Character rules: Always answer as the custom character\./);
        assert.match(systemMessage.content, /User=Morgan/);
        assert.match(systemMessage.content, /Char=Mira/);
        assert.doesNotMatch(systemMessage.content, /DEFAULT TEMPLATE/);
        assert.doesNotMatch(systemMessage.content, /\{\{system_prompt\}\}/);
    """
    run_node_module(code)


def test_whitespace_only_values_still_drop_their_conditional_blocks():
    """Source tracking must not make a whitespace-only value truthy in {{#var}}.

    Imported cards commonly carry fields like `"scenario": " "`; the legacy
    builder dropped those blocks, and the marker-based resolver must agree or
    wrapper labels leak into the outgoing prompt with nothing after them.
    """
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', scenario: ' ' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: 'SYSTEM.{{#scenario}}\nScenario: {{scenario}}{{/scenario}}'
                + '{{#author_note}}\nAUTHOR NOTE: {{author_note}}{{/author_note}}',
            post_history_content: '',
        }];
        state.activeChat = { author_note: '   ' };
        state.messages = [{ id: 1, role: 'user', text: 'hi' }];

        const payload = buildChatPayload();
        assert.equal(payload.messages[0].role, 'system');
        assert.equal(payload.messages[0].content, 'SYSTEM.');

        // A real value still resolves and is attributed to its source.
        state.activeChat.author_note = 'Keep it cozy.';
        const analysis = analyzeContext({});
        assert.match(analysis.messages[0].content, /AUTHOR NOTE: Keep it cozy\./);
        assert.doesNotMatch(analysis.messages[0].content, /Scenario:/);
        assert.ok(analysis.segments.some(segment => segment.id === 'author_note'));
    """
    run_node_module(code)


def test_default_post_history_template_preserves_character_card_behavior():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = {
            name: 'Mira',
            post_history_instructions: 'Stay close to {{char}} and leave room for {{user}}.',
        };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Default',
            content: 'System for {{char}}',
            post_history_content: '{{#post_history_instructions}}[Post-History Instructions]\n{{post_history_instructions}}{{/post_history_instructions}}',
        }];
        state.messages = [
            { role: 'user', text: 'Hello' },
            { role: 'character', text: 'Hi there.' },
            { role: 'user', text: 'What happens next?' },
        ];

        const payload = buildChatPayload();
        const lastMessage = payload.messages[payload.messages.length - 1];

        assert.equal(lastMessage.role, 'user');
        assert.match(lastMessage.content, /What happens next\?/);
        assert.match(lastMessage.content, /\[Post-History Instructions\]/);
        assert.match(lastMessage.content, /Stay close to Mira and leave room for Morgan\./);
    """
    run_node_module(code)


def test_empty_post_history_template_appends_nothing():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = {
            name: 'Mira',
            post_history_instructions: 'This should not be sent.',
        };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'No Post History',
            content: 'System for {{char}}',
            post_history_content: '',
        }];
        state.messages = [{ role: 'user', text: 'Hello' }];

        const payload = buildChatPayload();

        assert.equal(payload.messages.length, 2);
        assert.deepEqual(payload.messages.map(m => m.role), ['system', 'user']);
        assert.doesNotMatch(JSON.stringify(payload.messages), /This should not be sent/);
        assert.doesNotMatch(JSON.stringify(payload.messages), /Post-History/);
    """
    run_node_module(code)


def test_static_post_history_template_appends_without_character_field():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', post_history_instructions: '' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Static OOC',
            content: 'System for {{char}}',
            post_history_content: '((OOC: Write only {{char}}. Do not narrate for {{user}}.))',
        }];
        state.messages = [
            { role: 'user', text: 'Hello' },
            { role: 'character', text: 'Hi there.' },
        ];

        const payload = buildChatPayload();
        const lastMessage = payload.messages[payload.messages.length - 1];

        assert.equal(lastMessage.role, 'user');
        assert.equal(lastMessage.content, '((OOC: Write only Mira. Do not narrate for Morgan.))');
    """
    run_node_module(code)


def test_post_history_conditionals_drop_when_character_field_empty():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', post_history_instructions: '' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Conditional',
            content: 'System for {{char}}',
            post_history_content: '{{#post_history_instructions}}[Post-History Instructions]\n{{post_history_instructions}}{{/post_history_instructions}}',
        }];
        state.messages = [{ role: 'user', text: 'Hello' }];

        const payload = buildChatPayload();

        assert.equal(payload.messages.length, 2);
        assert.doesNotMatch(JSON.stringify(payload.messages), /Post-History/);
    """
    run_node_module(code)


def test_user_template_wraps_final_user_message_in_place():
    """{{user_message}} lets the template position lore/author-note around the
    user's own text within a single, final user turn."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', post_history_instructions: '' };
        state.activeChat = { author_note: 'Remember: the sky is green.' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'User wrap',
            content: 'System for {{char}}',
            post_history_content: '[Note]\n{{author_note}}\n\n{{user_message}}',
        }];
        state.messages = [
            { role: 'user', text: 'Hello' },
            { role: 'character', text: 'Hi there.' },
            { role: 'user', text: 'What happens next?' },
        ];

        const payload = buildChatPayload();
        const roles = payload.messages.map(m => m.role);
        const lastMessage = payload.messages[payload.messages.length - 1];

        // system, user(Hello), assistant(Hi), user(wrapped) — no extra turn.
        assert.deepEqual(roles, ['system', 'user', 'assistant', 'user']);
        assert.equal(lastMessage.role, 'user');
        // Author-note sits BEFORE the user's own text, in one turn.
        assert.equal(lastMessage.content, '[Note]\nRemember: the sky is green.\n\nWhat happens next?');
        // The raw message is not also present as its own bare turn.
        assert.equal(
            payload.messages.filter(m => m.content === 'What happens next?').length, 0);
    """
    run_node_module(code)


def test_user_template_can_place_content_after_user_message():
    """The user's text can sit before appended template content when
    {{user_message}} comes first."""
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira', post_history_instructions: '' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'User then OOC',
            content: 'System for {{char}}',
            post_history_content: '{{user_message}}\n\n((OOC: stay in character as {{char}}.))',
        }];
        state.messages = [
            { role: 'user', text: 'Hello' },
            { role: 'character', text: 'Hi there.' },
            { role: 'user', text: 'Tell me a story.' },
        ];

        const payload = buildChatPayload();
        const lastMessage = payload.messages[payload.messages.length - 1];

        assert.equal(lastMessage.role, 'user');
        assert.equal(lastMessage.content, 'Tell me a story.\n\n((OOC: stay in character as Mira.))');
        assert.equal(payload.messages.length, 4);
    """
    run_node_module(code)


def test_summary_tokens_reduce_the_raw_message_budget():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Summary-aware',
            content: '{{summary}}',
            post_history_content: '',
        }];
        state.contextMaxTokens = '100';
        el.settingsContextTokens = { value: '100' };
        el.samplerMaxTokens = { value: '20' };
        el.sendThinking = { checked: true };
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

        state.autoSummariesEnabled = false;
        const globallyPaused = buildChatPayload();

        const rawWithout = withoutSummary.messages.filter(m => /message-\d+-/.test(m.content));
        const rawWith = withSummary.messages.filter(m => /message-\d+-/.test(m.content));
        const rawPaused = globallyPaused.messages.filter(m => /message-\d+-/.test(m.content));
        // Full-budget accounting includes role framing and the rendered system
        // message, so it safely retains one fewer turn than the old raw-only
        // estimate in each state.
        assert.equal(rawWithout.length, 6);
        assert.equal(rawWith.length, 3);
        assert.equal(rawPaused.length, 6);
        assert.doesNotMatch(JSON.stringify(globallyPaused.messages), /STORY SO FAR/);
    """
    run_node_module(code)


def test_custom_template_without_summary_gets_fallback_memory_block():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Legacy custom prompt',
            content: 'CUSTOM ROLEPLAY INSTRUCTIONS',
            post_history_content: '',
        }];
        state.activeChat = {
            summary_enabled: true,
            summary: {
                lines: [{ section: 'story', text: 'The lighthouse lens is cracked.', pinned: false }],
            },
        };
        state.messages = [{ id: 1, role: 'user', text: 'What do we do next?' }];

        const payload = buildChatPayload();
        const system = payload.messages.find(m => m.role === 'system');

        assert.match(system.content, /CUSTOM ROLEPLAY INSTRUCTIONS/);
        assert.match(system.content, /\[MEMORY — STORY SO FAR\]/);
        assert.match(system.content, /The lighthouse lens is cracked\./);
        assert.equal(
            (JSON.stringify(payload.messages).match(/The lighthouse lens is cracked\./g) || []).length,
            1,
        );
    """
    run_node_module(code)


def test_templates_with_summary_slot_do_not_duplicate_fallback_memory():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activeSystemPromptId = 1;
        state.activeChat = {
            summary_enabled: true,
            summary: {
                lines: [{ section: 'story', text: 'Morgan carries the brass key.', pinned: false }],
            },
        };
        state.messages = [{ id: 1, role: 'user', text: 'Open the door.' }];

        state.systemPrompts = [{
            id: 1,
            name: 'System memory slot',
            content: 'CUSTOM\n{{summary}}',
            post_history_content: '',
        }];
        const systemSlot = buildChatPayload();
        assert.equal(
            (JSON.stringify(systemSlot.messages).match(/Morgan carries the brass key\./g) || []).length,
            1,
        );
        assert.doesNotMatch(systemSlot.messages[0].content, /\[MEMORY — STORY SO FAR\]/);

        state.systemPrompts = [{
            id: 1,
            name: 'User memory slot',
            content: 'CUSTOM',
            post_history_content: 'Remember this:\n{{summary}}',
        }];
        const userSlot = buildChatPayload();
        assert.equal(
            (JSON.stringify(userSlot.messages).match(/Morgan carries the brass key\./g) || []).length,
            1,
        );
        assert.doesNotMatch(userSlot.messages[0].content, /\[MEMORY — STORY SO FAR\]/);
    """
    run_node_module(code)


def test_summary_slot_removed_by_false_conditional_gets_fallback():
    code = BASE_NODE_SETUP + r"""
        state.activePersona = { name: 'Morgan', description: '' };
        state.activeCharacter = { name: 'Mira' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Conditional memory slot',
            content: 'CUSTOM\n{{#persona}}{{summary}}{{/persona}}',
            post_history_content: '',
        }];
        state.activeChat = {
            summary_enabled: true,
            summary_up_to_msg_id: 1,
            summary: {
                lines: [{ section: 'story', text: 'The observatory door is sealed.', pinned: false }],
            },
        };
        state.messages = [
            { id: 1, role: 'character', text: 'SUMMARIZED RAW TURN' },
            { id: 2, role: 'user', text: 'LIVE TURN' },
        ];

        const payload = buildChatPayload();
        const serialized = JSON.stringify(payload.messages);

        assert.match(payload.messages[0].content, /\[MEMORY — STORY SO FAR\]/);
        assert.match(serialized, /The observatory door is sealed\./);
        assert.doesNotMatch(serialized, /SUMMARIZED RAW TURN/);
        assert.match(serialized, /LIVE TURN/);
    """
    run_node_module(code)


def test_regen_payload_excludes_summarized_turn_but_keeps_first_live_turn():
    code = BASE_NODE_SETUP + r"""
        state.activeCharacter = { name: 'Mira' };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            name: 'Summary aware',
            content: '{{summary}}',
            post_history_content: '',
        }];
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
        assert.match(serialized, /FIRST LIVE TURN/);
        assert.doesNotMatch(serialized, /ASSISTANT BEING REGENERATED/);
    """
    run_node_module(code)


def test_context_analysis_attributes_every_semantic_source_and_reconciles_totals():
    code = BASE_NODE_SETUP + r"""
        state.contextMaxTokens = '1000';
        el.settingsContextTokens = { value: '1000' };
        el.samplerMaxTokens = { value: '100' };
        state.summaryCapPct = '10';
        state.activeCharacter = {
            name: 'Mira',
            description: 'A careful cartographer.',
            personality: 'Patient and observant.',
            system_prompt: 'Never break character.',
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
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: 'RULES {{system_prompt}}\nCARD {{description}}\nPERSONA {{persona}}\nLORE {{lorebook}}\nLORE AGAIN {{lorebook}}\nNOTE {{author_note}}\nMEMORY {{summary}}',
            post_history_content: '{{user_message}}',
        }];
        state.messages = [
            { id: 1, role: 'user', text: 'Where are we?' },
            { id: 2, role: 'character', text: 'At the cliff path.' },
        ];

        const analysis = analyzeContext({
            summaryText: 'The lantern was lost in the storm.',
            draftText: 'Search the observatory.',
        });
        const byId = new Map(analysis.segments.map(segment => [segment.id, segment.tokens]));
        for (const id of [
            'system_prompt', 'character_card', 'persona', 'lorebook',
            'author_note', 'auto_summary', 'message_history', 'current_draft',
            'response_reserve', 'unused',
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


def test_full_context_budget_trims_history_after_fixed_prompt_sources():
    code = BASE_NODE_SETUP + r"""
        state.contextMaxTokens = '120';
        el.settingsContextTokens = { value: '120' };
        el.samplerMaxTokens = { value: '20' };
        state.activeCharacter = {
            name: 'Mira',
            description: 'x'.repeat(100),
            system_prompt: 'y'.repeat(80),
        };
        state.activeChat = { summary_enabled: false, author_note: 'z'.repeat(60) };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: '{{system_prompt}} {{description}} {{author_note}}',
            post_history_content: '',
        }];
        state.messages = Array.from({ length: 10 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `turn-${i}-${'a'.repeat(20)}`,
        }));

        const analysis = analyzeContext();
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
    code = BASE_NODE_SETUP + r"""
        import { tooltipForSegment } from './static/js/context-meter.js';

        state.contextMaxTokens = '120';
        el.settingsContextTokens = { value: '120' };
        el.samplerMaxTokens = { value: '20' };
        state.activeCharacter = {
            name: 'Mira',
            description: 'x'.repeat(100),
            system_prompt: 'y'.repeat(80),
        };
        state.activeChat = { summary_enabled: false, author_note: 'z'.repeat(60) };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: '{{system_prompt}} {{description}} {{author_note}}',
            post_history_content: '',
        }];
        state.messages = Array.from({ length: 10 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `turn-${i}-${'a'.repeat(20)}`,
        }));

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

        state.contextMaxTokens = '0';
        el.settingsContextTokens = { value: '0' };
        el.samplerMaxTokens = { value: '30' };
        state.activeCharacter = { name: 'Mira' };
        state.activeChat = { summary_enabled: false };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: 'CUSTOM INSTRUCTIONS',
            post_history_content: '',
        }];
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


def test_context_analysis_keeps_latest_turn_and_reports_unavoidable_overflow():
    code = BASE_NODE_SETUP + r"""
        state.contextMaxTokens = '20';
        el.settingsContextTokens = { value: '20' };
        el.samplerMaxTokens = { value: '10' };
        state.activeCharacter = { name: 'Mira', system_prompt: 'x'.repeat(400) };
        state.activeChat = { summary_enabled: false };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: '{{system_prompt}}',
            post_history_content: '',
        }];
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


def test_summary_fallback_is_attributed_and_unlimited_context_has_no_unused_segment():
    code = BASE_NODE_SETUP + r"""
        state.contextMaxTokens = '0';
        el.settingsContextTokens = { value: '0' };
        el.samplerMaxTokens = { value: '30' };
        state.activeCharacter = { name: 'Mira' };
        state.activeChat = { summary_enabled: true };
        state.activeSystemPromptId = 1;
        state.systemPrompts = [{
            id: 1,
            content: 'CUSTOM INSTRUCTIONS',
            post_history_content: '',
        }];
        state.messages = [{ id: 1, role: 'user', text: 'Continue.' }];

        const analysis = analyzeContext({ summaryText: 'The bridge is broken.' });
        const byId = new Map(analysis.segments.map(segment => [segment.id, segment.tokens]));
        assert.ok(byId.get('auto_summary') > 0);
        assert.ok(byId.get('response_reserve') > 0);
        assert.equal(byId.has('unused'), false);
        assert.equal(analysis.overflowTokens, 0);
        assert.match(analysis.messages[0].content, /\[MEMORY — STORY SO FAR\]/);
    """
    run_node_module(code)
