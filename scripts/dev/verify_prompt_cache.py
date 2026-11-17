"""Small real-Codex harness cache check in disposable GlossX application data.

No fake provider and no personal configuration edits. CLI authentication and
ordinary CLI thread persistence use the user's already installed Codex.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="glossx-cache-verify-") as temp:
        os.environ["GLOSS_DATA_DIR"] = temp
        os.environ["GLOSS_CACHE_DIR"] = str(Path(temp) / "cache")
        os.environ["GLOSSX_HOME"] = str(Path(temp) / "home")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
        from app import config
        from app.harness import workspace
        from app.harness.manager import SessionManager
        from app.library import store
        from app.providers import registry

        cfg = config.load_config()
        cfg["provider"] = "local_codex"
        cfg["providers"]["local_codex"].update(model=args.model, timeout=180)
        config.save_config(cfg)
        store.init_db()
        (workspace.root_for(None) / "cache-check.txt").write_text("cache verified\n", encoding="utf-8")
        report = {"model": args.model, "calls": [], "turns": [], "ok": False}
        provider = registry.get_provider("local_codex")
        original = provider.complete_session
        async def measured(system, messages, model, *, session_id=None):
            started = time.monotonic()
            text, usage, thread = await original(system, messages, model, session_id=session_id)
            call = {"number": len(report["calls"]) + 1, "resumed": bool(session_id),
                    "sent_characters": len(system) + sum(len(m["content"]) for m in messages),
                    "duration_seconds": round(time.monotonic() - started, 3), "usage": usage,
                    "cache_hit_rate": usage["cached_input_tokens"] / usage["input_tokens"] if usage["input_tokens"] else 0}
            report["calls"].append(call)
            print(json.dumps(call), flush=True)
            return text, usage, thread
        provider.complete_session = measured

        async def run():
            manager = SessionManager()
            try:
                session = manager.create(title="Real cache check", mode="ask", provider="local_codex", model=args.model)
                sid = session["id"]
                rt = manager._rt(sid)
                for number in range(1, 4):
                    await manager.send_message(sid,
                        f"Turn {number}: use the GlossX fs_read tool once to read cache-check.txt, even if read before. "
                        "After its result, reply with exactly: cache verified. Do not use your own tools.")
                    await asyncio.wait_for(rt.task, timeout=360)
                    snapshot = manager.get(sid)
                    assert snapshot["attention"] != "error", "model failed"
                    tools = [m for m in snapshot["messages"] if m.get("type") == "tool_call" and m.get("tool") == "fs_read"]
                    assert len(tools) >= number and all(m["status"] == "done" for m in tools)
                    note = next(m for m in reversed(snapshot["messages"]) if m.get("kind") == "turn_usage")
                    report["turns"].append(note["usage"])
                report["session_usage"] = manager.get(sid)["usage"]
                assert len(report["calls"]) >= 6
                assert not report["calls"][0]["resumed"] and all(call["resumed"] for call in report["calls"][1:])
                assert all(call["usage"]["exact"] for call in report["calls"])
                assert report["session_usage"]["llm_calls"] == len(report["calls"])
                last_total = report["calls"][-1]["usage"]["_thread_usage"]
                for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
                    assert sum(call["usage"][key] for call in report["calls"]) == last_total[key]
                    assert report["session_usage"][key] == last_total[key]
                ledger = store.ai_usage_summary(task_type="agent")["total"]
                assert ledger["input_tokens"] == last_total["input_tokens"]
                assert ledger["cached_input_tokens"] == last_total["cached_input_tokens"]
                report["ledger_totals"] = ledger
                report["raw_cumulative_matches_delta_sum"] = True
                report["ok"] = True
            finally:
                await manager.shutdown()
        try:
            asyncio.run(run())
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            provider.complete_session = original
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"ok": report["ok"], "calls": len(report["calls"]), "error": report.get("error")}), flush=True)


if __name__ == "__main__":
    main()
