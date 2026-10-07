"""The transcript draws a long chat from its newest end, a page at a time."""

from helpers import run_node_module


# Just enough DOM for buildMessageEl() and the transcript scroller. The
# scroller lays its children out in a column — a message 50px tall, the
# context separator 10px — so scrollHeight, scrollTop and each drawn
# element's position behave the way the browser's do.
TRANSCRIPT_SETUP = r"""
    import assert from 'node:assert/strict';
    import { state, el } from './static/js/state.js';
    import { renderMessages, drawOlderMessages, drawOlderNearTop } from './static/js/messages.js';
    import {
        updateContextBoundary, updateContextViews, jumpToContextBoundary, getCurrentContextAnalysis,
    } from './static/js/context-meter.js';

    const camel = name => name.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

    function matches(node, selector) {
        return selector.split(',').some(part => {
            const classes = [...part.matchAll(/\.([\w-]+)/g)].map(m => m[1]);
            const attrs = [...part.matchAll(/\[data-([\w-]+)="([^"]*)"\]/g)];
            const own = new Set(node.className.split(/\s+/));
            return classes.every(c => own.has(c))
                && attrs.every(([, key, value]) => String(node.dataset[camel(key)]) === value);
        });
    }

    class FakeNode {
        constructor(tag = 'div') {
            this.tag = tag;
            this.children = [];
            this.parent = null;
            this.className = '';
            this.dataset = {};
            this.style = {};
            this.textContent = '';
            this.innerHTML = '';
        }
        get classList() {
            const node = this;
            const list = () => new Set(node.className.split(/\s+/).filter(Boolean));
            return {
                add(...names) { const s = list(); names.forEach(n => s.add(n)); node.className = [...s].join(' '); },
                remove(...names) { const s = list(); names.forEach(n => s.delete(n)); node.className = [...s].join(' '); },
                contains(name) { return list().has(name); },
                toggle(name, on) { (on ?? !list().has(name)) ? this.add(name) : this.remove(name); },
            };
        }
        get childNodes() { return this.children; }
        get height() {
            if (this.className.split(/\s+/).includes('message-container')) return 50;
            if (this.className.split(/\s+/).includes('context-boundary')) return 10;
            return 0;
        }
        adopt(nodes) {
            return nodes.flatMap(n => (n.fragment ? n.children.splice(0) : [n])).map(n => {
                n.parent?.children.splice(n.parent.children.indexOf(n), 1);
                n.parent = this;
                return n;
            });
        }
        append(...nodes) { this.children.push(...this.adopt(nodes)); }
        appendChild(node) { this.append(node); return node; }
        prepend(...nodes) { this.children.unshift(...this.adopt(nodes)); }
        insertBefore(node, ref) {
            if (!ref) return this.appendChild(node);
            const adopted = this.adopt([node]);
            this.children.splice(this.children.indexOf(ref), 0, ...adopted);
            return node;
        }
        replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
        remove() { this.parent?.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
        setAttribute() {}
        removeAttribute() {}
        closest(selector) {
            for (let n = this; n; n = n.parent) if (matches(n, selector)) return n;
            return null;
        }
        querySelectorAll(selector) {
            const found = [];
            const walk = n => n.children.forEach(c => { if (matches(c, selector)) found.push(c); walk(c); });
            walk(this);
            return found;
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        getBoundingClientRect() {
            let top = 0;
            let child = this;
            while (child.parent && !child.parent.isScroller) child = child.parent;
            const scroller = child.parent;
            if (!scroller) return { top: 0 };
            for (const sibling of scroller.children) {
                if (sibling === child) break;
                top += sibling.height;
            }
            return { top: top - scroller.scrollTop };
        }
    }

    class FakeScroller extends FakeNode {
        constructor(clientHeight) {
            super();
            this.isScroller = true;
            this.clientHeight = clientHeight;
            this._scrollTop = 0;
        }
        get scrollHeight() { return this.children.reduce((sum, c) => sum + c.height, 0); }
        get scrollTop() { return this._scrollTop; }
        set scrollTop(value) {
            this._scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight));
        }
        getBoundingClientRect() { return { top: 0 }; }
    }

    globalThis.document = {
        createElement: tag => new FakeNode(tag),
        createDocumentFragment: () => Object.assign(new FakeNode(), { fragment: true }),
    };
    globalThis.marked = { parse: text => text };
    globalThis.DOMPurify = { sanitize: html => html };

    const scroller = new FakeScroller(600);
    el.chatHistory = scroller;
    el.settingsContextTokens = { value: '0' };
    el.samplerMaxTokens = { value: '256' };
    state.extraRequestParams = '';
    state.activeCharacter = { id: 1, name: 'Sasha' };
    state.activePersona = { name: 'Matt', description: '' };
    state.systemPrompts = [{ id: 1, content: 'You are {{char}}.', post_history_content: '' }];
    state.activeSystemPromptId = 1;
    state.lorebooks = [];
    state.activeChat = { id: 1, summary_enabled: 0 };

    function chatOf(count) {
        return Array.from({ length: count }, (_, i) => ({
            id: i + 1,
            role: i % 2 ? 'user' : 'character',
            text: `Turn ${i + 1}: ` + 'some words '.repeat(8),
            swipes: [{ content: `Turn ${i + 1}` }],
            activeSwipeIndex: 0,
        }));
    }
    const drawnIds = () => scroller.querySelectorAll('.message').map(m => Number(m.dataset.msgId));
    const boundaryBefore = () => {
        const index = scroller.children.findIndex(c => c.classList.contains('context-boundary'));
        return index === -1 ? null : Number(scroller.children[index + 1].querySelector('.message').dataset.msgId);
    };
"""


