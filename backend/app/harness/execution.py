"""Run shell commands / scripts / hooks with timeouts and process-group kill."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ..platform_support import subprocess_group_options, terminate_process_tree

OUTPUT_TAIL = 30_000
MAX_CAPTURE = 4_000_000


def shell_argv(command: str) -> list[str] | str:
    """How to run a shell command line with :func:`run_process`.

    On Windows the line is returned as a string and handed to ``cmd.exe``
    verbatim (an argv list would be re-quoted with ``\"`` escapes that cmd does
    not understand, breaking any command that contains double quotes).
    """
    if os.name == "nt":
        return command
    shell = shutil.which("bash") or "/bin/sh"
    return [shell, "-c", command]


def python_interpreter() -> str:
    """The backend's Python; frozen desktop builds fall back to python on PATH."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    found = shutil.which("python3") or shutil.which("python")
    if not found:
        raise RuntimeError("no Python interpreter is available (install Python and put it on PATH)")
    return found


async def _pump(stream: asyncio.StreamReader | None, sink: bytearray, state: dict[str, Any]) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        sink.extend(chunk)
        state["total"] += len(chunk)
        if len(sink) > MAX_CAPTURE:
            del sink[: len(sink) - MAX_CAPTURE]
            state["truncated"] = True


def _decode(data: bytes | bytearray) -> str:
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        if os.name == "nt":  # console programs often write in the OEM code page
            try:
                return bytes(data).decode("oem")
            except (UnicodeDecodeError, LookupError):
                pass
        return bytes(data).decode("utf-8", errors="replace")


async def run_process(
    argv: list[str] | str,
    *,
    cwd: Path,
    timeout: float,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run ``argv``; kill the whole process group on timeout or cancellation."""
    started = time.monotonic()
    if env is None and not getattr(sys, "frozen", False):
        # Let commands find the backend's own interpreter as `python` even when
        # the machine has no Python on PATH (common on Windows).
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    options: dict[str, Any] = dict(
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        **subprocess_group_options(),
    )
    if isinstance(argv, str):  # a shell command line (see shell_argv)
        proc = await asyncio.create_subprocess_shell(argv, **options)
    else:
        proc = await asyncio.create_subprocess_exec(*argv, **options)
    out, err = bytearray(), bytearray()
    state = {"total": 0, "truncated": False}
    pumps = [asyncio.create_task(_pump(proc.stdout, out, state)),
             asyncio.create_task(_pump(proc.stderr, err, state))]
    timed_out = False
    try:
        if stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
        if not timed_out:
            await asyncio.wait(pumps, timeout=5)
    finally:
        if proc.returncode is None:
            for p in pumps:
                p.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            await terminate_process_tree(proc)
        for p in pumps:
            if not p.done():
                p.cancel()
    stdout = _decode(out)
    stderr = _decode(err)
    return {
        "exit_code": proc.returncode if not timed_out else None,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 3),
        "stdout": stdout[-OUTPUT_TAIL:],
        "stderr": stderr[-OUTPUT_TAIL:],
        "output_truncated": state["truncated"] or len(stdout) > OUTPUT_TAIL or len(stderr) > OUTPUT_TAIL,
    }


def format_run(result: dict[str, Any], header: str) -> str:
    parts = [header]
    if result.get("timed_out"):
        parts.append(f"TIMED OUT after {result['duration_s']}s (process group killed)")
    else:
        parts.append(f"exit code {result.get('exit_code')} in {result['duration_s']}s")
    if result.get("output_truncated"):
        parts.append("(output truncated to the last part)")
    if result.get("stdout"):
        parts.append("--- stdout ---\n" + result["stdout"].rstrip())
    if result.get("stderr"):
        parts.append("--- stderr ---\n" + result["stderr"].rstrip())
    if not result.get("stdout") and not result.get("stderr"):
        parts.append("(no output)")
    return "\n".join(parts)
