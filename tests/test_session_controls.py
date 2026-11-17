"""Thread controls: rewinding in place, per-thread reasoning effort, model suggestions."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.harness import workspace
from app.harness.manager import SessionBusyError, SessionManager


def _thread(manager):
    snapshot = manager.create(mode="ask")
    rt = manager._rt(snapshot["id"])
    t = time.time() - 100
    rt.session["messages"] = [
        {"id": "u1", "type": "user", "text": "first", "created_at": t},
        {"id": "a1", "type": "assistant", "text": "one", "created_at": t + 1},
        {"id": "u2", "type": "user", "text": "second", "created_at": t + 2},
        {"id": "c2", "type": "tool_call", "tool": "fs_write", "status": "done", "created_at": t + 3, "session_id": rt.id},
        {"id": "a2", "type": "assistant", "text": "two", "created_at": t + 4},
    ]
    rt.session["provider_thread"] = {"id": "old"}
    return rt


def test_rewind_to_user_message_drops_it_and_everything_after(client):
    manager = SessionManager()
    rt = _thread(manager)
    out = asyncio.run(manager.rewind(rt.id, "u2"))
    assert out["removed"] == 3 and out["reverted"] == []
    ids = [m["id"] for m in rt.session["messages"] if m.get("type") != "system_note"]
    assert ids == ["u1", "a1"]
    assert "provider_thread" not in rt.session
    assert any(m.get("kind") == "cache_reset" and m.get("reason") == "rewind" for m in rt.session["messages"])


def test_rewind_to_assistant_keeps_it_and_restores_files(client):
    manager = SessionManager()
    rt = _thread(manager)
    root = workspace.root_for(None)
    target = root / "rewind-me.txt"
    target.write_text("before", encoding="utf-8")
    # an edit made by this thread's later tool call, and an older one that must stay
    old = workspace.record_checkpoint("default", "rewind-me.txt", "older", "before", source="fs_write",
                                      session_id=rt.id, tool_call_id="c0")
    workspace._write_json(workspace._cp_dir("default") / f"{old['id']}.json",
                          {**workspace._load_cp("default", old["id"]), "created_at": time.time() - 1000})
    target.write_text("after", encoding="utf-8")
    workspace.record_checkpoint("default", "rewind-me.txt", "before", "after", source="fs_write",
                                session_id=rt.id, tool_call_id="c2")
    out = asyncio.run(manager.rewind(rt.id, "a1", include=True, restore_files=True))
    assert out["removed"] == 3 and out["reverted"] == ["rewind-me.txt"] and out["conflicts"] == []
    assert target.read_text(encoding="utf-8") == "before"
    assert [m["id"] for m in rt.session["messages"] if m.get("type") != "system_note"] == ["u1", "a1"]


def test_rewind_refuses_unknown_message_and_running_turn(client):
    manager = SessionManager()
    rt = _thread(manager)
    with pytest.raises(LookupError):
        asyncio.run(manager.rewind(rt.id, "nope"))
    rt.running = True
    with pytest.raises(SessionBusyError):
        asyncio.run(manager.rewind(rt.id, "u2"))
    rt.running = False


def test_rewind_endpoint(client):
    created = client.post("/api/sessions", json={"mode": "ask"}).json()
    manager = client.app.state.sessions
    rt = manager._rt(created["id"])
    rt.session["messages"] = [{"id": "u1", "type": "user", "text": "hi", "created_at": time.time()},
                              {"id": "a1", "type": "assistant", "text": "yo", "created_at": time.time()}]
    response = client.post(f"/api/sessions/{created['id']}/rewind", json={"message_id": "u1"})
    assert response.status_code == 200, response.text
    assert response.json()["removed"] == 2
    assert client.post(f"/api/sessions/{created['id']}/rewind", json={"message_id": "zz"}).status_code == 404


def test_thread_reasoning_effort_is_saved_validated_and_passed_on(client, monkeypatch):
    from app.providers import registry
    created = client.post("/api/sessions", json={"mode": "ask", "effort": "high"}).json()
    assert created["model"].get("effort") == "high"
    patched = client.patch(f"/api/sessions/{created['id']}", json={"model": {"effort": "low"}})
    assert patched.status_code == 200 and patched.json()["model"]["effort"] == "low"
    assert client.patch(f"/api/sessions/{created['id']}", json={"model": {"effort": "turbo"}}).status_code == 400
    cleared = client.patch(f"/api/sessions/{created['id']}", json={"model": {"effort": None}}).json()
    assert "effort" not in cleared["model"]

    seen = {}

    class Fake:
        async def stream(self, system, messages, model):
            from app.providers.base import runtime_option
            seen["effort"] = runtime_option("effort")
            yield "ok"

    monkeypatch.setitem(registry._PROVIDERS, "effort_contract", Fake())

    async def run():
        async for _ in registry.stream("sys", [{"role": "user", "content": "hi"}], provider="effort_contract", effort="max"):
            pass
    asyncio.run(run())
    assert seen["effort"] == "max"


def test_model_catalog_lists_claude_and_custom_models(client):
    client.post("/api/settings", json={"providers": {"local_claude": {"models": ["my-claude-id"]}}})
    models = client.get("/api/settings/models").json()["models"]
    ids = [m["id"] for m in models["local_claude"]]
    assert "opus" in ids and "my-claude-id" in ids
    assert any(m["id"] == "claude-sonnet-5-5" for m in models["anthropic"])
    client.post("/api/settings", json={"providers": {"local_claude": {"models": []}}})
