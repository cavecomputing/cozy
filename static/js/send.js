import { state, el, llm } from './state.js';
import {
    autoResize, showToast, showApiNotice, scrollToBottom, maybeScrollToBottom,
    setSendButtonMode, updateComposerState, beginGeneration, endGeneration,
} from './utils.js';
import { appendMessage, streamInto, regenerateLastAssistantMessage } from './messages.js';
import { generateResponse } from './request-builder.js';
import { hasVisibleResponse, closeIncompleteThinking } from './thinking.js';
import { clearDraft, saveDraft } from './drafts.js';
import { executeSlashCommand } from './slash-commands.js';
import { ensureSummaryReadyForSend, maybeTriggerSummary } from './summaries.js';
import { flushLLMSettingsSave } from './llm-settings.js';

// ═══════════════════════════════════════════════════════════════════════════
// SEND MESSAGE
// ═══════════════════════════════════════════════════════════════════════════
/** Send `text` (the composer's, by default), or with none ask for a reply to the chat as it stands. */
export async function handleSend(text = el.userInput.value.trim()) {
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

    // A retry sends nothing, so whatever has been typed since stays put.
    if (text) {
        el.userInput.value = '';
        autoResize(el.userInput);
    }
    // Sending takes the reader to the bottom and follows the reply from there,
    // wherever they were scrolled to. Scrolling up during the reply ends it.
    scrollToBottom();

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
        if (!await appendMessage('user', text, true)) {
            // Not saved, and the toast said so. Back into the composer, ahead
            // of anything typed meanwhile, unless another chat owns it by now.
            if (state.activeChat?.id === chatId) {
                el.userInput.value = [text, el.userInput.value].filter(Boolean).join('\n');
                autoResize(el.userInput);
                saveDraft();
            }
            return;
        }
        clearDraft();
    }

    // Create loading bubble (not persisted)
    const loadingContainer = await appendMessage('character', '', false);
    loadingContainer.classList.add('streaming');
    const loadingMsg = loadingContainer.querySelector('.message');
    const contentEl = loadingMsg.querySelector('.message-content');
    const msgBody = loadingMsg.querySelector('.msg-body');
    contentEl.innerHTML = '<div class="message-loading"><span></span><span></span><span></span></div>';

    const stream = streamInto(msgBody, contentEl);
    let reply = null;
    let failed = false;
    // The memory update and the reply can be pointed at different endpoints, so
    // an upstream error is only actionable if the toast says which one failed.
    let source = 'Auto Summaries API';
    try {
        // Persisting this user turn can move an older message outside the raw
        // context window. Fold that newly aged-out history into memory before
        // building the request so no turn falls between the two. A failed
        // update warns and lets the reply go ahead rather than blocking it.
        await ensureSummaryReadyForSend(signal);

        source = 'Chat API';
        reply = await generateResponse(0, stream.onToken, signal);
        await stream.settle(signal);
    } catch (err) {
        // Stopped on purpose, or cut off by an error: keep the text as an
        // ordinary reply. An implicit abort (chat switch) leaves stopRequested
        // false and falls through to the discard below. Reasoning with no
        // response yet is dropped: analyzeContext strips thinking, so it would
        // persist as a blank bubble that never reaches the model again.
        const cutOff = err.name !== 'AbortError';
        if ((cutOff || llm.stopRequested) && hasVisibleResponse(stream.text)) {
            reply = closeIncompleteThinking(stream.text);
        }
        if (cutOff) {
            failed = true;
            console.error('LLM error:', err);
            // Retry asks again for the reply this send was waiting on, or for
            // another take beside the part that was kept. /retry is not the
            // same thing: it regenerates the reply before it.
            const retry = reply ? regenerateLastAssistantMessage : () => handleSend('');
            showToast(`${reply ? 'Reply cut short. ' : ''}${source}: ${err.message}`, 'error', 8000, {
                label: 'Retry',
                onClick: () => { if (state.activeChat?.id === chatId) retry(); },
            });
        }
    } finally {
        stream.stop();
    }

    // appendMessage persists against whatever chat is active now, so a reply
    // that outlived its chat has to be dropped rather than misfiled.
    if (state.activeChat?.id !== chatId) {
        loadingContainer.remove();
        return;
    }
    if (!reply) {
        loadingContainer.remove();
        // Nothing salvaged. A stop is silent and a failure already toasted; a
        // stream that simply completed empty is worth saying out loud, since
        // otherwise the message POST answers with an opaque 400.
        if (!failed && !signal.aborted) showToast('The model returned no text');
        return;
    }
    // The finished reply goes in before the stream comes out. Taking the
    // stream out first shrinks the transcript by a whole reply for a moment,
    // and a browser that paints or settles its scroll in that moment leaves
    // the view stranded up where it clamped.
    const saving = appendMessage('character', reply, true);
    loadingContainer.remove();
    maybeScrollToBottom();
    await saving;
    // Fold any history displaced by the new reply in the background. The
    // next send's preflight waits for this job if it is still running.
    maybeTriggerSummary();
}
