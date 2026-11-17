"""Opt-in real Chromium acceptance; uses isolated GlossX data and home.

Run: backend/.venv/Scripts/python.exe scripts/dev/verify_browser.py
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import subprocess
import ctypes
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
TEMP = Path(tempfile.mkdtemp(prefix="glossx-browser-acceptance-"))
os.environ["GLOSS_DATA_DIR"] = str(TEMP / "data")
os.environ["GLOSSX_HOME"] = str(TEMP / "home")
sys.path.insert(0, str(ROOT / "backend"))

from app.harness.browser.engine import BrowserManager
from app.harness.browser.cdp import CDPClient
from app.harness import settings
from app.library import store
from app.harness.browser.process import BrowserProcess

REPORT = {'data_dir': str(TEMP), 'checks': []}
suffix = '-api' if '--api-only' in sys.argv else '-lifecycle' if '--lifecycle-only' in sys.argv else ''
REPORT_PATH = ROOT / 'docs' / 'progress' / f'browser-verification{suffix}.json'


def check(name, value=True):
    assert value, name
    REPORT['checks'].append(name)
    print('PASS', name, flush=True)


def owned_pids(profile, root_pid):
    if os.name != 'nt':
        return [root_pid]
    # Inspect ownership, never terminate unrelated browser processes.
    command = 'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress'
    rows = json.loads(subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True))
    pids = {root_pid}
    changed = True
    while changed:
        children = {row['ProcessId'] for row in rows if row['ParentProcessId'] in pids}
        changed = bool(children - pids)
        pids |= children
    return sorted(pids)


def alive(pid):
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)

if os.environ.get("BROWSER_TRACE"):
    original_call = CDPClient.call

    async def traced_call(self, method, *args, **kwargs):
        print("START", method, kwargs.get("session_id"), flush=True)
        try:
            return await original_call(self, method, *args, **kwargs)
        finally:
            print("END", method, flush=True)

    CDPClient.call = traced_call


class Sessions:
    def _rt(self, sid):
        return SimpleNamespace(session={"project_id": None}, emit=lambda *args: None)


async def main():
    browser = BrowserManager(Sessions())
    pids = []
    local_server = None
    store.init_db()
    try:
        async with asyncio.timeout(240):
            check('installed browser', (await browser.status())['available'])
            result = await browser.action("acceptance", "open", {"url": "https://arxiv.org/abs/1706.03762"})
            check('public arxiv navigation', '1706.03762' in result['url'])
            read = await browser.action("acceptance", "read", {})
            check('arxiv readability', 'Attention Is All You Need' in read['text'])
            snap = await browser.action("acceptance", "snapshot", {})
            check('AX refs and link URLs', '[e' in snap['text'] and 'url="https://' in snap['text'])
            shot = await browser.action('acceptance', 'screenshot', {})
            check('workspace screenshot', shot['path'].endswith('.png') and shot['image_url'].startswith('/api/sessions/'))
            found = await browser.action('acceptance', 'find', {'text': 'attention'})
            check('find text', bool(found['matches']))
            downloaded = await browser.action('acceptance', 'download', {'url': 'https://arxiv.org/pdf/1706.03762'})
            check('guarded PDF download', downloaded['size'] > 1000)
            imported = await browser.action('acceptance', 'import_paper', {})
            check('arxiv import and workspace', bool(imported['paper_id']))
            frames = browser.frames('acceptance')
            frame = await anext(frames)
            check('JPEG viewer frame', len(frame['data']) > 100 and frame['width'] == 1280)
            await browser.action('acceptance', 'new_tab', {'url': 'about:blank'})
            await browser.action('acceptance', 'close_tab', {})
            for _ in range(20):
                frame = await anext(frames)
                if frame is not None:
                    break
            check('viewer survives tab closure', frame is not None)
            await frames.aclose()
            await browser.action('second', 'open', {'url': 'about:blank'})
            a = browser.states['acceptance']
            b = browser.states['second']
            check('session context isolation', a.context != b.context)
            foreign = a.active
            try:
                await browser.action('second', 'switch_tab', {'id': foreign})
            except LookupError:
                check('foreign tab refused')
            else:
                raise AssertionError('Foreign tab accepted')
            await browser.action('acceptance', 'takeover', {'controlling': True}, user=True)
            try:
                await browser.action('acceptance', 'read', {})
            except Exception as error:
                check('agent blocked during takeover', 'user is controlling' in str(error))
            await browser.action('acceptance', 'takeover', {'controlling': False}, user=True)
            process = browser.browser.process
            pids = await asyncio.to_thread(owned_pids, browser.browser.profile, process.pid)
            REPORT['public_browser_pids'] = pids
            await browser.close()
            check('all public browser descendants stopped', not any(alive(pid) for pid in pids))

            html = b'''<main><h1>Controlled article</h1><p>Readable article text</p>
<input aria-label="Note"><input type="password" aria-label="Password">
<select aria-label="Choice"><option>A</option><option>B</option></select>
<form><button>Submit</button></form><a href="/next">Next</a></main>
<script>document.querySelector('form').onsubmit=e=>{e.preventDefault();document.querySelector('p').textContent='Saved note'};</script>'''

            async def serve(reader, writer):
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: ' + str(len(html)).encode() + b'\r\n\r\n' + html)
                await writer.drain()
                writer.close()

            local_server = await asyncio.start_server(serve, '127.0.0.1', 0)
            port = local_server.sockets[0].getsockname()[1]
            local_url = f'http://127.0.0.1:{port}/'
            try:
                await browser.action('local', 'open', {'url': local_url})
            except ValueError:
                check('private URL blocked by default')
            settings.save({'browser': {'allow_private': True}})
            await browser.action('local', 'open', {'url': local_url})
            snap = await browser.action('local', 'snapshot', {})
            import re
            note_ref = re.search(r'\[(e\d+)\] textbox "Note"', snap['text']).group(1)
            select_ref = re.search(r'\[(e\d+)\] combobox "Choice"', snap['text']).group(1)
            await browser.action('local', 'type', {'ref': note_ref, 'text': 'Example'})
            await browser.action('local', 'select', {'ref': select_ref, 'value': 'B'})
            await browser.action('local', 'press', {'key': 'Tab'})
            await browser.action('local', 'scroll', {'direction': 'down'})
            page = browser.states['local'].pages[browser.states['local'].active]
            check('ref typing and select', await browser.evaluate(page, "document.querySelector('input').value==='Example' && document.querySelector('select').value==='B'"))
            link_ref = re.search(r'\[(e\d+)\] link "Next"', snap['text']).group(1)
            await browser.action('local', 'click', {'ref': link_ref})
            await browser.action('local', 'back', {})
            await browser.action('local', 'forward', {})
            check('ref click and navigation history', '/next' in browser.view('local')['url'])
            await browser.action('local', 'reload', {})
            try:
                await browser.element(page, note_ref, 'function(){return this.value}')
            except ValueError:
                check('navigation invalidates old refs')
            process = browser.browser.process
            pids = await asyncio.to_thread(owned_pids, browser.browser.profile, process.pid)
            REPORT['local_browser_pids'] = pids
    finally:
        await browser.close()
        if local_server:
            local_server.close()
            await local_server.wait_closed()
        check('local browser process tree stopped', not any(alive(pid) for pid in pids))


def api_check():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.providers import registry
    sequence = iter([
        ('browser_open', {'url': 'https://arxiv.org/abs/1706.03762'}),
        ('browser_snapshot', {}), ('browser_read', {}),
        ('browser_screenshot', {}), ('browser_import_paper', {}),
    ])

    async def fake(*args, **kwargs):
        item = next(sequence, None)
        if item:
            name, arguments = item
            yield '<tool_call>' + json.dumps({'name': name, 'arguments': arguments}) + '</tool_call>'
        else:
            yield 'Browser acceptance completed.'

    registry.stream = fake
    with TestClient(app) as client:
        response = client.post('/api/sessions', json={'title': 'Fake provider browser acceptance', 'mode': 'auto'})
        response.raise_for_status()
        sid = response.json()['id']
        client.post(f'/api/sessions/{sid}/messages', json={'text': 'Read and import Attention Is All You Need.'}).raise_for_status()
        deadline = time.monotonic() + 180
        approvals = 0
        while time.monotonic() < deadline:
            session = client.get(f'/api/sessions/{sid}').json()
            for pending in session.get('pending_approvals', []):
                call = pending.get('tool_call_id') or pending.get('id')
                client.post(f'/api/sessions/{sid}/approvals/{call}', json={'decision': 'once'}).raise_for_status()
                approvals += 1
            if session['status'] == 'idle':
                break
            time.sleep(.1)
        else:
            raise AssertionError('Fake provider turn timed out')
        calls = [m for m in session['messages'] if m.get('type') == 'tool_call']
        REPORT['api_calls'] = [{k: call.get(k) for k in ('tool', 'name', 'status')} for call in calls]
        check('five real browser tool calls via API', len(calls) == 5 and all(c['status'] == 'done' for c in calls))
        check('import explicitly approved', approvals > 0)
        check('import tool exposes paper', any('paper_id' in str(c) for c in calls))
        client.delete(f'/api/sessions/{sid}').raise_for_status()
        check('deleted session browser 404', client.get(f'/api/sessions/{sid}/browser').status_code == 404)


async def lifecycle_check():
    browser = BrowserProcess(TEMP / 'lifecycle', idle_timeout=.2)
    pids = []
    try:
        await browser.start()
        browser.acquire()
        pids = await asyncio.to_thread(owned_pids, browser.profile, browser.process.pid)
        await asyncio.sleep(.4)
        check('lease prevents idle shutdown', browser.process is not None and browser.process.returncode is None)
        watchdog = browser._idle_task
        browser.release()
        await asyncio.wait_for(asyncio.shield(watchdog), 10)
        check('idle shutdown stops descendants', not any(alive(pid) for pid in pids))
        browser.idle_timeout = 300
        await browser.start()
        pids = await asyncio.to_thread(owned_pids, browser.profile, browser.process.pid)
        browser.process.kill()
        await browser.process.wait()
        await browser.start()
        check('crash restart cleans old descendants', not any(alive(pid) for pid in pids))
        pids = await asyncio.to_thread(owned_pids, browser.profile, browser.process.pid)
    finally:
        await browser.close()
    check('restarted process tree closed', not any(alive(pid) for pid in pids))

    entered = asyncio.Event()
    original_open = CDPClient.open

    async def delayed_open(url):
        entered.set()
        await asyncio.Event().wait()

    CDPClient.open = staticmethod(delayed_open)
    cancelled = BrowserProcess(TEMP / 'cancelled')
    task = asyncio.create_task(cancelled.start())
    try:
        await asyncio.wait_for(entered.wait(), 25)
        pids = await asyncio.to_thread(owned_pids, cancelled.profile, cancelled.process.pid)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('Startup cancellation was swallowed')
        check('cancelled real startup cleans descendants', not any(alive(pid) for pid in pids))
    finally:
        CDPClient.open = original_open
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await cancelled.close()


if __name__ == "__main__":
    try:
        if '--api-only' not in sys.argv and '--lifecycle-only' not in sys.argv:
            asyncio.run(main())
            REPORT['direct_success'] = True
        if '--api' in sys.argv or '--api-only' in sys.argv:
            api_check()
        if '--lifecycle' in sys.argv or '--lifecycle-only' in sys.argv:
            asyncio.run(lifecycle_check())
        REPORT['success'] = True
    except BaseException as error:
        REPORT['success'] = False
        REPORT['error'] = repr(error)
        raise
    finally:
        destination = REPORT_PATH
        destination.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REPORT', destination, flush=True)
