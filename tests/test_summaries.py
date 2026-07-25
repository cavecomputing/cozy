"""Tests for the Auto Summaries feature — pure summary logic, the background
summarizer worker/endpoints, per-chat persistence, settings masking, and the
startup recovery migration."""

from contextlib import contextmanager

import pytest

import shared
import routes.chats as chat_routes
from routes.settings import get_settings
import routes.summaries as summaries
from summarizer import (
    append_summary,
    build_append_messages,
    build_compress_messages,
    dump_summary_json,
    enforce_cap,
    estimate_tokens,
    merge_pins,
    parse_compressed_lines,
    parse_summary,
    parse_summary_json,
    parse_summarizer_output,
    pinned_texts,
    reinsert_pins_proportionally,
    strip_thinking_content,
    summary_to_text,
)


# ── Pure summarizer logic ───────────────────────────────────────────────────

def test_parse_summary_roundtrip():
    text = (
        "STORY SO FAR\n"
        "- Nell pulled Luna from the sea during a storm.\n"
        "- They sailed the Widow's Teeth.\n"
        "\n"
        "BONDS\n"
        "- Luna & Nell: deep trust, turning romantic."
    )
    obj = parse_summary(text)
    sections = [l['section'] for l in obj['lines']]
    assert sections == ['story', 'story', 'bonds']
    # Rendering back produces both headings and all three bullets.
    rendered = summary_to_text(obj)
    assert 'STORY SO FAR' in rendered and 'BONDS' in rendered
    assert rendered.count('\n- ') + rendered.count('- ', 0, 20) >= 1
    assert len([l for l in rendered.splitlines() if l.startswith('- ')]) == 3


def test_parse_summary_strips_various_bullets_and_decorated_headings():
    obj = parse_summary("**STORY SO FAR**\n* first\n• second\n[BONDS]\n- a & b: allies")
    assert [l['text'] for l in obj['lines']] == ['first', 'second', 'a & b: allies']
    assert obj['lines'][-1]['section'] == 'bonds'


def test_enforce_cap_keeps_pins_and_bonds():
    obj = {'lines': [
        {'section': 'story', 'text': 'x' * 400, 'pinned': False},
        {'section': 'story', 'text': 'pinned story ' * 5, 'pinned': True},
        {'section': 'bonds', 'text': 'A & B: allies ' * 5, 'pinned': False},
    ]}
    # Cap large enough for the pinned line + bond, but not the ~100-token story line.
    capped, warning = enforce_cap(obj, 60)
    texts = [l['text'] for l in capped['lines']]
    # The long unpinned story line is dropped first; pinned + bonds survive.
    assert not any(t == 'x' * 400 for t in texts)
    assert any(l.get('pinned') for l in capped['lines'])
    assert any(l['section'] == 'bonds' for l in capped['lines'])


def test_enforce_cap_pins_over_cap_warns():
    obj = {'lines': [
        {'section': 'story', 'text': 'pinned ' * 50, 'pinned': True},
    ]}
    capped, warning = enforce_cap(obj, 5)
    assert capped['lines'] and capped['lines'][0]['pinned']  # pin kept
    assert warning  # warned that pins exceed the cap


def test_enforce_cap_noop_when_under():
    obj = {'lines': [{'section': 'story', 'text': 'short', 'pinned': False}]}
    capped, warning = enforce_cap(obj, 1000)
    assert capped['lines'] == obj['lines'] and not warning


def test_estimate_tokens_matches_heuristic():
    # max(words*1.3, chars/4): "a b c d" -> words 4*1.3=5.2 -> ceil 6
    assert estimate_tokens('a b c d') == 6
    assert estimate_tokens('') == 0


def test_merge_pins_restores_dropped_pinned_line():
    prev = {'lines': [{'section': 'bonds', 'text': 'Luna & Nell: trust', 'pinned': True}]}
    fresh = parse_summary('STORY SO FAR\n- new event')
    merged = merge_pins(fresh, prev)
    assert any(l['pinned'] and l['text'] == 'Luna & Nell: trust' for l in merged['lines'])


def test_merge_pins_restores_a_story_pin_to_its_chronological_slot():
    """STORY is a timeline: a recovered beat must not land after newer ones."""
    prev = {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},
        {'section': 'story', 'text': 'PINNED', 'pinned': True},
        {'section': 'story', 'text': 'S2', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: allies', 'pinned': False},
    ]}
    # The model dropped the pinned beat but kept everything around it.
    fresh = parse_summary('STORY SO FAR\n- S1\n- S2\n\nBONDS\n- A & B: allies')
    merged = merge_pins(fresh, prev)
    assert [l['text'] for l in merged['lines']] == ['S1', 'PINNED', 'S2', 'A & B: allies']


def test_merge_pins_restores_a_leading_story_pin_before_later_beats():
    prev = {'lines': [
        {'section': 'story', 'text': 'PINNED', 'pinned': True},
        {'section': 'story', 'text': 'S1', 'pinned': False},
    ]}
    fresh = parse_summary('STORY SO FAR\n- S1')
    merged = merge_pins(fresh, prev)
    assert [l['text'] for l in merged['lines']] == ['PINNED', 'S1']


def test_parse_summary_json_tolerates_junk():
    assert parse_summary_json('not json') == {'lines': []}
    assert parse_summary_json('')['lines'] == []
    good = dump_summary_json({'lines': [{'section': 'bonds', 'text': 'a', 'pinned': True}]})
    assert parse_summary_json(good)['lines'][0]['pinned'] is True


def test_build_compress_messages_numbers_only_the_batch():
    msgs = build_compress_messages(['first beat', 'second beat'])
    assert msgs[0]['role'] == 'system'
    body = msgs[1]['content']
    # The batch is numbered so the model has an explicit order to preserve...
    assert '1. first beat' in body and '2. second beat' in body
    # ...and nothing else travels with it: no running summary, no chat messages.
    assert 'CURRENT SUMMARY' not in body and 'NEW MESSAGES' not in body


@pytest.mark.parametrize('reply,count,expected', [
    ('- merged', 3, ['merged']),
    ('- one\n- two', 3, ['one', 'two']),
    ('1. numbered anyway', 2, ['numbered anyway']),
    ('STORY SO FAR\n- stray heading ignored', 2, ['stray heading ignored']),
])
def test_parse_compressed_lines_accepts_a_shrunk_batch(reply, count, expected):
    assert parse_compressed_lines(reply, count) == expected


@pytest.mark.parametrize('reply,count', [
    ('', 3),                        # nothing usable
    ('   \n```\n```', 3),           # fences only
    ('- a\n- b\n- c', 3),           # unchanged line count: no compression
    ('- a\n- b\n- c\n- d', 3),      # grew: not a compression
])
def test_parse_compressed_lines_rejects_unusable_replies(reply, count):
    with pytest.raises(ValueError):
        parse_compressed_lines(reply, count)


