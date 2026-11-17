"""Provider interface + shared message rendering.

Internal message format is OpenAI-style: a list of ``{"role", "content"}`` dicts
plus a separate ``system`` string. Each provider adapts that to its transport.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator

try:
    import tiktoken

    _TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - optional in packaged installs
    _TOKEN_ENCODING = None

Message = dict  # {"role": "user"|"assistant"|"system", "content": str}
_RUNTIME_OPTIONS: ContextVar[dict[str, Any]] = ContextVar(
    "gloss_provider_runtime_options", default={}
)
_STREAM_USAGE: ContextVar[dict | None] = ContextVar("gloss_stream_usage", default=None)


@contextmanager
def capture_stream_usage():
    """Per-call, task-local usage sink; never share mutable provider state."""
    usage: dict = {}
    token = _STREAM_USAGE.set(usage)
    try:
        yield usage
    finally:
        _STREAM_USAGE.reset(token)


def report_stream_usage(usage: dict) -> None:
    sink = _STREAM_USAGE.get()
    if sink is not None:
        sink.update(usage)
        sink.setdefault("_complete", True)


def normalize_usage(raw: dict | None, system: str = "", messages: list[Message] | None = None,
                    text: str = "") -> dict:
    """Total input includes cache reads/writes; old token aliases stay supported."""
    raw = raw or {}
    exact = bool(raw.get("exact", any(k in raw for k in ("input_tokens", "prompt_tokens"))
                     and any(int(raw.get(k) or 0) for k in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens"))))
    inp = int(raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0)
    out = int(raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0)
    if not exact:
        inp = estimate_text_tokens(system + "\n" + message_text(messages or []))
        out = estimate_text_tokens(text)
    cached = min(max(0, int(raw.get("cached_input_tokens") or 0)), max(0, inp)) if exact else 0
    return {"input_tokens": max(0, inp), "cached_input_tokens": cached,
            "cache_write_tokens": max(0, int(raw.get("cache_write_tokens") or 0)),
            "output_tokens": max(0, out), "reasoning_tokens": max(0, int(raw.get("reasoning_tokens") or 0)),
            "exact": exact, "prompt_tokens": max(0, inp), "completion_tokens": max(0, out),
            "total_tokens": max(0, inp) + max(0, out)}


def anthropic_usage(raw: dict | None) -> dict:
    raw = raw or {}
    return {"input_tokens": int(raw.get("input_tokens") or 0) + int(raw.get("cache_read_input_tokens") or 0)
            + int(raw.get("cache_creation_input_tokens") or 0),
            "cached_input_tokens": int(raw.get("cache_read_input_tokens") or 0),
            "cache_write_tokens": int(raw.get("cache_creation_input_tokens") or 0),
            "output_tokens": int(raw.get("output_tokens") or 0), "exact": "input_tokens" in raw}


class ProviderSessionUnavailableError(RuntimeError):
    """A persisted provider conversation can no longer be resumed."""


@contextmanager
def provider_runtime_options(**options: Any):
    """Temporarily override transport details without changing its public API."""
    merged = {**_RUNTIME_OPTIONS.get(), **{k: v for k, v in options.items() if v is not None}}
    token = _RUNTIME_OPTIONS.set(merged)
    try:
        yield
    finally:
        _RUNTIME_OPTIONS.reset(token)


def runtime_option(name: str, default: Any = None) -> Any:
    return _RUNTIME_OPTIONS.get().get(name, default)


# -- live progress (what a local CLI is doing while it works) ---------------------
# A sink receives ``{"kind": "thinking", "text": delta}`` (reasoning as it
# streams) or ``{"kind": "step", "text": "..."}`` (one action the CLI took).
_PROGRESS: ContextVar[Any] = ContextVar("gloss_provider_progress", default=None)


@contextmanager
def progress_sink(callback: Any):
    token = _PROGRESS.set(callback)
    try:
        yield
    finally:
        _PROGRESS.reset(token)


def wants_progress() -> bool:
    return _PROGRESS.get() is not None


def report_progress(kind: str, text: str) -> None:
    sink = _PROGRESS.get()
    if sink is None or not text:
        return
    try:
        sink({"kind": kind, "text": text})
    except Exception:  # noqa: BLE001 - progress is best effort
        pass


async def communicate_lines(proc: Any, stdin: bytes | None, timeout: float, on_line: Any) -> tuple[bytes, bytes]:
    """``proc.communicate`` that also hands each stdout line to ``on_line`` as it
    arrives. Raises ``asyncio.TimeoutError`` like ``wait_for`` would."""
    import asyncio

    async def feed() -> None:
        if proc.stdin is None:
            return
        try:
            if stdin:
                proc.stdin.write(stdin)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    out: list[bytes] = []

    def emit(line: bytes) -> None:
        try:
            on_line(line)
        except Exception:  # noqa: BLE001 - a bad line must not break the call
            pass

    async def read_out() -> None:
        # Chunks, not readline(): one JSON event can exceed the stream's line limit.
        pending = b""
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                if pending:
                    emit(pending)
                return
            out.append(chunk)
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                emit(line)

    async def run() -> bytes:
        results = await asyncio.gather(feed(), read_out(), proc.stderr.read())
        await proc.wait()
        return results[2]

    err = await asyncio.wait_for(run(), timeout=timeout)
    return b"".join(out), err


def estimate_text_tokens(text: str) -> int:
    """Return a clearly-labelled best-effort token estimate.

    Transports occasionally omit usage. We use the same useful tokenizer as the feature
    prompt budgeter when it is available, with a conservative character
    fallback for minimal installations.  Callers must retain whether a value
    came from this function rather than presenting it as provider billing data.
    """
    if not text:
        return 0
    if _TOKEN_ENCODING is not None:
        return len(_TOKEN_ENCODING.encode(text))
    return max(1, len(text) // 4)


def message_text(messages: list[Message]) -> str:
    """Flatten text message content for an approximate input-token count."""
    parts: list[str] = []
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        parts.append(str(content))
    return "\n".join(parts)


def render_transcript(messages: list[Message]) -> str:
    """Flatten a conversation into a single text prompt (for the claude CLI).

    Mirrors the claude_proxy behaviour: a lone user turn is passed verbatim,
    otherwise turns are labelled and a trailing ``[Assistant]:`` cue is appended.
    """
    convo: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if isinstance(content, list):  # multimodal -> keep text blocks
            content = "\n".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if role in ("system", "developer"):
            continue  # system handled separately
        elif role == "assistant":
            convo.append(f"[Assistant]:\n{content}")
        else:
            convo.append(f"[User]:\n{content}")
    if len(convo) == 1 and convo[0].startswith("[User]:\n"):
        return convo[0][len("[User]:\n") :]
    return "\n\n".join(convo) + "\n\n[Assistant]:"


def split_system(messages: list[Message], system: str | None) -> tuple[str, list[Message]]:
    """Pull any system/developer messages into the system string."""
    sys_parts = []
    if system:
        sys_parts.append(system)
    rest = []
    for m in messages:
        if m.get("role") in ("system", "developer"):
            c = m.get("content") or ""
            if c:
                sys_parts.append(c)
        else:
            rest.append(m)
    return "\n\n".join(sys_parts), rest


class Provider:
    """Abstract LLM provider."""

    name = "base"

    async def test_connection(self) -> str:
        """Run the smallest provider request that proves the connection works."""
        text, _ = await self.complete(
            "You are a connectivity check. Reply with exactly: OK",
            [{"role": "user", "content": "ping"}],
        )
        return (text or "").strip()

    async def stream(
        self, system: str, messages: list[Message], model: str | None = None
    ) -> AsyncIterator[str]:
        """Yield text deltas. Default: chunk the non-stream completion."""
        text, usage = await self.complete(system, messages, model)
        report_stream_usage(usage)
        # emit in modest chunks so the UI feels live
        step = 24
        for i in range(0, len(text), step):
            yield text[i : i + step]

    async def complete(
        self, system: str, messages: list[Message], model: str | None = None
    ) -> tuple[str, dict]:
        """Return (text, usage_dict). Must be implemented by subclasses."""
        raise NotImplementedError
