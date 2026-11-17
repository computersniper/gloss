"""Measure invocation-local Codex overhead overrides, preserving auth/config."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.platform_support import resolve_cli_command, subprocess_group_options, terminate_process_tree
from app.providers.local_codex import _usage_from_jsonl

LEAN = ["skills.include_instructions=false", "skills.bundled.enabled=false", "features.plugins=false",
        "features.apps=false", "features.memories=false", "project_doc_max_bytes=0"]

async def main_async(output: Path):
    report = {"model": "gpt-5.6-sol", "cases": []}
    with tempfile.TemporaryDirectory(prefix="glossx-overhead-") as directory:
        for label, overrides in [("baseline", []), ("lean", LEAN)]:
            last = Path(directory) / "last.txt"
            cmd = resolve_cli_command("codex") + ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-C", directory, "-m", report["model"], "-o", str(last)]
            for option in overrides:
                cmd.extend(["-c", option])
            cmd.append("-")
            proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **subprocess_group_options())
            try:
                out, _ = await asyncio.wait_for(proc.communicate(b"Reply with exactly: OK. Do not use tools."), timeout=120)
                usage = _usage_from_jsonl(out)
                case = {"case": label, "overrides": overrides, "returncode": proc.returncode, "reply": last.read_text(encoding="utf-8").strip() if last.exists() else "", "usage": usage}
                report["cases"].append(case)
                print(json.dumps(case), flush=True)
            finally:
                if proc.returncode is None:
                    await terminate_process_tree(proc)
    report["ok"] = all(c["returncode"] == 0 and c["reply"].rstrip(".") == "OK" and c["usage"] for c in report["cases"])
    if report["ok"]:
        report["input_reduction"] = report["cases"][0]["usage"]["input_tokens"] - report["cases"][1]["usage"]["input_tokens"]
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args().output))
