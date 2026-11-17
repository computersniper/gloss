"""Remote access over the internet: a Cloudflare quick tunnel to this backend.

``cloudflared tunnel --url http://127.0.0.1:<port>`` connects out to Cloudflare
and gets a random ``https://<words>.trycloudflare.com`` address; nothing on this
computer or the router is opened. It needs no Cloudflare account. The address
changes each time the tunnel starts, so a phone pairs again after a restart.

Requests through the tunnel reach the backend from cloudflared on 127.0.0.1;
``remote_access`` recognises them by Cloudflare's headers and treats them as
other devices (pairing required), never as this computer.

``cloudflared`` is used from PATH, from ``GLOSS_CLOUDFLARED``, or downloaded once
from Cloudflare's GitHub releases into the cache directory.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import config, remote_access

logger = logging.getLogger(__name__)

RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _asset() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    if sys.platform == "win32":
        return f"cloudflared-windows-{arch}.exe"
    if sys.platform == "darwin":
        return f"cloudflared-darwin-{arch}.tgz"
    return f"cloudflared-linux-{arch}"


def _cached() -> Path:
    return config.CACHE_DIR / "cloudflared" / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")


def find_binary() -> Path | None:
    pinned = os.environ.get("GLOSS_CLOUDFLARED")
    if pinned:
        return Path(pinned)
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    cached = _cached()
    return cached if cached.exists() else None


def _download() -> Path:
    target = _cached()
    target.parent.mkdir(parents=True, exist_ok=True)
    asset = _asset()
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        downloaded = Path(tmp) / asset
        with urllib.request.urlopen(RELEASES + asset, timeout=120) as response, downloaded.open("wb") as out:
            shutil.copyfileobj(response, out)
        if asset.endswith(".tgz"):
            with tarfile.open(downloaded) as archive:
                member = next(m for m in archive.getmembers() if Path(m.name).name == "cloudflared" and m.isfile())
                member.name = "cloudflared"
                archive.extract(member, tmp)
            downloaded = Path(tmp) / "cloudflared"
        downloaded.chmod(downloaded.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(downloaded, target)
    return target


class Tunnel:
    """One quick tunnel per backend, started and stopped from Settings."""

    def __init__(self) -> None:
        self.state = "off"  # off | downloading | starting | running | error
        self.url: str | None = None
        self.error = ""
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        return {"state": self.state, "url": self.url, "error": self.error}

    def start(self, port: int) -> None:
        if self._task and not self._task.done():
            return
        self.error = ""
        self._task = asyncio.create_task(self._run(port))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
        if task:
            task.cancel()
        self._process = None
        self.state, self.url = "off", None

    async def _run(self, port: int) -> None:
        try:
            binary = find_binary()
            if binary is None:
                self.state = "downloading"
                binary = await asyncio.to_thread(_download)
            self.state = "starting"
            self._process = await asyncio.create_subprocess_exec(
                str(binary), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            assert self._process.stdout is not None
            async for raw in self._process.stdout:
                line = raw.decode("utf-8", "replace")
                found = URL_PATTERN.search(line)
                if found and not self.url:
                    self.url = found.group(0)
                    self.state = "running"
                    logger.info("Remote access tunnel at %s", self.url)
            code = await self._process.wait()
            if self._task is not None:  # not a stop() from us
                self.state, self.url = "error", None
                self.error = f"cloudflared exited (code {code})"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # download or launch failed
            logger.exception("Remote access tunnel failed")
            self.state, self.url = "error", None
            self.error = str(exc) or exc.__class__.__name__


TUNNEL = Tunnel()


def listening_port() -> int | None:
    port = os.environ.get("GLOSS_BIND_PORT", "")
    return int(port) if port.isdigit() else None


def start_if_enabled(port: int | None = None) -> None:
    port = port or listening_port()
    if port and remote_access.load_state().get("tunnel") and TUNNEL.state in {"off", "error"} and not TUNNEL.error:
        TUNNEL.start(port)
