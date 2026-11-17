"""Cache accounting, stable history and safe transport resume contracts."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import httpx
import pytest

from app.harness import context, engine, permissions
from app.harness.session import new_session, Runtime, public_message
from app.harness.tools import ToolSpec
from app.library import store
from app.providers import registry
from app.providers.base import anthropic_usage, normalize_usage, capture_stream_usage
from app.providers.local_codex import _usage_from_jsonl, _usage_for_call, _load_usage, _save_usage
from app.providers.anthropic_api import AnthropicProvider
from app.providers import openai_api, anthropic_api


def _session():
    return new_session(title="cache", project_id=None, mode="ask", provider=None, model=None, context_limit=120000)


def test_exact_provider_usage_and_missing_fallback():
    events = b'noise\n' + json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 22029, "cached_input_tokens": 9856, "output_tokens": 5, "reasoning_output_tokens": 2}}).encode()
    usage = normalize_usage(_usage_from_jsonl(events))
    assert (usage["input_tokens"], usage["cached_input_tokens"], usage["reasoning_tokens"], usage["exact"]) == (22029, 9856, 2, True)
    claude = normalize_usage(anthropic_usage({"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30, "output_tokens": 4}))
    assert (claude["input_tokens"], claude["cached_input_tokens"], claude["cache_write_tokens"]) == (60, 20, 30)
    missing = normalize_usage({}, "rules", [{"role": "user", "content": "hello"}], "answer")
    assert missing["input_tokens"] > 0 and missing["output_tokens"] > 0 and not missing["exact"]
    assert normalize_usage({"input_tokens": 0, "output_tokens": 0, "exact": True})["exact"]


def test_codex_thread_totals_delta_and_restart_baseline(tmp_path, monkeypatch):
    from app.providers import local_codex
    monkeypatch.setattr(local_codex.app_config, "DATA_DIR", tmp_path)
    first = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10, "reasoning_tokens": 2, "exact": True}
    _save_usage("thread", first)
    # Reads from disk, so a new provider/process can resume without recounting.
    assert _load_usage("thread")["input_tokens"] == 100
    next_total = {"input_tokens": 220, "cached_input_tokens": 140, "output_tokens": 25, "reasoning_tokens": 6, "exact": True}
    call = _usage_for_call(next_total, _load_usage("thread"), True)
    assert (call["input_tokens"], call["cached_input_tokens"], call["output_tokens"], call["reasoning_tokens"]) == (120, 100, 15, 4)
    assert call["_thread_usage"]["input_tokens"] == 220 and call["exact"]
    assert not _usage_for_call(next_total, None, True)["exact"]
    assert not _usage_for_call(first, next_total, True)["exact"]
    # New replacement thread starts from zero despite a stale caller baseline.
    assert _usage_for_call(first, next_total, False)["input_tokens"] == 100


def test_prompt_prefix_survives_mode_scope_day_todos_branch(monkeypatch):
    session = _session()
    monkeypatch.setattr(context, "instructions", lambda _: [("Global", "global rules"), ("Project", "project rules")])
    monkeypatch.setattr(context.store, "get_paper", lambda _: {"title": "Paper", "year": "2026"})
    table = {name: ToolSpec(name, name, {}, category, None) for name, category in [("z_write", "write"), ("a_read", "read")]}
    session["messages"] = [{"id": "u1", "type": "user", "text": "first"}]
    first_sys = context.system_prompt(session, table, workspace_root=None)
    first_msgs = copy.deepcopy(context.provider_messages(session))
    session["messages"].append({"type": "assistant", "text": "answer"})
    session.update(mode="auto", scope_papers=["paper"], todos=[{"content": "task", "status": "pending"}], branch="experiment")
    monkeypatch.setattr(context.time, "strftime", lambda _: "2099-01-01")
    session["messages"].append({"id": "u2", "type": "user", "text": "second"})
    second_sys = context.system_prompt(session, dict(reversed(list(table.items()))), workspace_root=None)
    second_msgs = context.provider_messages(session)
    assert first_sys == second_sys
    assert context.HOST_RULE in first_sys and first_sys.index("a_read:") < first_sys.index("z_write:")
    assert first_msgs == second_msgs[:len(first_msgs)]
    assert "2099-01-01" not in first_sys and "2099-01-01" in second_msgs[-1]["content"]
    assert "experiment" in second_msgs[-1]["content"] and "MODE: auto" in second_msgs[-1]["content"]
    assert "_prompt_item" not in public_message(session["messages"][0])
    assert not permissions.mode_allows("ask", table["z_write"].category)


def test_old_usage_ledger_migration_preserves_records(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(store, "DB_PATH", path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE ai_usage(id TEXT PRIMARY KEY,task_type TEXT NOT NULL,provider TEXT NOT NULL,model TEXT,paper_id TEXT,plugin_id TEXT,prompt_tokens INTEGER NOT NULL DEFAULT 0,completion_tokens INTEGER NOT NULL DEFAULT 0,total_tokens INTEGER NOT NULL DEFAULT 0,estimated INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL)")
        db.execute("INSERT INTO ai_usage VALUES ('old','chat','openai',NULL,NULL,NULL,100,10,110,0,1)")
    store.init_db()
    store.init_db()
    store.record_ai_usage(task_type="chat", provider="openai", model=None, paper_id=None, plugin_id=None,
                          prompt_tokens=200, completion_tokens=20, total_tokens=220, estimated=False, cached_input_tokens=150, cache_write_tokens=20)
    summary = store.ai_usage_summary()
    assert summary["total"]["tasks"] == 2 and summary["total"]["prompt_tokens"] == 300
    assert summary["total"]["cached_input_tokens"] == 150 and summary["total"]["cache_hit_rate"] == 0.5
    assert summary["by_provider"][0]["provider"] == "openai" and len(summary["by_day"]) == 2
    assert next(row for row in summary["recent"] if row["id"] == "old")["cached_input_tokens"] == 0


def test_harness_resume_suffix_and_reset_fallback(monkeypatch):
    session = _session()
    calls = []
    class CLI:
        async def complete_session(self, system, messages, model, *, session_id=None):
            calls.append((system, copy.deepcopy(messages), model, session_id))
            if session_id == "gone":
                raise registry.ProviderSessionUnavailableError("thread not found")
            return "answer", {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 1, "exact": True}, "thread"
    p = CLI()
    messages = [{"role": "user", "content": "first"}]
    async def call(model="test"):
        with registry.harness_session(session), capture_stream_usage() as usage:
            result = "".join([part async for part in registry._harness_stream(p, "local_codex", "stable system", messages, model)])
            assert result == "answer" and usage["cached_input_tokens"] == 80
    asyncio.run(call())
    messages.extend([{"role": "assistant", "content": "answer"}, {"role": "user", "content": "tool result"}])
    asyncio.run(call())
    assert calls[-1] == ("", [messages[-1]], "test", "thread")
    session["provider_thread"]["id"] = "gone"
    messages.extend([{"role": "assistant", "content": "answer"}, {"role": "user", "content": "third"}])
    asyncio.run(call())
    assert calls[-2][3] == "gone" and calls[-1][3] is None and calls[-1][1] == messages
    messages.extend([{"role": "assistant", "content": "answer"}, {"role": "user", "content": "fourth"}])
    asyncio.run(call("new-model"))
    assert calls[-1][3] is None
    session["context"]["compactions"] = 1
    asyncio.run(call("new-model"))
    assert calls[-1][3] is None


def test_anthropic_cache_breakpoints_do_not_change_text():
    body = AnthropicProvider()._body("identity\n\n# Tools\n- read", [{"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "new"}], "claude", False)
    assert len(body["system"]) == 2 and all(block["cache_control"] == {"type": "ephemeral"} for block in body["system"])
    assert body["messages"][-2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][-1]["content"] == "new"


def test_session_meter_emits_exact_usage_and_persists_turn_note(monkeypatch):
    session = _session()
    class Manager:
        def inbox_changed(self, *_): pass
    rt = Runtime(session, Manager())
    emitted = []
    monkeypatch.setattr(rt, "emit", lambda name, data: emitted.append((name, data)))
    monkeypatch.setattr(rt, "persist", lambda: None)
    meter = engine.UsageMeter(session, rt)
    for _ in range(2):
        meter.before_llm_call()
        meter.after_usage({"input_tokens": 100, "cached_input_tokens": 75, "output_tokens": 10, "exact": True})
    assert session["usage"]["llm_calls"] == 2 and session["usage"]["cache_hit_rate"] == .75
    meter.turn["duration_seconds"] = 1.2
    engine.finish_turn(rt, {"message_id": "m_end", "usage": meter.turn})
    assert [name for name, _ in emitted].count("usage.updated") == 2
    note = next(m for m in session["messages"] if m.get("kind") == "turn_usage")
    assert note["usage"]["input_tokens"] == 200 and note["turn_id"] == "m_end"


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_stream_exact_cache_usage_reaches_session_and_ledger(client, monkeypatch, kind):
    session = _session()
    rt = Runtime(session, object())
    monkeypatch.setattr(rt, "emit", lambda *_: None)
    meter = engine.UsageMeter(session, rt)
    module = openai_api if kind == "openai" else anthropic_api
    provider = openai_api.OpenAIProvider() if kind == "openai" else AnthropicProvider()
    posted = []
    def handler(request):
        posted.append(json.loads(request.content))
        if kind == "openai":
            events = [{"choices": [{"delta": {"content": "answer"}}]},
                      {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 70}, "completion_tokens_details": {"reasoning_tokens": 4}}}]
        else:
            events = [{"type": "message_start", "message": {"usage": {"input_tokens": 20, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 10, "output_tokens": 0}}},
                      {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}},
                      {"type": "message_delta", "usage": {"output_tokens": 10}}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join("data: " + json.dumps(e) + "\n\n" for e in events))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(module, "_cfg", lambda: {"base_url": "https://example.invalid", "timeout": 10})
    monkeypatch.setitem(registry._PROVIDERS, "contract_" + kind, provider)
    async def run():
        with registry.call_meter(meter), registry.usage_context("cache-contract-" + kind):
            return "".join([chunk async for chunk in registry.stream("rules", [{"role": "user", "content": "question"}], provider="contract_" + kind)])
    assert asyncio.run(run()) == "answer"
    assert session["usage"]["input_tokens"] == 100 and session["usage"]["cached_input_tokens"] == 70
    assert session["usage"]["output_tokens"] == 10 and session["usage"]["exact"]
    ledger = store.ai_usage_summary(task_type="cache-contract-" + kind)
    assert ledger["total"]["cache_hit_rate"] == .7 and ledger["total"]["estimated_tasks"] == 0
    if kind == "openai":
        assert posted[0]["stream_options"] == {"include_usage": True}
        assert session["usage"]["reasoning_tokens"] == 4
    else:
        assert session["usage"]["cache_write_tokens"] == 10


def test_manual_compaction_and_fork_reset_thread_and_account_usage(client, monkeypatch):
    from app.harness.manager import SessionManager
    class Fake:
        async def complete(self, *args, **kwargs):
            return "summary", {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10, "exact": True}
    monkeypatch.setitem(registry._PROVIDERS, "compact_contract", Fake())
    manager = SessionManager()
    snapshot = manager.create(provider="compact_contract", mode="ask")
    rt = manager._rt(snapshot["id"])
    rt.session["context"]["limit"] = 1000
    rt.session["provider_thread"] = {"id": "old-thread"}
    rt.session["messages"] = [{"id": "m" + str(i), "type": "user" if i % 2 == 0 else "assistant", "text": "long content " * 100} for i in range(6)]
    result = asyncio.run(engine.drive_compaction(rt))
    assert result["compacted"] and "provider_thread" not in rt.session
    assert rt.session["usage"]["llm_calls"] == 1 and rt.session["usage"]["cached_input_tokens"] == 60
    assert any(m.get("kind") == "cache_reset" and m.get("reason") == "compaction" for m in rt.session["messages"])
    assert next(m for m in rt.session["messages"] if m.get("kind") == "turn_usage")["usage"]["input_tokens"] == 100
    rt.session["provider_thread"] = {"id": "new-thread"}
    fork = manager.fork(rt.id, None)
    assert "provider_thread" not in manager._rt(fork["id"]).session
    assert any(m.get("kind") == "cache_reset" and m.get("reason") == "fork" for m in fork["messages"])


def test_subagent_usage_is_included_once_in_parent_turn(client, monkeypatch):
    from app.harness.manager import SessionManager
    from app.harness.tools import HarnessContext
    from app.providers.base import report_stream_usage
    class Fake:
        async def stream(self, *args, **kwargs):
            yield "child answer"
            report_stream_usage({"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 10, "exact": True})
    monkeypatch.setitem(registry._PROVIDERS, "child_contract", Fake())
    monkeypatch.setattr(engine.extensions, "find_agent", lambda *_: {"name": "test", "prompt": "task", "tools": ["fs_read"], "model": "inherit"})
    manager = SessionManager()
    session = manager.create(provider="child_contract", mode="ask")
    rt = manager._rt(session["id"])
    ctx = HarnessContext(run_id=rt.id, paper_ids=[], spans=rt.spans, runtime=rt)
    meter = engine.UsageMeter(rt.session, rt)
    async def run():
        with registry.call_meter(meter):
            result = await engine.run_subagent(ctx, description="test", prompt="answer", agent_type="test")
            assert not result.is_error
    asyncio.run(run())
    assert rt.session["usage"]["llm_calls"] == meter.turn["llm_calls"] == 1
    assert rt.session["usage"]["input_tokens"] == meter.turn["input_tokens"] == 100
    assert rt.session["usage"]["cached_input_tokens"] == meter.turn["cached_input_tokens"] == 70
    assert rt.session["usage"]["exact"]


def test_codex_classic_resume_uses_durable_delta_and_harness_overrides_are_local(tmp_path, monkeypatch):
    from app.providers import local_codex
    from app.providers.base import provider_runtime_options
    totals = [{"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10},
              {"input_tokens": 160, "cached_input_tokens": 70, "output_tokens": 14}]
    commands = []
    class Process:
        returncode = 0
        def __init__(self, command): self.command = command
        async def communicate(self, _):
            from pathlib import Path
            Path(self.command[self.command.index("-o") + 1]).write_text("answer", encoding="utf-8")
            return (json.dumps({"type": "thread.started", "thread_id": "accounting-thread"}) + "\n" + json.dumps({"type": "turn.completed", "usage": totals.pop(0)})).encode(), b""
    async def spawn(*command, **kwargs):
        commands.append(command)
        return Process(command)
    monkeypatch.setattr(local_codex.app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(local_codex, "ensure_codex_sandbox", lambda: tmp_path)
    async def command():
        return ["codex"]
    monkeypatch.setattr(local_codex, "_command", command)
    monkeypatch.setattr(local_codex, "_cfg", lambda: {"timeout": 10})
    monkeypatch.setattr(local_codex.asyncio, "create_subprocess_exec", spawn)
    async def run_first():
        with provider_runtime_options(harness=True):
            return await local_codex.LocalCodexProvider().complete_session("system", [{"role": "user", "content": "first"}])
    first = asyncio.run(run_first())
    second = asyncio.run(local_codex.LocalCodexProvider().complete_session("fresh paper context", [{"role": "user", "content": "second"}], session_id=first[2]))
    assert first[1]["input_tokens"] == 100 and second[1]["input_tokens"] == 60
    assert second[1]["cached_input_tokens"] == 50 and second[1]["completion_tokens"] == 4 and second[1]["exact"]
    assert all(option in commands[0] for option in local_codex.HARNESS_CONFIG_OVERRIDES)
    assert not any(option in commands[1] for option in local_codex.HARNESS_CONFIG_OVERRIDES)
    assert "--ignore-user-config" not in commands[0]


def test_compatible_openai_rejecting_stream_options_still_streams_estimates(client, monkeypatch):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": "unknown parameter stream_options"})
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"answer"}}]}\n\n')
    original = httpx.AsyncClient
    monkeypatch.setattr(openai_api.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(openai_api, "_cfg", lambda: {"base_url": "https://example.invalid"})
    monkeypatch.setitem(registry._PROVIDERS, "compat_contract", openai_api.OpenAIProvider())
    session = _session()
    meter = engine.UsageMeter(session)
    async def run():
        with registry.call_meter(meter), registry.usage_context("compat-contract"):
            return "".join([s async for s in registry.stream("system", [{"role": "user", "content": "hi"}], provider="compat_contract")])
    assert asyncio.run(run()) == "answer" and len(requests) == 2
    assert not session["usage"]["exact"] and session["usage"]["cached_input_tokens"] == 0
    assert session["usage"]["llm_calls"] == 1


@pytest.mark.parametrize("terminal", [True, False])
def test_failed_stream_records_complete_usage_or_labels_partial_estimate(client, monkeypatch, terminal):
    from app.providers.base import report_stream_usage
    class Broken:
        async def stream(self, *_):
            yield "partial answer"
            report_stream_usage({"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 5, "exact": True, "_complete": terminal})
            raise RuntimeError("stream disconnected")
    monkeypatch.setitem(registry._PROVIDERS, "broken_contract", Broken())
    session = _session()
    meter = engine.UsageMeter(session)
    async def run():
        with registry.call_meter(meter), registry.usage_context("broken-contract-" + str(terminal)):
            async for _ in registry.stream("system", [{"role": "user", "content": "hi"}], provider="broken_contract"):
                pass
    with pytest.raises(RuntimeError, match="disconnected"):
        asyncio.run(run())
    assert session["usage"]["llm_calls"] == 1 and session["usage"]["exact"] is terminal
    ledger = store.ai_usage_summary(task_type="broken-contract-" + str(terminal))["total"]
    assert ledger["tasks"] == 1 and ledger["exact"] is terminal
    assert ledger["input_tokens"] > 0
    if terminal:
        assert ledger["input_tokens"] == 100 and ledger["cached_input_tokens"] == 70


def test_visible_reply_normalization_does_not_rewrite_model_history(monkeypatch):
    from app.providers.base import report_stream_usage
    class Text:
        async def stream(self, *_):
            yield "first\n\n\nsecond"
            report_stream_usage({"input_tokens": 10, "output_tokens": 5, "exact": True})
    monkeypatch.setitem(registry._PROVIDERS, "spacing_contract", Text())
    session = _session()
    session["model"]["provider"] = "spacing_contract"
    session["messages"] = [{"type": "user", "id": "u1", "text": "hi"}]
    rt = Runtime(session, object())
    monkeypatch.setattr(rt, "emit", lambda *_: None)
    meter = engine.UsageMeter(session, rt)
    async def run():
        with registry.call_meter(meter):
            message, _ = await engine.stream_assistant(rt, "system", meter)
        return message
    message = asyncio.run(run())
    assert message["text"] == "first\n\nsecond"
    assert context.message_prompt(message) == ("assistant", "first\n\n\nsecond")


def test_no_output_failure_marks_session_usage_unknown_without_inventing_tokens(monkeypatch):
    class Failed:
        async def stream(self, *_):
            raise RuntimeError("no usage received")
            yield ""
    monkeypatch.setitem(registry._PROVIDERS, "failed_empty", Failed())
    session = _session()
    meter = engine.UsageMeter(session)
    async def run():
        with registry.call_meter(meter):
            async for _ in registry.stream("system", [{"role": "user", "content": "hi"}], provider="failed_empty"):
                pass
    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert session["usage"]["llm_calls"] == 1 and not session["usage"]["exact"]
    assert session["usage"]["input_tokens"] == session["usage"]["output_tokens"] == 0
