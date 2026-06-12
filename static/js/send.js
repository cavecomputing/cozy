import { state, el, llm } from './state.js';
import { autoResize, showToast, showApiNotice, hideApiNotice, maybeScrollToBottom, setSendButtonMode, updateComposerState } from './utils.js';
import { appendMessage, renderMarkdown } from './messages.js';
import { generateResponse } from './request-builder.js';
import { parseThinkingContent, renderThinkingBlock } from './thinking.js';
import { maybeAutoNameChat } from './chats.js';
import { clearDraft } from './drafts.js';
import { executeSlashCommand } from './slash-commands.js';

// ═══════════════════════════════════════════════════════════════════════════
// SEND MESSAGE
// ═══════════════════════════════════════════════════════════════════════════
export async function handleSend() {
    const text = el.userInput.value.trim();
    if (!state.activeCharacter || !state.activeChat) return;
    if (executeSlashCommand(text)) return;

    // Preflight: without a model the request can't be built — keep the
    // user's text in the composer and point them at settings instead of
    // persisting a message that will never get a reply.
    if (!state.apiModel) {
        showApiNotice();
        return;
    }
    hideApiNotice();

    el.userInput.value = '';
    autoResize(el.userInput);
    state.autoScroll = true;

    llm.abortController = new AbortController();
    const { signal } = llm.abortController;
    setSendButtonMode('stop');
    el.sendBtn.disabled = false;
    updateComposerState();

    const nudge = text ? null : 'Continue.';
    if (text) {
        await appendMessage('user', text, true);
        clearDraft();
        maybeAutoNameChat(text);
    }

    // Create loading bubble (not persisted)
    const loadingContainer = await appendMessage('character', '', false);
    const loadingMsg = loadingContainer.querySelector('.message');
    const contentEl = loadingMsg.querySelector('.message-content');
    const msgBody = loadingMsg.querySelector('.msg-body');
    contentEl.innerHTML = '<div class="message-loading"><span></span><span></span><span></span></div>';

    try {
        const reply = await generateResponse(0, (accumulated) => {
            const parsed = parseThinkingContent(accumulated);
            renderThinkingBlock(msgBody, parsed);
            renderMarkdown(contentEl, parsed.response);
            maybeScrollToBottom();
        }, signal, nudge);

        // Finalize: remove loading container and persist the real message
        loadingContainer.remove();
        await appendMessage('character', reply, true);

    } catch (err) {
        loadingContainer.remove();
        if (err.name !== 'AbortError') {
            console.error('LLM error:', err);
            showToast(err.message);
        }
    } finally {
        llm.abortController = null;
        setSendButtonMode('send');
        updateComposerState();
    }
}
