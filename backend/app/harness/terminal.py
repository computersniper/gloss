"""Owned, persistent shells for explicit local workspace terminal connections."""
from __future__ import annotations

import asyncio
import codecs
import os
import shutil
import signal
import sys
import uuid
import base64
import json
import re
import time
from contextlib import suppress as contextlib_suppress
from pathlib import Path

from .. import config
from ..platform_support import subprocess_group_options, terminate_process_tree
from . import workspace

POWERSHELL_BOOTSTRAP = r'''
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$pending = ''
Write-Output '__GLOSSX_TERMINAL_READY__'
while ($null -ne ($line = [Console]::ReadLine())) {
    $pending += $line + [Environment]::NewLine
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseInput($pending, [ref]$tokens, [ref]$errors) > $null
    if (@($errors | Where-Object { $_.IncompleteInput }).Count) { continue }
    try { Invoke-Expression $pending | Out-Default } catch { Write-Error $_ }
    $pending = ''
}
'''


SCROLLBACK_CHARS = 256_000
HISTORY_LIMIT = 1000
MAX_LIVE_PER_WORKSPACE = 8
SAVE_DELAY = 1.5
_ID = re.compile(r'^[a-f0-9]{32}$')


def store_dir(key: str) -> Path:
    """``<DATA_DIR>/terminals/<workspace key>/``: saved scrollback and command
    history, outside the workspace so they survive relocation and restarts."""
    path = config.DATA_DIR / 'terminals' / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def valid_id(session_id: str | None) -> bool:
    return bool(session_id and _ID.fullmatch(session_id))


def load_history(key: str) -> list[str]:
    try:
        data = json.loads((store_dir(key) / 'history.json').read_text(encoding='utf-8'))
        return [line for line in data if isinstance(line, str)][-HISTORY_LIMIT:]
    except (OSError, ValueError, TypeError):
        return []


def append_history(key: str, commands: list[str]) -> list[str]:
    history = load_history(key)
    for command in commands:
        if command and (not history or history[-1] != command):
            history.append(command)
    history = history[-HISTORY_LIMIT:]
    path = store_dir(key) / 'history.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(history, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)
    return history


def _meta_path(key: str, session_id: str) -> Path:
    return store_dir(key) / f'{session_id}.json'


def saved_sessions(key: str) -> dict[str, dict]:
    found = {}
    for path in store_dir(key).glob('*.json'):
        if not valid_id(path.stem):
            continue
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            found[path.stem] = meta
    return found


