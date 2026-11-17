"""Real Codex/browser/import acceptance against a disposable GlossX server.

Start uvicorn with isolated GLOSS_DATA_DIR/GLOSS_CACHE_DIR/GLOSSX_HOME first.
Only this server's settings are changed; no provider or browser is simulated.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"ok": False, "model": args.model, "tools": [], "approvals": []}
    try:
        with httpx.Client(base_url=args.base, timeout=180, trust_env=False) as client:
            def api(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()

            paths = api("GET", "/api/harness/settings")["paths"]
            assert Path(paths["data"]).resolve() == args.data_dir.resolve(), "server data is not isolated"
            assert args.data_dir.resolve() in Path(paths["home"]).resolve().parents, "server home is not isolated"
            api("POST", "/api/settings", json={"provider": "local_codex", "providers": {
                "local_codex": {"model": args.model, "timeout": 180}}})
            session = api("POST", "/api/sessions", json={"title": "Real browser acceptance",
                "mode": "agent", "provider": "local_codex", "model": args.model})
            sid = session["id"]
            report["session_id"] = sid
            api("POST", f"/api/sessions/{sid}/messages", json={"text":
                "Use GlossX browser_open to open https://arxiv.org/abs/1706.03762, then browser_read "
                "to confirm the paper title Attention Is All You Need, then browser_import_paper "
                "to import that paper into the GlossX library. Wait for the import approval. "
                "Use these GlossX tools only; do not use your own shell/network tools. "
                "Finish with the title and imported paper id."})
            deadline = time.monotonic() + 900
            approved = set()
            while time.monotonic() < deadline:
                snapshot = api("GET", f"/api/sessions/{sid}")
                for approval in snapshot.get("pending_approvals", []):
                    call_id = approval["tool_call_id"]
                    if call_id in approved:
                        continue
                    assert approval.get("tool") == "browser_import_paper", f"Unexpected approval: {approval.get('tool')}"
                    api("POST", f"/api/sessions/{sid}/approvals/{call_id}", json={"decision": "once"})
                    approved.add(call_id)
                    report["approvals"].append({"tool": approval["tool"], "decision": "once"})
                    print("Approved library import", flush=True)
                if snapshot["status"] == "idle":
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Real Codex browser acceptance timed out")
            report["tools"] = [{"tool": m["tool"], "status": m["status"], "error": m.get("error")}
                for m in snapshot["messages"] if m.get("type") == "tool_call"]
            assert snapshot.get("attention") == "done", str(snapshot.get("attention"))
            assert all(tool["status"] == "done" for tool in report["tools"]), report["tools"]
            assert {tool["tool"] for tool in report["tools"]} >= {"browser_open", "browser_read", "browser_import_paper"}
            assert report["approvals"], "Library-write approval was bypassed"
            papers = api("GET", "/api/papers")
            if isinstance(papers, dict):
                papers = papers.get("papers", [])
            paper = next(p for p in papers if "Attention Is All You Need" in p["title"])
            project = api("GET", f"/api/papers/{paper['id']}/project")
            assert project["kind"] == "paper" and paper["id"] in project["paper_ids"]
            assert Path(project["workspace"]["path"]).is_dir()
            report.update(paper_id=paper["id"], project_id=project["id"], usage=snapshot["usage"])
            assert 0 <= report["usage"]["cached_input_tokens"] <= report["usage"]["input_tokens"]
            with sqlite3.connect(args.data_dir / "moonlight.db") as database:
                row = database.execute("SELECT COUNT(*), SUM(prompt_tokens), SUM(cached_input_tokens), "
                    "SUM(completion_tokens), SUM(reasoning_tokens) FROM ai_usage WHERE task_type='agent'").fetchone()
            ledger = dict(zip(("llm_calls", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"), row))
            assert all(ledger[key] == report["usage"][key] for key in ledger), "SQLite ledger differs from session usage"
            report["ledger_totals"] = ledger
            report["ok"] = True
            print("PASS real Codex/browser/library/workspace", flush=True)
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
