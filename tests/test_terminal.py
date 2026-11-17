"""Real persistent shells, socket admission, workspace migration and cleanup."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from app.harness import workspace
from app.harness.terminal import TerminalSession, TerminalManager


def receive_until(socket, needle):
    text = ''
    for _ in range(60):
        message = socket.receive_json()
        assert message['type'] not in {'error', 'exit'}, message
        if message['type'] == 'output':
            text += message['data']
            if needle in text:
                return text
    pytest.fail(f'Terminal did not print {needle!r}: {text!r}')


def test_websocket_persistent_shell_cwd_unicode_and_disconnect(client):
    from app.main import app
    root = workspace.root_for(None)
    (root / 'terminal-child').mkdir(exist_ok=True)
    with client.websocket_connect('/api/workspace/default/terminal', headers={'origin': 'http://testserver'}) as socket:
        ready = socket.receive_json()
        assert ready['type'] == 'ready'
        assert ready['cwd'] == str(root)
        assert ready['pty'] == (os.name != 'nt')
        assert app.state.terminals.has_active(None)
        socket.send_json({'type': 'input', 'data': 'cd terminal-child\n'})
        socket.send_json({'type': 'input', 'data': 'echo GLOSSX终端_READY\npwd\n'})
        text = receive_until(socket, 'terminal-child')
        if 'GLOSSX终端_READY' not in text:
            text += receive_until(socket, 'GLOSSX终端_READY')
        assert 'GLOSSX终端_READY' in text
        socket.send_json({'type': 'resize', 'cols': 100, 'rows': 30})
        socket.send_json({'type': 'close'})
        with pytest.raises(WebSocketDisconnect):
            while True:
                socket.receive_json()
    assert not app.state.terminals.has_active(None)


@pytest.mark.parametrize('headers', [{'origin': 'https://evil.example'}, {'origin': 'null'}, {'host': 'evil.example', 'origin': 'http://evil.example'}])
def test_cross_origin_or_untrusted_host_cannot_launch_shell(client, headers):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect('/api/workspace/default/terminal', headers=headers):
            pytest.fail('Untrusted WebSocket was accepted')
    assert error.value.code == 1008


def test_terminal_refuses_relocating_workspace(client):
    workspace.begin_relocation('default')
    try:
        with client.websocket_connect('/api/workspace/default/terminal') as socket:
            message = socket.receive_json()
            assert message['type'] == 'error'
            assert 'relocation' in message['message']
    finally:
        workspace.end_relocation('default')


def test_real_shell_close_and_manager_shutdown(tmp_path):
    async def check():
        manager = TerminalManager()
        session = TerminalSession('default', tmp_path)
        manager.sessions[session.id] = session
        await session.start()
        process = session.process
        assert process.returncode is None
        await manager.close()
        assert process.returncode is not None
        assert not manager.has_active(None)
        with pytest.raises(RuntimeError, match='stopping'):
            await manager.open(None)
    asyncio.run(check())


def test_detach_reattach_replays_scrollback_and_keeps_history(client):
    from app.main import app
    with client.websocket_connect('/api/workspace/default/terminal', headers={'origin': 'http://testserver'}) as socket:
        ready = socket.receive_json()
        session_id = ready['id']
        socket.send_json({'type': 'input', 'data': 'echo DETACH_MARK_1\n'})
        receive_until(socket, 'DETACH_MARK_1\r\n')
        socket.send_json({'type': 'detach'})
    # The shell keeps running with nobody attached; relocation/branch checks ignore it.
    assert any(row['id'] == session_id and row['alive'] for row in client.get('/api/workspace/default/terminals').json()['sessions'])
    for _ in range(100):
        if not app.state.terminals.has_active(None):
            break
        time.sleep(0.02)
    assert not app.state.terminals.has_active(None)
    listing = client.get('/api/workspace/default/terminals').json()
    assert 'echo DETACH_MARK_1' in listing['history']
    with client.websocket_connect(f'/api/workspace/default/terminal?session={session_id}', headers={'origin': 'http://testserver'}) as socket:
        ready = socket.receive_json()
        assert ready['id'] == session_id
        assert 'echo DETACH_MARK_1' in ready['history']
        replay = socket.receive_json()
        assert replay.get('replay') and 'DETACH_MARK_1' in replay['data']
        socket.send_json({'type': 'close'})
        with pytest.raises(WebSocketDisconnect):
            while True:
                socket.receive_json()
    rows = client.get('/api/workspace/default/terminals').json()['sessions']
    assert all(row['id'] != session_id for row in rows)


def test_saved_session_reopens_after_restart(tmp_path):
    async def check():
        manager = TerminalManager()
        session = await manager.open(None)
        await session.write('echo RESTART_MARK\n')
        for _ in range(100):
            if 'RESTART_MARK\r\n' in session.scrollback:
                break
            await asyncio.sleep(0.05)
        await manager.close()  # app quits: shells die, scrollback stays on disk
        restarted = TerminalManager()
        rows = restarted.list(None)
        assert any(row['id'] == session.id and not row['alive'] for row in rows)
        again = await restarted.open(None, session.id)
        assert again.id == session.id and again.title == session.title
        assert 'RESTART_MARK' in again.scrollback and '--- restored ---' in again.scrollback
        assert await restarted.delete(None, session.id)
        await restarted.close()
        assert all(row['id'] != session.id for row in TerminalManager().list(None))
    asyncio.run(check())
