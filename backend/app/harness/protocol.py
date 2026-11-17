"""Provider-agnostic tool-calling protocol.

The model asks for tools with blocks at the end of its message::

    <tool_call>{"name": "fs_read", "arguments": {"path": "notes.md"}}</tool_call>

and the harness answers with ``<tool_result name="..." id="...">`` blocks whose
body is wrapped as untrusted data. Works with any text-in/text-out provider
(local ``claude`` / ``codex`` CLIs, Anthropic, OpenAI) -- no native function
calling needed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..agent.context import wrap_untrusted
from ..json_utils import extract_json

OPEN = "<tool_call>"
CLOSE = "</tool_call>"
MAX_CALLS_PER_MESSAGE = 16
_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)
# a model must never write tool results itself; anything from here on is dropped
_FAKE_RESULT = re.compile(r"<\s*tool_result\b", re.I)
_PROTOCOL_TAG = re.compile(r"<(\s*/?\s*)(tool_call|tool_result)", re.I)


@dataclass
class ParsedCall:
    name: str
    arguments: dict[str, Any]
    error: str | None = None
    raw: str = ""


def cut_fake_results(raw: str) -> str:
    match = _FAKE_RESULT.search(raw or "")
    return raw[: match.start()] if match else (raw or "")


def _parse_one(body: str) -> ParsedCall:
    text = body.strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    data: Any
    try:
        data = json.loads(text)
    except ValueError:
        data = extract_json(text)
    if not isinstance(data, dict):
        return ParsedCall("", {}, "the block is not a JSON object", body[:500])
    name = data.get("name") or data.get("tool") or data.get("function")
    if isinstance(name, dict):  # {"function": {"name": ..., "arguments": ...}}
        data = {**data, **name}
        name = name.get("name")
    if not isinstance(name, str) or not name.strip():
        return ParsedCall("", {}, "missing \"name\"", body[:500])
    args: Any = None
    for key in ("arguments", "args", "input", "parameters"):
        if key in data:
            args = data[key]
            break
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return ParsedCall(name.strip(), {}, "\"arguments\" is not valid JSON", body[:500])
    if not isinstance(args, dict):
        return ParsedCall(name.strip(), {}, "\"arguments\" must be a JSON object", body[:500])
    return ParsedCall(name.strip(), args, None, body[:2000])


def parse_message(raw: str) -> tuple[str, list[ParsedCall]]:
    """Return ``(visible_text, calls)`` for a finished assistant message."""
    raw = cut_fake_results(raw)
    calls = [_parse_one(m.group(1)) for m in _BLOCK.finditer(raw)]
    visible = _BLOCK.sub("", raw)
    # an unterminated trailing block (stream ended early)
    idx = visible.find(OPEN)
    if idx >= 0:
        tail = visible[idx + len(OPEN):]
        visible = visible[:idx]
        if tail.strip():
            calls.append(_parse_one(tail))
    visible = re.sub(r"\n{3,}", "\n\n", visible).strip()
    return visible, calls


class VisibleStream:
    """Filter streamed deltas so ``<tool_call>`` blocks never reach the UI text."""

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False
        self.stopped = False

    @staticmethod
    def _partial(buf: str, tag: str) -> int:
        for n in range(min(len(tag) - 1, len(buf)), 0, -1):
            if tag.startswith(buf[-n:]):
                return n
        return 0

    def feed(self, chunk: str) -> str:
        if self.stopped:
            return ""
        self.buf += chunk
        out: list[str] = []
        while True:
            if self.inside:
                j = self.buf.find(CLOSE)
                if j < 0:
                    self.buf = self.buf[-(len(CLOSE) - 1):]
                    break
                self.buf = self.buf[j + len(CLOSE):]
                self.inside = False
                continue
            fake = _FAKE_RESULT.search(self.buf)
            i = self.buf.find(OPEN)
            if fake and (i < 0 or fake.start() < i):
                out.append(self.buf[: fake.start()])
                self.buf = ""
                self.stopped = True
                break
            if i >= 0:
                out.append(self.buf[:i])
                self.buf = self.buf[i + len(OPEN):]
                self.inside = True
                continue
            keep = max(self._partial(self.buf, OPEN), self._partial(self.buf, "<tool_result"))
            out.append(self.buf[: len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return "".join(out)

    def flush(self) -> str:
        if self.inside or self.stopped:
            return ""
        out, self.buf = self.buf, ""
        return out


def neutralize(text: str) -> str:
    """Tool output must not be able to open/close protocol blocks."""
    return _PROTOCOL_TAG.sub(lambda m: "&lt;" + m.group(1) + m.group(2), text)


def _attr(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in "._:-")[:80]


def render_result(name: str, call_id: str, text: str, *, status: str = "ok",
                  trusted: bool = False) -> str:
    """One ``<tool_result>`` block; tool output is wrapped as untrusted data."""
    body = neutralize(text or "")
    if not trusted:
        body = wrap_untrusted(f"tool:{name}", body)
    return (f'<tool_result name="{_attr(name) or "unknown"}" id="{_attr(call_id)}" '
            f'status="{status}">\n{body}\n</tool_result>')


def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


PROTOCOL_INSTRUCTIONS = """\
# How to use tools
You can call tools. To call one or more tools, END your message with one block per call:
<tool_call>{"name": "<tool name>", "arguments": {<JSON arguments>}}</tool_call>
Rules:
- The block content must be a single JSON object with "name" and "arguments". No comments, no trailing text inside the block.
- Put all tool calls at the very end of your message, after any explanation. Calls in the same message run in parallel, so only batch calls that do not depend on each other.
- Never write <tool_result> blocks yourself. The harness runs your calls and replies with <tool_result name="..." id="..." status="ok|error"> blocks; then you continue.
- When you have everything you need, reply normally (Markdown) WITHOUT any <tool_call> block; that ends your turn.
- If a tool result says a call was denied, do not retry the same call; adapt or ask the user.
- Cite paper evidence with the [S:<span_id>] markers that paper tools return.
"""
