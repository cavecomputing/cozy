"""Frontend send lifecycle guards against rapid repeated activation."""

from helpers import run_node_module


SEND_GUARD_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el, llm } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import { queueLLMSettingsSave } from './static/js/llm-settings.js';
    import { handleSend } from './static/js/send.js';

    Object.assign(el, {
        userInput: {
            value: 'hello there', disabled: false, placeholder: '',
            style: {}, scrollHeight: 20,
        },
        sendBtn: {
            disabled: false, innerHTML: '', title: '',
            setAttribute() {}, classList: { toggle() {} },
        },
    });
    state.activeCharacter = { id: 1, name: 'Mira' };
    state.activeChat = { id: 7, summary_enabled: false };
    state.chats = [state.activeChat];
    // Stop after settings preflight; the test is about the interval before the
    // composer is cleared and the abort controller exists.
    state.apiModel = '';
"""


def test_rapid_second_send_is_rejected_during_settings_preflight():
    """Two Enter keydowns cannot both wait behind the same settings save."""
    run_node_module(SEND_GUARD_SETUP + r"""
        let markSaveStarted;
        const saveStarted = new Promise(resolve => { markSaveStarted = resolve; });
        let releaseSave;
        const saveBlocked = new Promise(resolve => { releaseSave = resolve; });
        let saveCalls = 0;
        API.saveSettings = async () => {
            saveCalls += 1;
            markSaveStarted();
            await saveBlocked;
            return {};
        };
        queueLLMSettingsSave({ temperature: '0.7' });

        const first = handleSend();
        await saveStarted;
        assert.equal(llm.generationActive, true);
        assert.equal(el.sendBtn.disabled, true);

        // This is the second rapid Enter: it must return synchronously through
        // the guard rather than joining the preflight and continuing later.
        await handleSend();
        assert.equal(el.userInput.value, 'hello there');
        assert.equal(saveCalls, 1);
        assert.equal(llm.generationActive, true);

        releaseSave();
        await first;
        assert.equal(llm.generationActive, false);
        assert.equal(llm.abortController, null);
        assert.equal(el.sendBtn.disabled, false);
    """)


def test_active_generation_guard_does_not_replace_the_controller():
    """The Enter path cannot overwrite the controller owned by a live send."""
    run_node_module(SEND_GUARD_SETUP + r"""
        const controller = new AbortController();
        llm.generationActive = true;
        llm.abortController = controller;

        await handleSend();

        assert.equal(llm.abortController, controller);
        assert.equal(llm.generationActive, true);
        assert.equal(el.userInput.value, 'hello there');
    """)
