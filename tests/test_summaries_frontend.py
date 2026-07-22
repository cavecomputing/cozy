"""Node-backed regression tests for the Auto Summaries browser coordinator."""

import shutil
import subprocess

import pytest


def run_node_module(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for frontend summary tests')
    result = subprocess.run(
        [node, '--input-type=module', '-e', code],
        cwd='.',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


BASE_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { API } from './static/js/api.js';
    import {
        ensureSummaryReadyForSend,
        maybeTriggerSummary,
    } from './static/js/summaries.js';

    Object.assign(el, {
        apiEndpoint: { value: 'http://main.example/v1' },
        settingsContextTokens: { value: '32' },
        samplerMaxTokens: { value: '10' },
        sendThinking: { checked: true },
    });
    state.apiModel = 'main-model';
    state.summaryApiEndpoint = '';
    state.summaryApiModel = '';
    state.summaryTriggerInterval = '20';
    state.messages = Array.from({ length: 3 }, (_, i) => ({
        id: i + 1,
        role: i % 2 ? 'character' : 'user',
        text: `message-${i}-abcdefghijklmno`,
    }));
    state.activeChat = {
        id: 7,
        summary_enabled: true,
        summary: { lines: [] },
        summary_up_to_msg_id: null,
        summary_status: 'idle',
        summary_status_detail: '',
    };
    state.chats = [state.activeChat];
"""


def test_main_endpoint_fallback_triggers_on_first_aged_out_message():
    code = BASE_SETUP + r"""
        const calls = [];
        API.runSummary = async (chatId, options) => {
            calls.push({ chatId, ...options });
            return {
                id: chatId,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        await maybeTriggerSummary();

        assert.equal(calls.length, 1);
        assert.equal(calls[0].chatId, 7);
        // Full context accounting includes message framing and fixed prompt
        // content, so the first two turns are outside this tiny 32-token window.
        assert.equal(calls[0].up_to_msg_id, 2);
    """
    run_node_module(code)


def test_first_boundary_retires_a_full_interval_block():
    code = BASE_SETUP + r"""
        el.settingsContextTokens.value = '202';
        el.samplerMaxTokens.value = '10';
        state.messages = Array.from({ length: 25 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `m-${i}-abcdefghij`,
        }));
        const calls = [];
        API.runSummary = async (chatId, options) => {
            calls.push({ chatId, ...options });
            return {
                id: chatId,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        await maybeTriggerSummary();

        assert.equal(calls.length, 1);
        assert.equal(calls[0].up_to_msg_id, 20);
    """
    run_node_module(code)


def test_interval_waits_for_context_pressure_and_then_retires_next_block():
    code = BASE_SETUP + r"""
        el.settingsContextTokens.value = '202';
        el.samplerMaxTokens.value = '10';
        state.messages = Array.from({ length: 44 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `m-${i}-abcdefghij`,
        }));
        state.activeChat.summary_up_to_msg_id = 20;
        const calls = [];
        API.runSummary = async (chatId, options) => {
            calls.push(options.up_to_msg_id);
            return {
                id: chatId,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        await maybeTriggerSummary();
        assert.deepEqual(calls, []);

        state.messages.push({
            id: 45,
            role: 'user',
            text: 'm-44-abcdefghij',
        });
        await maybeTriggerSummary();
        assert.deepEqual(calls, [40]);
    """
    run_node_module(code)


def test_interval_does_not_summarize_early_when_everything_fits():
    code = BASE_SETUP + r"""
        el.settingsContextTokens.value = '1000';
        state.messages = Array.from({ length: 40 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `message-${i}`,
        }));
        let calls = 0;
        API.runSummary = async () => { calls += 1; };

        await maybeTriggerSummary();

        assert.equal(calls, 0);
    """
    run_node_module(code)


def test_summary_trigger_waits_for_debounced_settings_save():
    code = BASE_SETUP + r"""
        const { queueLLMSettingsSave } = await import('./static/js/llm-settings.js');
        state.summaryApiEndpoint = 'http://new-summary.example/v1';
        state.summaryApiModel = 'new-summary-model';

        let releaseSave;
        let markSaveStarted;
        const saveStarted = new Promise(resolve => { markSaveStarted = resolve; });
        const order = [];
        API.saveSettings = async fields => {
            order.push(`save:${fields.summary_api_model}`);
            markSaveStarted();
            await new Promise(resolve => { releaseSave = resolve; });
            order.push('saved');
            return {};
        };
        API.runSummary = async (chatId, options) => {
            order.push('run');
            return {
                id: chatId,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        queueLLMSettingsSave({
            summary_api_endpoint: state.summaryApiEndpoint,
            summary_api_model: state.summaryApiModel,
        });
        const triggering = maybeTriggerSummary();
        await saveStarted;
        assert.deepEqual(order, ['save:new-summary-model']);
        releaseSave();
        await triggering;
        assert.deepEqual(order, ['save:new-summary-model', 'saved', 'run']);
    """
    run_node_module(code)


def test_settings_barrier_drains_edits_queued_during_inflight_save():
    code = r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import {
            flushLLMSettingsSave,
            queueLLMSettingsSave,
        } from './static/js/llm-settings.js';

        state.activePresetId = null;
        const calls = [];
        let releaseFirst;
        let markFirstStarted;
        const firstStarted = new Promise(resolve => { markFirstStarted = resolve; });
        API.saveSettings = async fields => {
            calls.push({ ...fields });
            if (calls.length === 1) {
                markFirstStarted();
                await new Promise(resolve => { releaseFirst = resolve; });
            }
            return {};
        };

        queueLLMSettingsSave({ api_endpoint: 'http://first.example/v1' });
        const barrier = flushLLMSettingsSave({ strict: true });
        await firstStarted;
        queueLLMSettingsSave({ api_model: 'second-model' });
        releaseFirst();
        await barrier;

        assert.deepEqual(calls, [
            { api_endpoint: 'http://first.example/v1' },
            { api_model: 'second-model' },
        ]);
    """
    run_node_module(code)


def test_strict_settings_barrier_rejects_and_requeues_failed_save():
    code = r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import {
            flushLLMSettingsSave,
            queueLLMSettingsSave,
        } from './static/js/llm-settings.js';

        state.activePresetId = null;
        const nativeSetTimeout = globalThis.setTimeout;
        globalThis.setTimeout = (fn, ms, ...args) =>
            nativeSetTimeout(fn, Math.min(ms, 5), ...args);
        globalThis.document = {
            getElementById() { return { appendChild() {} }; },
            createElement() {
                return { setAttribute() {}, appendChild() {}, remove() {} };
            },
        };

        const calls = [];
        API.saveSettings = async fields => {
            calls.push({ ...fields });
            if (calls.length === 1) throw new Error('disk unavailable');
            return {};
        };

        queueLLMSettingsSave({ summary_api_model: 'retry-model' });
        await assert.rejects(
            flushLLMSettingsSave({ strict: true }),
            /Settings could not be saved: disk unavailable/,
        );
        await flushLLMSettingsSave({ strict: true });

        assert.deepEqual(calls, [
            { summary_api_model: 'retry-model' },
            { summary_api_model: 'retry-model' },
        ]);
    """
    run_node_module(code)


def test_concurrent_strict_flushes_never_retry_stale_value_after_newer_edit():
    code = r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import {
            flushLLMSettingsSave,
            queueLLMSettingsSave,
        } from './static/js/llm-settings.js';

        state.activePresetId = null;
        globalThis.document = {
            getElementById() { return { appendChild() {} }; },
            createElement() {
                return { setAttribute() {}, appendChild() {}, remove() {} };
            },
        };
        const calls = [];
        let releaseOld;
        let markOldStarted;
        const oldStarted = new Promise(resolve => { markOldStarted = resolve; });
        API.saveSettings = async fields => {
            calls.push({ ...fields });
            if (calls.length === 1) {
                markOldStarted();
                await new Promise(resolve => { releaseOld = resolve; });
                throw new Error('old save failed');
            }
            return {};
        };

        queueLLMSettingsSave({ api_model: 'old-model' });
        const first = flushLLMSettingsSave({ strict: true });
        await oldStarted;
        queueLLMSettingsSave({ api_model: 'new-model' });
        const second = flushLLMSettingsSave({ strict: true });
        releaseOld();

        const failed = await Promise.allSettled([first, second]);
        assert.deepEqual(failed.map(result => result.status), ['rejected', 'rejected']);
        await flushLLMSettingsSave({ strict: true });

        // The failed old snapshot is merged behind the queued new value. The
        // unsafe implementation produced old, new, old here.
        assert.deepEqual(calls, [
            { api_model: 'old-model' },
            { api_model: 'new-model' },
        ]);
    """
    run_node_module(code)


def test_connection_test_and_model_fetch_flush_settings_before_api_calls():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import {
            fetchModels,
            queueLLMSettingsSave,
            testLLMConnection,
        } from './static/js/llm-settings.js';

        state.activePresetId = null;
        Object.assign(el, {
            testApi: { disabled: false },
            testResult: { textContent: '', className: '' },
            refreshModels: {
                classList: { add() {}, remove() {} },
                setAttribute() {},
            },
            apiModel: { value: '', setAttribute() {} },
            modelPickerMenu: {
                hidden: true,
                innerHTML: '',
                appendChild() {},
                querySelector() { return null; },
            },
        });
        globalThis.document = {
            createElement() {
                return {
                    className: '',
                    textContent: '',
                    dataset: {},
                    setAttribute() {},
                    classList: { add() {} },
                };
            },
        };
        const order = [];
        API.saveSettings = async fields => {
            order.push(`save:${Object.keys(fields)[0]}`);
            return {};
        };
        API.testLLM = async () => { order.push('test'); return { reply: 'ok' }; };
        API.getModels = async () => { order.push('models'); return { models: [] }; };

        queueLLMSettingsSave({ api_endpoint: 'http://new.example/v1' });
        await testLLMConnection();
        queueLLMSettingsSave({ api_key: 'sk-new' });
        await fetchModels({ force: true });

        assert.deepEqual(order, [
            'save:api_endpoint',
            'test',
            'save:api_key',
            'models',
        ]);
    """
    run_node_module(code)


def test_global_gate_save_is_immediate_and_awaitable():
    code = r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import { persistAutoSummariesEnabled } from './static/js/llm-settings.js';

        state.activePresetId = null;
        let release;
        let markStarted;
        const started = new Promise(resolve => { markStarted = resolve; });
        let saved = false;
        API.saveSettings = async fields => {
            assert.deepEqual(fields, { auto_summaries_enabled: '0' });
            markStarted();
            await new Promise(resolve => { release = resolve; });
            saved = true;
            return {};
        };

        const persisting = persistAutoSummariesEnabled(false);
        await started;
        assert.equal(saved, false);
        release();
        await persisting;
        assert.equal(saved, true);
    """
    run_node_module(code)


def test_main_api_key_queue_ignores_masks_but_persists_empty_clear():
    code = r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import {
            flushLLMSettingsSave,
            queueMainApiKeySave,
        } from './static/js/llm-settings.js';

        state.activePresetId = null;
        const calls = [];
        API.saveSettings = async fields => { calls.push({ ...fields }); return {}; };

        assert.equal(queueMainApiKeySave('••••••••'), false);
        assert.equal(queueMainApiKeySave('sk-…1234'), false);
        await flushLLMSettingsSave({ strict: true });
        assert.deepEqual(calls, []);

        assert.equal(queueMainApiKeySave(''), true);
        await flushLLMSettingsSave({ strict: true });
        assert.deepEqual(calls, [{ api_key: '' }]);
    """
    run_node_module(code)


def test_send_guard_waits_for_summary_run_before_resolving():
    code = BASE_SETUP + r"""
        let release;
        let requestedUpTo = null;
        let markRunStarted;
        const runStarted = new Promise(resolve => { markRunStarted = resolve; });
        API.runSummary = (chatId, options) => {
            requestedUpTo = options.up_to_msg_id;
            markRunStarted();
            return new Promise(resolve => { release = resolve; });
        };

        let ready = false;
        const guard = ensureSummaryReadyForSend().then(() => { ready = true; });
        await runStarted;
        assert.equal(ready, false);
        assert.equal(requestedUpTo, 2);

        release({
            id: 7,
            summary_enabled: true,
            summary: { lines: [] },
            summary_up_to_msg_id: requestedUpTo,
            summary_status: 'idle',
            summary_status_detail: '',
        });
        await guard;
        assert.equal(ready, true);
    """
    run_node_module(code)


def test_send_guard_rejects_invalid_run_response():
    code = BASE_SETUP + r"""
        API.runSummary = async () => null;
        await assert.rejects(
            ensureSummaryReadyForSend(),
            /invalid status response/,
        );
    """
    run_node_module(code)


def test_send_guard_joins_active_background_run_without_posting_again():
    code = BASE_SETUP + r"""
        state.activeChat.summary_status = 'running';
        state.activeChat.summary_status_detail = 'Summarizing...';
        let runCalls = 0;
        let statusCalls = 0;
        API.runSummary = async () => { runCalls += 1; };
        API.getSummaryStatus = async () => {
            statusCalls += 1;
            return {
                id: 7,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: 2,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        await ensureSummaryReadyForSend();

        assert.equal(runCalls, 0);
        assert.equal(statusCalls, 1);
        assert.equal(state.activeChat.summary_up_to_msg_id, 2);
    """
    run_node_module(code)


def test_send_guard_waits_when_run_endpoint_reports_already_running():
    code = BASE_SETUP + r"""
        let runCalls = 0;
        let statusCalls = 0;
        API.runSummary = async () => {
            runCalls += 1;
            return {
                id: 7,
                already_running: true,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: null,
                summary_status: 'running',
                summary_status_detail: 'Summarizing...',
            };
        };
        API.getSummaryStatus = async () => {
            statusCalls += 1;
            return {
                id: 7,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: 2,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        const nativeSetTimeout = globalThis.setTimeout;
        globalThis.setTimeout = (fn, _ms) => nativeSetTimeout(fn, 0);
        await ensureSummaryReadyForSend();

        assert.equal(runCalls, 1);
        assert.equal(statusCalls, 1);
        assert.equal(state.activeChat.summary_up_to_msg_id, 2);
    """
    run_node_module(code)


def test_send_guard_stops_waiting_when_summaries_are_paused():
    code = BASE_SETUP + r"""
        let releaseRun;
        let markRunStarted;
        const runStarted = new Promise(resolve => { markRunStarted = resolve; });
        let statusCalls = 0;
        API.runSummary = async (chatId, options) => {
            markRunStarted();
            await new Promise(resolve => { releaseRun = resolve; });
            return {
                id: chatId,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: null,
                summary_status: 'running',
                summary_status_detail: 'Summarizing...',
            };
        };
        API.getSummaryStatus = async () => { statusCalls += 1; };

        const guard = ensureSummaryReadyForSend();
        await runStarted;
        state.autoSummariesEnabled = false;
        releaseRun();
        await guard;

        assert.equal(statusCalls, 0);
    """
    run_node_module(code)


def test_send_guard_blocks_unconfigured_memory_gap_only_when_needed():
    code = BASE_SETUP + r"""
        el.apiEndpoint.value = '';
        state.apiModel = '';

        await assert.rejects(
            ensureSummaryReadyForSend(),
            /Configure an Auto Summaries endpoint and model/,
        );

        // With no context limit, nothing ages out and ordinary sending remains
        // available even when no summarizer is configured.
        el.settingsContextTokens.value = '0';
        await ensureSummaryReadyForSend();
    """
    run_node_module(code)


def test_summary_state_completion_and_reset_refresh_context_budget_hook():
    code = BASE_SETUP + r"""
        const {
            applySummaryState,
            setSummaryBudgetChangeHandler,
        } = await import('./static/js/summaries.js');
        let refreshes = 0;
        setSummaryBudgetChangeHandler(() => { refreshes += 1; });

        applySummaryState({
            id: 7,
            summary_enabled: true,
            summary: {
                lines: [{ section: 'story', text: 'A newly completed memory.', pinned: false }],
            },
            summary_up_to_msg_id: 1,
            summary_status: 'idle',
            summary_status_detail: '',
        }, 7);
        assert.equal(refreshes, 1);

        // Status-only polling with unchanged content must not churn the meter.
        applySummaryState({
            id: 7,
            summary_enabled: true,
            summary: state.activeChat.summary,
            summary_up_to_msg_id: 1,
            summary_status: 'running',
            summary_status_detail: 'Checking...',
        }, 7);
        assert.equal(refreshes, 1);

        applySummaryState({
            id: 7,
            summary_enabled: true,
            summary: { lines: [] },
            summary_up_to_msg_id: null,
            summary_status: 'idle',
            summary_status_detail: '',
        }, 7);
        assert.equal(refreshes, 2);
    """
    run_node_module(code)


def test_global_pause_disables_summary_behavior_and_per_chat_actions():
    code = BASE_SETUP + r"""
        const { renderMemorySummaryCard } = await import('./static/js/summaries.js');
        state.autoSummariesEnabled = false;
        el.apiEndpoint.value = '';
        state.apiModel = '';
        el.summaryToggle = {};
        el.summaryRebuildBtn = {};
        el.summaryResetBtn = {};
        let runCalls = 0;
        API.runSummary = async () => { runCalls += 1; };

        await ensureSummaryReadyForSend();
        await maybeTriggerSummary();
        renderMemorySummaryCard();

        assert.equal(runCalls, 0);
        assert.equal(el.summaryToggle.checked, true);
        assert.equal(el.summaryToggle.disabled, true);
        assert.equal(el.summaryRebuildBtn.disabled, true);
        assert.equal(el.summaryResetBtn.disabled, true);
    """
    run_node_module(code)


def test_unlimited_context_summary_status_does_not_show_zero_cap():
    code = BASE_SETUP + r"""
        const { renderMemorySummaryCard } = await import('./static/js/summaries.js');
        el.settingsContextTokens.value = '0';
        state.activeChat.summary = {
            lines: [{ section: 'story', text: 'A remembered event.', pinned: false }],
        };
        state.activeChat.summary_up_to_msg_id = 1;
        el.summaryToggle = {};
        el.summaryStatus = { className: '', textContent: '', appendChild() {} };

        renderMemorySummaryCard();

        assert.match(el.summaryStatus.textContent, /no cap/);
        assert.doesNotMatch(el.summaryStatus.textContent, /\/ 0 tokens/);
    """
    run_node_module(code)


def test_enable_response_does_not_start_run_for_newly_selected_chat():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import { initSummaryHandlers } from './static/js/summaries.js';

        const listeners = {};
        el.summaryToggle = {
            checked: true,
            disabled: false,
            addEventListener(type, fn) { listeners[type] = fn; },
        };
        el.summaryRebuildBtn = { addEventListener() {} };
        el.summaryResetBtn = { addEventListener() {} };

        const chatA = { id: 1, summary_enabled: false, summary: { lines: [] } };
        const chatB = { id: 2, summary_enabled: false, summary: { lines: [] } };
        state.activeChat = chatA;
        state.chats = [chatA, chatB];
        state.messages = [{ id: 10, role: 'user', text: 'old message' }];

        let finishUpdate;
        API.updateChat = () => new Promise(resolve => { finishUpdate = resolve; });
        let runCalls = 0;
        API.runSummary = async () => { runCalls += 1; };

        initSummaryHandlers();
        const enabling = listeners.change();
        state.activeChat = chatB;
        finishUpdate({
            id: 1,
            summary_enabled: true,
            summary: { lines: [] },
            summary_up_to_msg_id: null,
            summary_status: 'idle',
            summary_status_detail: '',
        });
        await enabling;

        assert.equal(runCalls, 0);
        assert.equal(chatA.summary_enabled, true);
        assert.equal(state.activeChat.id, 2);
    """
    run_node_module(code)


def test_status_polling_retries_after_transient_failure():
    code = r"""
        import assert from 'node:assert/strict';
        import { state } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import { startStatusPolling, stopStatusPolling } from './static/js/summaries.js';

        const chat = {
            id: 4,
            summary_enabled: true,
            summary: { lines: [] },
            summary_status: 'running',
            summary_status_detail: 'Starting...',
        };
        state.activeChat = chat;
        state.chats = [chat];

        let calls = 0;
        API.getSummaryStatus = async () => {
            calls += 1;
            if (calls === 1) throw new Error('temporary network error');
            return {
                id: 4,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: null,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        const nativeSetTimeout = globalThis.setTimeout;
        globalThis.setTimeout = (fn, _ms) => nativeSetTimeout(fn, 0);
        startStatusPolling(4);
        await new Promise(resolve => nativeSetTimeout(resolve, 25));
        stopStatusPolling();

        assert.equal(calls, 2);
        assert.equal(chat.summary_status, 'idle');
    """
    run_node_module(code)


def test_summary_pin_api_uses_dedicated_put_endpoint():
    code = r"""
        import assert from 'node:assert/strict';
        import { API } from './static/js/api.js';

        let request;
        globalThis.fetch = async (url, options) => {
            request = { url, options };
            return { ok: true, json: async () => ({ id: 9 }) };
        };

        await API.updateSummaryPin(9, {
            text: 'Mira trusts Morgan.',
            section: 'bonds',
            pinned: true,
        });

        assert.equal(request.url, '/api/chats/9/summary/pins');
        assert.equal(request.options.method, 'PUT');
        assert.deepEqual(JSON.parse(request.options.body), {
            text: 'Mira trusts Morgan.',
            section: 'bonds',
            pinned: true,
        });
    """
    run_node_module(code)


def test_summary_run_api_only_joins_explicit_already_running_conflict():
    code = r"""
        import assert from 'node:assert/strict';
        import { API } from './static/js/api.js';

        globalThis.fetch = async () => ({
            status: 409,
            ok: false,
            json: async () => ({
                already_running: true,
                summary_status: 'running',
                id: 3,
            }),
        });
        const joined = await API.runSummary(3, { up_to_msg_id: 10 });
        assert.equal(joined.summary_status, 'running');

        globalThis.fetch = async () => ({
            status: 409,
            ok: false,
            json: async () => ({ error: 'Auto Summaries are disabled in Settings' }),
        });
        await assert.rejects(
            API.runSummary(3, { up_to_msg_id: 10 }),
            /disabled in Settings/,
        );
    """
    run_node_module(code)


def test_swipe_generation_waits_for_summary_at_regen_context_boundary():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import { generateSwipe } from './static/js/messages.js';

        globalThis.DOMPurify = { sanitize: value => value };
        globalThis.marked = { parse: value => value };
        globalThis.document = {
            createElement() {
                return {
                    className: '',
                    textContent: '',
                    remove() {},
                    setAttribute() {},
                    classList: { add() {}, remove() {}, toggle() {} },
                };
            },
        };

        Object.assign(el, {
            apiEndpoint: { value: 'http://main.example/v1' },
            settingsContextTokens: { value: '32' },
            samplerMaxTokens: { value: '10' },
            sendThinking: { checked: true },
            sendBtn: {
                disabled: false,
                innerHTML: '',
                title: '',
                setAttribute() {},
                classList: { toggle() {} },
            },
            chatHistory: {
                querySelector() { return null; },
                insertBefore() {},
            },
        });
        state.apiModel = 'main-model';
        state.activeCharacter = { name: 'Mira' };
        state.activePersona = { name: 'Morgan' };
        state.systemPrompts = [{ id: 1, content: 'Reply as {{char}}.' }];
        state.activeSystemPromptId = 1;
        state.activeSamplers = new Set();
        state.autoSummariesEnabled = true;
        state.summaryApiEndpoint = '';
        state.summaryApiModel = '';
        state.messages = Array.from({ length: 4 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `message-${i}-abcdefghijklmno`,
        }));
        state.activeChat = {
            id: 7,
            summary_enabled: true,
            summary: {
                lines: [{ section: 'story', text: 'The first turn is remembered.' }],
            },
            summary_up_to_msg_id: 1,
            summary_status: 'idle',
            summary_status_detail: '',
        };
        state.chats = [state.activeChat];

        let releaseSummary;
        let markSummaryStarted;
        const summaryStarted = new Promise(resolve => { markSummaryStarted = resolve; });
        let summaryFinished = false;
        let summaryTarget = null;
        let generationCalls = 0;
        API.runSummary = async (chatId, options) => {
            summaryTarget = options.up_to_msg_id;
            markSummaryStarted();
            await new Promise(resolve => { releaseSummary = resolve; });
            summaryFinished = true;
            return {
                id: chatId,
                summary_enabled: true,
                // Keep the completed synthetic summary token cost at zero so
                // this test isolates regen ordering after the first pass.
                summary: { lines: [] },
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };
        API.streamChatCompletion = async payload => {
            generationCalls += 1;
            assert.equal(summaryFinished, true);
            const serialized = JSON.stringify(payload.messages);
            assert.doesNotMatch(serialized, /message-0-abcdefghijklmno/);
            assert.doesNotMatch(serialized, /message-1-abcdefghijklmno/);
            assert.match(serialized, /message-2-abcdefghijklmno/);
            assert.doesNotMatch(serialized, /message-3-abcdefghijklmno/);
            return 'a new swipe';
        };
        API.addSwipe = async () => ({});

        const contentEl = { innerHTML: '' };
        const msgBody = { querySelector() { return null; } };
        const msgEl = {
            dataset: {
                msgId: '4',
                rawText: state.messages[3].text,
                swipes: JSON.stringify([{ content: state.messages[3].text }]),
                activeSwipeIndex: '0',
            },
            querySelector(selector) {
                if (selector === '.message-content') return contentEl;
                if (selector === '.msg-body') return msgBody;
                return null;
            },
        };
        const swipes = [{ content: state.messages[3].text }];

        const generating = generateSwipe(msgEl, swipes, 0);
        await summaryStarted;
        assert.equal(generationCalls, 0);
        // Message 1 is already summarized. Regeneration excludes message 4;
        // of the remaining live turns, message 2 ages out and message 3 stays
        // raw. A normal (non-regen) boundary would also age out message 3.
        assert.equal(summaryTarget, 2);
        releaseSummary();
        const idx = await generating;

        assert.equal(generationCalls, 1);
        assert.equal(idx, 1);
        assert.equal(state.messages[3].text, 'a new swipe');
    """
    run_node_module(code)


def test_context_boundary_starts_before_first_post_watermark_message_when_all_fit():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { updateContextBoundary } from './static/js/context-meter.js';

        state.autoSummariesEnabled = true;
        state.activeChat = {
            id: 7,
            summary_enabled: true,
            summary_up_to_msg_id: 2,
            summary: { lines: [{ section: 'story', text: 'Old turns retained.' }] },
        };
        state.messages = [
            { id: 1, role: 'user', text: 'old one' },
            { id: 2, role: 'character', text: 'old two' },
            { id: 3, role: 'user', text: 'first live' },
            { id: 4, role: 'character', text: 'second live' },
        ];
        el.settingsContextTokens = { value: '1000' };
        el.samplerMaxTokens = { value: '10' };
        el.sendThinking = { checked: true };

        const containers = new Map(state.messages.map(message => [
            message.id,
            { id: `container-${message.id}` },
        ]));
        let insertedBefore = null;
        el.chatHistory = {
            querySelector(selector) {
                if (selector === '.context-boundary') return null;
                if (selector === '.message-container') return containers.get(1);
                const match = selector.match(/data-msg-id="(\d+)"/);
                if (!match) return null;
                const container = containers.get(Number(match[1]));
                return container ? { closest() { return container; } } : null;
            },
            insertBefore(_boundary, target) { insertedBefore = target; },
        };
        globalThis.document = {
            createElement() { return { className: '', textContent: '' }; },
        };

        updateContextBoundary();

        assert.equal(insertedBefore, containers.get(3));
    """
    run_node_module(code)


def test_context_boundary_follows_last_message_when_summary_covers_everything():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { updateContextBoundary } from './static/js/context-meter.js';

        state.autoSummariesEnabled = true;
        state.activeChat = {
            id: 7,
            summary_enabled: true,
            summary_up_to_msg_id: 2,
            summary: { lines: [{ section: 'story', text: 'Everything retained.' }] },
        };
        state.messages = [
            { id: 1, role: 'user', text: 'old one' },
            { id: 2, role: 'character', text: 'old two' },
        ];
        el.settingsContextTokens = { value: '1000' };
        el.samplerMaxTokens = { value: '10' };
        el.sendThinking = { checked: true };

        let appended = null;
        let inserted = false;
        el.chatHistory = {
            querySelector(selector) {
                if (selector === '.context-boundary') return null;
                if (selector === '.message-container') return { id: 'first' };
                return null;
            },
            appendChild(boundary) { appended = boundary; },
            insertBefore() { inserted = true; },
        };
        globalThis.document = {
            createElement() { return { className: '', textContent: '' }; },
        };

        updateContextBoundary();

        assert.ok(appended);
        assert.equal(appended.className, 'context-boundary');
        assert.equal(inserted, false);
    """
    run_node_module(code)


def test_rebuild_clears_stale_summary_when_full_history_fits_without_it():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import { initSummaryHandlers } from './static/js/summaries.js';

        const listeners = {};
        el.summaryToggle = { addEventListener() {} };
        el.summaryRebuildBtn = {
            addEventListener(type, fn) { listeners[type] = fn; },
        };
        el.summaryResetBtn = { addEventListener() {} };
        el.settingsContextTokens = { value: '32' };
        el.samplerMaxTokens = { value: '10' };
        el.sendThinking = { checked: true };
        state.autoSummariesEnabled = true;
        state.messages = [
            { id: 1, role: 'user', text: 'message-0-abcdefghijklmno' },
            { id: 2, role: 'character', text: 'message-1-abcdefghijklmno' },
        ];
        state.activeChat = {
            id: 7,
            summary_enabled: true,
            summary_up_to_msg_id: 1,
            summary: {
                lines: [{ section: 'story', text: 'A stale summary consumes context.' }],
            },
            summary_status: 'idle',
            summary_status_detail: '',
        };
        state.chats = [state.activeChat];

        let resets = 0;
        let runs = 0;
        API.resetSummary = async chatId => {
            resets += 1;
            return {
                id: chatId,
                summary_enabled: true,
                summary_up_to_msg_id: null,
                summary: { lines: [] },
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };
        API.runSummary = async () => { runs += 1; throw new Error('should not run'); };

        initSummaryHandlers();
        await listeners.click();

        assert.equal(resets, 1);
        assert.equal(runs, 0);
        assert.equal(state.activeChat.summary_up_to_msg_id, null);
    """
    run_node_module(code)


def test_rebuild_stabilizes_summary_shift_without_interval_rounding_or_reload_run():
    code = r"""
        import assert from 'node:assert/strict';
        import { state, el } from './static/js/state.js';
        import { API } from './static/js/api.js';
        import {
            initSummaryHandlers,
            maybeTriggerSummary,
        } from './static/js/summaries.js';

        const listeners = {};
        el.summaryToggle = { addEventListener() {} };
        el.summaryRebuildBtn = {
            addEventListener(type, fn) { listeners[type] = fn; },
        };
        el.summaryResetBtn = { addEventListener() {} };
        el.settingsContextTokens = { value: '202' };
        el.samplerMaxTokens = { value: '10' };
        el.sendThinking = { checked: true };
        state.autoSummariesEnabled = true;
        state.summaryTriggerInterval = '20';
        state.apiModel = 'main-model';
        state.summaryApiEndpoint = 'http://summary.example/v1';
        state.summaryApiModel = 'summary-model';
        state.messages = Array.from({ length: 25 }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'character' : 'user',
            text: `m-${i}-abcdefghij`,
        }));
        state.activeChat = {
            id: 7,
            summary_enabled: true,
            summary_up_to_msg_id: null,
            summary: { lines: [] },
            summary_status: 'idle',
            summary_status_detail: '',
        };
        state.chats = [state.activeChat];

        const rebuiltSummary = {
            lines: [{ section: 'story', text: 'x'.repeat(40) }],
        };
        const calls = [];
        API.runSummary = async (chatId, options) => {
            calls.push({ chatId, ...options });
            if (options.rebuild) {
                return {
                    id: chatId,
                    summary_enabled: true,
                    summary: { lines: [] },
                    summary_up_to_msg_id: null,
                    summary_status: 'running',
                    summary_status_detail: 'Summarizing…',
                };
            }
            return {
                id: chatId,
                summary_enabled: true,
                summary: rebuiltSummary,
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };
        API.getSummaryStatus = async chatId => ({
            id: chatId,
            summary_enabled: true,
            summary: rebuiltSummary,
            summary_up_to_msg_id: calls[0].up_to_msg_id,
            summary_status: 'idle',
            summary_status_detail: '',
        });

        const nativeSetTimeout = globalThis.setTimeout;
        globalThis.setTimeout = (fn, _ms) => nativeSetTimeout(fn, 0);
        initSummaryHandlers();
        await listeners.click();

        assert.deepEqual(calls.map(call => ({
            upTo: call.up_to_msg_id,
            rebuild: call.rebuild,
        })), [
            { upTo: 2, rebuild: true },
            // The final summary displaced messages 3 and 4. Stabilization folds
            // in exactly those two instead of rounding through message 22.
            { upTo: 4, rebuild: false },
        ]);
        assert.equal(state.activeChat.summary_up_to_msg_id, 4);

        await maybeTriggerSummary();
        assert.equal(calls.length, 2);
    """
    run_node_module(code)


def test_global_off_cancellation_state_allows_immediate_catchup_on_resume():
    code = BASE_SETUP + r"""
        const {
            markSummaryRunsCancelled,
            onGlobalSummarySettingChanged,
        } = await import('./static/js/summaries.js');

        state.autoSummariesEnabled = false;
        state.activeChat.summary_status = 'running';
        state.activeChat.summary_status_detail = 'Summarizing 1 message...';
        markSummaryRunsCancelled();
        assert.equal(state.activeChat.summary_status, 'idle');
        assert.equal(state.activeChat.summary_status_detail, '');

        let runs = 0;
        API.getSummaryStatus = async chatId => ({
            id: chatId,
            summary_enabled: true,
            summary: { lines: [] },
            summary_up_to_msg_id: null,
            summary_status: 'idle',
            summary_status_detail: '',
        });
        API.runSummary = async (chatId, options) => {
            runs += 1;
            return {
                id: chatId,
                summary_enabled: true,
                summary: { lines: [] },
                summary_up_to_msg_id: options.up_to_msg_id,
                summary_status: 'idle',
                summary_status_detail: '',
            };
        };

        state.autoSummariesEnabled = true;
        await onGlobalSummarySettingChanged();

        assert.equal(runs, 1);
        assert.equal(state.activeChat.summary_up_to_msg_id, 2);
    """
    run_node_module(code)
