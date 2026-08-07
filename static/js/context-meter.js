import { state, el } from './state.js';
import { scrollToBottom } from './utils.js';
import { summaryToText } from './summaries.js';
import { getContextTokenBudget, getRawHistoryMessages } from './context-budget.js';
import { analyzeContext } from './context-analysis.js';
import { retargetTooltip } from './tooltips.js';
import { saveLLMSettings } from './llm-settings.js';

function activeSummaryText() {
    return state.activeChat?.summary_enabled
        ? summaryToText(state.activeChat.summary)
        : '';
}

export function getCurrentContextAnalysis({ includeDraft = false } = {}) {
    return analyzeContext({
        summaryText: activeSummaryText(),
        draftText: includeDraft ? el.userInput?.value || '' : '',
    });
}

/**
 * Whether a context-window separator is drawn in the transcript, and so
 * whether the Message history segment is worth clicking. Mirrors the guard in
 * updateContextBoundary() rather than probing the DOM, since the meter and the
 * boundary re-render in either order.
 */
function canJumpToBoundary(analysis) {
    return analysis.maxTokens > 0 && (state.messages?.length || 0) > 0;
}

export function tooltipForSegment(segment, analysis) {
    const formatted = segment.tokens.toLocaleString();
    const pct = analysis.maxTokens > 0
        ? `${((segment.tokens / analysis.maxTokens) * 100).toFixed(1)}% of context`
        : `${((segment.tokens / Math.max(1, analysis.allocatedTokens)) * 100).toFixed(1)}% of accounted tokens`;
    let detail = `≈ ${formatted} tokens · ${pct}`;
    if (segment.id === 'message_history') {
        const inWindow = analysis.selectedMessageIds?.length || 0;
        const total = state.messages?.length || 0;
        detail += total > inWindow
            ? `<br>Reaches back ${inWindow} message${inWindow === 1 ? '' : 's'} (of ${total}); older turns live in the summary or are out of the window.`
            : `<br>Reaches back through all ${total} message${total === 1 ? '' : 's'}.`;
        if (canJumpToBoundary(analysis)) {
            detail += '<br>Click to jump to where the window starts.';
        }
    }
    if (segment.id === 'unused') detail += '<br>Available for future conversation context.';
    if (segment.id === 'response_reserve') detail += '<br>Held back so the model has room to answer.';
    const hasDuplicate = analysis.segments
        .some(other => other !== segment && other.id === segment.id);
    const zoneLabel = segment.zone === 'system' ? 'System template'
        : (segment.zone === 'user' ? 'User template' : '');
    const placement = hasDuplicate && zoneLabel ? ` · ${zoneLabel}` : '';
    return `<strong>${segment.label}${placement}</strong><br>${detail}`;
}

function renderSegments(analysis) {
    const bar = el.contextTokenBar;
    if (!bar) return;
    bar.replaceChildren();

    const denominator = analysis.maxTokens > 0 && analysis.overflowTokens === 0
        ? analysis.maxTokens
        : Math.max(1, analysis.allocatedTokens);

    for (const segment of analysis.segments) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'context-meter-segment';
        button.dataset.source = segment.id;
        button.dataset.segmentKey = segment.key;
        button.dataset.tip = tooltipForSegment(segment, analysis);
        button.style.flexBasis = `${(segment.tokens / denominator) * 100}%`;
        const hasDuplicate = analysis.segments
            .some(other => other !== segment && other.id === segment.id);
        const zoneLabel = segment.zone === 'system' ? 'System template'
            : (segment.zone === 'user' ? 'User template' : '');
        const placement = hasDuplicate && zoneLabel ? `, ${zoneLabel}` : '';
        const jumps = segment.id === 'message_history' && canJumpToBoundary(analysis);
        button.classList.toggle('context-meter-segment--jump', jumps);
        button.setAttribute(
            'aria-label',
            `${segment.label}${placement}: approximately ${segment.tokens.toLocaleString()} tokens`
            + (jumps ? '. Activate to scroll to the start of the context window' : ''),
        );
        bar.appendChild(button);
    }

    // Rebuilding detaches the segment an open tooltip may be anchored to
    // (tapped open on touch, where nothing else dismisses it). Follow it to
    // the same ordered replacement so the numbers stay live, or dismiss it
    // when that segment no longer exists.
    retargetTooltip(previous => previous.classList?.contains('context-meter-segment')
        ? bar.querySelector(`[data-segment-key="${previous.dataset.segmentKey}"]`)
        : null);
}

/**
 * Show or hide the meter and remember the choice. Shared by the Settings
 * checkbox and /meter, so either one leaves the other looking right.
 */
export function setContextMeterVisible(visible) {
    state.showContextTokenMeter = visible;
    if (el.settingsContextMeterToggle) el.settingsContextMeterToggle.checked = visible;
    saveLLMSettings({ show_context_token_meter: visible ? '1' : '0' });
    updateContextMeter();
}

