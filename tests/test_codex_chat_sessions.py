from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path

import pytest

from app.features import chat as chat_feature
from app.library import store
from app.providers import local_codex
from app.routers import ai


def test_first_turn_title_prefers_short_question_topic() -> None:
    assert (
        chat_feature.summarize_chat_title(
            "这篇论文的核心贡献是什么？", "这篇论文提出了一种新的训练方法。"
        )
        == "这篇论文的核心贡献是什么"
    )


def test_first_turn_title_uses_answer_for_long_selected_content() -> None:
    user = "Explain and discuss this selected content:\n\n" + "dense equation " * 20
    answer = "The equation defines the paper's contrastive objective. More detail follows."

    title = chat_feature.summarize_chat_title(user, answer)
    assert title.startswith("The equation defines the paper's contrastive")
    assert len(title) <= 48


def test_init_db_migrates_legacy_chat_session_columns(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE chats (
                   id TEXT PRIMARY KEY,
                   paper_id TEXT,
                   title TEXT,
                   created_at REAL
               )"""
        )
        connection.execute(
            "INSERT INTO chats (id,paper_id,title,created_at) VALUES (?,?,?,?)",
            ("chat-1", "paper-1", "Legacy", 1.0),
        )

    monkeypatch.setattr(store, "DB_PATH", db_path)
    store.init_db()

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(chats)").fetchall()
        }
        row = connection.execute(
            "SELECT title,provider,provider_session_id FROM chats WHERE id='chat-1'"
        ).fetchone()

    assert {"provider", "provider_session_id"} <= columns
    assert row == ("Legacy", None, None)


def _install_codex_process(
    monkeypatch, tmp_path: Path, *, returncode: int = 0, stderr: bytes = b""
) -> list[dict]:
    calls: list[dict] = []

    class Process:
        pid = 12345

        def __init__(self, args: tuple[str, ...]) -> None:
            self.args = args
            self.returncode = returncode

        async def communicate(self, input_bytes: bytes):
            calls[-1]["input"] = input_bytes.decode()
            if "-i" in self.args:
                calls[-1]["image_bytes"] = Path(
                    self.args[self.args.index("-i") + 1]
                ).read_bytes()
            if self.returncode == 0:
                output_path = Path(self.args[self.args.index("-o") + 1])
                output_path.write_text("session answer", encoding="utf-8")
            event = {
                "type": "thread.started",
                "thread_id": "019f8460-3ffe-7062-a8ce-c1b25ddebb65",
            }
            return (json.dumps(event).encode() + b"\n", stderr)

    async def create(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Process(args)

    async def command():
        return ["codex"]
    monkeypatch.setattr(local_codex, "_command", command)
    monkeypatch.setattr(local_codex, "ensure_codex_sandbox", lambda: tmp_path)
    monkeypatch.setattr(local_codex, "subprocess_group_options", lambda: {})
    monkeypatch.setattr(
        local_codex,
        "_cfg",
        lambda: {"model": "", "effort": "", "timeout": 60},
    )
    monkeypatch.setattr(local_codex.asyncio, "create_subprocess_exec", create)
    return calls


def test_codex_paper_chat_creates_then_resumes_persisted_session(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _install_codex_process(monkeypatch, tmp_path)
    provider = local_codex.LocalCodexProvider()

    first = asyncio.run(
        provider.complete_session(
            "paper system",
            [{"role": "user", "content": "first question"}],
        )
    )
    session_id = first[2]
    second = asyncio.run(
        provider.complete_session(
            "new retrieval context",
            [{"role": "user", "content": "follow up"}],
            session_id=session_id,
        )
    )

    first_args = calls[0]["args"]
    assert first_args[:2] == ("codex", "exec")
    assert "resume" not in first_args
    assert "--ephemeral" not in first_args
    assert ("-s", "read-only") == (
        first_args[first_args.index("-s")],
        first_args[first_args.index("-s") + 1],
    )
    assert calls[0]["input"].endswith("first question")

    second_args = calls[1]["args"]
    assert second_args[:3] == ("codex", "exec", "resume")
    assert session_id in second_args
    assert "--ephemeral" not in second_args
    assert "-C" not in second_args
    assert calls[1]["input"].endswith("follow up")
    assert second[2] == session_id


def test_codex_one_shot_completion_remains_ephemeral(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _install_codex_process(monkeypatch, tmp_path)

    text, _ = asyncio.run(
        local_codex.LocalCodexProvider().complete(
            "system", [{"role": "user", "content": "summarize"}]
        )
    )

    assert text == "session answer"
    assert "--ephemeral" in calls[0]["args"]


def test_codex_chat_passes_image_attachment_and_cleans_temp_file(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _install_codex_process(monkeypatch, tmp_path)
    image_data = b"\x89PNG\r\n\x1a\nchat-region"

    asyncio.run(
        local_codex.LocalCodexProvider().complete_session(
            "paper system",
            [{
                "role": "user",
                "content": "Explain the selected figure",
                "attachments": [{
                    "image_data_url": "data:image/png;base64,"
                    + base64.b64encode(image_data).decode()
                }],
            }],
        )
    )

    args = calls[0]["args"]
    image_path = Path(args[args.index("-i") + 1])
    assert "-i" in args
    assert calls[0]["image_bytes"] == image_data
    assert not image_path.exists()


def test_codex_missing_resume_target_has_specific_error(
    tmp_path: Path, monkeypatch
) -> None:
    _install_codex_process(
        monkeypatch,
        tmp_path,
        returncode=1,
        stderr=b"No saved session found with ID missing-session",
    )

    with pytest.raises(local_codex.CodexSessionUnavailableError):
        asyncio.run(
            local_codex.LocalCodexProvider().complete_session(
                "system",
                [{"role": "user", "content": "follow up"}],
                session_id="missing-session",
            )
        )


def test_chat_resume_sends_only_latest_turn_and_keeps_fresh_retrieval(
    monkeypatch,
) -> None:
    captured: dict = {}

    monkeypatch.setattr(chat_feature, "_build_context", lambda *_args: "fresh paper excerpt")
    monkeypatch.setattr(
        chat_feature.store,
        "get_chat",
        lambda _chat_id: {
            "id": "chat-1",
            "provider": "local_codex",
            "provider_session_id": "codex-session-1",
        },
    )
    monkeypatch.setattr(
        chat_feature.registry,
        "resolve_provider_name",
        lambda _provider=None: "local_codex",
    )

    async def complete_in_session(system, messages, **kwargs):
        captured.update(system=system, messages=messages, kwargs=kwargs)
        return "continued answer", {}, "codex-session-1"

    monkeypatch.setattr(
        chat_feature.registry, "complete_in_session", complete_in_session
    )
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow up"},
    ]
    state: dict = {}

    async def collect() -> str:
        chunks = []
        async for chunk in chat_feature.chat_stream(
            "paper-1", messages, chat_id="chat-1", session_state=state
        ):
            chunks.append(chunk)
        return "".join(chunks)

    assert asyncio.run(collect()) == "continued answer"
    assert captured["messages"] == [messages[-1]]
    assert captured["kwargs"]["session_id"] == "codex-session-1"
    assert "fresh paper excerpt" in captured["system"]
    assert state == {
        "provider": "local_codex",
        "provider_session_id": "codex-session-1",
    }


def test_missing_codex_session_rebuilds_from_complete_history(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(chat_feature, "_build_context", lambda *_args: "context")
    monkeypatch.setattr(
        chat_feature.store,
        "get_chat",
        lambda _chat_id: {
            "provider": "local_codex",
            "provider_session_id": "missing-session",
        },
    )
    monkeypatch.setattr(
        chat_feature.registry,
        "resolve_provider_name",
        lambda _provider=None: "local_codex",
    )

    async def complete_in_session(_system, messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        if kwargs["session_id"]:
            raise local_codex.CodexSessionUnavailableError("session not found")
        return "rebuilt", {}, "replacement-session"

    monkeypatch.setattr(
        chat_feature.registry, "complete_in_session", complete_in_session
    )
    messages = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]
    state: dict = {}

    async def collect() -> str:
        return "".join(
            [
                chunk
                async for chunk in chat_feature.chat_stream(
                    "paper-1",
                    messages,
                    chat_id="chat-1",
                    session_state=state,
                )
            ]
        )

    assert asyncio.run(collect()) == "rebuilt"
    assert calls[0]["messages"] == [messages[-1]]
    assert calls[0]["session_id"] == "missing-session"
    assert calls[1]["messages"] == messages
    assert calls[1]["session_id"] is None
    assert state["provider_session_id"] == "replacement-session"


def test_commit_chat_turn_updates_history_and_session_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    store.init_db()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO chats (id,paper_id,title,created_at) VALUES (?,?,?,?)",
            ("chat-1", "paper-1", "Chat", 1.0),
        )

    store.commit_chat_turn(
        "chat-1",
        "question",
        "answer",
        provider="local_codex",
        provider_session_id="session-1",
    )

    assert [(m["role"], m["content"]) for m in store.list_messages("chat-1")] == [
        ("user", "question"),
        ("assistant", "answer"),
    ]
    chat = store.get_chat("chat-1")
    assert chat["provider"] == "local_codex"
    assert chat["provider_session_id"] == "session-1"


def test_chat_endpoint_commits_codex_session_with_completed_turn(
    client, paper_id, monkeypatch
) -> None:
    chat_id = client.post(f"/api/papers/{paper_id}/chats", json={}).json()["id"]

    async def fake_chat_stream(_paper_id, messages, **kwargs):
        assert kwargs["chat_id"] == chat_id
        assert messages[-1]["content"] == "question"
        kwargs["session_state"].update(
            provider="local_codex",
            provider_session_id="codex-session-1",
        )
        yield "answer"

    monkeypatch.setattr(ai.chat_feat, "chat_stream", fake_chat_stream)

    response = client.post(
        "/api/chat",
        json={
            "paper_id": paper_id,
            "chat_id": chat_id,
            "messages": [{"role": "user", "content": "question"}],
        },
    )

    assert response.status_code == 200
    assert '"delta": "answer"' in response.text
    assert [(m["role"], m["content"]) for m in store.list_messages(chat_id)] == [
        ("user", "question"),
        ("assistant", "answer"),
    ]
    chat = store.get_chat(chat_id)
    assert chat["provider"] == "local_codex"
    assert chat["provider_session_id"] == "codex-session-1"
    assert chat["title"] == "question"
    public_chat = client.get(f"/api/papers/{paper_id}/chats").json()["chats"][0]
    assert "provider_session_id" not in public_chat


def test_codex_explicit_cli_bypasses_old_path(monkeypatch):
    selected = []
    monkeypatch.setattr(local_codex, "_cfg", lambda: {"cli_path": "C:/New Codex/codex.exe"})
    from app import cli_discovery
    monkeypatch.setattr(cli_discovery, "resolve_cli_command", lambda name: selected.append(name) or [name])
    assert asyncio.run(local_codex._command()) == ["C:/New Codex/codex.exe"]
    assert selected == ["C:/New Codex/codex.exe"]


def test_codex_failure_extracts_nested_server_message():
    reason = "The model is not supported when using Codex with a ChatGPT account."
    nested = json.dumps({"type": "error", "status": 400, "error": {"message": reason}})
    output = "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "x"},
        {"type": "error", "message": nested},
        {"type": "turn.failed", "error": {"message": nested}},
    ]).encode()
    assert local_codex._failure_detail(b"warning: can degrade performance and cause issues", output, "") == reason
