import { state, el, llm } from './state.js';
import {
    autoResize, showToast, showApiNotice, scrollToBottom, maybeScrollToBottom,
    setSendButtonMode, updateComposerState, beginGeneration, endGeneration, whenGenerationEnds,
} from './utils.js';
import { appendMessage, streamInto, regenerateLastAssistantMessage } from './messages.js';
import { refreshOpenChat } from './chats.js';
import { generateResponse } from './request-builder.js';
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
        endGeneration();
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

    await receiveReply(chatId, onToken => generateResponse(0, onToken, signal, chatId), true);
}

/**
 * Draw the reply to chat `chatId` at the end of it as the server writes it,
 * and the saved message in its place once it is done. `read` opens the stream
 * and resolves with that message. `memoryFirst` is for a reply this page asks
 * for itself, which waits on the chat's memory before it does; one being
 * rejoined is already under way.
 */
export async function receiveReply(chatId, read, memoryFirst = false) {
    const { signal } = llm.abortController;
    // Create loading bubble (not persisted)
    const loadingContainer = await appendMessage('character', '', false);
    loadingContainer.classList.add('streaming');
    const loadingMsg = loadingContainer.querySelector('.message');
    const contentEl = loadingMsg.querySelector('.message-content');
    const msgBody = loadingMsg.querySelector('.msg-body');
    contentEl.innerHTML = '<div class="message-loading"><span></span><span></span><span></span></div>';

    const stream = streamInto(msgBody, contentEl);
    let saved = null;
    // Whether the reader has been told why there is no reply, or needs no telling.
    let explained = false;
    // The memory update and the reply can be pointed at different endpoints, so
    // an upstream error is only actionable if the toast says which one failed.
    let source = 'Auto Summaries API';
    try {
        // Persisting this user turn can move an older message outside the raw
        // context window. Fold that newly aged-out history into memory before
        // building the request so no turn falls between the two. A failed
        // update warns and lets the reply go ahead rather than blocking it.
        if (memoryFirst) await ensureSummaryReadyForSend(signal);

        source = 'Chat API';
        saved = await read(stream.onToken);
        await stream.settle(signal);
    } catch (err) {
        // The server keeps what it had of a reply that an error cut off, as an
        // ordinary reply, unless that was only reasoning: analyzeContext
        // strips thinking, so it would be a blank bubble that never reaches
        // the model again. Leaving the chat (an abort) only stops this page
        // listening, and a lost connection likewise: the reply is still being
        // written, or is saved, and the page goes back for it.
        saved = err.saved;
        explained = true;
        if (err.dropped) {
            whenGenerationEnds(() => refreshOpenChat(true));
        } else if (err.name !== 'AbortError') {
            console.error('LLM error:', err);
            // Retry asks again for the reply this send was waiting on, or for
            // another take beside the part that was kept. /retry is not the
            // same thing: it regenerates the reply before it.
            const retry = saved ? regenerateLastAssistantMessage : () => handleSend('');
            showToast(`${saved ? 'Reply cut short. ' : ''}${source}: ${err.message}`, 'error', 8000, {
                label: 'Retry',
                onClick: () => { if (state.activeChat?.id === chatId) retry(); },
            });
        }
    } finally {
        stream.stop();
    }

    // The reply was saved to its own chat. Only the open chat is on screen.
    if (state.activeChat?.id !== chatId) {
        loadingContainer.remove();
        return;
    }
    if (!saved) {
        loadingContainer.remove();
        // Nothing kept. A stop is silent and a failure already toasted; a
        // stream that simply completed empty is worth saying out loud.
        if (!explained && !llm.stopRequested) showToast('The model returned no text');
        return;
    }
    // The finished reply goes in before the stream comes out. Taking the
    // stream out first shrinks the transcript by a whole reply for a moment,
    // and a browser that paints or settles its scroll in that moment leaves
    // the view stranded up where it clamped.
    const landing = appendMessage('character', saved.content, true, saved);
    loadingContainer.remove();
    maybeScrollToBottom();
    await landing;
    // Fold any history displaced by the new reply in the background. The
    // next send's preflight waits for this job if it is still running.
    maybeTriggerSummary();
}
