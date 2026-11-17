"""Small read-only Codex model probe. Prints usage, never raw CLI context.

Uses existing CLI authentication without editing personal configuration.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--ignore-rules", action="store_true")
    parser.add_argument("--output", help="save only the probe summary as JSON")
    args = parser.parse_args()
    executable = shutil.which("codex")
    if not executable:
        parser.error("codex CLI is not installed")
    with tempfile.TemporaryDirectory(prefix="glossx-model-probe-") as directory:
        from pathlib import Path
        last_message = Path(directory) / "last-message.txt"
        command = [executable, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
             "--sandbox", "read-only", "--model", args.model, "-C", directory,
             "--output-last-message", str(last_message),
             "Reply with exactly: OK. Do not use tools."]
        if args.ignore_rules:
            command.insert(2, "--ignore-rules")
        result = subprocess.run(
            command,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        final_reply = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    usage, reply, errors = None, [], []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                reply.append(item.get("text", ""))
        if event.get("type") in {"error", "turn.failed"}:
            errors.append(str(event.get("message") or event.get("error") or "CLI error")[:500])
    passed = result.returncode == 0 and (final_reply or "".join(reply)).strip() == "OK" and usage is not None
    report = json.dumps({"model": args.model, "ignore_rules": args.ignore_rules,
                        "ok": passed, "returncode": result.returncode,
                        "usage": usage, "errors": errors}, ensure_ascii=False, indent=2)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(report, encoding="utf-8")
    print(report)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
