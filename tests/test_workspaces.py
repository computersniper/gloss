"""Workspace contracts exercised through HTTP, with deterministic provider turns."""
import asyncio
import json
import shutil
import threading
import time
from pathlib import Path

import pytest


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def project(client):
    p = ok(client.post('/api/agent/projects', json={'name': 'Workspace contract'}))
    return p


def write(client, pid, content, path='notes.txt'):
    return ok(client.put(f'/api/workspace/{pid}/file', params={'path': path}, json={'content': content}))


def wait_idle(client, sid):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        s = ok(client.get(f'/api/sessions/{sid}'))
        if s['status'] == 'idle':
            return s
        time.sleep(.02)
    pytest.fail('turn did not finish')


def test_paper_workspace_creation_scope_and_preservation(client, paper_id):
    p = ok(client.get(f'/api/papers/{paper_id}/project'))
    assert p['kind'] == 'paper' and p['paper_ids'] == [paper_id]
    assert ok(client.get(f'/api/papers/{paper_id}/project'))['id'] == p['id']
    assert len(ok(client.get('/api/agent/projects', params={'paper_id': paper_id}))) == 1
    root = Path(p['workspace']['path'])
    assert paper_id in (root / 'GLOSSX.md').read_text(encoding='utf-8')
    assert (root / 'notes/README.md').exists()
    session = ok(client.post('/api/sessions', json={'paper_id': paper_id}))
    assert session['project_id'] == p['id'] and paper_id in session['scope_papers']
    write(client, p['id'], 'keep my research')
    ok(client.delete(f'/api/papers/{paper_id}'))
    assert ok(client.get(f"/api/agent/projects/{p['id']}"))['paper_ids'] == []
    assert (root / 'notes.txt').read_text(encoding='utf-8') == 'keep my research'


@pytest.mark.skipif(not shutil.which('git'), reason='git is unavailable')
def test_vcs_history_conflict_stash_restore_and_protected_paths(client, project):
    pid = project['id']
    base = f'/api/workspace/{pid}/vcs'
    assert ok(client.get(base + '/status'))['branch'] == 'main'
    write(client, pid, 'base\n')
    original = ok(client.post(base + '/commit', json={'message': 'base'}))['commit']['id']
    ok(client.post(base + '/branches', json={'name': 'experiment', 'checkout': True}))
    write(client, pid, 'experiment\n')
    ok(client.post(base + '/commit', json={'message': 'experiment'}))
    ok(client.post(base + '/checkout', json={'branch': 'main'}))
    merged = ok(client.post(base + '/merge', json={'branch': 'experiment'}))
    assert merged['merged'] and merged['fast_forward']
    ok(client.post(base + '/branches', json={'name': 'other', 'from': original, 'checkout': True}))
    write(client, pid, 'other\n')
    ok(client.post(base + '/commit', json={'message': 'other'}))
    ok(client.post(base + '/checkout', json={'branch': 'main'}))
    conflict = ok(client.post(base + '/merge', json={'branch': 'other'}))
    assert not conflict['merged'] and conflict['conflicts'] == ['notes.txt']
    assert ok(client.get(base + '/status'))['clean']
    assert not (Path(project['workspace']['path']) / '.git/MERGE_HEAD').exists()
    write(client, pid, 'uncommitted\n')
    assert client.post(base + '/checkout', json={'branch': 'other'}).status_code == 409
    ok(client.post(base + '/checkout', json={'branch': 'other', 'stash': True}))
    ok(client.post(base + '/checkout', json={'branch': 'main'}))
    assert (Path(project['workspace']['path']) / 'notes.txt').read_text() == 'uncommitted\n'
    restored = ok(client.post(base + '/restore', json={'rev': original}))
    assert restored['restored'] and restored['saved_commit']
    assert ok(client.get(base + '/file', params={'path': 'notes.txt'}))['content'] == 'base\n'
    write(client, pid, 'discard\n')
    write(client, pid, 'new', 'new.txt')
    ok(client.post(base + '/discard', json={'paths': ['notes.txt', 'new.txt']}))
    assert ok(client.get(base + '/status'))['clean']
    for path in ['.git/config', '.glossx/checkpoints/private.json']:
        assert client.put(f'/api/workspace/{pid}/file', params={'path': path}, json={'content': 'bad'}).status_code == 400
        assert client.post(base + '/discard', json={'paths': [path]}).status_code == 400
    assert client.delete(base + '/branches/main').status_code == 409


