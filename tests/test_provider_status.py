from __future__ import annotations

import asyncio
from pathlib import Path

from app.providers import local_codex, registry


class _HealthyProvider:
    async def test_connection(self) -> str:
        return "signed in"


class _BrokenProvider:
    async def test_connection(self) -> str:
        raise RuntimeError("not signed in")


def test_registry_records_successful_connection_status(monkeypatch) -> None:
    monkeypatch.setitem(registry._PROVIDERS, "healthy", _HealthyProvider())
    registry.reset_status("healthy")

    result = asyncio.run(registry.test_connection("healthy"))

    assert result["ok"] is True
    assert result["reply"] == "signed in"
    assert registry.provider_statuses()["healthy"]["status"] == "connected"


def test_registry_records_failed_connection_status(monkeypatch) -> None:
    monkeypatch.setitem(registry._PROVIDERS, "broken", _BrokenProvider())
    registry.reset_status("broken")

    result = asyncio.run(registry.test_connection("broken"))

    assert result["ok"] is False
    assert result["error"] == "not signed in"
    assert registry.provider_statuses()["broken"]["status"] == "error"


def test_local_codex_connection_uses_fast_login_status(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"Logged in using ChatGPT\n", b""

    async def create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Process()

    async def command():
        return ["codex"]
    monkeypatch.setattr(local_codex, "_command", command)
    monkeypatch.setattr(local_codex, "ensure_codex_sandbox", lambda: tmp_path)
    monkeypatch.setattr(local_codex, "subprocess_group_options", lambda: {"creationflags": 1})
    monkeypatch.setattr(local_codex.asyncio, "create_subprocess_exec", create)

    reply = asyncio.run(local_codex.LocalCodexProvider().test_connection())

    assert captured["args"] == ("codex", "login", "status")
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["creationflags"] == 1
    assert reply == "Logged in using ChatGPT\nCLI: codex"
