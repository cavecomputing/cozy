import { state, el, llm } from './state.js';
import {
    autoResize, showToast, showApiNotice, hideApiNotice, maybeScrollToBottom,
    setSendButtonMode, updateComposerState, beginGeneration, endGeneration,
} from './utils.js';
import { appendMessage, renderMarkdown } from './messages.js';
import { generateResponse } from './request-builder.js';
import {
    parseThinkingContent, renderThinkingBlock, hasVisibleResponse, closeIncompleteThinking,
} from './thinking.js';
import { clearDraft } from './drafts.js';
import { executeSlashCommand } from './slash-commands.js';
import { applyOutputFilters } from './regex-filters.js';
import { ensureSummaryReadyForSend, maybeTriggerSummary } from './summaries.js';
import { flushLLMSettingsSave } from './llm-settings.js';

// ═══════════════════════════════════════════════════════════════════════════
// SEND MESSAGE
// ═══════════════════════════════════════════════════════════════════════════
export async function handleSend() {
    const text = el.userInput.value.trim();
    if (!state.activeCharacter || !state.activeChat) return;
    if (executeSlashCommand(text)) return;

    // Claim the slot before the first await. The abort controller is created
    // after settings preflight, which otherwise leaves rapid Enter presses or
    // clicks free to start multiple sends in that window.
    if (!beginGeneration()) return;
    el.sendBtn.disabled = true;

    try {
        await sendOnce(text);
    } finally {
        // Keep the slot through assistant-message persistence, not just the
        // stream, so the next prompt cannot race the completed reply into DB.
        llm.abortController = null;
        llm.stopRequested = false;
        setSendButtonMode('send');
        endGeneration();
        updateComposerState();
    }
}

async function sendOnce(text) {
    // Preflight: without a model the request can't be built — keep the
    // user's text in the composer and point them at settings instead of
    // persisting a message that will never get a reply.
    try {
        // Endpoint/model/key edits are debounced. Persist them before either a
        // summary worker or the chat completion reads settings on the server.
        await flushLLMSettingsSave({ strict: true });
    } catch (e) {
        console.error('Settings flush failed before send:', e);
        return;
    }

    if (!state.apiModel) {
        showApiNotice();
        return;
    }
    hideApiNotice();

    el.userInput.value = '';
    autoResize(el.userInput);
    state.autoScroll = true;

    // The reply belongs to the chat that was open when the request went out —
    // switching chats mid-stream must not redirect it into the new one.
    const chatId = state.activeChat.id;

    llm.abortController = new AbortController();
    llm.stopRequested = false;
    const { signal } = llm.abortController;
    setSendButtonMode('stop');
    el.sendBtn.disabled = false;
    updateComposerState();

    if (text) {
        await appendMessage('user', text, true);
        clearDraft();
    }

    // Create loading bubble (not persisted)
    const loadingContainer = await appendMessage('character', '', false);
    const loadingMsg = loadingContainer.querySelector('.message');
    const contentEl = loadingMsg.querySelector('.message-content');
    const msgBody = loadingMsg.querySelector('.msg-body');
    contentEl.innerHTML = '<div class="message-loading"><span></span><span></span><span></span></div>';

    // Kept in step with the stream so a Stop can still salvage it.
    let streamed = '';
    let reply = null;
    let failed = false;
    // The memory update and the reply can be pointed at different endpoints, so
    // an upstream error is only actionable if the toast says which one failed.
    let source = 'Auto Summaries API';
    try {
        // Persisting this user turn can move an older message outside the raw
        // context window. Fold that newly aged-out history into memory before
        // building the request so no turn falls between the two.
        await ensureSummaryReadyForSend(signal);

        source = 'Chat API';
        reply = await generateResponse(0, (accumulated) => {
            streamed = accumulated;
            const parsed = parseThinkingContent(accumulated);
            renderThinkingBlock(msgBody, parsed);
            renderMarkdown(contentEl, parsed.response, true);
            maybeScrollToBottom();
        }, signal);

    } catch (err) {
        if (err.name !== 'AbortError') {
            failed = true;
            console.error('LLM error:', err);
            showToast(`${source}: ${err.message}`);
        } else if (llm.stopRequested) {
            // Stopped on purpose — keep the text as an ordinary reply. An
            // implicit abort (chat switch) leaves stopRequested false and falls
            // through to the discard below. Reasoning with no response yet is
            // dropped: analyzeContext strips thinking, so it would persist as a
            // blank bubble that never reaches the model again.
            if (hasVisibleResponse(streamed)) reply = closeIncompleteThinking(streamed);
        }
    }

    loadingContainer.remove();
    // appendMessage persists against whatever chat is active now, so a reply
    // that outlived its chat has to be dropped rather than misfiled.
    if (state.activeChat?.id !== chatId) return;
    if (!reply) {
        // Nothing salvaged. A stop is silent and a failure already toasted; a
        // stream that simply completed empty is worth saying out loud, since
        // otherwise the message POST answers with an opaque 400.
        if (!failed && !signal.aborted) showToast('The model returned no text');
        return;
    }
    // Regex filters rewrite the reply before it is persisted, so the corrected
    // text is what gets saved, shown, and read back into context next turn.
    reply = applyOutputFilters(reply);
    await appendMessage('character', reply, true);
    // Fold any history displaced by the new reply in the background. The
    // next send's preflight waits for this job if it is still running.
    maybeTriggerSummary();
}
