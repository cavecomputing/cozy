"""Message stamps as Cozy shows them: short on the message, whole in its tooltip."""

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

