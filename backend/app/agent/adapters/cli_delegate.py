"""CLI agent delegates: ``delegate.claude_code`` and ``delegate.codex``.

The local ``claude`` / ``codex`` CLI runs **with tools enabled** inside a
per-run sandbox workspace ``<DATA_DIR>/workspaces/<run_id>/`` (never the
user's home or repository). Input ``{task, files?: {name: content}}``; output
``{summary, files_changed, stdout_tail}``. Permission ``code:execute`` (needs
approval unless auto-approved), trust ``external``; 15-minute timeout and the
whole process group is killed on cancel or timeout.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ... import config
from ...platform_support import resolve_cli_command, subprocess_group_options, terminate_process_tree
from ..capabilities import Capability, RunContext

DELEGATE_TIMEOUT = 15 * 60.0
MAX_FILES = 50
MAX_FILE_BYTES = 1_000_000
TAIL_CHARS = 2000
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

CLI_BINARIES = {"claude": "claude", "codex": "codex"}


def workspace_for(run_id: str) -> Path:
    """Create and return the sandbox workspace for ``run_id``."""
    if not _RUN_ID.fullmatch(str(run_id or "")):
        raise ValueError("invalid run id for workspace")
    root = config.WORKSPACES_DIR.resolve()
    path = (root / run_id).resolve()
    if path.parent != root:
        raise ValueError("workspace escapes the workspaces directory")
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_relative(name: Any) -> PurePosixPath:
    text = str(name or "").strip().replace("\\", "/")
    if not text or len(text) > 200 or "\0" in text:
        raise ValueError(f"invalid file name: {name!r}")
    posix = PurePosixPath(text)
    if posix.is_absolute() or PureWindowsPath(text).drive or any(p in {"..", ""} for p in posix.parts):
        raise ValueError(f"file name must be a relative path inside the workspace: {name!r}")
    return posix


def write_files(workspace: Path, files: Any) -> list[str]:
    if files in (None, {}):
        return []
    if not isinstance(files, dict):
        raise ValueError("'files' must be an object of {name: content}")
    if len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files")
    written = []
    for name, content in files.items():
        rel = safe_relative(name)
        if not isinstance(content, str):
            raise ValueError(f"content of {rel} must be a string")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{rel} is larger than {MAX_FILE_BYTES} bytes")
        target = (workspace / Path(*rel.parts)).resolve()
        if workspace not in target.parents:
            raise ValueError(f"{rel} escapes the workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(rel.as_posix())
    return written


def snapshot(workspace: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for root, dirs, names in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"}]
        for n in names:
            path = Path(root) / n
            try:
                stat = path.stat()
                if stat.st_size <= 4_000_000:
                    digest = hashlib.sha1(path.read_bytes()).hexdigest()
                else:
                    digest = f"{stat.st_size}:{stat.st_mtime_ns}"
            except OSError:
                continue
            state[path.relative_to(workspace).as_posix()] = digest
        if len(state) > 20000:
            break
    return state


def changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    paths = {p for p in after if before.get(p) != after[p]} | {p for p in before if p not in after}
    return sorted(paths)[:200]


def cli_available(agent: str) -> bool:
    try:
        resolve_cli_command(CLI_BINARIES[agent])
        return True
    except Exception:
        return False


def _sandbox_note(workspace: Path) -> str:
    return (
        f"You are a delegate of GlossX working in the sandbox directory {workspace}. "
        "Read, create and modify files only inside this directory and run commands only "
        "from it. Do not access the user's home directory, other repositories, "
        "credentials or the network unless the task explicitly requires it. When done, "
        "reply with a short summary of what you did and which files you changed."
    )


async def _run(cmd: list[str], *, prompt: str, cwd: Path, env: dict[str, str] | None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        **subprocess_group_options(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), DELEGATE_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        await terminate_process_tree(proc)
        raise RuntimeError(f"delegate timed out after {int(DELEGATE_TIMEOUT)}s") from None
    finally:
        if proc.returncode is None:  # cancelled: kill the whole process group
            await terminate_process_tree(proc)
    return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


async def _claude(task: str, workspace: Path) -> tuple[str, str]:
    cfg = config.load_config()["providers"].get("local_claude", {})
    cmd = resolve_cli_command("claude") + [
        "-p",
        "--output-format", "json",
        "--no-session-persistence",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash",
        "--append-system-prompt", _sandbox_note(workspace),
    ]
    if cfg.get("model"):
        cmd += ["--model", str(cfg["model"])]
    env = os.environ.copy()
    # sandbox HOME: keeps the subscription login but not the user's CLAUDE.md / memory
    home = config.ensure_claude_sandbox()
    env["HOME"] = str(home)
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    code, out, err = await _run(cmd, prompt=task, cwd=workspace, env=env)
    tail = (out + ("\n" + err if err else ""))[-TAIL_CHARS:]
    if code != 0:
        raise RuntimeError(f"claude exited {code}: {tail[-800:]}")
    try:
        data = json.loads(out)
    except ValueError:
        return out.strip()[-4000:], tail
    if data.get("is_error"):
        raise RuntimeError(str(data.get("result") or "claude reported an error")[:800])
    return str(data.get("result") or "")[:4000], tail


async def _codex(task: str, workspace: Path) -> tuple[str, str]:
    cfg = config.load_config()["providers"].get("local_codex", {})
    fd, out_path = tempfile.mkstemp(prefix="glossx-delegate-", suffix=".txt")
    os.close(fd)
    try:
        cmd = resolve_cli_command("codex") + [
            "exec",
            "--skip-git-repo-check",
            "-s", "workspace-write",
            "-C", str(workspace),
            "--ephemeral",
            "-o", out_path,
        ]
        if cfg.get("model"):
            cmd += ["-m", str(cfg["model"])]
        cmd.append("-")
        code, out, err = await _run(
            cmd, prompt=f"{_sandbox_note(workspace)}\n\nTASK:\n{task}", cwd=workspace, env=None
        )
        tail = (out + ("\n" + err if err else ""))[-TAIL_CHARS:]
        if code != 0:
            raise RuntimeError(f"codex exited {code}: {tail[-800:]}")
        try:
            summary = Path(out_path).read_text(encoding="utf-8").strip()
        except OSError:
            summary = ""
        return summary[:4000], tail
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _handler(agent: str):
    async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
        task = str(args.get("task") or "").strip()
        if not task:
            raise ValueError("'task' is required")
        if len(task) > 20000:
            raise ValueError("'task' is too long")
        workspace = workspace_for(ctx.run_id)
        write_files(workspace, args.get("files"))
        before = snapshot(workspace)
        runner = _claude if agent == "claude" else _codex
        summary, tail = await runner(task, workspace)
        after = snapshot(workspace)
        return {
            "summary": summary,
            "files_changed": changed(before, after),
            "stdout_tail": tail,
            "workspace": str(workspace),
        }

    return handler


def delegate_capabilities(conns: dict[str, Any]) -> list[Capability]:
    cli = conns.get("cli_agents") or {}
    schema = {
        "task": "str: precise instructions for the coding agent",
        "files": "object (optional): {relative name: text content} written into the workspace first",
    }
    caps = []
    if (cli.get("claude") or {}).get("enabled"):
        caps.append(Capability(
            name="delegate.claude_code",
            requirements=["Claude Code CLI"],
            kind="agent",
            card="Delegate a coding or experiment task to Claude Code with tools enabled, inside this run's sandbox workspace.",
            input_schema=dict(schema),
            output_schema={"summary": "str", "files_changed": "list[str]", "stdout_tail": "str"},
            handler=_handler("claude"),
            permissions=["code:execute"],
            cost="llm",
            source="cli",
            side_effects="runs code in the run's sandbox workspace",
            trust="external",
            timeout=DELEGATE_TIMEOUT + 30,
        ))
    if (cli.get("codex") or {}).get("enabled"):
        caps.append(Capability(
            name="delegate.codex",
            requirements=["Codex CLI"],
            kind="agent",
            card="Delegate a coding or experiment task to OpenAI Codex CLI with tools enabled, inside this run's sandbox workspace.",
            input_schema=dict(schema),
            output_schema={"summary": "str", "files_changed": "list[str]", "stdout_tail": "str"},
            handler=_handler("codex"),
            permissions=["code:execute"],
            cost="llm",
            source="cli",
            side_effects="runs code in the run's sandbox workspace",
            trust="external",
            timeout=DELEGATE_TIMEOUT + 30,
        ))
    return caps
