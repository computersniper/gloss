from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.providers import local_claude, local_codex


class _BlockingProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.pid = 12345
        self.started = asyncio.Event()

    async def communicate(self, _input=None):
        self.started.set()
        await asyncio.Event().wait()


class _Input:
    def write(self, _data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _StreamingProcess(_BlockingProcess):
    def __init__(self) -> None:
        super().__init__()
        self.stdin = _Input()
        event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
        }
        self.stdout = self._lines(json.dumps(event).encode() + b"\n")

    async def _lines(self, first: bytes):
        yield first
        await asyncio.Event().wait()

    async def wait(self):
        await asyncio.Event().wait()


def _install_process_mocks(monkeypatch, module, process):
    async def create(*_args, **_kwargs):
        return process

    terminated = asyncio.Event()

    async def terminate(proc):
        assert proc is process
        proc.returncode = -9
        terminated.set()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(module, "terminate_process_tree", terminate)
    async def command():
        return ["provider"]
    monkeypatch.setattr(module, "_command", command)
    return terminated


@pytest.mark.parametrize("module", [local_claude, local_codex])
def test_complete_cancellation_terminates_provider_process_tree(
    tmp_path: Path, monkeypatch, module
) -> None:
    async def exercise() -> None:
        process = _BlockingProcess()
        terminated = _install_process_mocks(monkeypatch, module, process)
        if module is local_claude:
            monkeypatch.setattr(module, "_pick_model", lambda _model: "model")
            monkeypatch.setattr(module, "_effort", lambda: "")
            monkeypatch.setattr(module, "_timeout", lambda: 60.0)
            monkeypatch.setattr(module, "_sandbox_env", lambda: ({}, str(tmp_path)))
        else:
            monkeypatch.setattr(
                module,
                "_cfg",
                lambda: {"model": "", "effort": "", "timeout": 60},
            )
            monkeypatch.setattr(module, "ensure_codex_sandbox", lambda: tmp_path)

        task = asyncio.create_task(module.LocalClaudeProvider().complete("", []) if module is local_claude else module.LocalCodexProvider().complete("", []))
        await process.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert terminated.is_set()

    asyncio.run(exercise())


def test_claude_stream_close_terminates_provider_process_tree(
    tmp_path: Path, monkeypatch
) -> None:
    async def exercise() -> None:
        process = _StreamingProcess()
        terminated = _install_process_mocks(monkeypatch, local_claude, process)
        monkeypatch.setattr(local_claude, "_pick_model", lambda _model: "model")
        monkeypatch.setattr(local_claude, "_effort", lambda: "")
        monkeypatch.setattr(local_claude, "_sandbox_env", lambda: ({}, str(tmp_path)))

        stream = local_claude.LocalClaudeProvider().stream("", [])
        assert await anext(stream) == "hello"
        await stream.aclose()
        assert terminated.is_set()

    asyncio.run(exercise())
