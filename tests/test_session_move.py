"""Moving a conversation changes its future workspace, preserving old work."""
from pathlib import Path


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_move_keeps_messages_files_and_resets_cli_resume(client):
    source = ok(client.post('/api/agent/projects', json={'name': 'Move source'}))
    target = ok(client.post('/api/agent/projects', json={'name': 'Move target'}))
    thread = ok(client.post('/api/sessions', json={'project_id': source['id'], 'title': 'Keep my chat'}))
    manager = client.app.state.sessions
    rt = manager._rt(thread['id'])
    original = {'id': 'original', 'type': 'user', 'text': 'Keep this message', 'attachments': [], 'created_at': 1}
    rt.session['messages'].append(original)
    rt.session['provider_thread'] = {'id': 'old-cli-session'}
    old_file = Path(source['workspace']['path']) / 'original.txt'
    old_file.write_text('keep the old work', encoding='utf-8')
    moved = ok(client.post(f"/api/sessions/{thread['id']}/project", json={'project_id': target['id']}))
    assert moved['project_id'] == target['id'] and moved['project']['name'] == 'Move target'
    assert moved['workspace']['path'] == target['workspace']['path']
    assert any(m.get('id') == 'original' for m in moved['messages'])
    assert 'provider_thread' not in rt.session
    assert old_file.read_text(encoding='utf-8') == 'keep the old work'
    assert not (Path(target['workspace']['path']) / 'original.txt').exists()
    assert thread['id'] not in {s['id'] for s in ok(client.get('/api/sessions', params={'project_id': source['id']}))}
    assert thread['id'] in {s['id'] for s in ok(client.get('/api/sessions', params={'project_id': target['id']}))}
    assert client.post(f"/api/sessions/{thread['id']}/rewind", json={'message_id': 'original', 'restore_files': True}).status_code == 400
    from app.harness.manager import SessionManager
    assert SessionManager().get(thread['id'])['project_id'] == target['id']
    general = ok(client.post(f"/api/sessions/{thread['id']}/project", json={'project_id': None}))
    assert general['project_id'] is None and general['project'] is None


def test_move_validation_and_archived_status(client):
    target = ok(client.post('/api/agent/projects', json={'name': 'Unavailable target'}))
    thread = ok(client.post('/api/sessions', json={}))
    url = f"/api/sessions/{thread['id']}/project"
    rt = client.app.state.sessions._rt(thread['id'])
    rt.running = True
    try:
        assert client.post(url, json={'project_id': target['id']}).status_code == 409
    finally:
        rt.running = False
    assert client.post(url, json={'project_id': 'missing'}).status_code == 404
    assert client.post(url, json={}).status_code == 422
    ok(client.post(f"/api/agent/projects/{target['id']}/lifecycle", json={'action': 'archive'}))
    assert client.post(url, json={'project_id': target['id']}).status_code == 400
    ok(client.post(f"/api/agent/projects/{target['id']}/lifecycle", json={'action': 'restore'}))
    ok(client.patch(f"/api/sessions/{thread['id']}", json={'archived': True}))
    assert ok(client.post(url, json={'project_id': target['id']}))['archived']