def test_reinsert_pins_proportionally_places_by_relative_position():
    story = [{'section': 'story', 'text': t, 'pinned': False} for t in 'abcdefghij']
    held = [
        (0.0, {'section': 'story', 'text': 'EARLY', 'pinned': True}),
        (0.9, {'section': 'story', 'text': 'LATE', 'pinned': True}),
    ]
    out = [l['text'] for l in reinsert_pins_proportionally(story, held)]
    assert out[0] == 'EARLY'
    assert out.index('LATE') > out.index('i')


def test_reinsert_pins_reuses_an_identical_regenerated_line():
    story = [{'section': 'story', 'text': 'SAME BEAT', 'pinned': False}]
    held = [(0.5, {'section': 'story', 'text': 'SAME BEAT', 'pinned': True})]

    out = reinsert_pins_proportionally(story, held)

    assert out == [{'section': 'story', 'text': 'SAME BEAT', 'pinned': True}]


# ── Append-mode pure logic ──────────────────────────────────────────────────

def test_append_summary_accumulates_dedups_and_replaces_bonds():
    prev = {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: wary allies', 'pinned': False},
    ]}
    reply = {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},   # duplicate — dropped
        {'section': 'story', 'text': 'S2', 'pinned': False},   # new — appended
        {'section': 'bonds', 'text': 'A & B: firm allies', 'pinned': False},  # replaces
    ]}
    out = append_summary(prev, reply)
    story = [l['text'] for l in out['lines'] if l['section'] == 'story']
    bonds = [l['text'] for l in out['lines'] if l['section'] == 'bonds']
    assert story == ['S1', 'S2']              # accumulates, no duplicate
    assert bonds == ['A & B: firm allies']    # bonds section replaced, not doubled
    # Story block precedes bonds block (matches summary_to_text ordering).
    sections = [l['section'] for l in out['lines']]
    assert sections == ['story', 'story', 'bonds']
    # Inputs are not mutated.
    assert [l['text'] for l in prev['lines']] == ['S1', 'A & B: wary allies']


def test_append_summary_first_batch_from_empty():
    out = append_summary({'lines': []}, parse_summary('STORY SO FAR\n- first\n\nBONDS\n- A & B: allies'))
    assert [l['text'] for l in out['lines']] == ['first', 'A & B: allies']


def test_append_summary_keeps_previous_bonds_when_reply_has_none():
    """A reply with an empty BONDS section must not erase relationship state."""
    prev = {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: wary allies', 'pinned': False},
        {'section': 'bonds', 'text': 'A & C: owes a debt', 'pinned': True},
    ]}
    # parse_summarizer_output accepts a bare BONDS heading with no bullets.
    reply = parse_summarizer_output('STORY SO FAR\n- S2\n\nBONDS')
    out = append_summary(prev, reply)
    bonds = [l['text'] for l in out['lines'] if l['section'] == 'bonds']
    assert bonds == ['A & B: wary allies', 'A & C: owes a debt']
    assert [l['text'] for l in out['lines'] if l['section'] == 'story'] == ['S1', 'S2']
    # Carried-forward lines keep their pin state.
    assert [l['pinned'] for l in out['lines'] if l['section'] == 'bonds'] == [False, True]


def test_build_append_messages_asks_for_new_story_only():
    from summarizer import APPEND_INSTRUCTIONS
    msgs = build_append_messages(
        'PREV', [{'role': 'user', 'content': 'hi there'}], ['keep me'], 3000)
    assert msgs[0]['content'] == APPEND_INSTRUCTIONS
    user = msgs[1]['content']
    assert 'PREV' in user and 'keep me' in user and 'hi there' in user and '3000' in user
    assert 'NEW story bullets' in user and 'BONDS' in user
    assert 'Rewrite the running summary' not in user  # not the compress builder


@pytest.mark.parametrize('used_pct, cap, expected', [
    (0.5, 1000, True),    # comfortably below the 80% line
    (0.95, 1000, False),  # above the line → compress
    (2.0, 0, True),       # unlimited context → always append
])
def test_should_append_threshold(used_pct, cap, expected):
    # Build a story whose token estimate is ~used_pct of cap (chars/4 heuristic).
    target_tokens = int((cap or 1000) * used_pct)
    obj = {'lines': [{'section': 'story', 'text': 'x' * (target_tokens * 4), 'pinned': False}]}
    assert summaries._should_append(obj, cap) is expected


def test_parse_summarizer_output_rejects_empty_and_malformed_content():
    for content in ('', 'just some prose', 'STORY SO FAR\n\nBONDS'):
        with pytest.raises(ValueError):
            parse_summarizer_output(content)


def test_parse_summarizer_output_strips_reasoning_block():
    obj = parse_summarizer_output(
        '<think>private chain of thought</think>\n'
        'STORY SO FAR\n- visible event\n\nBONDS\n- A & B: allies'
    )
    assert 'private chain of thought' not in summary_to_text(obj)
    assert 'visible event' in summary_to_text(obj)


def test_strip_thinking_content_handles_multiple_and_incomplete_blocks():
    assert strip_thinking_content(
        'before<think>one</think>middle<thinking>two</thinking>after'
    ) == 'beforemiddleafter'
    assert strip_thinking_content('visible<|thinking|>unfinished') == 'visible'


def test_enforce_cap_never_erases_a_nonempty_summary():
    capped, warning = enforce_cap({
        'lines': [{'section': 'story', 'text': 'important context ' * 20, 'pinned': False}],
    }, 1)
    assert capped['lines'][0]['text']
    assert warning


# ── Helpers for endpoint/worker tests ───────────────────────────────────────

def _add_messages(client, chat_id, n):
    ids = []
    for i in range(n):
        role = 'user' if i % 2 == 0 else 'character'
        r = client.post(f'/api/chats/{chat_id}/messages', json={'role': role, 'content': f'msg {i}'})
        assert r.status_code in (200, 201), r.get_data(as_text=True)
        ids.append(r.get_json()['id'])
    return ids


def _store_summary(chat_id, summary_obj, watermark=None):
    """Seed server-owned summary state without using the public chat patch route."""
    with shared.get_db() as conn:
        conn.execute(
            'UPDATE chats SET summary_json=?, summary_up_to_msg_id=? WHERE id=?',
            (dump_summary_json(summary_obj), watermark, chat_id),
        )


CANNED = "STORY SO FAR\n- Something happened.\n\nBONDS\n- A & B: close allies."


