"""Local `claude` CLI provider (subscription auth — no API key).

Lifted from paperbench/claude_proxy/server.py ``_call_claude``: shells out to
``claude -p`` with all tools disabled so the agentic CLI behaves as a pure LLM
endpoint. This is the default provider and needs only the `claude` binary on PATH.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import AsyncIterator

from ..config import ensure_claude_sandbox, load_config
from ..platform_support import (
    resolve_cli_command,
    subprocess_group_options,
    terminate_process_tree,
)
from .base import (
    Message,
    Provider,
    ProviderSessionUnavailableError,
    render_transcript,
    runtime_option,
    anthropic_usage,
    normalize_usage,
    report_stream_usage,
    communicate_lines,
    report_progress,
    wants_progress,
)

async def _command() -> list[str]:
    from ..cli_discovery import select_command
    configured = str(load_config()["providers"]["local_claude"].get("cli_path") or "")
    return await asyncio.to_thread(select_command, "claude", configured)


CLAUDE_SHORT = ("sonnet", "opus", "haiku", "fable")


class ClaudeSessionUnavailableError(ProviderSessionUnavailableError):
    """Raised when Claude Code can no longer resume a persisted session."""


def _session_is_unavailable(detail: str) -> bool:
    text = detail.lower()
    return any(
        marker in text
        for marker in (
            "session not found",
            "no conversation found",
            "no session found",
            "could not find session",
            "unknown session",
            "invalid session id",
        )
    )


def _sandbox_env() -> tuple[dict, str]:
    """Return (env, cwd) that isolate the CLI from the user's CLAUDE.md/memory."""
    sb = ensure_claude_sandbox()
    env = os.environ.copy()
    env["HOME"] = str(sb)
    if os.name == "nt":
        env["USERPROFILE"] = str(sb)
    return env, str(sb)


def _pick_model(requested: str | None) -> str:
    cfg = load_config()["providers"]["local_claude"]
    default = cfg.get("model", "sonnet")
    if requested and ("claude" in requested or requested in CLAUDE_SHORT):
        return requested
    return default


def _timeout() -> float:
    return float(load_config()["providers"]["local_claude"].get("timeout", 600))


def _effort() -> str:
    configured = load_config()["providers"]["local_claude"].get("effort")
    return (runtime_option("effort", configured) or "").strip()


def _materialize_message_images(messages: list[Message]) -> tuple[str | None, list[str]]:
    """Create a dedicated read-only attachment directory for Claude's Read tool."""
    data_urls = [
        attachment.get("image_data_url") or ""
        for message in messages
        for attachment in (message.get("attachments") or [])
    ]
    prefix = "data:image/png;base64,"
    data_urls = [value for value in data_urls if value.startswith(prefix)]
    if not data_urls:
        return None, []
    directory = tempfile.mkdtemp(prefix="gloss-chat-images-")
    paths = []
    try:
        for index, data_url in enumerate(data_urls, start=1):
            path = Path(directory) / f"pdf-region-{index}.png"
            path.write_bytes(base64.b64decode(data_url[len(prefix):], validate=True))
            paths.append(str(path))
        return directory, paths
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _image_prompt_suffix(paths: list[str]) -> str:
    if not paths:
        return ""
    listed = "\n".join(f"- {path}" for path in paths)
    return (
        "\n\n[Attached PDF-region screenshots]\n"
        f"{listed}\nUse the Read tool to inspect these images before answering."
    )


