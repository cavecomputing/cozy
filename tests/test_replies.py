"""A reply is written and saved by the server, whoever is or isn't listening."""

import json
import threading
import time

import pytest

from cozy.routes import llm
from cozy.summarizer import close_incomplete_thinking

HOLD = object()
PAYLOAD = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hi'}]}


def said(text):
    return {'choices': [{'delta': {'content': text}}]}


def thought(text):
    return {'choices': [{'delta': {'reasoning_content': text}}]}


class Upstream:
    """A model server that lets its reply out up to each HOLD, until released."""

    ok = True
    status_code = 200

    def __init__(self, chunks):
        self.chunks = chunks
        self.release = threading.Event()
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed.set()
        return False

    def iter_lines(self, delimiter=None):
        for chunk in self.chunks:
            if chunk is HOLD:
                assert self.release.wait(5)
            elif chunk == '[DONE]':
                yield b'data: [DONE]'
            else:
                yield f'data: {json.dumps(chunk)}'.encode()


@pytest.fixture
def model(client, monkeypatch):
    """Point the chat connection at an Upstream; returns how to script it."""
    client.put('/api/settings', json={'api_endpoint': 'http://upstream.test/v1'})

    def script(*chunks):
        upstream = Upstream(chunks)
        monkeypatch.setattr(llm.http_requests, 'post', lambda *args, **kwargs: upstream)
        return upstream
    return script


def wait_for(condition):
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, 'timed out'
        time.sleep(0.01)


def events(response):
    """The SSE payloads of a reply stream, parsed where they are JSON."""
    payloads = [line[6:] for line in response.get_data(as_text=True).split('\n')
                if line.startswith('data: ')]
    return [p if p == '[DONE]' else json.loads(p) for p in payloads]


def texts(client, chat):
    return [m['content'] for m in client.get(f'/api/chats/{chat["id"]}/messages').get_json()]


def test_a_reply_is_saved_though_the_page_never_reads_it(client, sample_chat, model):
    upstream = model(said('Hel'), HOLD, said('lo'), '[DONE]')
    # The page asks, and is gone before the first word.
    client.post(f'/api/llm/chat?chat_id={sample_chat["id"]}', json=PAYLOAD).close()
    assert texts(client, sample_chat) == []

    upstream.release.set()
    wait_for(lambda: texts(client, sample_chat) == ['Hello'])
    message = client.get(f'/api/chats/{sample_chat["id"]}/messages').get_json()[0]
    assert message['role'] == 'character'


def test_a_page_that_comes_back_reads_the_reply_from_its_first_word(client, sample_chat, model):
    upstream = model(thought('Hmm.'), said('Hel'), HOLD, said('lo'), '[DONE]')
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    client.post(url, json=PAYLOAD).close()
    wait_for(lambda: len(llm._replies[sample_chat['id']].events) == 2)

    rejoined = client.get(url)
    assert rejoined.status_code == 200
    assert rejoined.content_type.startswith('text/event-stream')
    assert 'X-Swipe-Of' not in rejoined.headers
    upstream.release.set()

    *chunks, done, saved = events(rejoined)
    assert chunks == [thought('Hmm.'), said('Hel'), said('lo')]
    assert done == '[DONE]'
    assert saved['saved']['content'] == '<think>Hmm.</think>Hello'
    assert saved['saved']['id'] == client.get(f'/api/chats/{sample_chat["id"]}/messages').get_json()[0]['id']


def test_the_chat_says_when_no_reply_is_running_and_when_one_just_ended(client, sample_chat, model):
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    assert client.get(url).status_code == 204

    model(said('Hello'), '[DONE]')
    client.post(url, json=PAYLOAD).get_data()
    # A page that loaded the chat a moment before the save has to look again.
    assert client.get(url).status_code == 205

    llm._forget(llm._replies[sample_chat['id']])
    assert client.get(url).status_code == 204


def test_one_reply_at_a_time_per_chat(client, sample_chat, model):
    upstream = model(said('Hel'), HOLD, '[DONE]')
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    first = client.post(url, json=PAYLOAD)

    second = client.post(url, json=PAYLOAD)
    assert second.status_code == 409
    assert 'already' in second.get_json()['error']

    upstream.release.set()
    first.get_data()
    # Once it has ended, the next one may start.
    model(said('again'), '[DONE]')
    assert client.post(url, json=PAYLOAD).status_code == 200


def test_stop_keeps_what_arrived_and_ends_the_stream(client, sample_chat, model):
    upstream = model(thought('Hmm.'), said('Hel'), HOLD, said('lo'), '[DONE]')
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    reading = client.post(url, json=PAYLOAD)
    wait_for(lambda: len(llm._replies[sample_chat['id']].events) == 2)

    assert client.delete(url).get_json() == {'ok': True}

    *chunks, saved, done = events(reading)
    assert chunks == [thought('Hmm.'), said('Hel')]
    assert saved['saved']['content'] == '<think>Hmm.</think>Hel'
    # An ending the page can tell from a dropped connection.
    assert done == '[DONE]'
    assert texts(client, sample_chat) == ['<think>Hmm.</think>Hel']

    # The model's next chunk finds nobody waiting, and the request to it is closed.
    upstream.release.set()
    assert upstream.closed.wait(5)
    assert texts(client, sample_chat) == ['<think>Hmm.</think>Hel']