# ── The worker ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('body', [
    {},
    {'choices': []},
    {'choices': [{}]},
    {'choices': [{'message': {'content': ''}}]},
    {'choices': [{'message': {'content': None}}]},
])
def test_call_summarizer_rejects_malformed_completion_body(client, monkeypatch, body):
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://summarizer',
        'summary_api_model': 'summary-model',
    })

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return body

    monkeypatch.setattr(summaries.http_requests, 'post', lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError):
        summaries.call_summarizer([{'role': 'user', 'content': 'summarize'}])


@pytest.mark.parametrize(('cap_tokens', 'expected'), [(50, 512), (500, 625), (4000, 5000)])
def test_call_summarizer_bounds_single_response_with_headroom(
        client, monkeypatch, cap_tokens, expected):
    """The completion stays bounded but keeps headroom over the cap: a
    compress-mode overshoot must hit enforce_cap's oldest-first trim, not the
    provider's newest-first truncation."""
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://summarizer',
        'summary_api_model': 'summary-model',
    })
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': CANNED}}]}

    def fake_post(*args, **kwargs):
        captured.update(kwargs['json'])
        return FakeResponse()

    monkeypatch.setattr(summaries.http_requests, 'post', fake_post)

    assert summaries.call_summarizer(
        [{'role': 'user', 'content': 'summarize'}], cap_tokens
    ) == CANNED
    assert captured['max_tokens'] == expected


def test_call_summarizer_rejects_length_truncated_completion(client, monkeypatch):
    """finish_reason == 'length' means chopped memory; it must fail the batch
    rather than being parsed (a cut mid-bullet still parses cleanly)."""
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://summarizer',
        'summary_api_model': 'summary-model',
    })

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{
                'finish_reason': 'length',
                'message': {'content': 'STORY SO FAR\n- Beat.\n\nBONDS\n- A & B: allies bound by the'},
            }]}

    monkeypatch.setattr(summaries.http_requests, 'post', lambda *a, **k: FakeResponse())

    with pytest.raises(RuntimeError, match='cut off'):
        summaries.call_summarizer([{'role': 'user', 'content': 'summarize'}], 500)


def test_run_job_folds_and_advances_watermark(client, sample_chat, monkeypatch):
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(messages) or CANNED)
    ids = _add_messages(client, sample_chat['id'], 5)

    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT * FROM chats WHERE id=?', (sample_chat['id'],)).fetchone()
    assert row['summary_status'] == 'idle'
    assert row['summary_up_to_msg_id'] == ids[-1]
    obj = parse_summary_json(row['summary_json'])
    assert any(l['section'] == 'bonds' for l in obj['lines'])
    assert len(calls) >= 1


def test_run_job_batches_by_interval(client, sample_chat, monkeypatch):
    client.put('/api/settings', json={'summary_trigger_interval': '2'})
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(1) or CANNED)
    ids = _add_messages(client, sample_chat['id'], 5)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    # ceil(5 / 2) == 3 batches
    assert len(calls) == 3


def test_run_job_incremental(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    ids = _add_messages(client, sample_chat['id'], 3)
    summaries._run_summary_job(sample_chat['id'], ids[1], rebuild=False)
    with shared.get_db() as conn:
        wm1 = conn.execute('SELECT summary_up_to_msg_id FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()[0]
    assert wm1 == ids[1]
    # Second run only folds messages after the watermark.
    seen = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: seen.append(messages) or CANNED)
    summaries._run_summary_job(sample_chat['id'], ids[2], rebuild=False)
    with shared.get_db() as conn:
        wm2 = conn.execute('SELECT summary_up_to_msg_id FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()[0]
    assert wm2 == ids[2]
    assert 'msg 2' in seen[-1][1]['content']  # only the new message went in


def test_run_job_error_sets_error_status(client, sample_chat, monkeypatch):
    def boom(messages, cap_tokens=0):
        raise RuntimeError('summarizer exploded')
    monkeypatch.setattr(summaries, 'call_summarizer', boom)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_status, summary_status_detail FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    assert row['summary_status'] == 'error'
    assert 'exploded' in row['summary_status_detail']


@pytest.mark.parametrize('reply', [
    '',
    'not the requested format',
    'STORY SO FAR\n\nBONDS',
])
def test_run_job_rejects_invalid_model_content_without_advancing(
        client, sample_chat, monkeypatch, reply):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: reply)
    ids = _add_messages(client, sample_chat['id'], 2)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (sample_chat['id'],),
        ).fetchone()
    assert row['summary_json'] == ''
    assert row['summary_up_to_msg_id'] is None
    assert row['summary_status'] == 'error'


def test_run_job_caps_overlong_summary_without_second_call(client, sample_chat, monkeypatch):
    client.put('/api/settings', json={
        'context_max_tokens': '500',
        'summary_cap_pct': '10',
    })
    over_cap = f"STORY SO FAR\n- {'important context ' * 100}\n\nBONDS\n- A & B: trust"
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return over_cap

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1])

    assert len(calls) == 1
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (sample_chat['id'],),
        ).fetchone()
    stored = summary_to_text(parse_summary_json(row['summary_json']))
    assert estimate_tokens(stored) <= 50
    assert row['summary_up_to_msg_id'] == ids[-1]


def test_append_mode_accumulates_story_across_batches(client, sample_chat, monkeypatch):
    """The core fix: consecutive batches ADD story beats instead of compressing."""
    from summarizer import APPEND_INSTRUCTIONS
    client.put('/api/settings', json={'summary_trigger_interval': '1'})
    replies = iter((
        'STORY SO FAR\n- S1 happened\n\nBONDS\n- A & B: allies',
        'STORY SO FAR\n- S2 happened\n\nBONDS\n- A & B: allies',
    ))
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    story = summary_to_text(parse_summary_json(row['summary_json']))
    assert 'S1 happened' in story and 'S2 happened' in story  # additive, both kept
    # Both batches used the additive builder while under cap.
    assert len(calls) == 2
    assert all(m[0]['content'] == APPEND_INSTRUCTIONS for m in calls)