def test_inbox_read_keeps_blocking_attention_and_replays(client, project):
    from app.main import app
    sid = ok(client.post('/api/sessions', json={'project_id': project['id']}))['id']
    rt = app.state.sessions._rt(sid)
    rt.approvals['c_test'] = {'tool_call_id': 'c_test', 'tool': 'fs_write', 'args': {}, 'session_id': sid}
    rt.emit('approval.requested', {})
    assert ok(client.get('/api/sessions', params={'view': 'inbox'}))[0]['id'] == sid
    read = ok(client.post(f'/api/sessions/{sid}/read'))
    assert not read['unread'] and read['attention'] == 'approval'
    rt.approvals.clear()
    rt.emit('turn.completed', {'stop_reason': 'completed'})
    assert ok(client.get('/api/sessions/inbox/summary'))['unread'] >= 1
    async def replay():
        stream = app.state.sessions.subscribe_inbox()
        event, payload, seq = await anext(stream)
        assert event == 'inbox.updated' and payload['unread'] >= 1
        await stream.aclose()
    asyncio.run(replay())
    ok(client.post(f'/api/sessions/{sid}/read'))
    assert ok(client.get(f'/api/sessions/{sid}'))['attention'] is None


@pytest.mark.skipif(not shutil.which('git'), reason='git is unavailable')
def test_fake_provider_turn_autocommits_and_project_setting_disables_it(client, project, monkeypatch):
    from app.providers import registry
    calls = 0
    async def fake(*args, **kwargs):
        nonlocal calls
        calls += 1
        messages = args[1] if len(args) > 1 else kwargs.get('messages', [])
        if calls % 2:
            content = 'Write evidence' if calls == 1 else 'Write again'
            yield '<tool_call>' + json.dumps({'name': 'fs_write', 'arguments': {'path': 'agent.txt', 'content': content}}) + '</tool_call>'
        else:
            yield 'Finished.'
    monkeypatch.setattr(registry, 'stream', fake)
    pid = project['id']
    ok(client.get(f'/api/workspace/{pid}/vcs/status'))
    sid = ok(client.post('/api/sessions', json={'project_id': pid, 'mode': 'auto'}))['id']
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'Write evidence'}))
    s = wait_idle(client, sid)
    assert any(m.get('kind') == 'commit' and m.get('commit') for m in s['messages'])
    log = ok(client.get(f'/api/workspace/{pid}/vcs/log'))
    assert log[0]['session_id'] == sid and 'Turn: 1' in log[0]['message']
    assert ok(client.get(f'/api/workspace/{pid}/vcs/status'))['clean']
    tools = ok(client.get('/api/harness/tools', params={'mode': 'ask'}))['tools']
    assert {t['name'] for t in tools} >= {'vcs_status', 'vcs_log', 'vcs_diff', 'vcs_commit', 'vcs_branch', 'vcs_checkout', 'vcs_merge'}
    root = Path(project['workspace']['path'])
    (root / '.glossx/settings.json').write_text('{"vcs":{"auto_commit":false}}')
    head = ok(client.get(f'/api/workspace/{pid}/vcs/status'))['head']
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'Write again'}))
    wait_idle(client, sid)
    status = ok(client.get(f'/api/workspace/{pid}/vcs/status'))
    assert not status['clean'] and status['head'] == head
    assert (root / 'agent.txt').read_text() == 'Write again'


def test_config_paths_and_global_home_isolation(client, project):
    from app import config
    from app.harness import permissions, settings, tools
    paths = ok(client.get('/api/harness/settings', params={'project_id': project['id']}))['paths']
    assert paths['home'] == str(config.GLOSSX_HOME)
    assert Path(paths['home']).parent == config.DATA_DIR
    async def check():
        table = tools.build_table(await tools.harness_registry(settings.load()), mode='auto', has_project=True)
        decision, _ = permissions.decide(table['fs_write'], {'path': '.glossx/settings.json', 'content': '{}'}, 'auto', {'permissions': {'allow': ['fs_write']}})
        assert decision == 'ask'
    asyncio.run(check())


