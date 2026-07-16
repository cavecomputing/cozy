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
    build_summarizer_messages,
    build_tighten_messages,
    dump_summary_json,
    enforce_cap,
    estimate_tokens,
    merge_pins,
    parse_summary,
    parse_summary_json,
    parse_summarizer_output,
    pinned_texts,
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


def test_parse_summary_json_tolerates_junk():
    assert parse_summary_json('not json') == {'lines': []}
    assert parse_summary_json('')['lines'] == []
    good = dump_summary_json({'lines': [{'section': 'bonds', 'text': 'a', 'pinned': True}]})
    assert parse_summary_json(good)['lines'][0]['pinned'] is True


def test_build_summarizer_messages_includes_context():
    msgs = build_summarizer_messages(
        'PREV', [{'role': 'user', 'content': 'hi'}], ['keep me'], 3000)
    assert msgs[0]['role'] == 'system'
    joined = msgs[1]['content']
    assert 'PREV' in joined and 'keep me' in joined and 'hi' in joined and '3000' in joined


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


def test_build_tighten_messages_requests_semantic_rewrite():
    messages = build_tighten_messages('long draft', ['pin me'], 100)
    prompt = messages[1]['content']
    assert 'OVER-BUDGET' in prompt and 'no more than 100' in prompt
    assert 'pin me' in prompt and 'no new chat history' in prompt


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


def test_run_job_semantically_tightens_before_hard_cap(client, sample_chat, monkeypatch):
    client.put('/api/settings', json={
        'context_max_tokens': '500',
        'summary_cap_pct': '10',
    })
    over_cap = f"STORY SO FAR\n- {'important context ' * 100}\n\nBONDS\n- A & B: trust"
    tightened = 'STORY SO FAR\n- Essential context remains.\n\nBONDS\n- A & B: trust.'
    replies = iter((over_cap, tightened))
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1])

    assert len(calls) == 2
    assert 'OVER-BUDGET SUMMARY DRAFT' in calls[1][1]['content']
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (sample_chat['id'],),
        ).fetchone()
    assert 'Essential context remains.' in summary_to_text(parse_summary_json(row['summary_json']))
    assert row['summary_up_to_msg_id'] == ids[-1]


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


def test_global_pause_discards_inflight_result_and_keeps_last_checkpoint(
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
            client.put('/api/settings', json={'auto_summaries_enabled': '0'})
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


@pytest.mark.parametrize('pause_scope', ['global', 'chat'])
def test_disable_reenable_new_run_cannot_revive_old_worker(
        client, sample_chat, monkeypatch, pause_scope):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    message_id = _add_messages(client, cid, 1)[0]
    spawned_tokens = []

    def capture_spawn(chat_id, up_to_msg_id, rebuild, job_token):
        assert chat_id == cid and up_to_msg_id == message_id
        spawned_tokens.append(job_token)

    monkeypatch.setattr(summaries, '_spawn_job', capture_spawn)
    first = client.post(
        f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
    )
    assert first.status_code == 202
    old_token = spawned_tokens[0]

    def replace_run_during_old_call(messages, cap_tokens=0):
        if pause_scope == 'global':
            client.put('/api/settings', json={'auto_summaries_enabled': '0'})
            with shared.get_db() as conn:
                paused_row = conn.execute(
                    'SELECT summary_status, summary_status_detail FROM chats WHERE id=?',
                    (cid,),
                ).fetchone()
            assert paused_row['summary_status'] == 'idle'
            assert paused_row['summary_status_detail'] == ''
            client.put('/api/settings', json={'auto_summaries_enabled': '1'})
        else:
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
        lambda chat_id, up_to_msg_id, rebuild, job_token: spawned_tokens.append(job_token),
    )
    started = client.post(
        f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
    )
    assert started.status_code == 202
    job_token = spawned_tokens[0]
    assert summaries._job_tokens.get(cid) == job_token

    if terminal_event == 'pause':
        paused = client.put('/api/settings', json={'auto_summaries_enabled': '0'})
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


def test_run_endpoint_honors_global_feature_gate(client, sample_chat, monkeypatch):
    message_id = _add_messages(client, sample_chat['id'], 1)[0]
    client.put(f'/api/chats/{sample_chat["id"]}', json={'summary_enabled': True})
    client.put('/api/settings', json={'auto_summaries_enabled': '0'})
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)

    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': message_id},
    )

    assert response.status_code == 409
    assert 'disabled' in response.get_json()['error']


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
    assert s['summary_trigger_interval'] == '20'
    assert s['auto_summaries_enabled'] == '1'


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