def test_near_cap_compresses_first_then_appends(client, sample_chat, monkeypatch):
    """Near the cap, a run makes room by compressing existing story lines and THEN
    folds the new messages in additively — rather than one call doing both."""
    from summarizer import APPEND_INSTRUCTIONS, COMPRESS_INSTRUCTIONS
    # cap ≈ 40 tokens: big enough that the compressed result survives enforce_cap,
    # small enough that the seeded summary below is already over 0.8 × cap.
    client.put('/api/settings', json={'context_max_tokens': '400', 'summary_cap_pct': '10'})
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        if messages[0]['content'] == COMPRESS_INSTRUCTIONS:
            return '- merged beat'
        return 'STORY SO FAR\n- brand new\n\nBONDS\n- A & B: allies'

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    # Pre-seed a summary well over 0.8 * cap (cap ≈ 10 tokens here) with enough
    # unpinned story lines to form one compression batch.
    _store_summary(sample_chat['id'], {'lines': [
        {'section': 'story', 'text': 'x' * 80, 'pinned': False},
        {'section': 'story', 'text': 'y' * 80, 'pinned': False},
        {'section': 'story', 'text': 'z' * 80, 'pinned': False},
    ]}, watermark=ids[0])
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    modes = [m[0]['content'] for m in calls]
    assert COMPRESS_INSTRUCTIONS in modes and APPEND_INSTRUCTIONS in modes
    assert modes.index(COMPRESS_INSTRUCTIONS) < modes.index(APPEND_INSTRUCTIONS)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    story = summary_to_text(parse_summary_json(row['summary_json']))
    assert 'merged beat' in story      # the three old lines collapsed into one
    assert 'brand new' in story        # and the batch still got folded in


def test_compression_keeps_pins_and_leaves_bonds_alone(client, sample_chat, monkeypatch):
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages[1]['content'])
        return '- merged'

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    obj = {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},
        {'section': 'story', 'text': 'S2', 'pinned': False},
        {'section': 'story', 'text': 'KEEP', 'pinned': True},
        {'section': 'story', 'text': 'S3', 'pinned': False},
        {'section': 'story', 'text': 'S4', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: allies', 'pinned': False},
    ]}
    out = summaries._compress_story(sample_chat['id'], obj, 3)
    texts = [l['text'] for l in out['lines']]

    # The pin splits the run, so two batches ran and neither saw the pinned text.
    assert len(calls) == 2
    assert all('KEEP' not in body for body in calls)
    # Pin stays in position, bonds untouched, order preserved.
    assert texts == ['merged', 'KEEP', 'merged', 'A & B: allies']


def test_compression_aborts_the_pass_when_a_batch_reply_is_unusable(
    client, sample_chat, monkeypatch
):
    """A bad later reply discards earlier in-memory splices from the same pass."""
    replies = iter(('- merged', '- a\n- b\n- c\n- d'))   # second grew: rejected

    def complete(messages, cap_tokens=0):
        return next(replies)

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    obj = {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},
        {'section': 'story', 'text': 'S2', 'pinned': False},
        {'section': 'story', 'text': 'PIN', 'pinned': True},
        {'section': 'story', 'text': 'S3', 'pinned': False},
        {'section': 'story', 'text': 'S4', 'pinned': False},
    ]}
    with pytest.raises(RuntimeError, match=r'Compression batch 2/2 failed'):
        summaries._compress_story(sample_chat['id'], obj, 3)
    assert [l['text'] for l in obj['lines']] == ['S1', 'S2', 'PIN', 'S3', 'S4']


def test_append_overflow_is_trimmed_without_second_call(client, sample_chat, monkeypatch):
    """An additive reply that overshoots the cap is trimmed locally."""
    from summarizer import APPEND_INSTRUCTIONS
    client.put('/api/settings', json={'context_max_tokens': '500', 'summary_cap_pct': '10'})
    over_cap = f"STORY SO FAR\n- {'sprawling detail ' * 100}\n\nBONDS\n- A & B: allies"
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return over_cap

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    assert len(calls) == 1
    assert calls[0][0]['content'] == APPEND_INSTRUCTIONS
    with shared.get_db() as conn:
        raw = conn.execute(
            'SELECT summary_json FROM chats WHERE id=?', (sample_chat['id'],)
        ).fetchone()[0]
    assert estimate_tokens(summary_to_text(parse_summary_json(raw))) <= 50


