"""Lazy Chromium process lifecycle, using a GlossX-owned (never user) profile."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import os
import signal
import uuid
import shutil
from collections import deque
from pathlib import Path

from ...platform_support import subprocess_group_options, terminate_process_tree
from .cdp import CDPClient, CDPError
from .discovery import BrowserExecutable, discover_all

logger = logging.getLogger(__name__)
# Profiles of crashed or killed GlossX processes are removed after a day.
STALE_PROFILE_SECONDS = 24 * 3600
DESKTOP_BROWSER = "GlossX built-in browser"


def desktop_endpoint() -> str | None:
    """The CDP endpoint of the desktop app's own Chromium, when running inside it."""
    return os.environ.get("GLOSSX_BROWSER_CDP") or None


class BrowserProcess:
    def __init__(self, data_dir: Path, *, path: str | None = None, headed: bool = False,
                 idle_timeout: float = 300):
        self._profile_parent = data_dir.resolve() / "browser"
        # A fixed profile may hand off to a different running desktop instance
        # or retain bad startup state. Never share it or touch a personal profile.
        self.profile = self._new_profile()
        self._cleaned = False
        self.path = path
        self.headed = headed
        self.idle_timeout = idle_timeout
        self.process: asyncio.subprocess.Process | None = None
        self.client: CDPClient | None = None
        self.executable: BrowserExecutable | None = None
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None
        self._last_used = time.monotonic()
        self._leases = 0
        self.proxy_port = None
        self._stderr_task = None
        self._stderr_tail = deque(maxlen=30)
        self._job = None

    async def _drain_stderr(self, stream):
        while chunk := await stream.readline():
            self._stderr_tail.append(chunk.decode('utf-8', 'replace').strip()[:1000])

    def _startup_error(self, message):
        detail = '\n'.join(self._stderr_tail)[-6000:]
        return CDPError(message + (':\n' + detail if detail else ''))

    def touch(self):
        self._last_used = time.monotonic()

    def acquire(self):
        """An active operation/viewer prevents the idle watchdog closing CDP."""
        self._leases += 1
        self.touch()

    def release(self):
        self._leases = max(0, self._leases - 1)
        self.touch()

    def _new_profile(self) -> Path:
        return self._profile_parent / f"profile-{os.getpid()}-{uuid.uuid4().hex[:12]}"

    def _remove_profile(self, profile: Path):
        # Only remove this process's random, explicitly owned folders.
        # Preserve legacy DATA/browser/profile and other live instances.
        resolved = profile.resolve()
        if resolved.parent == self._profile_parent and resolved.name.startswith(f'profile-{os.getpid()}-'):
            shutil.rmtree(resolved, True)

    def _remove_stale_profiles(self):
        """Folders left by earlier GlossX processes that crashed or were killed."""
        mine = f'profile-{os.getpid()}-'
        cutoff = time.time() - STALE_PROFILE_SECONDS
        try:
            entries = list(self._profile_parent.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir() and entry.name.startswith('profile-') and not entry.name.startswith(mine) \
                        and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, True)
            except OSError:
                continue

    def _child_env(self) -> dict[str, str]:
        """The browser must not inherit the desktop shell's WebView2 or bundler state."""
        env = dict(os.environ)
        for key in list(env):
            upper = key.upper()
            if upper.startswith(('WEBVIEW2_', '_PYI_', 'PYINSTALLER_')) or upper in {'_MEIPASS2', 'PYTHONHOME', 'PYTHONPATH'}:
                env.pop(key)
        return env

    def _args(self, executable: BrowserExecutable, profile: Path) -> list[str]:
        args = [str(executable.path), "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--disable-background-networking", "--disable-sync", "--disable-component-update",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                # A background-mode browser can swallow our launch; logging to
                # stderr puts the reason for any early exit into the error.
                "--disable-background-mode", "--enable-logging=stderr"]
        args += ["--disable-quic", "--disable-features=WebRtcAllowInputVolumeAdjustment,MediaRouter"]
        if self.proxy_port:
            args += [f"--proxy-server=http://127.0.0.1:{self.proxy_port}",
                     "--proxy-bypass-list=<-loopback>",
                     "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
        if not self.headed:
            args.append("--headless=new")
        args.append("about:blank")
        return args

    async def start(self) -> CDPClient:
        async with self._lock:
            self.touch()
            endpoint = desktop_endpoint()
            if endpoint:
                # The desktop app's pages are part of its window: there is no
                # process to launch, and no idle shutdown under a page in view.
                if self.client is None or self.client._closed:
                    self.client = await CDPClient.open(endpoint)
                    self.executable = BrowserExecutable(DESKTOP_BROWSER, Path("electron"))
                return self.client
            if self.process is not None and self.process.returncode is None and self.client is not None and not self.client._closed:
                return self.client
            await self._stop()
            candidates = await asyncio.to_thread(discover_all, self.path)
            if not candidates:
                raise CDPError("No browser found. Install Edge/Chrome/Chromium or set browser.path in GlossX settings.")
            if not self._cleaned:
                self._cleaned = True
                await asyncio.to_thread(self._remove_stale_profiles)
            failures = []
            for executable in candidates:
                # An exit with code 0 before DevTools is ready means the launch was
                # handed to another browser process; a fresh profile avoids that.
                for attempt in range(2):
                    try:
                        return await self._launch(executable)
                    except CDPError as error:
                        failures.append(f"{executable.browser} ({executable.path}): {error}")
                        logger.warning("Browser launch failed: %s", failures[-1])
                        if getattr(error, 'exit_code', None) != 0:
                            break
            if len(failures) == 1:
                raise CDPError(failures[0])
            raise CDPError("Every installed browser failed to start:\n" + "\n\n".join(failures))

    async def _launch(self, executable: BrowserExecutable) -> CDPClient:
        self.executable = executable
        previous, self.profile = self.profile, self._new_profile()
        if previous != self.profile:
            await asyncio.to_thread(self._remove_profile, previous)
        await asyncio.to_thread(self.profile.mkdir, parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        args = self._args(executable, self.profile)
        try:
            self._stderr_tail.clear()
            self.process = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, env=self._child_env(),
                **subprocess_group_options(),
            )
            if os.name == 'nt':
                from .job import BrowserJob
                self._job = BrowserJob()
                self._job.assign(self.process.pid)
            self._stderr_task = asyncio.create_task(self._drain_stderr(self.process.stderr))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.returncode is not None:
                    code = self.process.returncode
                    # Let the drain task collect what the browser printed.
                    await asyncio.wait({self._stderr_task}, timeout=1)
                    error = self._startup_error(f"Browser exited during startup (code {code})")
                    error.exit_code = code
                    raise error
                try:
                    lines = (await asyncio.to_thread(port_file.read_text, encoding="utf-8")).splitlines()
                    port = int(lines[0])
                    endpoint = lines[1]
                    if not 1 <= port <= 65535 or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", endpoint):
                        raise ValueError("Invalid debugger endpoint")
                except (OSError, ValueError, IndexError):
                    await asyncio.sleep(0.05)
                    continue
                self.client = await CDPClient.open(f"ws://127.0.0.1:{port}{endpoint}")
                self._idle_task = asyncio.create_task(self._watch_idle(), name="glossx-browser-idle")
                logger.info("Browser started: %s, profile %s", executable.path, self.profile)
                return self.client
            raise self._startup_error("Browser startup timed out; check browser.path and profile permissions")
        except BaseException:
            # Includes cancelled launch: don't leave a child running.
            cleanup = asyncio.create_task(self._stop())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            raise

    async def _watch_idle(self):
        while True:
            await asyncio.sleep(max(0.05, min(30, self.idle_timeout / 2)))
            if not self._leases and time.monotonic() - self._last_used >= self.idle_timeout:
                async with self._lock:
                    if not self._leases and time.monotonic() - self._last_used >= self.idle_timeout:
                        await self._stop()
                        return

    async def _stop(self):
        task, self._idle_task = self._idle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        client, self.client = self.client, None
        process, self.process = self.process, None
        # terminate_process_tree uses communicate(), so relinquish ownership of
        # stderr before it starts draining subprocess pipes.
        stderr_task, self._stderr_task = self._stderr_task, None
        if stderr_task is not None:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        try:
            if client is not None:
                await client.close()
        finally:
            job, self._job = self._job, None
            if job is not None:
                job.close()
            if process is not None:
                if os.name != 'nt':
                    # The parent may already have exited while its pgid still
                    # contains renderers. The generic helper returns early then.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await terminate_process_tree(process)

    async def close(self):
        async with self._lock:
            cleanup = asyncio.create_task(self._stop())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            finally:
                await asyncio.to_thread(self._remove_profile, self.profile)
