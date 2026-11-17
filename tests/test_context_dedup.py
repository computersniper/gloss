"""Attachments already in the live conversation are not added to the context again."""
import time
from pathlib import Path

import pytest


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def wait_idle(client, sid):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        s = ok(client.get(f'/api/sessions/{sid}'))
        if s['status'] == 'idle':
            return s
        time.sleep(.02)
    pytest.fail('turn did not finish')


def test_repeated_paper_selection_and_file_are_sent_once(client, paper_id, monkeypatch):
    from app.providers import registry
    seen = []

    async def fake(*args, **kwargs):
        messages = args[1] if len(args) > 1 else kwargs.get('messages', [])
        seen.append('\n'.join(m['content'] for m in messages))
        yield 'Done.'

    monkeypatch.setattr(registry, 'stream', fake)
    project = ok(client.post('/api/agent/projects', json={'name': 'Dedup'}))
    root = Path(project['workspace']['path'])
    (root / 'notes.md').write_text('MY UNIQUE NOTE', encoding='utf-8')
    sid = ok(client.post('/api/sessions', json={'project_id': project['id']}))['id']
    selection = 'A VERY DISTINCT SELECTED SENTENCE'
    attach = lambda n: [  # noqa: E731
        {'kind': 'paper', 'ref': paper_id},
        {'kind': 'span', 'ref': f'sel-{n}', 'label': 'p.1', 'text': selection},
        {'kind': 'file', 'ref': 'notes.md'},
    ]
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'first', 'attachments': attach(1)}))
    wait_idle(client, sid)
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'second', 'attachments': attach(2)}))
    s = wait_idle(client, sid)
    prompt = seen[-1]
    assert prompt.count(selection) == 1
    assert prompt.count('MY UNIQUE NOTE') == 1
    assert prompt.count(f'Paper id: {paper_id}') == 1
    assert 'already earlier in this conversation' in prompt
    second = [m for m in s['messages'] if m.get('type') == 'user'][-1]
    assert all(a.get('repeat') for a in second['attachments'])

    # a changed file is attached again in full
    (root / 'notes.md').write_text('MY UNIQUE NOTE, REVISED', encoding='utf-8')
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'third', 'attachments': [{'kind': 'file', 'ref': 'notes.md'}]}))
    wait_idle(client, sid)
    assert 'MY UNIQUE NOTE, REVISED' in seen[-1]


def test_side_chat_reads_a_recap_of_the_thread_and_never_writes_to_it(client, monkeypatch):
    from app.providers import registry
    from app.routers import ai

    async def fake(*args, **kwargs):
        yield 'The agent answer about transformers.'

    monkeypatch.setattr(registry, 'stream', fake)
    project = ok(client.post('/api/agent/projects', json={'name': 'Side'}))
    sid = ok(client.post('/api/sessions', json={'project_id': project['id']}))['id']
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'Compare attention variants'}))
    before = wait_idle(client, sid)
    captured = {}

    async def fake_chat_stream(paper_id, messages, **kwargs):
        captured.update(kwargs, paper_id=paper_id, messages=messages)
        yield 'side answer'

    monkeypatch.setattr(ai.chat_feat, 'chat_stream', fake_chat_stream)
    response = client.post('/api/chat', json={'session_id': sid, 'messages': [{'role': 'user', 'content': 'what is MQA?'}]})
    assert response.status_code == 200 and 'side answer' in response.text
    recap = captured['main_context']
    assert 'User: Compare attention variants' in recap and 'Agent: The agent answer' in recap
    after = ok(client.get(f'/api/sessions/{sid}'))
    assert len(after['messages']) == len(before['messages'])

    # an unknown thread only drops the recap
    client.post('/api/chat', json={'session_id': 'missing', 'messages': [{'role': 'user', 'content': 'hi'}]})
    assert captured['main_context'] is None


def test_side_chat_system_prompt_marks_the_recap_as_reference(monkeypatch):
    import asyncio
    from app.features import chat as chat_feat
    from app.providers import registry

    seen = {}

    async def fake_stream(system, messages, **kwargs):
        seen['system'] = system
        yield 'ok'

    monkeypatch.setattr(registry, 'stream', fake_stream)

    async def run():
        return [c async for c in chat_feat.chat_stream(None, [{'role': 'user', 'content': 'q'}], main_context='User: hello')]
    asyncio.run(run())
    assert '<main_thread>\nUser: hello\n</main_thread>' in seen['system']
    assert 'side chat' in seen['system']