def test_append_mode_merges_bonds_without_duplicating(client, sample_chat, monkeypatch):
    """Append mode replaces the BONDS section, so a relationship stays a single line."""
    monkeypatch.setattr(
        summaries, 'call_summarizer',
        lambda messages, cap_tokens=0: 'STORY SO FAR\n- new beat\n\nBONDS\n- A & B: close now')
    ids = _add_messages(client, sample_chat['id'], 2)
    _store_summary(sample_chat['id'], {'lines': [
        {'section': 'story', 'text': 'earlier beat', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: uneasy', 'pinned': False},
    ]}, watermark=ids[0])
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    obj = parse_summary_json(row['summary_json'])
    bonds = [l['text'] for l in obj['lines'] if l['section'] == 'bonds']
    story = [l['text'] for l in obj['lines'] if l['section'] == 'story']
    assert bonds == ['A & B: close now']            # merged, not duplicated
    assert story == ['earlier beat', 'new beat']    # story accumulated


def test_append_mode_survives_reply_with_empty_bonds_section(client, sample_chat, monkeypatch):
    """A batch whose reply omits BONDS bullets keeps the stored relationship state."""
    monkeypatch.setattr(
        summaries, 'call_summarizer',
        lambda messages, cap_tokens=0: 'STORY SO FAR\n- new beat\n\nBONDS')
    ids = _add_messages(client, sample_chat['id'], 2)
    _store_summary(sample_chat['id'], {'lines': [
        {'section': 'story', 'text': 'earlier beat', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: uneasy', 'pinned': False},
    ]}, watermark=ids[0])
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json, summary_status FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    obj = parse_summary_json(row['summary_json'])
    assert row['summary_status'] == 'idle'
    assert [l['text'] for l in obj['lines'] if l['section'] == 'bonds'] == ['A & B: uneasy']
    assert [l['text'] for l in obj['lines'] if l['section'] == 'story'] == ['earlier beat', 'new beat']


def test_run_job_strips_hidden_thinking_when_disabled(client, sample_chat, monkeypatch):
    client.put('/api/settings', json={'send_thinking': '0'})
    response = client.post(
        f'/api/chats/{sample_chat["id"]}/messages',
        json={
            'role': 'character',
            'content': '<think>private reasoning</think>The visible answer.',
        },
    )
    message_id = response.get_json()['id']
    calls = []
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda messages, cap_tokens=0: calls.append(messages) or CANNED,
    )

    summaries._run_summary_job(sample_chat['id'], message_id)

    prompt = calls[0][1]['content']
    assert 'private reasoning' not in prompt
    assert 'The visible answer.' in prompt


def test_thinking_only_chunk_advances_without_model_call(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    original = {'lines': [
        {'section': 'story', 'text': 'Existing visible memory.', 'pinned': False},
    ]}
    _store_summary(cid, original)
    response = client.post(f'/api/chats/{cid}/messages', json={
        'role': 'character',
        'content': '<think>reasoning that should stay hidden</think>',
    })
    message_id = response.get_json()['id']
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda *args, **kwargs: pytest.fail('thinking-only history must not call the model'),
    )

    summaries._run_summary_job(cid, message_id)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == original
    assert row['summary_up_to_msg_id'] == message_id
    assert row['summary_status'] == 'idle'


def test_thinking_only_chunk_is_summarized_when_thinking_is_enabled(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    client.put('/api/settings', json={'send_thinking': '1'})
    response = client.post(f'/api/chats/{cid}/messages', json={
        'role': 'character', 'content': '<think>included reasoning</think>',
    })
    message_id = response.get_json()['id']
    calls = []
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda messages, cap_tokens=0: calls.append(messages) or CANNED,
    )

    summaries._run_summary_job(cid, message_id)

    assert len(calls) == 1
    assert 'included reasoning' in calls[0][1]['content']


def test_thinking_only_rebuild_publishes_pins_without_model_call(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    original = {'lines': [
        {'section': 'story', 'text': 'Old unpinned detail.', 'pinned': False},
        {'section': 'bonds', 'text': 'Exact pinned bond.', 'pinned': True},
    ]}
    _store_summary(cid, original)
    response = client.post(f'/api/chats/{cid}/messages', json={
        'role': 'character', 'content': '<thinking>hidden only</thinking>',
    })
    message_id = response.get_json()['id']
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda *args, **kwargs: pytest.fail('thinking-only rebuild must not call the model'),
    )

    summaries._run_summary_job(cid, message_id, rebuild=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == {'lines': [original['lines'][1]]}
    assert row['summary_up_to_msg_id'] == message_id


def test_pause_discards_inflight_result_and_keeps_last_checkpoint(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    client.put('/api/settings', json={'summary_trigger_interval': '1'})
    ids = _add_messages(client, cid, 2)
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            'WHERE id=?',
            (cid,),
        )
    calls = 0

    def pause_on_second_call(messages, cap_tokens=0):
        nonlocal calls
        calls += 1
        if calls == 2:
            client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', pause_on_second_call)

    summaries._run_summary_job(cid, ids[-1], require_running=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert calls == 2
    assert row['summary_up_to_msg_id'] == ids[0]
    assert parse_summary_json(row['summary_json'])['lines']
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''


def test_per_chat_pause_during_rebuild_preserves_previous_state(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    ids = _add_messages(client, cid, 2)
    original = {'lines': [
        {'section': 'story', 'text': 'Complete previous memory.', 'pinned': False},
    ]}
    _store_summary(cid, original, ids[-1])
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            'WHERE id=?',
            (cid,),
        )

    def disable_during_call(messages, cap_tokens=0):
        response = client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        assert response.status_code == 200
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', disable_during_call)
    summaries._run_summary_job(cid, ids[-1], rebuild=True, require_running=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == original
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''


def test_disable_reenable_new_run_cannot_revive_old_worker(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    message_id = _add_messages(client, cid, 1)[0]
    spawned_tokens = []

    def capture_spawn(chat_id, up_to_msg_id, rebuild, job_token, compress_only=False):
        assert chat_id == cid and up_to_msg_id == message_id
        spawned_tokens.append(job_token)

    monkeypatch.setattr(summaries, '_spawn_job', capture_spawn)
    first = client.post(
        f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
    )
    assert first.status_code == 202
    old_token = spawned_tokens[0]

    def replace_run_during_old_call(messages, cap_tokens=0):
        paused = client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        assert paused.get_json()['summary_status'] == 'idle'
        client.put(f'/api/chats/{cid}', json={'summary_enabled': True})

        replacement = client.post(
            f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
        )
        assert replacement.status_code == 202
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', replace_run_during_old_call)
    summaries._run_summary_job(
        cid,
        message_id,
        require_running=True,
        job_token=old_token,
    )

    assert len(spawned_tokens) == 2
    assert spawned_tokens[1] != old_token
    assert summaries._job_tokens.get(cid) == spawned_tokens[1]
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == {'lines': []}
    assert row['summary_up_to_msg_id'] is None
    assert row['summary_status'] == 'running'
    assert row['summary_status_detail'] == 'Starting…'


@pytest.mark.parametrize('terminal_event', ['pause', 'delete'])
def test_terminal_worker_releases_current_generation_when_status_cannot_change(
        client, sample_chat, monkeypatch, terminal_event):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    message_id = _add_messages(client, cid, 1)[0]
    spawned_tokens = []

    monkeypatch.setattr(
        summaries,
        '_spawn_job',
        lambda chat_id, up_to_msg_id, rebuild, job_token, *a: spawned_tokens.append(job_token),
    )
    started = client.post(
        f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
    )
    assert started.status_code == 202
    job_token = spawned_tokens[0]
    assert summaries._job_tokens.get(cid) == job_token

    if terminal_event == 'pause':
        paused = client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        assert paused.status_code == 200
    else:
        deleted = client.delete(f'/api/chats/{cid}')
        assert deleted.status_code == 200

    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda *args, **kwargs: pytest.fail('paused/deleted worker must not call the model'),
    )
    summaries._run_summary_job(
        cid,
        message_id,
        require_running=True,
        job_token=job_token,
    )

    assert cid not in summaries._job_tokens


def test_run_job_rebuild_resets(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    ids = _add_messages(client, sample_chat['id'], 3)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    # Rebuild folds from scratch and still ends at the same watermark.
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=True)
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_up_to_msg_id, summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert parse_summary_json(row['summary_json'])['lines']


def test_rebuild_keeps_previous_state_until_all_batches_succeed(
        client, sample_chat, monkeypatch):
    ids = _add_messages(client, sample_chat['id'], 3)
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    summaries._run_summary_job(sample_chat['id'], ids[-1])
    with shared.get_db() as conn:
        before = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (sample_chat['id'],),
        ).fetchone()
        old_json, old_watermark = before['summary_json'], before['summary_up_to_msg_id']

    client.put('/api/settings', json={'summary_trigger_interval': '1'})
    calls = 0

    def fail_second_batch(messages, cap_tokens=0):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('second batch failed')
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', fail_second_batch)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=True)

    with shared.get_db() as conn:
        after = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (sample_chat['id'],),
        ).fetchone()
    assert after['summary_json'] == old_json
    assert after['summary_up_to_msg_id'] == old_watermark
    assert after['summary_status'] == 'error'


def test_rebuild_preserves_pins_and_publishes_only_after_completion(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    original = {'lines': [
        {'section': 'story', 'text': 'Old generated detail.', 'pinned': False},
        {'section': 'bonds', 'text': 'Keep this bond exactly.', 'pinned': True},
    ]}
    _store_summary(cid, original)
    ids = _add_messages(client, cid, 2)
    observed_during_call = []

    def complete(messages, cap_tokens=0):
        with shared.get_db() as conn:
            raw = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()[0]
        observed_during_call.append(parse_summary_json(raw))
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    summaries._run_summary_job(cid, ids[-1], rebuild=True)

    assert observed_during_call[0] == original
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()
    rebuilt = parse_summary_json(row['summary_json'])
    assert any(
        line['text'] == 'Keep this bond exactly.' and line['pinned']
        for line in rebuilt['lines']
    )


# ── Endpoints ───────────────────────────────────────────────────────────────

def test_run_endpoint_overlap_guard(client, sample_chat, monkeypatch):
    # Stub the thread spawn so the claimed 'running' state persists for the test.
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    client.put(f'/api/chats/{sample_chat["id"]}', json={'summary_enabled': True})
    ids = _add_messages(client, sample_chat['id'], 2)
    r1 = client.post(f'/api/chats/{sample_chat["id"]}/summary/run', json={'up_to_msg_id': ids[-1]})
    assert r1.status_code == 202
    assert r1.get_json()['summary_status'] == 'running'
    # A second run while one is in flight is a no-op.
    r2 = client.post(f'/api/chats/{sample_chat["id"]}/summary/run', json={'up_to_msg_id': ids[-1]})
    assert r2.status_code == 409
    assert r2.get_json()['already_running'] is True


@pytest.mark.parametrize('boundary', [None, 0, -1, '1', True])
def test_run_endpoint_requires_positive_integer_boundary(
        client, sample_chat, monkeypatch, boundary):
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': boundary},
    )
    assert response.status_code == 400
    with shared.get_db() as conn:
        status = conn.execute(
            'SELECT summary_status FROM chats WHERE id=?', (sample_chat['id'],)
        ).fetchone()[0]
    assert status == 'idle'


def test_run_endpoint_rejects_boundary_from_another_chat(
        client, sample_chat, sample_character, monkeypatch):
    other = client.post(
        f'/api/characters/{sample_character["id"]}/chats',
        json={'name': 'Other chat'},
    ).get_json()
    other_message = _add_messages(client, other['id'], 1)[0]
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)

    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': other_message},
    )

    assert response.status_code == 400
    assert 'this chat' in response.get_json()['error']