def test_stop_mid_reasoning_keeps_nothing(client, sample_chat, model):
    """Reasoning is left out of every later prompt: alone it is a blank message."""
    upstream = model(thought('still weighing it up'), HOLD)
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    reading = client.post(url, json=PAYLOAD)
    wait_for(lambda: len(llm._replies[sample_chat['id']].events) == 1)

    client.delete(url)

    assert events(reading) == [thought('still weighing it up'), '[DONE]']
    assert texts(client, sample_chat) == []
    upstream.release.set()


def test_a_reply_the_provider_gave_up_on_keeps_its_partial(client, sample_chat, model):
    model(said('Hel'), {'error': {'message': 'Provider returned error', 'code': 502}}, said('lo'))
    reading = client.post(f'/api/llm/chat?chat_id={sample_chat["id"]}', json=PAYLOAD)

    chunk, saved, error = events(reading)
    assert chunk == said('Hel')
    # What was kept comes ahead of the error, which is where the page stops reading.
    assert saved['saved']['content'] == 'Hel'
    assert error == {'error': 'Provider returned error'}
    assert texts(client, sample_chat) == ['Hel']


def test_a_reply_that_fails_before_any_text_saves_nothing(client, sample_chat, model):
    model({'error': 'overloaded'})
    reading = client.post(f'/api/llm/chat?chat_id={sample_chat["id"]}', json=PAYLOAD)

    assert events(reading) == [{'error': 'overloaded'}]
    assert texts(client, sample_chat) == []


def test_a_new_swipe_is_saved_to_its_message_and_put_on_show(client, sample_chat, model):
    chat_id = sample_chat['id']
    message = client.post(f'/api/chats/{chat_id}/messages', json={
        'role': 'character', 'content': 'first take',
    }).get_json()
    upstream = model(said('second '), HOLD, said('take'), '[DONE]')
    url = f'/api/llm/chat?chat_id={chat_id}'
    client.post(f'{url}&message_id={message["id"]}', json=PAYLOAD).close()
    wait_for(lambda: len(llm._replies[chat_id].events) == 1)

    # A page that rejoins is told which message the swipe belongs to.
    rejoined = client.get(url)
    assert rejoined.headers['X-Swipe-Of'] == str(message['id'])
    upstream.release.set()
    saved = events(rejoined)[-1]['saved']

    assert saved['content'] == 'second take'
    assert saved['message_id'] == message['id']
    [row] = client.get(f'/api/chats/{chat_id}/messages').get_json()
    assert row['content'] == 'second take'
    assert [s['content'] for s in row['swipes']] == ['first take', 'second take']
    assert row['swipe_index'] == 1


def test_the_reply_has_to_name_a_chat_and_a_message_in_it(client, sample_chat, sample_character, model):
    model(said('Hello'), '[DONE]')
    assert client.post('/api/llm/chat', json=PAYLOAD).status_code == 400
    assert client.post('/api/llm/chat?chat_id=999', json=PAYLOAD).status_code == 404

    other = client.post(f'/api/characters/{sample_character["id"]}/chats', json={'name': 'Other'}).get_json()
    message = client.post(f'/api/chats/{other["id"]}/messages', json={
        'role': 'character', 'content': 'elsewhere',
    }).get_json()
    wrong_chat = client.post(
        f'/api/llm/chat?chat_id={sample_chat["id"]}&message_id={message["id"]}', json=PAYLOAD)
    assert wrong_chat.status_code == 404
    assert llm._replies == {}


def test_a_reply_to_a_chat_deleted_meanwhile_ends_without_one(client, sample_chat, model):
    upstream = model(said('Hel'), HOLD, said('lo'), '[DONE]')
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    reading = client.post(url, json=PAYLOAD)
    wait_for(lambda: len(llm._replies[sample_chat['id']].events) == 1)

    client.delete(f'/api/chats/{sample_chat["id"]}')
    upstream.release.set()

    error = events(reading)[-1]['error']
    assert error.startswith('The reply could not be saved')
    assert llm._replies[sample_chat['id']].done


def test_a_listener_with_nothing_to_hear_is_sent_a_comment_line(client, sample_chat, model, monkeypatch):
    """Writing is how the server finds out that a page has gone."""
    monkeypatch.setattr(llm, 'FOLLOW_IDLE_SECONDS', 0.01)
    upstream = model(HOLD, said('Hello'), '[DONE]')
    stream = client.post(f'/api/llm/chat?chat_id={sample_chat["id"]}', json=PAYLOAD).response

    assert next(stream) == b': waiting\n\n'
    upstream.release.set()
    assert b'"saved"' in b''.join(stream)


def test_a_block_left_open_is_closed_ahead_of_the_response():
    assert close_incomplete_thinking('<think>cut off here') == '<think>cut off here</think>'
    assert close_incomplete_thinking('Preface<thinking>cut off') == '<thinking>cut off</thinking>Preface'
    # Closed and tag-free text pass through untouched.
    assert close_incomplete_thinking('<think>r</think>body') == '<think>r</think>body'
    assert close_incomplete_thinking('reasoning</think>body') == 'reasoning</think>body'
    assert close_incomplete_thinking('plain body') == 'plain body'


def test_a_stopped_reply_closes_the_block_the_model_left_open_in_its_text(client, sample_chat, model):
    upstream = model(said('An answer. <think>second thoughts'), HOLD)
    url = f'/api/llm/chat?chat_id={sample_chat["id"]}'
    reading = client.post(url, json=PAYLOAD)
    wait_for(lambda: len(llm._replies[sample_chat['id']].events) == 1)

    client.delete(url)

    reading.get_data()
    assert texts(client, sample_chat) == ['<think>second thoughts</think>An answer.']
    upstream.release.set()