def test_a_long_chat_draws_only_its_newest_page():
    run_node_module(TRANSCRIPT_SETUP + r"""
        state.messages = chatOf(250);
        renderMessages();

        const ids = drawnIds();
        assert.equal(ids.length, 100);
        assert.equal(ids[0], 151);
        assert.equal(ids.at(-1), 250);
        // Opened at the newest message, with well over a screen of history above.
        assert.equal(scroller.scrollTop, scroller.scrollHeight - scroller.clientHeight);
        // Message 151 opens the drawn page but not the chat, so it is no greeting.
        assert.equal(scroller.querySelector('.message').dataset.isGreeting, undefined);
    """)


def test_scrolling_up_draws_older_pages_without_moving_the_view():
    run_node_module(TRANSCRIPT_SETUP + r"""
        state.messages = chatOf(250);
        renderMessages();

        // The reader scrolls to within a screen of the top.
        scroller.scrollTop = 120;
        const reading = scroller.querySelector('.message[data-msg-id="152"]');
        const before = reading.getBoundingClientRect().top;
        drawOlderNearTop();

        assert.deepEqual(drawnIds().slice(0, 2), [51, 52]);
        assert.equal(drawnIds().length, 200);
        assert.equal(reading.getBoundingClientRect().top, before, 'the view must hold still');

        // The last page is short, and its first message is the greeting.
        scroller.scrollTop = 0;
        drawOlderNearTop();
        assert.equal(drawnIds().length, 250);
        assert.equal(scroller.querySelector('.message').dataset.isGreeting, 'true');
        assert.equal(drawOlderMessages(), false, 'nothing older is left');
    """)


def test_a_short_newest_page_keeps_drawing_until_it_can_scroll():
    """A transcript that cannot scroll never fires the scroll that asks for more."""
    run_node_module(TRANSCRIPT_SETUP + r"""
        scroller.clientHeight = 8000;   // taller than a page of 100 messages
        state.messages = chatOf(250);
        renderMessages();
        assert.ok(drawnIds().length > 100);
        assert.ok(scroller.scrollTop >= scroller.clientHeight || drawnIds().length === 250);
    """)


def test_a_switched_chat_draws_nothing_older_of_the_old_one():
    """selectChat() replaces state.messages before it redraws."""
    run_node_module(TRANSCRIPT_SETUP + r"""
        state.messages = chatOf(250);
        renderMessages();
        state.messages = chatOf(300);
        assert.equal(drawOlderMessages(), false);
        assert.equal(drawnIds().length, 100);
    """)