def test_run_endpoint_honors_per_chat_feature_gate(client, sample_chat, monkeypatch):
    message_id = _add_messages(client, sample_chat['id'], 1)[0]
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)

    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': message_id},
    )

    assert response.status_code == 409
    assert 'this chat' in response.get_json()['error']


def test_run_endpoint_missing_chat(client):
    r = client.post('/api/chats/999999/summary/run', json={'up_to_msg_id': 1})
    assert r.status_code == 404


def test_reset_endpoint_clears_summary_and_watermark(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    ids = _add_messages(client, sample_chat['id'], 3)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    # Pin a line so we can confirm pins are discarded too.
    _store_summary(sample_chat['id'], {
        'lines': [{'section': 'bonds', 'text': 'keep?', 'pinned': True}],
    }, ids[-1])

    r = client.post(f'/api/chats/{sample_chat["id"]}/summary/reset')
    assert r.status_code == 200
    body = r.get_json()
    assert body['summary'] == {'lines': []}
    assert body['summary_up_to_msg_id'] is None
    assert body['summary_status'] == 'idle'
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    assert row['summary_json'] == '' and row['summary_up_to_msg_id'] is None


def test_reset_endpoint_rejects_while_summary_is_running(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    client.put(f'/api/chats/{sample_chat["id"]}', json={'summary_enabled': True})
    message_id = _add_messages(client, sample_chat['id'], 1)[0]
    started = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': message_id},
    )
    assert started.status_code == 202

    response = client.post(f'/api/chats/{sample_chat["id"]}/summary/reset')

    assert response.status_code == 409
    assert response.get_json()['summary_status'] == 'running'


def test_reset_endpoint_missing_chat(client):
    assert client.post('/api/chats/999999/summary/reset').status_code == 404


def test_status_endpoint(client, sample_chat):
    r = client.get(f'/api/chats/{sample_chat["id"]}/summary/status')
    assert r.status_code == 200
    body = r.get_json()
    assert body['summary_status'] == 'idle'
    assert body['summary'] == {'lines': []}


def test_pin_endpoint_updates_only_the_requested_line(client, sample_chat):
    cid = sample_chat['id']
    summary_obj = {'lines': [
        {'section': 'story', 'text': 'A plot beat.', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: allies.', 'pinned': False},
    ]}
    _store_summary(cid, summary_obj)

    response = client.put(f'/api/chats/{cid}/summary/pins', json={
        'section': 'bonds',
        'text': 'A & B: allies.',
        'pinned': True,
    })

    assert response.status_code == 200
    lines = response.get_json()['summary']['lines']
    assert lines[0]['pinned'] is False
    assert lines[1]['pinned'] is True


def test_pin_endpoint_rejects_stale_or_invalid_identity(client, sample_chat):
    cid = sample_chat['id']
    response = client.put(f'/api/chats/{cid}/summary/pins', json={
        'section': 'story',
        'text': 'No longer present',
        'pinned': True,
    })
    assert response.status_code == 409

    response = client.put(f'/api/chats/{cid}/summary/pins', json={
        'section': 'other',
        'text': 'No longer present',
        'pinned': 'yes',
    })
    assert response.status_code == 400


def test_worker_reconciles_pin_changed_during_model_call(client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    text = 'Preserve this exact shared moment.'
    _store_summary(cid, {'lines': [
        {'section': 'bonds', 'text': text, 'pinned': False},
    ]})
    message_id = _add_messages(client, cid, 1)[0]

    def complete(messages, cap_tokens=0):
        response = client.put(f'/api/chats/{cid}/summary/pins', json={
            'section': 'bonds', 'text': text, 'pinned': True,
        })
        assert response.status_code == 200
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    summaries._run_summary_job(cid, message_id)

    with shared.get_db() as conn:
        raw = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()[0]
    obj = parse_summary_json(raw)
    assert any(line['text'] == text and line['pinned'] for line in obj['lines'])


def test_worker_reconciles_unpin_changed_during_model_call(client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    text = 'This line can now be rewritten.'
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': text, 'pinned': True},
    ]})
    message_id = _add_messages(client, cid, 1)[0]

    def complete(messages, cap_tokens=0):
        response = client.put(f'/api/chats/{cid}/summary/pins', json={
            'section': 'story', 'text': text, 'pinned': False,
        })
        assert response.status_code == 200
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    summaries._run_summary_job(cid, message_id)

    with shared.get_db() as conn:
        raw = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()[0]
    line = next(line for line in parse_summary_json(raw)['lines'] if line['text'] == text)
    assert line['pinned'] is False


# ── Chat persistence (update_chat / chat_to_dict) ───────────────────────────

def test_update_chat_can_toggle_summary_enablement(client, sample_chat):
    cid = sample_chat['id']
    r = client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    assert r.status_code == 200
    assert r.get_json()['summary_enabled'] is True


def test_update_chat_rejects_client_summary_content(client, sample_chat):
    cid = sample_chat['id']
    original = {'lines': [
        {'section': 'story', 'text': 'Server-owned memory.', 'pinned': False},
    ]}
    _store_summary(cid, original)

    response = client.put(f'/api/chats/{cid}', json={
        'summary_json': {'lines': [
            {'section': 'story', 'text': 'Stale client memory.', 'pinned': False},
        ]},
    })

    assert response.status_code == 400
    with shared.get_db() as conn:
        raw = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()[0]
    assert parse_summary_json(raw) == original


def test_generic_chat_patch_cannot_overwrite_interleaved_worker_publish(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    message_id = _add_messages(client, cid, 1)[0]
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    original_get_db = chat_routes.get_db
    interleaved = False

    class InterleavingConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            nonlocal interleaved
            if not interleaved and sql.startswith('UPDATE chats SET'):
                interleaved = True
                summaries._run_summary_job(cid, message_id)
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextmanager
    def interleaving_get_db():
        with original_get_db() as conn:
            yield InterleavingConnection(conn)

    monkeypatch.setattr(chat_routes, 'get_db', interleaving_get_db)

    response = client.put(f'/api/chats/{cid}', json={'name': 'Renamed safely'})

    assert response.status_code == 200
    assert interleaved is True
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT name, summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert row['name'] == 'Renamed safely'
    assert row['summary_up_to_msg_id'] == message_id
    assert 'Something happened.' in summary_to_text(parse_summary_json(row['summary_json']))


def test_update_chat_ignores_client_watermark(client, sample_chat):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_up_to_msg_id': 12345})
    with shared.get_db() as conn:
        wm = conn.execute('SELECT summary_up_to_msg_id FROM chats WHERE id=?', (cid,)).fetchone()[0]
    assert wm is None  # watermark is server-managed only


# ── Settings masking + config ───────────────────────────────────────────────

def test_summary_api_key_masked_and_not_clobbered(client):
    client.put('/api/settings', json={'summary_api_key': 'secretkey1234'})
    r = client.get('/api/settings')
    body = r.get_json()
    assert 'summary_api_key' not in body
    assert body['summary_api_key_set'] is True
    assert body['summary_api_key_masked'] and body['summary_api_key_masked'] != 'secretkey1234'
    # Echoing the masked placeholder back must not overwrite the real key.
    client.put('/api/settings', json={'summary_api_key': body['summary_api_key_masked']})
    assert get_settings().get('summary_api_key') == 'secretkey1234'
    # An explicit empty value is different from the mask and restores main-key fallback.
    cleared = client.put('/api/settings', json={'summary_api_key': ''}).get_json()
    assert get_settings().get('summary_api_key') == ''
    assert cleared['summary_api_key_set'] is False


def test_summary_llm_settings_fallback(client):
    client.put('/api/settings', json={
        'api_endpoint': 'http://main', 'api_key': 'mainkey', 'api_model': 'main-model',
    })
    # Blank summary_* -> falls back to the main connection.
    assert summaries._summary_llm_settings() == ('http://main', 'mainkey', 'main-model')
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://sum', 'summary_api_model': 'sum-model',
    })
    ep, key, model = summaries._summary_llm_settings()
    assert ep == 'http://sum' and model == 'sum-model' and key == 'mainkey'


