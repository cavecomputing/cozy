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