class LocalClaudeProvider(Provider):
    name = "local_claude"

    async def test_connection(self) -> str:
        """Check subscription login without launching a full Claude inference."""
        cmd = await _command()
        cmd += ["auth", "status", "--json"]
        env, cwd = _sandbox_env()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                **subprocess_group_options(),
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
            except asyncio.TimeoutError:
                await terminate_process_tree(proc)
                raise RuntimeError("claude auth status timed out after 20s")
            detail = (out or err).decode(errors="replace").strip()
            if proc.returncode != 0:
                raise RuntimeError(detail or f"claude auth status exited {proc.returncode}")
            try:
                status = json.loads(detail)
            except json.JSONDecodeError:
                status = {}
            if status and not status.get("loggedIn", False):
                raise RuntimeError("Claude CLI is not signed in")
            return detail or "Claude is signed in"
        finally:
            if proc is not None and proc.returncode is None:
                await terminate_process_tree(proc)

    async def complete(
        self, system: str, messages: list[Message], model: str | None = None
    ) -> tuple[str, dict]:
        text, usage, _ = await self._run_completion(
            system,
            messages,
            model,
            persist_session=False,
        )
        return text, usage

    async def complete_session(
        self,
        system: str,
        messages: list[Message],
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> tuple[str, dict, str]:
        """Create or resume a persisted Claude Code conversation."""
        text, usage, resolved_session_id = await self._run_completion(
            system,
            messages,
            model,
            persist_session=True,
            session_id=session_id,
        )
        if not resolved_session_id:
            raise RuntimeError("claude CLI did not report a session id")
        return text, usage, resolved_session_id

    async def _run_completion(
        self,
        system: str,
        messages: list[Message],
        model: str | None,
        *,
        persist_session: bool,
        session_id: str | None = None,
    ) -> tuple[str, dict, str | None]:
        image_dir, image_paths = _materialize_message_images(messages)
        prompt = render_transcript(messages) + _image_prompt_suffix(image_paths)
        model = _pick_model(model)
        cmd = await _command()
        # With someone watching, stream events so thinking shows up as it happens;
        # the final ``result`` event carries the same fields as ``json`` output.
        live = wants_progress()
        cmd += [
            "-p",
            *(["--output-format", "stream-json", "--verbose", "--include-partial-messages"] if live
              else ["--output-format", "json"]),
            "--tools", "Read" if image_paths else "",
            "--model", model,
        ]
        if persist_session:
            if session_id:
                cmd += ["--resume", session_id]
        else:
            cmd.append("--no-session-persistence")
        if image_dir:
            cmd += ["--add-dir", image_dir, "--permission-mode", "dontAsk"]
        if _effort():
            cmd += ["--effort", _effort()]
        tmp_path = None
        proc = None
        try:
            if system:
                # argv is capped ~128KB; papers easily exceed that -> use a file
                fd, tmp_path = tempfile.mkstemp(prefix="gloss-sys-", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(system)
                cmd += ["--system-prompt-file", tmp_path]

            env, cwd = _sandbox_env()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                **subprocess_group_options(),
            )
            result: dict = {}

            def on_line(line: bytes) -> None:
                evt = json.loads(line)
                if evt.get("type") == "result":
                    result.update(evt)
                else:
                    _report_event(evt)

            try:
                if live:
                    out, err = await communicate_lines(proc, prompt.encode(), _timeout(), on_line)
                else:
                    out, err = await asyncio.wait_for(
                        proc.communicate(prompt.encode()), timeout=_timeout()
                    )
            except asyncio.TimeoutError:
                await terminate_process_tree(proc)
                raise RuntimeError(f"claude CLI timed out after {_timeout()}s")

            if proc.returncode != 0:
                detail = (err + b"\n" + out).decode(errors="replace")[:4000]
                if session_id and _session_is_unavailable(detail):
                    raise ClaudeSessionUnavailableError(detail)
                raise RuntimeError(f"claude CLI exited {proc.returncode}: {detail}")
            data = result if live else json.loads(out.decode())
            if live and not data:
                detail = (err + b"\n" + out).decode(errors="replace")[-2000:]
                raise RuntimeError(f"claude CLI reported no result: {detail}")
            if data.get("is_error"):
                detail = str(data.get("result", ""))[:2000]
                if session_id and _session_is_unavailable(detail):
                    raise ClaudeSessionUnavailableError(detail)
                raise RuntimeError(detail)
            usage = data.get("usage", {}) or {}
            norm = normalize_usage(anthropic_usage(usage), system, messages, data.get("result", ""))
            return data.get("result", ""), norm, data.get("session_id") or session_id
        finally:
            if proc is not None and proc.returncode is None:
                await terminate_process_tree(proc)
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if image_dir:
                shutil.rmtree(image_dir, ignore_errors=True)

    async def stream(
        self, system: str, messages: list[Message], model: str | None = None
    ) -> AsyncIterator[str]:
        """True token streaming via `--output-format stream-json`, with fallback."""
        image_dir, image_paths = _materialize_message_images(messages)
        prompt = render_transcript(messages) + _image_prompt_suffix(image_paths)
        model = _pick_model(model)
        cmd = await _command()
        cmd += [
            "-p",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--no-session-persistence",
            "--tools", "Read" if image_paths else "",
            "--model", model,
        ]
        if image_dir:
            cmd += ["--add-dir", image_dir, "--permission-mode", "dontAsk"]
        if _effort():
            cmd += ["--effort", _effort()]
        tmp_path = None
        proc = None
        streamed_any = False
        try:
            if system:
                fd, tmp_path = tempfile.mkstemp(prefix="gloss-sys-", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(system)
                cmd += ["--system-prompt-file", tmp_path]

            env, cwd = _sandbox_env()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                **subprocess_group_options(),
            )
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

            saw_partial = False
            async for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                partial, full = _extract_delta(evt)
                if evt.get("type") == "result":
                    report_stream_usage(anthropic_usage(evt.get("usage")))
                if partial:
                    saw_partial = True
                    streamed_any = True
                    yield partial
                elif full and not saw_partial:
                    # Only use the whole-message text if no partials streamed,
                    # otherwise it duplicates the deltas above.
                    streamed_any = True
                    yield full
            await proc.wait()
        except (FileNotFoundError, RuntimeError):
            streamed_any = False
        finally:
            if proc is not None and proc.returncode is None:
                await terminate_process_tree(proc)
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if image_dir:
                shutil.rmtree(image_dir, ignore_errors=True)

        if not streamed_any:
            # Fallback: blocking completion, chunked out.
            text, usage = await self.complete(system, messages, model)
            report_stream_usage(usage)
            step = 24
            for i in range(0, len(text), step):
                yield text[i : i + step]


def _report_event(evt: dict) -> None:
    """Report thinking deltas and tool use from a claude stream-json event."""
    if evt.get("type") == "stream_event":
        inner = evt.get("event", {}) or {}
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta", {}) or {}
            if delta.get("type") == "thinking_delta":
                report_progress("thinking", str(delta.get("thinking") or ""))
        return
    if evt.get("type") == "assistant":
        for block in (evt.get("message", {}) or {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                args = block.get("input") or {}
                detail = next((str(args[k]) for k in ("file_path", "path", "command", "pattern", "url", "query") if args.get(k)), "")
                report_progress("step", f"{block.get('name') or 'Tool'} {detail}".strip())


def _extract_delta(evt: dict) -> tuple[str, str]:
    """Return (partial_delta, full_message_text) from a claude stream-json event.

    At most one is non-empty. ``partial`` comes from incremental
    ``content_block_delta`` events; ``full`` from a whole ``assistant`` message.
    """
    t = evt.get("type")
    if t == "stream_event":
        inner = evt.get("event", {})
        if inner.get("type") == "content_block_delta":
            d = inner.get("delta", {})
            if d.get("type") == "text_delta":
                return d.get("text", ""), ""
        return "", ""
    if t == "assistant":
        msg = evt.get("message", {})
        parts = []
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "", "".join(parts)
    return "", ""