def test_config_defaults_seeded(client):
    s = get_settings()
    assert s['summary_cap_pct'] == '10'
    assert s['summary_trigger_interval'] == '10'
    assert s['summary_compress_batch'] == '3'


# ── Compress endpoint + rebuild pin placement ───────────────────────────────

def test_compress_run_starts_without_a_message_boundary(client, sample_chat, monkeypatch):
    """Compression never advances the watermark, so it needs no up_to_msg_id."""
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    spawned = []
    monkeypatch.setattr(
        summaries, '_spawn_job',
        lambda chat_id, up_to, rebuild, token, compress_only=False:
            spawned.append((up_to, rebuild, compress_only)),
    )
    r = client.post(f'/api/chats/{cid}/summary/run', json={'compress': True})
    assert r.status_code == 202
    assert spawned == [(None, False, True)]


def test_compress_and_rebuild_are_mutually_exclusive(client, sample_chat):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    r = client.post(f'/api/chats/{cid}/summary/run',
                    json={'compress': True, 'rebuild': True})
    assert r.status_code == 400


def test_compress_job_shrinks_story_without_moving_the_watermark(
    client, sample_chat, monkeypatch
):
    cid = sample_chat['id']
    monkeypatch.setattr(summaries, 'call_summarizer', lambda m, cap_tokens=0: '- merged')
    ids = _add_messages(client, cid, 2)
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': 'S1', 'pinned': False},
        {'section': 'story', 'text': 'S2', 'pinned': False},
        {'section': 'story', 'text': 'S3', 'pinned': False},
        {'section': 'bonds', 'text': 'A & B: allies', 'pinned': False},
    ]}, watermark=ids[0])

    summaries._run_summary_job(cid, None, compress_only=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (cid,)).fetchone()
    texts = [l['text'] for l in parse_summary_json(row['summary_json'])['lines']]
    assert texts == ['merged', 'A & B: allies']
    assert row['summary_up_to_msg_id'] == ids[0]     # untouched
    assert row['summary_status'] == 'idle'