def test_separator_waits_for_its_message_to_be_drawn():
    """A window reaching past the drawn pages used to put the separator on the
    first drawn message, claiming everything above it was out of context."""
    run_node_module(TRANSCRIPT_SETUP + r"""
        el.settingsContextTokens.value = '4800';
        state.messages = chatOf(250);
        renderMessages();

        const first = getCurrentContextAnalysis().firstSelectedMessageId;
        assert.ok(first < 151, `the window should reach past the drawn page, starts at ${first}`);
        assert.equal(scroller.querySelector('.context-boundary'), null);

        // The separator lands in the page drawn above the view, and must not
        // push what the reader is looking at down by its own height.
        scroller.scrollTop = 0;
        const reading = scroller.querySelector('.message[data-msg-id="151"]');
        const before = reading.getBoundingClientRect().top;
        drawOlderNearTop();
        assert.equal(boundaryBefore(), first);
        assert.equal(reading.getBoundingClientRect().top, before);
    """)


def test_a_chat_that_fits_whole_draws_no_separator():
    """A separator above the greeting marked nothing but the top of the chat."""
    run_node_module(TRANSCRIPT_SETUP + r"""
        el.settingsContextTokens.value = '100000';
        state.messages = chatOf(30);
        renderMessages();

        assert.equal(getCurrentContextAnalysis().firstSelectedMessageId, 1);
        assert.equal(scroller.querySelector('.context-boundary'), null);
        assert.equal(jumpToContextBoundary(), false);
    """)


def test_jump_draws_down_to_an_undrawn_separator():
    run_node_module(TRANSCRIPT_SETUP + r"""
        el.settingsContextTokens.value = '4800';
        state.messages = chatOf(250);
        renderMessages();
        const first = getCurrentContextAnalysis().firstSelectedMessageId;

        assert.equal(jumpToContextBoundary(), true);
        assert.equal(boundaryBefore(), first);
        const boundary = scroller.querySelector('.context-boundary');
        // Landed just below the top edge, as for a drawn separator.
        assert.equal(boundary.getBoundingClientRect().top, 28);
    """)


def test_hidden_meter_leaves_the_draft_out_of_the_separator():
    """Nothing quotes a draft-inclusive count with the meter hidden, so the
    separator measures without it and typing has nothing to recompute."""
    run_node_module(TRANSCRIPT_SETUP + r"""
        el.settingsContextTokens.value = '600';
        state.messages = chatOf(30);
        let draftReads = 0;
        el.userInput = { get value() { draftReads += 1; return 'ramble '.repeat(60); } };
        // The meter itself, so that it draws rather than bailing before it analyses.
        Object.assign(el, {
            contextTokenMeter: Object.assign(new FakeNode(), { hidden: true }),
            contextTokenLabel: new FakeNode(),
            contextTokenBar: new FakeNode(),
        });
        renderMessages();

        state.showContextTokenMeter = false;
        draftReads = 0;
        updateContextViews();
        assert.equal(draftReads, 0);
        const undrafted = boundaryBefore();

        // Shown, the draft evicts older turns and the separator moves down with
        // them, from one draft-inclusive analysis shared with the meter.
        state.showContextTokenMeter = true;
        draftReads = 0;
        updateContextViews();
        assert.equal(draftReads, 1);
        assert.equal(el.contextTokenMeter.hidden, false, 'the meter drew from the shared analysis');
        assert.ok(boundaryBefore() > undrafted);
    """)


def test_a_draft_filling_the_window_puts_the_separator_after_the_last_message():
    """With no message of the chat in the window, the old fallback put the
    separator on the first drawn message, which paging made a moving target."""
    run_node_module(TRANSCRIPT_SETUP + r"""
        el.settingsContextTokens.value = '600';
        el.userInput = { value: 'ramble '.repeat(400) };
        state.showContextTokenMeter = true;
        state.messages = chatOf(250);
        renderMessages();

        assert.equal(getCurrentContextAnalysis({ includeDraft: true }).firstSelectedMessageId, null);
        assert.equal(scroller.children.at(-1).className, 'context-boundary');
        scroller.scrollTop = 0;
        drawOlderNearTop();
        assert.equal(scroller.children.at(-1).className, 'context-boundary');
    """)
