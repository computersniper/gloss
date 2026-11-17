"""Verify paper workspaces and a real Codex turn through a disposable server.

Start uvicorn with isolated GLOSS_DATA_DIR, GLOSS_CACHE_DIR and GLOSSX_HOME,
then run with the backend venv Python:
  scripts/dev/verify_workspace.py --base http://127.0.0.1:8793 --data-dir <temp-dir>
No fake provider is installed; only the disposable server's settings change.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import fitz
import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"base": args.base, "checks": [], "tools": []}
    with httpx.Client(base_url=args.base, timeout=120, trust_env=False) as client:
        def api(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def check(name, condition):
            if not condition:
                raise AssertionError(name)
            report["checks"].append(name)
            print("PASS", name, flush=True)

        settings = api("GET", "/api/harness/settings")
        paths = settings.get("paths", {})
        check("server uses the requested disposable data directory",
              Path(paths.get("data", "")).resolve() == args.data_dir.resolve())
        check("agent home is isolated with the data directory",
              args.data_dir.resolve() in Path(paths["home"]).resolve().parents)
        api("POST", "/api/settings", json={"provider": "local_codex", "providers": {
            "local_codex": {"model": "gpt-5.6-sol"}}})
        api("PUT", "/api/harness/settings", json={"vcs": {"auto_commit": True}})

        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "GlossX workspace verification", fontsize=18)
            page.insert_text((72, 120), "Abstract")
            page.insert_text((72, 150), "This paper tests durable workspace notes and branches.")
            uploaded = api("POST", "/api/papers/upload", files={
                "file": ("workspace-verification.pdf", pdf.tobytes(), "application/pdf")})
        paper = uploaded.get("paper", uploaded)
        pid = paper["id"]
        project = api("GET", f"/api/papers/{pid}/project")
        project_id = project["id"]
        report.update(paper_id=pid, project_id=project_id)
        check("uploaded paper has its own project",
              project["kind"] == "paper" and pid in project["paper_ids"])
        check("paper project creation is idempotent",
              api("GET", f"/api/papers/{pid}/project")["id"] == project_id)
        workspace = f"/api/workspace/{project_id}"
        vcs = workspace + "/vcs"
        initial = api("GET", vcs + "/status")
        check("paper workspace has a clean main git repository",
              initial["available"] and initial["branch"] == "main" and initial["clean"])
        for path in ["GLOSSX.md", "notes/README.md", ".glossx/settings.json"]:
            check("seed file " + path, bool(api("GET", workspace + "/file", params={"path": path})))

        thread = api("POST", "/api/sessions", json={
            "title": "Real Codex workspace verification", "project_id": project_id,
            "mode": "agent", "provider": "local_codex", "model": "gpt-5.6-sol"})
        sid = thread["id"]
        report["session_id"] = sid
        api("POST", f"/api/sessions/{sid}/messages", json={
            "text": 'Use fs_write to create notes/verified.txt with exactly "real codex verified\\n". '
                    "Then use fs_read to verify it, and vcs_status to inspect the workspace. "
                    "Do not commit manually; finish with a short confirmation.",
            "attachments": [{"kind": "paper", "ref": pid}]})
        deadline = time.monotonic() + 900
        approved = set()
        while time.monotonic() < deadline:
            snapshot = api("GET", f"/api/sessions/{sid}")
            for approval in snapshot.get("pending_approvals", []):
                call_id = approval["tool_call_id"]
                if call_id not in approved:
                    summary = api("GET", "/api/sessions/inbox/summary")
                    check("pending approval is counted in the live inbox", summary["blocking"] >= 1)
                    api("POST", f"/api/sessions/{sid}/read", json={})
                    check("reading keeps an unresolved approval visible",
                          api("GET", f"/api/sessions/{sid}")["attention"] == "approval")
                    api("POST", f"/api/sessions/{sid}/approvals/{call_id}", json={"decision": "once"})
                    approved.add(call_id)
                    print("Approved", approval.get("tool"), flush=True)
            if snapshot["status"] == "idle":
                break
            time.sleep(1)
        else:
            raise TimeoutError("real Codex turn exceeded 900 seconds")
        messages = snapshot.get("messages", [])
        errors = [m.get("error") for m in messages if m.get("type") == "assistant" and m.get("error")]
        if errors:
            raise AssertionError("real Codex model call failed: " + "; ".join(errors))
        check("real Codex completed without model errors", snapshot.get("attention") == "done")
        report["tools"] = [{"tool": m["tool"], "status": m["status"], "error": m.get("error")}
                           for m in messages if m.get("type") == "tool_call"]
        check("real Codex tools completed successfully", report["tools"] and
              all(m["status"] == "done" for m in report["tools"]))
        check("real Codex used write, read and version-control tools",
              {m["tool"] for m in report["tools"]} >= {"fs_write", "fs_read", "vcs_status"})
        check("real Codex wrote and read the requested note",
              api("GET", workspace + "/file", params={"path": "notes/verified.txt"})["content"]
              == "real codex verified\n")
        history = api("GET", vcs + "/log")
        turn_commit = next((item for item in history if item.get("session_id") == sid), None)
        check("turn automatically committed with session and turn trailers",
              turn_commit is not None and turn_commit.get("turn") == 1)
        report["auto_commit"] = turn_commit["id"]
        check("workspace is clean after the turn", api("GET", vcs + "/status")["clean"])
        check("completed thread is unread in the inbox", snapshot["unread"] and snapshot["attention"] == "done")
        with client.stream("GET", "/api/sessions/inbox/events", timeout=10) as stream:
            stream.raise_for_status()
            event = None
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    streamed = json.loads(line.split(":", 1)[1])
                    check("inbox SSE replays its current summary",
                          event == "inbox.updated" and streamed["unread"] >= 1)
                    break
            else:
                raise AssertionError("inbox SSE ended before its summary")
        check("opening inbox SSE does not mark the thread read",
              api("GET", f"/api/sessions/{sid}")["unread"])
        inbox = api("GET", "/api/sessions", params={"view": "inbox"})
        check("inbox contains the completed thread", any(item["id"] == sid for item in inbox))
        api("POST", f"/api/sessions/{sid}/read", json={})
        viewed = api("GET", f"/api/sessions/{sid}")
        check("viewing clears unread and completed attention", not viewed["unread"] and viewed["attention"] is None)

        api("POST", vcs + "/branches", json={"name": "verification/alternative"})
        api("POST", vcs + "/checkout", json={"branch": "verification/alternative"})
        api("PUT", workspace + "/file", params={"path": "notes/branch.txt"}, json={"content": "alternative\n"})
        branch_commit = api("POST", vcs + "/commit", json={"message": "Verify alternative branch"})["commit"]
        api("POST", vcs + "/checkout", json={"branch": "main"})
        merged = api("POST", vcs + "/merge", json={"branch": "verification/alternative"})
        check("branch switches and fast-forward merge work", merged["merged"] and merged["fast_forward"])
        check("merged content survives read-back", api("GET", workspace + "/file", params={"path": "notes/branch.txt"})["content"] == "alternative\n")
        report["branch_commit"] = branch_commit["id"]
        api("POST", vcs + "/restore", json={"rev": turn_commit["id"]})
        check("restoring creates durable history and removes later files", api("GET", vcs + "/status")["clean"]
              and client.get(workspace + "/file", params={"path": "notes/branch.txt"}).status_code == 404)
        tree = api("GET", workspace + "/tree")["entries"]
        check("internal checkpoints stay out of the explorer", not any(".glossx/checkpoints" in entry["path"] for entry in tree))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
