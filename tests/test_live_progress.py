"""Live progress from local CLIs: thinking and steps reach the assistant message."""
from __future__ import annotations

import asyncio
import json
import sys

from app.harness import engine
from app.harness.manager import SessionManager
from app.providers import local_claude, local_codex, registry
from app.providers.base import communicate_lines, progress_sink, report_progress


def _collect():
    seen = []
    return seen, progress_sink(seen.append)


def test_codex_events_become_thinking_and_steps():
    seen, sink = _collect()
    lines = [
        {"type": "item.completed", "item": {"type": "reasoning", "text": "Look at the abstract first"}},
        {"type": "item.started", "item": {"type": "command_execution", "command": "ls notes"}},
        {"type": "item.started", "item": {"type": "web_search", "query": "attention is all you need"}},
        {"type": "turn.completed", "usage": {}},
    ]
    with sink:
        for line in lines:
            local_codex._progress_from_event(json.dumps(line).encode())
        local_codex._progress_from_event(b"not json")
    assert seen == [{"kind": "thinking", "text": "Look at the abstract first\n\n"},
                    {"kind": "step", "text": "$ ls notes"},
                    {"kind": "step", "text": "Search: attention is all you need"}]


def test_claude_events_become_thinking_and_steps():
    seen, sink = _collect()
    with sink:
        local_claude._report_event({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Hmm"}}})
        local_claude._report_event({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}}})
        local_claude._report_event({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "paper.md"}}]}})
    assert seen == [{"kind": "thinking", "text": "Hmm"}, {"kind": "step", "text": "Read paper.md"}]


def test_communicate_lines_streams_long_lines_and_returns_output():
    async def run():
        script = "import sys; d=sys.stdin.read(); print('a'*100000); print('second:' + d)"
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        lines = []
        out, err = await communicate_lines(proc, b"hello", 30, lambda line: lines.append(line.strip()))
        return out, lines
    out, lines = asyncio.run(run())
    assert lines[0] == b"a" * 100000 and lines[1] == b"second:hello"
    assert out.count(b"\n") == 2


def test_progress_lands_on_the_assistant_message(client, monkeypatch):
    class Fake:
        async def stream(self, system, messages, model):
            report_progress("thinking", "first ")
            report_progress("thinking", "thought")
            report_progress("step", "Read notes.md")
            yield "done"

    monkeypatch.setitem(registry._PROVIDERS, "progress_contract", Fake())
    manager = SessionManager()
    snapshot = manager.create(provider="progress_contract", mode="ask")
    rt = manager._rt(snapshot["id"])
    rt.session["messages"] = [{"id": "u1", "type": "user", "text": "hi"}]
    events = []
    original = rt.emit
    rt.emit = lambda name, payload: (events.append((name, payload)), original(name, payload))
    msg, _ = asyncio.run(engine.stream_assistant(rt, "sys", engine.UsageMeter(rt.session, rt)))
    assert msg["thinking"] == "first thought" and msg["steps"] == ["Read notes.md"]
    assert msg["thought_ms"] >= 0 and msg["text"] == "done"
    progress = [p for name, p in events if name == "message.progress"]
    assert [p["kind"] for p in progress] == ["thinking", "thinking", "step"]


def test_claude_cli_streams_thinking_when_watched(tmp_path, monkeypatch):
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Plan it"}}},
        {"type": "result", "result": "final answer", "session_id": "sess-1", "usage": {"input_tokens": 3, "output_tokens": 2}},
    ]
    script = tmp_path / "fake_claude.py"
    script.write_text("import sys, json\nsys.stdin.read()\nassert '--output-format' in sys.argv and sys.argv[sys.argv.index('--output-format') + 1] == 'stream-json'\n"
                      + "".join(f"print(json.dumps({e!r}))\n" for e in events), encoding="utf-8")

    async def command():
        return [sys.executable, str(script)]
    monkeypatch.setattr(local_claude, "_command", command)
    seen, sink = _collect()

    async def run():
        with sink:
            return await local_claude.LocalClaudeProvider()._run_completion("sys", [{"role": "user", "content": "hi"}], None, persist_session=True)
    text, usage, session = asyncio.run(run())
    assert (text, session) == ("final answer", "sess-1")
    assert seen == [{"kind": "thinking", "text": "Plan it"}]