@pytest.mark.parametrize('failed_call', [1, 2])
def test_compress_job_failure_preserves_summary_and_watermark_atomically(
    client, sample_chat, monkeypatch, failed_call
):
    """Neither a first-batch nor later-batch failure may publish partial work or
    feed unchanged lines through cap trimming."""
    cid = sample_chat['id']
    ids = _add_messages(client, cid, 2)
    original = {'lines': [
        {'section': 'story', 'text': ('S1 ' * 20).strip(), 'pinned': False},
        {'section': 'story', 'text': ('S2 ' * 20).strip(), 'pinned': False},
        {'section': 'story', 'text': 'PIN', 'pinned': True},
        {'section': 'story', 'text': ('S3 ' * 20).strip(), 'pinned': False},
        {'section': 'story', 'text': ('S4 ' * 20).strip(), 'pinned': False},
    ]}
    _store_summary(cid, original, watermark=ids[0])
    # Make the existing summary exceed the new cap. A failed compression must not
    # fall through to enforce_cap and delete the unchanged unpinned lines.
    client.put('/api/settings', json={
        'context_max_tokens': '100',
        'summary_cap_pct': '1',
        'summary_compress_batch': '3',
    })
    calls = 0

    def complete(messages, cap_tokens=0):
        nonlocal calls
        calls += 1
        if calls == failed_call:
            raise RuntimeError('provider unavailable')
        return '- merged'

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    summaries._run_summary_job(cid, None, compress_only=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == original
    assert row['summary_up_to_msg_id'] == ids[0]
    assert row['summary_status'] == 'error'
    assert f'Compression batch {failed_call}/2 failed' in row['summary_status_detail']


def test_automatic_compression_failure_keeps_pending_batch_unretired(
    client, sample_chat, monkeypatch
):
    cid = sample_chat['id']
    ids = _add_messages(client, cid, 2)
    original = {'lines': [
        {'section': 'story', 'text': 'x' * 80, 'pinned': False},
        {'section': 'story', 'text': 'y' * 80, 'pinned': False},
        {'section': 'story', 'text': 'z' * 80, 'pinned': False},
    ]}
    _store_summary(cid, original, watermark=ids[0])
    client.put('/api/settings', json={
        'context_max_tokens': '400',
        'summary_cap_pct': '10',
    })
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda messages, cap_tokens=0: (_ for _ in ()).throw(
            RuntimeError('provider unavailable')
        ),
    )

    summaries._run_summary_job(cid, ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == original
    assert row['summary_up_to_msg_id'] == ids[0]
    assert row['summary_status'] == 'error'


def test_rebuild_places_a_late_pin_late_in_the_regenerated_story(
    client, sample_chat, monkeypatch
):
    """A pin from the end of the old story must not lead the rebuilt timeline."""
    cid = sample_chat['id']
    replies = iter((
        'STORY SO FAR\n- R1\n\nBONDS\n- A & B: allies',
        'STORY SO FAR\n- R2\n\nBONDS\n- A & B: allies',
    ))
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda m, cap_tokens=0: next(replies))
    client.put('/api/settings', json={'summary_trigger_interval': '1'})
    ids = _add_messages(client, cid, 2)
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': 'old-a', 'pinned': False},
        {'section': 'story', 'text': 'old-b', 'pinned': False},
        {'section': 'story', 'text': 'LATE PIN', 'pinned': True},
    ]})

    summaries._run_summary_job(cid, ids[-1], rebuild=True)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()
    texts = [l['text'] for l in parse_summary_json(row['summary_json'])['lines']]
    assert 'LATE PIN' in texts
    assert texts.index('LATE PIN') > texts.index('R1')


def test_rebuild_reuses_an_identical_regenerated_pin(
    client, sample_chat, monkeypatch
):
    cid = sample_chat['id']
    ids = _add_messages(client, cid, 1)
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': 'SAME BEAT', 'pinned': True},
    ]})
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda messages, cap_tokens=0:
            'STORY SO FAR\n- SAME BEAT\n\nBONDS\n- A & B: allies',
    )

    summaries._run_summary_job(cid, ids[-1], rebuild=True)

    with shared.get_db() as conn:
        raw = conn.execute(
            'SELECT summary_json FROM chats WHERE id=?', (cid,)
        ).fetchone()[0]
    lines = parse_summary_json(raw)['lines']
    matches = [line for line in lines if line['text'] == 'SAME BEAT']
    assert matches == [{'section': 'story', 'text': 'SAME BEAT', 'pinned': True}]


def test_rebuild_drops_a_pin_unpinned_while_it_was_running(
    client, sample_chat, monkeypatch
):
    """Held story pins are captured at job start; an unpin during the run wins."""
    cid = sample_chat['id']
    ids = _add_messages(client, cid, 1)
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': 'old-a', 'pinned': False},
        {'section': 'story', 'text': 'DOOMED', 'pinned': True},
    ]})

    def unpin_then_reply(messages, cap_tokens=0):
        # Simulate the user unpinning the line mid-rebuild.
        _store_summary(cid, {'lines': [
            {'section': 'story', 'text': 'old-a', 'pinned': False},
            {'section': 'story', 'text': 'DOOMED', 'pinned': False},
        ]})
        return 'STORY SO FAR\n- R1\n\nBONDS\n- A & B: allies'

    monkeypatch.setattr(summaries, 'call_summarizer', unpin_then_reply)
    summaries._run_summary_job(cid, ids[-1], rebuild=True)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()
    assert 'DOOMED' not in [l['text'] for l in parse_summary_json(row['summary_json'])['lines']]


def test_unlimited_context_disables_summary_cap():
    assert summaries._cap_tokens({
        'context_max_tokens': '0',
        'summary_cap_pct': '10',
    }) == 0


@pytest.mark.parametrize(
    'pct',
    ['not-a-number', 'nan', 'inf', '-inf', float('nan'), float('inf'), 10 ** 400],
)
def test_summary_cap_defaults_malformed_or_non_finite_percent(pct):
    assert summaries._cap_tokens({
        'context_max_tokens': '1000',
        'summary_cap_pct': pct,
    }) == 100


@pytest.mark.parametrize(
    ('pct', 'expected'),
    [('0', 10), ('-5', 10), ('1', 10), ('90', 900), ('91', 900), ('1e308', 900)],
)
def test_summary_cap_clamps_percent_to_ui_bounds(pct, expected):
    assert summaries._cap_tokens({
        'context_max_tokens': '1000',
        'summary_cap_pct': pct,
    }) == expected


@pytest.mark.parametrize(
    'context_tokens',
    ['not-an-integer', 'nan', 'inf', '-inf', float('nan'), float('inf')],
)
def test_summary_cap_defaults_malformed_or_non_finite_context(context_tokens):
    assert summaries._cap_tokens({
        'context_max_tokens': context_tokens,
        'summary_cap_pct': '10',
    }) == 3276


def test_summary_cap_handles_arbitrarily_large_context_without_float_overflow():
    context_tokens = 10 ** 400
    assert summaries._cap_tokens({
        'context_max_tokens': context_tokens,
        'summary_cap_pct': '90',
    }) == context_tokens * 9 // 10


# ── Migration ───────────────────────────────────────────────────────────────

def test_migration_idempotent_and_columns_present():
    # Fixture already ran init_db once; a second call must not error.
    shared.init_db()
    with shared.get_db() as conn:
        cols = {c[1] for c in conn.execute('PRAGMA table_info(chats)').fetchall()}
    for col in ('summary_enabled', 'summary_json', 'summary_up_to_msg_id',
                'summary_status', 'summary_status_detail'):
        assert col in cols


def test_startup_recovery_resets_running(client, sample_chat):
    cid = sample_chat['id']
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='stale progress' "
            'WHERE id=?',
            (cid,),
        )
    shared.init_db()  # should clear stale 'running'
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_status, summary_status_detail FROM chats WHERE id=?', (cid,)
        ).fetchone()
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''
