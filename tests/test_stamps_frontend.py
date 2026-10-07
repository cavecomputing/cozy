"""Dates as Cozy shows them: message stamps, unrenamed chat names, and how long ago."""

from helpers import run_node_module


SETUP = r"""
    import assert from 'node:assert/strict';
    import { shortStamp, fullStamp, parseDbStamp } from './static/js/utils.js';

    // Local times, so the day boundaries are the reader's own.
    const sent = new Date(2026, 9, 7, 0, 22);
"""


def test_today_shows_only_the_time():
    run_node_module(SETUP + r"""
        const stamp = shortStamp(sent, new Date(2026, 9, 7, 15, 0));
        assert.match(stamp, /22/);
        assert.doesNotMatch(stamp, /2026|7/);
    """)


def test_earlier_this_year_drops_the_year():
    run_node_module(SETUP + r"""
        // Yesterday by a few minutes is already another day.
        const stamp = shortStamp(new Date(2026, 9, 6, 23, 50), new Date(2026, 9, 7, 0, 5));
        assert.match(stamp, /6/);
        assert.match(stamp, /50/);
        assert.doesNotMatch(stamp, /2026/);
    """)


def test_an_earlier_year_shows_the_date_without_the_time():
    run_node_module(SETUP + r"""
        const stamp = shortStamp(sent, new Date(2027, 0, 2, 9, 0));
        assert.match(stamp, /2026/);
        assert.doesNotMatch(stamp, /22/);
    """)


def test_the_tooltip_keeps_everything():
    run_node_module(SETUP + r"""
        const full = fullStamp(sent);
        assert.match(full, /2026/);
        assert.match(full, /22/);
        // Database stamps are UTC without a zone marker.
        assert.equal(parseDbStamp('2026-10-07 00:22:00').getTime(), Date.UTC(2026, 9, 7, 0, 22));
    """)


def test_an_unrenamed_chat_reads_as_a_date():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { displayChatName } from './static/js/utils.js';

        // chatStamp()'s name, local time, from this year and from an earlier one.
        const thisYear = new Date().getFullYear();
        const recent = displayChatName({ name: `${thisYear}-10-07:00-19-59` });
        assert.match(recent, /19/);
        assert.doesNotMatch(recent, new RegExp(thisYear));
        assert.doesNotMatch(recent, /59/, 'seconds are left out');
        assert.match(displayChatName({ name: '2025-03-22:20-06-42' }), /2025/);

        // A name someone chose, and the retired stamp with no year to read, stay as they are.
        assert.equal(displayChatName({ name: 'Road trip' }), 'Road trip');
        assert.equal(displayChatName({ name: 'May 18 16:54:17' }), 'May 18 16:54:17');
    """)


def test_time_ago_picks_the_largest_unit_that_fits():
    run_node_module(r"""
        import assert from 'node:assert/strict';
        import { timeAgo } from './static/js/utils.js';

        const now = new Date(2026, 9, 7, 12, 0, 0);
        const ago = seconds => timeAgo(new Date(now - seconds * 1000), now);
        const hour = 3600, day = 24 * hour;
        // Pinned against the same formatter rather than English text.
        const expected = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

        assert.equal(ago(90), expected.format(-1, 'minute'));
        assert.equal(ago(2 * hour + 59 * 60), expected.format(-2, 'hour'));
        assert.equal(ago(day), expected.format(-1, 'day'));
        assert.equal(ago(10 * day), expected.format(-1, 'week'));
        assert.equal(ago(400 * day), expected.format(-1, 'year'));
        // A server clock running ahead is still "now", not the future.
        assert.equal(timeAgo(new Date(now.getTime() + 5000), now), expected.format(0, 'second'));
    """)
