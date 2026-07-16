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
        assert.equal(rawWithout.length, 7);
        assert.equal(rawWith.length, 4);
        assert.equal(rawPaused.length, 7);
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