class TerminalSession:
    def __init__(self, key: str, cwd: Path, session_id: str | None = None, *,
                 title: str = '', scrollback: str = '', created: float | None = None):
        self.id = session_id if valid_id(session_id) else uuid.uuid4().hex
        self.key = key
        self.cwd = cwd
        self.title = title
        self.created = created or time.time()
        self.scrollback = scrollback[-SCROLLBACK_CHARS:]
        self.subscribers: set[asyncio.Queue] = set()
        self.exit_code = None
        self.ended = False
        self.pump_task = None
        self._save_handle = None
        self.on_exit = None
        self.process = None
        self.master = None
        self.job = None
        self.shell = None
        self.powershell = False
        self.pty = os.name != 'nt'
        self._close_task = None

    async def start(self):
        env = dict(os.environ)
        env.pop('BASH_ENV', None)
        env.pop('ENV', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        # Shell-native history is kept per workspace and flushed after every
        # prompt, so it survives both detaching and a hard kill.
        env['HISTFILE'] = str(store_dir(self.key) / 'shell_history')
        env['HISTSIZE'] = env['HISTFILESIZE'] = env['SAVEHIST'] = str(HISTORY_LIMIT)
        env['PROMPT_COMMAND'] = 'history -a'
        if os.name == 'nt':
            self.shell = shutil.which('powershell.exe') or shutil.which('cmd.exe')
            if not self.shell:
                raise RuntimeError('No PowerShell or cmd shell is installed')
            powershell = Path(self.shell).name.lower() == 'powershell.exe'
            self.powershell = powershell
            encoded = base64.b64encode(POWERSHELL_BOOTSTRAP.encode('utf-16-le')).decode('ascii')
            args = [self.shell, '-NoLogo', '-NoProfile', '-OutputFormat', 'Text', '-EncodedCommand', encoded] if powershell else [self.shell, '/D', '/Q']
            self.process = await asyncio.create_subprocess_exec(
                *args, cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                **subprocess_group_options(),
            )
            from .browser.job import BrowserJob
            self.job = BrowserJob()
            self.job.assign(self.process.pid)
            if powershell:
                async with asyncio.timeout(10):
                    while True:
                        line = await self.process.stdout.readline()
                        if b'__GLOSSX_TERMINAL_READY__' in line:
                            break
                        if not line:
                            raise RuntimeError('PowerShell exited before terminal initialization')
        else:
            import fcntl
            import pty
            import termios
            self.shell = shutil.which('bash') or shutil.which('zsh') or shutil.which('sh')
            if not self.shell:
                raise RuntimeError('No POSIX shell is installed')
            name = Path(self.shell).name
            args = [self.shell, '--noprofile', '--norc', '-i'] if name == 'bash' else [self.shell, '-f', '-i'] if name == 'zsh' else [self.shell, '-i']
            master, slave = pty.openpty()
            self.master = master
            os.set_blocking(master, False)

            def controlling_terminal():
                # Only one syscall after fork; modules are imported beforehand.
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            try:
                self.process = await asyncio.create_subprocess_exec(
                    *args, cwd=self.cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True, preexec_fn=controlling_terminal,
                )
            finally:
                os.close(slave)

    # -- tmux-style detach/attach -------------------------------------------
    def begin_pump(self):
        self.pump_task = asyncio.create_task(self._pump())

    async def _pump(self):
        try:
            async for message in self.output():
                if message['type'] == 'output':
                    self.scrollback = (self.scrollback + message['data'])[-SCROLLBACK_CHARS:]
                    self._schedule_save()
                else:
                    self.exit_code = message.get('code')
                self._publish(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - surface to attached clients
            self._publish({'type': 'error', 'message': str(error)})
        finally:
            self.ended = True
            self.save()
            if self.on_exit is not None:
                self.on_exit(self)

    def _publish(self, message):
        for queue in list(self.subscribers):
            queue.put_nowait(message)

    def attach(self) -> asyncio.Queue:
        """Subscribe; the caller replays ``self.scrollback`` first. No await
        happens in between, so no output is lost or duplicated."""
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def detach(self, queue: asyncio.Queue):
        self.subscribers.discard(queue)

    @property
    def attached(self) -> bool:
        return bool(self.subscribers)

    def _schedule_save(self):
        if self._save_handle is None:
            loop = asyncio.get_running_loop()
            self._save_handle = loop.call_later(SAVE_DELAY, self.save)

    def meta(self):
        return {'id': self.id, 'title': self.title, 'created': self.created,
                'cwd': str(self.cwd), 'scrollback': self.scrollback}

    def save(self):
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        try:
            path = _meta_path(self.key, self.id)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.meta(), ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, path)
        except OSError:
            pass

    def forget(self):
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        with contextlib_suppress(OSError):
            _meta_path(self.key, self.id).unlink()

    def summary(self):
        return {'id': self.id, 'title': self.title, 'created': self.created, 'cwd': str(self.cwd),
                'alive': not self.ended, 'attached': self.attached}

    def ready(self):
        return {'type': 'ready', 'id': self.id, 'title': self.title, 'shell': self.shell,
                'cwd': str(self.cwd), 'pty': self.pty, 'platform': sys.platform,
                'interrupt_mode': 'signal' if self.pty else 'close',
                'note': '' if self.pty else 'Windows uses shell pipes; interrupt stops this terminal. Reconnect to start a new shell.'}

    async def write(self, text: str):
        if os.name == 'nt':
            text = text.replace('\r\n', '\n').replace('\n', '\r\n')
        data = text.encode('utf-8')
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError('Terminal shell has exited')
        if self.master is None:
            self.process.stdin.write(data)
            await self.process.stdin.drain()
            return
        loop = asyncio.get_running_loop()
        while data:
            try:
                size = os.write(self.master, data)
                data = data[size:]
            except BlockingIOError:
                future = loop.create_future()
                loop.add_writer(self.master, lambda: future.done() or future.set_result(None))
                try:
                    await future
                finally:
                    loop.remove_writer(self.master)

    async def read(self):
        if self.master is None:
            return await self.process.stdout.read(4096)
        loop = asyncio.get_running_loop()
        while True:
            try:
                return os.read(self.master, 4096)
            except BlockingIOError:
                future = loop.create_future()
                loop.add_reader(self.master, lambda: future.done() or future.set_result(None))
                try:
                    await future
                finally:
                    loop.remove_reader(self.master)
            except OSError:
                return b''  # PTY returns EIO when the last slave closes.

    async def output(self):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        while data := await self.read():
            text = decoder.decode(data)
            if text:
                yield {'type': 'output', 'data': text}
        tail = decoder.decode(b'', final=True)
        if tail:
            yield {'type': 'output', 'data': tail}
        code = await self.process.wait()
        yield {'type': 'exit', 'code': code}

    async def interrupt(self):
        if self.pty and self.master is not None:
            await self.write('\x03')
            return True
        # CREATE_NO_WINDOW shells have no console for CTRL_BREAK. Do not claim
        # pipe bytes interrupt children: stop the owned tree instead.
        await self.close()
        return False

    def resize(self, cols: int, rows: int):
        if self.master is not None:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await self._close_task
            raise

    async def _close(self):
        announced = self.ended
        if self.pump_task is not None and self.pump_task is not asyncio.current_task():
            self.pump_task.cancel()
            await asyncio.gather(self.pump_task, return_exceptions=True)
        self.ended = True
        if not announced:
            self._publish({'type': 'exit', 'code': self.exit_code})
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.process is not None:
            if os.name != 'nt':
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await terminate_process_tree(self.process)
        if self.master is not None:
            os.close(self.master)
            self.master = None


class TerminalManager:
    """Live shells outlive their WebSocket: a client that goes away detaches,
    and the next client reattaches and replays the scrollback (like tmux).
    Scrollback and history are saved, so after a restart a session reopens
    in a fresh shell below its previous output."""

    def __init__(self):
        self.sessions: dict[str, TerminalSession] = {}
        self.closed = False

    def has_active(self, project_id: str | None):
        """A client is attached to a shell in this workspace."""
        key = workspace.key_for(project_id)
        return any(session.key == key and session.attached for session in self.sessions.values())

    def has_live(self, project_id: str | None):
        key = workspace.key_for(project_id)
        return any(session.key == key for session in self.sessions.values())

    def list(self, project_id: str | None) -> list[dict]:
        key = workspace.key_for(project_id)
        rows = {sid: {'id': sid, 'title': meta.get('title') or '', 'created': meta.get('created') or 0,
                      'cwd': meta.get('cwd') or '', 'alive': False, 'attached': False}
                for sid, meta in saved_sessions(key).items()}
        for session in self.sessions.values():
            if session.key == key:
                rows[session.id] = session.summary()
        return sorted(rows.values(), key=lambda row: row['created'])

    async def open(self, project_id: str | None, session_id: str | None = None):
        """Reattach to ``session_id`` when it is still running, reopen it from
        its saved scrollback when it is not, or start a new shell."""
        if self.closed:
            raise RuntimeError('Terminal service is stopping')
        key = workspace.key_for(project_id)
        if workspace.is_relocating(project_id):
            raise workspace.WorkspaceBusyError('Workspace relocation is in progress')
        if project_id:
            from ..agent import projects
            projects.get(project_id)
        live = self.sessions.get(session_id or '')
        if live is not None and live.key == key and not live.ended:
            return live
        if sum(1 for s in self.sessions.values() if s.key == key) >= MAX_LIVE_PER_WORKSPACE:
            raise RuntimeError(f'At most {MAX_LIVE_PER_WORKSPACE} terminals can run in one workspace')
        saved = saved_sessions(key).get(session_id) if valid_id(session_id) else None
        if saved:
            previous = str(saved.get('scrollback') or '')
            if previous and not previous.endswith('\n'):
                previous += '\n'
            session = TerminalSession(key, workspace.root_for(project_id), session_id,
                                      title=str(saved.get('title') or ''), created=saved.get('created'),
                                      scrollback=previous + '\r\n--- restored ---\r\n' if previous else '')
        else:
            number = 1 + max([0] + [int(m.group(1)) for row in self.list(project_id)
                                     if (m := re.fullmatch(r'Terminal (\d+)', row['title']))])
            session = TerminalSession(key, workspace.root_for(project_id), title=f'Terminal {number}')
        # Register before the first await so relocation cannot race shell launch.
        self.sessions[session.id] = session
        try:
            await session.start()
        except BaseException:
            await self.remove(session, forget=False)
            raise
        session.on_exit = self._ended
        session.save()
        session.begin_pump()
        return session

    def _ended(self, session):
        """The shell exited by itself: release it, keep its scrollback."""
        if self.sessions.get(session.id) is session:
            self.sessions.pop(session.id, None)
            asyncio.get_running_loop().create_task(session.close())

    def get(self, project_id: str | None, session_id: str):
        session = self.sessions.get(session_id)
        return session if session is not None and session.key == workspace.key_for(project_id) else None

    async def remove(self, session, *, forget: bool = True):
        """Kill the shell; ``forget`` also drops its saved scrollback."""
        try:
            await session.close()
        finally:
            self.sessions.pop(session.id, None)
            if forget:
                session.forget()

    async def delete(self, project_id: str | None, session_id: str) -> bool:
        if not valid_id(session_id):
            return False
        key = workspace.key_for(project_id)
        live = self.get(project_id, session_id)
        if live is not None:
            await self.remove(live)
            return True
        path = _meta_path(key, session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    async def close_detached(self, project_id: str | None):
        """Stop shells nobody is attached to (keeping their scrollback)."""
        key = workspace.key_for(project_id)
        await asyncio.gather(*(self.remove(s, forget=False) for s in list(self.sessions.values())
                               if s.key == key and not s.attached))

    async def close(self):
        self.closed = True
        await asyncio.gather(*(self.remove(session, forget=False) for session in list(self.sessions.values())))