def test_migration_copies_without_overwriting_and_project_rules(tmp_path, monkeypatch):
    from app import config
    from app.harness import home, settings
    old = tmp_path / 'legacy'
    new = tmp_path / 'home'
    old.mkdir()
    (old / 'commands').mkdir()
    (old / 'commands/task.md').write_text('legacy command')
    (old / 'GLOSSX.md').write_text('legacy instructions')
    new.mkdir()
    (new / 'GLOSSX.md').write_text('new instructions')
    replacements = {'GLOSSX_HOME': new, 'GLOBAL_INSTRUCTIONS': new / 'GLOSSX.md',
                    'HARNESS_SETTINGS_PATH': new / 'settings.json', 'COMMANDS_DIR': new / 'commands',
                    'AGENTS_DIR': new / 'agents', 'SKILLS_DIR': new / 'skills',
                    'LEGACY_GLOBAL_INSTRUCTIONS': old / 'GLOSSX.md',
                    'LEGACY_COMMANDS_DIR': old / 'commands', 'LEGACY_AGENTS_DIR': old / 'agents'}
    for key, value in replacements.items():
        monkeypatch.setattr(config, key, value)
    home.migrate(force=True)
    assert (new / 'GLOSSX.md').read_text() == 'new instructions'
    assert (new / 'commands/task.md').read_text() == 'legacy command'
    assert (old / 'commands/task.md').exists()
    base = settings.normalize({'permissions': {'deny': ['fs_write(secret*)']}, 'trusted_plugins': ['trusted-plugin']})
    merged = settings.merge_project(base, {'permissions': {'allow': ['fs_write']}, 'trusted_plugins': ['project-plugin'], 'vcs': {'auto_commit': False}})
    assert merged['permissions']['deny'] == ['fs_write(secret*)']
    assert merged['trusted_plugins'] == ['trusted-plugin'] and not merged['vcs']['auto_commit']


def test_git_missing_is_explicit_and_busy_workspace_cannot_switch(client, project, monkeypatch):
    from app.harness import vcs
    from app.main import app
    pid = project['id']
    base = f'/api/workspace/{pid}/vcs'
    sid = ok(client.post('/api/sessions', json={'project_id': pid}))['id']
    rt = app.state.sessions._rt(sid)
    rt.running = True
    try:
        assert client.post(base + '/checkout', json={'branch': 'other'}).status_code == 409
        assert client.post('/api/sessions', json={'project_id': pid, 'branch': 'other'}).status_code == 409
    finally:
        rt.running = False
    monkeypatch.setattr(vcs, 'available', lambda: False)
    assert not ok(client.get(base + '/status'))['available']
    assert client.get(base + '/log').status_code == 409


@pytest.mark.skipif(not shutil.which('git'), reason='git is unavailable')
def test_interrupt_during_auto_commit_waits_for_git_and_finishes_turn(client, project, monkeypatch):
    from app.main import app
    from app.harness import vcs
    from app.providers import registry
    started, release = threading.Event(), threading.Event()
    async def fake(*args, **kwargs):
        yield 'Finished.'
    def blocked_commit(*args):
        started.set()
        assert release.wait(10), 'test did not release the git worker'
        return None
    monkeypatch.setattr(registry, 'stream', fake)
    monkeypatch.setattr(vcs, 'auto_commit', blocked_commit)
    pid = project['id']
    sid = ok(client.post('/api/sessions', json={'project_id': pid, 'mode': 'auto'}))['id']
    queue = app.state.sessions._rt(sid).subscribe()
    ok(client.post(f'/api/sessions/{sid}/messages', json={'text': 'Finish a turn'}))
    assert started.wait(10)
    responses = []
    stopping = threading.Thread(target=lambda: responses.append(client.post(f'/api/sessions/{sid}/interrupt')))
    stopping.start()
    try:
        time.sleep(.1)
        assert stopping.is_alive(), 'interrupt returned while git was still writing'
        assert ok(client.get(f'/api/sessions/{sid}'))['status'] == 'running'
        assert client.post(f'/api/workspace/{pid}/vcs/checkout', json={'branch': 'other'}).status_code == 409
    finally:
        release.set()
        stopping.join(15)
    assert not stopping.is_alive()
    assert ok(responses[0])['session']['status'] == 'idle'
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(event[0] == 'turn.completed' and event[1]['stop_reason'] == 'interrupted' for event in events)
    app.state.sessions._rt(sid).unsubscribe(queue)