export function updateContextMeter() {
    if (!el.contextTokenMeter || !el.contextTokenLabel || !el.contextTokenBar) return;
    if (!state.showContextTokenMeter || !state.activeChat) {
        el.contextTokenMeter.hidden = true;
        return;
    }
    const wasHidden = el.contextTokenMeter.hidden;
    const analysis = getCurrentContextAnalysis({ includeDraft: true });
    renderSegments(analysis);

    if (analysis.maxTokens <= 0) {
        el.contextTokenLabel.textContent = `${analysis.allocatedTokens.toLocaleString()} · no limit`;
        el.contextTokenMeter.dataset.level = 'ok';
    } else if (analysis.overflowTokens > 0) {
        el.contextTokenLabel.textContent = `${analysis.allocatedTokens.toLocaleString()} / ${analysis.maxTokens.toLocaleString()} · ${analysis.overflowTokens.toLocaleString()} over`;
        el.contextTokenMeter.dataset.level = 'danger';
    } else {
        const pct = Math.min(100, Math.round((analysis.allocatedTokens / analysis.maxTokens) * 100));
        el.contextTokenLabel.textContent = `${analysis.allocatedTokens.toLocaleString()} / ${analysis.maxTokens.toLocaleString()}`;
        el.contextTokenMeter.dataset.level = pct >= 90 ? 'danger' : (pct >= 70 ? 'warn' : 'ok');
    }

    const track = el.contextTokenBar.parentElement;
    if (track) {
        track.setAttribute('aria-valuemin', '0');
        const ariaMax = analysis.maxTokens > 0
            ? analysis.maxTokens
            : Math.max(1, analysis.allocatedTokens);
        track.setAttribute('aria-valuemax', String(ariaMax));
        track.setAttribute('aria-valuenow', String(Math.min(analysis.allocatedTokens, ariaMax)));
        track.setAttribute('aria-valuetext', el.contextTokenLabel.textContent);
    }
    el.contextTokenMeter.hidden = false;

    // Revealing the meter grows the composer and shrinks the chat area, which
    // can otherwise strand a bottom-anchored scroll just above the last turn.
    if (wasHidden && el.chatHistory) {
        const scroller = el.chatHistory;
        if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60) {
            scrollToBottom();
        }
    }
}

export function updateContextBoundary() {
    const existing = el.chatHistory?.querySelector('.context-boundary');
    if (existing) existing.remove();

    if (getContextTokenBudget() <= 0 || state.messages.length === 0) return;

    const rawMessages = getRawHistoryMessages(state.messages);
    // Same draft-inclusive view the meter uses. A long draft genuinely pushes
    // older turns out of the window, and the meter's own tooltip offers to jump
    // here — counting the draft in one place but not the other left the
    // separator sitting several messages away from the count it quoted.
    const analysis = getCurrentContextAnalysis({ includeDraft: true });
    let boundaryMessageId = analysis.firstSelectedMessageId;

    // Summarized transcript remains visible even though only the post-watermark
    // suffix is eligible for raw context.
    if (boundaryMessageId == null && rawMessages.length > 0
        && rawMessages.length < state.messages.length) {
        boundaryMessageId = rawMessages[0].id ?? null;
    }

    const boundary = document.createElement('div');
    boundary.className = 'context-boundary';
    boundary.textContent = 'Context window';

    if (rawMessages.length === 0) {
        el.chatHistory.appendChild(boundary);
        return;
    }

    if (boundaryMessageId != null) {
        const target = el.chatHistory.querySelector(`.message[data-msg-id="${boundaryMessageId}"]`);
        if (target) {
            el.chatHistory.insertBefore(boundary, target.closest('.message-container') || target);
            return;
        }
    }

    const firstContainer = el.chatHistory.querySelector('.message-container');
    if (firstContainer) el.chatHistory.insertBefore(boundary, firstContainer);
}

// Landing the separator flush with the top edge reads as having scrolled past
// it; a little headroom keeps it on screen as the landmark it is.
const BOUNDARY_JUMP_MARGIN = 28;
const BOUNDARY_FLASH_MS = 1400;

/**
 * Scroll the transcript to the context-window separator. Returns false when no
 * separator is drawn (no context limit, or an empty chat), so the caller can
 * leave the click inert instead of scrolling somewhere arbitrary.
 */
export function jumpToContextBoundary() {
    const scroller = el.chatHistory;
    const boundary = scroller?.querySelector('.context-boundary');
    if (!boundary) return false;

    const offset = boundary.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    // Instant, like scrollToBottom(): a `behavior: 'smooth'` scroll is silently
    // ignored on some embedded browsers, which would leave the jump doing
    // nothing at all. The flash below is what orients the user instead.
    scroller.scrollTop = Math.max(0, scroller.scrollTop + offset - BOUNDARY_JUMP_MARGIN);
    // The scroll listener will work this out for itself, but not before an
    // in-flight token arrives and yanks the view back down.
    state.autoScroll = false;

    // The separator is deliberately faint, so flash it — otherwise arriving
    // mid-transcript gives no confirmation of where the jump landed. Dropping
    // the class and forcing a reflow lets a repeat click replay the animation.
    boundary.classList.remove('context-boundary--flash');
    void boundary.offsetWidth;
    boundary.classList.add('context-boundary--flash');
    setTimeout(() => boundary.classList.remove('context-boundary--flash'), BOUNDARY_FLASH_MS);
    return true;
}

export function initContextMeter() {
    // Delegated: renderSegments() replaces every button on each update.
    el.contextTokenBar?.addEventListener('click', e => {
        if (e.target.closest('.context-meter-segment--jump')) jumpToContextBoundary();
    });
}
