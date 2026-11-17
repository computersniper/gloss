from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.providers import local_claude


def _install_claude_process(
    monkeypatch,
    tmp_path: Path,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
) -> list[dict]:
    calls: list[dict] = []

    class Process:
        pid = 12345

        def __init__(self, args: tuple[str, ...]) -> None:
            self.args = args
            self.returncode = returncode

        async def communicate(self, input_bytes: bytes):
            calls[-1]["input"] = input_bytes.decode()
            payload = {
                "type": "result",
                "is_error": False,
                "result": "session answer",
                "session_id": "claude-session-1",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
            return json.dumps(payload).encode(), stderr

    async def create(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Process(args)

    async def command():
        return ["claude"]
    monkeypatch.setattr(local_claude, "_command", command)
    monkeypatch.setattr(local_claude, "_sandbox_env", lambda: ({}, str(tmp_path)))
    monkeypatch.setattr(local_claude, "_pick_model", lambda _model: "sonnet")
    monkeypatch.setattr(local_claude, "_effort", lambda: "")
    monkeypatch.setattr(local_claude, "_timeout", lambda: 60)
    monkeypatch.setattr(local_claude, "subprocess_group_options", lambda: {})
    monkeypatch.setattr(local_claude.asyncio, "create_subprocess_exec", create)
    return calls


def test_claude_shared_paper_session_creates_then_resumes(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _install_claude_process(monkeypatch, tmp_path)
    provider = local_claude.LocalClaudeProvider()

    first = asyncio.run(
        provider.complete_session(
            "paper system",
            [{"role": "user", "content": "first task"}],
        )
    )
    second = asyncio.run(
        provider.complete_session(
            "new feature system",
            [{"role": "user", "content": "follow-up task"}],
            session_id=first[2],
        )
    )

    assert "--no-session-persistence" not in calls[0]["args"]
    assert "--resume" not in calls[0]["args"]
    assert calls[0]["input"] == "first task"
    assert ("--resume", "claude-session-1") == (
        calls[1]["args"][calls[1]["args"].index("--resume")],
        calls[1]["args"][calls[1]["args"].index("--resume") + 1],
    )
    assert calls[1]["input"] == "follow-up task"
    assert second[2] == "claude-session-1"


def test_claude_one_shot_completion_disables_session_persistence(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _install_claude_process(monkeypatch, tmp_path)

    text, usage = asyncio.run(
        local_claude.LocalClaudeProvider().complete(
            "system",
            [{"role": "user", "content": "summarize"}],
        )
    )

    assert text == "session answer"
    assert usage["total_tokens"] == 12
    assert "--no-session-persistence" in calls[0]["args"]


def test_claude_missing_resume_target_has_specific_error(
    tmp_path: Path, monkeypatch
) -> None:
    _install_claude_process(
        monkeypatch,
        tmp_path,
        returncode=1,
        stderr=b"No conversation found with session ID missing-session",
    )

    with pytest.raises(local_claude.ClaudeSessionUnavailableError):
        asyncio.run(
            local_claude.LocalClaudeProvider().complete_session(
                "system",
                [{"role": "user", "content": "follow up"}],
                session_id="missing-session",
            )
        )
