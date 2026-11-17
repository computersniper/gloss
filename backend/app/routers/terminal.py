"""Same-origin, local-client WebSocket access to an explicit user's shell."""
from __future__ import annotations

import asyncio
import ipaddress
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

router = APIRouter(tags=['terminal'])


def local_client(websocket):
    client = websocket.client.host if websocket.client else ''
    test_client = client == 'testclient'
    try:
        if not test_client and not ipaddress.ip_address(client).is_loopback:
            return False
    except ValueError:
        return False
    host = websocket.url.hostname
    if host not in {'localhost', '127.0.0.1', '::1'} and not (test_client and host == 'testserver'):
        return False
    origin = websocket.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        expected_scheme = 'https' if websocket.url.scheme == 'wss' else 'http'
        expected_port = websocket.url.port or (443 if expected_scheme == 'https' else 80)
        try:
            origin_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        except ValueError:
            return False
        if parsed.scheme != expected_scheme or parsed.hostname != host or origin_port != expected_port:
            return False
    return True


@router.get('/api/workspace/{project_id}/terminals')
async def list_terminals(project_id: str, request: Request):
    """Running and saved terminal sessions of a workspace, plus its command history."""
    from ..harness import terminal as terminal_store, workspace
    project = None if project_id == 'default' else project_id
    try:
        key = workspace.key_for(project)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {'sessions': request.app.state.terminals.list(project),
            'history': terminal_store.load_history(key)}


@router.delete('/api/workspace/{project_id}/terminals/{session_id}')
async def delete_terminal(project_id: str, session_id: str, request: Request):
    """Kill a session's shell (if running) and forget its saved scrollback."""
    try:
        found = await request.app.state.terminals.delete(None if project_id == 'default' else project_id, session_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not found:
        raise HTTPException(404, 'Terminal session not found')
    return {'ok': True}


@router.websocket('/api/workspace/{project_id}/terminal')
async def terminal(websocket: WebSocket, project_id: str):
    """Attach to a shell. ``?session=<id>`` reattaches to a running one (or
    reopens a saved one); disconnecting detaches and leaves it running; a
    ``close`` message ends it."""
    if not local_client(websocket):
        await websocket.close(code=1008, reason='Terminal requires a local same-origin client')
        return
    await websocket.accept()
    from ..harness.terminal import append_history, load_history
    manager = websocket.app.state.terminals
    session = None
    queue = None
    pump = None
    user_closed = False
    project = None if project_id == 'default' else project_id
    try:
        session = await manager.open(project, websocket.query_params.get('session') or None)
        queue = session.attach()
        replay = session.scrollback
        await websocket.send_json({**session.ready(), 'history': load_history(session.key)})
        if replay:
            await websocket.send_json({'type': 'output', 'data': replay, 'replay': True})

        async def output():
            while True:
                message = await queue.get()
                await websocket.send_json(message)
                if message['type'] == 'exit':
                    await websocket.close(code=1000)
                    return

        pump = asyncio.create_task(output())
        pending = ''
        while True:
            message = await websocket.receive_json()
            kind = message.get('type')
            if kind == 'input':
                data = message.get('data')
                if not isinstance(data, str) or len(data) > 65536:
                    raise ValueError('Terminal input must be text of at most 65536 characters')
                await session.write(data)
                pending += data
                if '\n' in pending:
                    *lines, pending = pending.split('\n')
                    pending = pending[-4096:]
                    commands = [line.strip() for line in lines if line.strip() and len(line) <= 4096]
                    if commands:
                        await asyncio.to_thread(append_history, session.key, commands)
            elif kind == 'interrupt':
                if not await session.interrupt():
                    break
            elif kind == 'resize':
                cols, rows = int(message.get('cols', 100)), int(message.get('rows', 30))
                if not (20 <= cols <= 500 and 5 <= rows <= 200):
                    raise ValueError('Invalid terminal dimensions')
                session.resize(cols, rows)
            elif kind == 'close':
                user_closed = True
                break
            elif kind == 'detach':
                break
            else:
                raise ValueError('Unknown terminal input type')
    except WebSocketDisconnect:
        pass
    except (ValueError, LookupError, OSError, RuntimeError) as error:
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json({'type': 'error', 'message': str(error)})
    finally:
        # Detach synchronously first: a cancelled handler may not get to run
        # anything after its next await.
        if session is not None and queue is not None:
            session.detach(queue)
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        if session is not None:
            if user_closed:
                await manager.remove(session)
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1000)
