"""Tests for the Auto Summaries feature — pure summary logic, the background
summarizer worker/endpoints, per-chat persistence, settings masking, and the
startup recovery migration."""

import sqlite3

import pytest

import shared
from routes.settings import get_settings
import routes.summaries as summaries
from summarizer import (
    build_summarizer_messages,
    dump_summary_json,
    enforce_cap,
    estimate_tokens,
    merge_pins,
    parse_summary,
    parse_summary_json,
    pinned_texts,
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


# ── Helpers for endpoint/worker tests ───────────────────────────────────────

def _add_messages(client, chat_id, n):
    ids = []
    for i in range(n):
        role = 'user' if i % 2 == 0 else 'character'
        r = client.post(f'/api/chats/{chat_id}/messages', json={'role': role, 'content': f'msg {i}'})
        assert r.status_code in (200, 201), r.get_data(as_text=True)
        ids.append(r.get_json()['id'])
    return ids


CANNED = "STORY SO FAR\n- Something happened.\n\nBONDS\n- A & B: close allies."


# ── The worker ──────────────────────────────────────────────────────────────

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


# ── Endpoints ───────────────────────────────────────────────────────────────

def test_run_endpoint_overlap_guard(client, sample_chat, monkeypatch):
    # Stub the thread spawn so the claimed 'running' state persists for the test.
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    ids = _add_messages(client, sample_chat['id'], 2)
    r1 = client.post(f'/api/chats/{sample_chat["id"]}/summary/run', json={'up_to_msg_id': ids[-1]})
    assert r1.status_code == 202
    assert r1.get_json()['summary_status'] == 'running'
    # A second run while one is in flight is a no-op.
    r2 = client.post(f'/api/chats/{sample_chat["id"]}/summary/run', json={'up_to_msg_id': ids[-1]})
    assert r2.status_code == 409
    assert r2.get_json()['already_running'] is True


def test_run_endpoint_missing_chat(client):
    r = client.post('/api/chats/999999/summary/run', json={'up_to_msg_id': 1})
    assert r.status_code == 404


def test_status_endpoint(client, sample_chat):
    r = client.get(f'/api/chats/{sample_chat["id"]}/summary/status')
    assert r.status_code == 200
    body = r.get_json()
    assert body['summary_status'] == 'idle'
    assert body['summary'] == {'lines': []}


# ── Chat persistence (update_chat / chat_to_dict) ───────────────────────────

def test_update_chat_summary_fields_and_pins(client, sample_chat):
    cid = sample_chat['id']
    obj = {'lines': [
        {'section': 'story', 'text': 'a happened', 'pinned': False},
        {'section': 'bonds', 'text': 'X & Y: rivals', 'pinned': True},
    ]}
    r = client.put(f'/api/chats/{cid}', json={'summary_enabled': True, 'summary_json': obj})
    assert r.status_code == 200
    body = r.get_json()
    assert body['summary_enabled'] is True
    assert body['summary']['lines'][1]['pinned'] is True


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
        conn.execute("UPDATE chats SET summary_status='running' WHERE id=?", (cid,))
    shared.init_db()  # should clear stale 'running'
    with shared.get_db() as conn:
        status = conn.execute('SELECT summary_status FROM chats WHERE id=?', (cid,)).fetchone()[0]
    assert status == 'idle'
