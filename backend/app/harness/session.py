"""Session model, persistence and the per-session event bus.

A session is a JSON document persisted at ``<DATA_DIR>/sessions/<id>.json``
(rewritten atomically whenever something durable changes). Large tool results
live next to it in ``<DATA_DIR>/sessions/results/<session id>/<ref>.txt``.
Message types (one ordered list):

* ``user``        ``{id, type, text, attachments, created_at, command?, expanded?}``
* ``assistant``   ``{id, type, text, created_at, streaming, interrupted?}``
* ``tool_call``   ``{id, type, tool, args, status, result_preview, result_ref, ...}``
* ``system_note`` ``{id, type, kind, text, created_at}``
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .. import config
from ..platform_support import read_utf8_text

logger = logging.getLogger(__name__)

STATUSES = ("idle", "running", "awaiting_approval", "awaiting_input")
TOOL_STATUSES = ("pending", "awaiting_approval", "running", "done", "error", "denied")
TERMINAL_TOOL = {"done", "error", "denied"}
_ID = re.compile(r"^[a-f0-9]{12,32}$")
_REF = re.compile(r"^r_[a-f0-9]{8,32}$")
MAX_LOADED = 2000
# events forwarded from a subagent session to its parent's stream
FORWARDED = {"tool.updated", "approval.requested", "input.requested"}
# inbox (docs/glossx-workspaces.md section 1)
BLOCKING = ("approval", "input")
ATTENTION = ("approval", "input", "error", "done")
ACTIVITY_EVENTS = {"message.created", "message.completed", "tool.updated", "approval.requested",
                   "input.requested", "approval.resolved", "input.resolved", "turn.completed",
                   "error"}
PREVIEW_CHARS = 160


def now() -> float:
    return time.time()


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:16]


def empty_usage() -> dict[str, Any]:
    return {"input_tokens_est": 0, "output_tokens_est": 0, "llm_calls": 0,
            "input_tokens": 0, "cached_input_tokens": 0, "cache_write_tokens": 0,
            "output_tokens": 0, "reasoning_tokens": 0, "cache_hit_rate": 0.0,
            "last_call": None, "exact": True}


def add_usage(target: dict, call: dict) -> None:
    for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"):
        target[key] = int(target.get(key) or 0) + int(call.get(key) or 0)
    target["exact"] = bool(target.get("exact", True)) and bool(call.get("exact", False))
    target["cache_hit_rate"] = target["cached_input_tokens"] / target["input_tokens"] if target["input_tokens"] else 0.0
    target["last_call"] = {"input": call.get("input_tokens", 0), "cached": call.get("cached_input_tokens", 0),
                           "output": call.get("output_tokens", 0), "exact": bool(call.get("exact")),
                           "hit_rate": call.get("cached_input_tokens", 0) / call["input_tokens"] if call.get("input_tokens") else 0.0}


def sessions_dir() -> Path:
    path = config.SESSIONS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def valid_id(session_id: str) -> bool:
    return isinstance(session_id, str) and bool(_ID.fullmatch(session_id))


def new_session(
    *,
    title: str | None,
    project_id: str | None,
    mode: str,
    provider: str | None,
    model: str | None,
    context_limit: int,
    parent_id: str | None = None,
    subagent_type: str | None = None,
) -> dict[str, Any]:
    ts = now()
    return {
        "id": uuid.uuid4().hex[:16],
        "title": (title or "").strip()[:200] or "New chat",
        "title_set": bool((title or "").strip()),
        "project_id": project_id or None,
        "parent_id": parent_id,
        "subagent_type": subagent_type,
        "created_at": ts,
        "updated_at": ts,
        "mode": mode,
        "model": {"provider": provider or None, "model": model or None},
        "messages": [],
        "todos": [],
        "status": "idle",
        "usage": empty_usage(),
        "context": {"tokens_est": 0, "limit": context_limit, "compactions": 0},
        "scope_papers": [],
        "last_activity_at": ts,
        "last_viewed_at": ts,
        "unread": False,
        "attention": None,
        "branch": None,
    }


def save(session: dict[str, Any]) -> None:
    try:
        target = sessions_dir() / f"{session['id']}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        logger.exception("could not persist session %s", session.get("id"))


def load_all() -> list[dict[str, Any]]:
    out = []
    try:
        files = sorted(sessions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:MAX_LOADED]
    except OSError:
        return out
    for path in files:
        try:
            data = json.loads(read_utf8_text(path))
            if not valid_id(str(data.get("id"))):
                continue
        except (OSError, ValueError):
            logger.warning("skipping unreadable session %s", path.name)
            continue
        out.append(recover(data))
    return out


def recover(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a session loaded from disk after a restart."""
    data.setdefault("messages", [])
    data.setdefault("todos", [])
    data.setdefault("scope_papers", [])
    data.setdefault("usage", {"input_tokens_est": 0, "output_tokens_est": 0, "llm_calls": 0})
    usage = data["usage"]
    if "input_tokens" not in usage:
        usage.update(input_tokens=int(usage.get("input_tokens_est") or 0),
                     output_tokens=int(usage.get("output_tokens_est") or 0), exact=not bool(usage.get("llm_calls")))
    for key, value in empty_usage().items():
        usage.setdefault(key, value)
    data.setdefault("context", {"tokens_est": 0, "limit": 120_000, "compactions": 0})
    data.setdefault("model", {"provider": None, "model": None})
    data.setdefault("mode", "agent")
    data.setdefault("title", "New chat")
    data.setdefault("parent_id", None)
    stamp = data.get("updated_at") or data.get("created_at") or now()
    data.setdefault("created_at", stamp)
    data.setdefault("updated_at", stamp)
    data.setdefault("last_activity_at", stamp)
    data.setdefault("last_viewed_at", stamp)
    data["unread"] = bool(data.get("unread", False))
    if data.get("attention") not in ("error", "done"):
        data["attention"] = None  # approvals / questions do not survive a restart
    data.setdefault("branch", None)
    data["status"] = "idle"
    for msg in data["messages"]:
        if msg.get("type") == "assistant" and msg.get("streaming"):
            msg["streaming"] = False
            msg["interrupted"] = True
        if msg.get("type") == "tool_call" and msg.get("status") not in TERMINAL_TOOL:
            msg["status"] = "error"
            msg["error"] = "interrupted (server restarted)"
            msg["finished_at"] = msg.get("finished_at") or now()
    return data


def delete_files(session_id: str) -> None:
    if not valid_id(session_id):
        return
    try:
        (sessions_dir() / f"{session_id}.json").unlink(missing_ok=True)
    except OSError:
        pass
    results = sessions_dir() / "results" / session_id
    if results.is_dir():
        for f in results.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            results.rmdir()
        except OSError:
            pass


# -- stored tool results ------------------------------------------------------------
def _results_dir(session_id: str) -> Path:
    path = sessions_dir() / "results" / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_result(session_id: str, text: str) -> str:
    ref = "r_" + uuid.uuid4().hex[:12]
    (_results_dir(session_id) / f"{ref}.txt").write_text(text, encoding="utf-8")
    return ref


def read_result(session_id: str, ref: str) -> str | None:
    if not valid_id(session_id) or not _REF.fullmatch(str(ref or "")):
        return None
    path = sessions_dir() / "results" / session_id / f"{ref}.txt"
    if not path.is_file():
        return None
    return read_utf8_text(path)


def copy_result(src_session: str, dst_session: str, ref: str) -> None:
    text = read_result(src_session, ref)
    if text is not None:
        (_results_dir(dst_session) / f"{ref}.txt").write_text(text, encoding="utf-8")


# -- public views -----------------------------------------------------------------
def preview(session: dict[str, Any]) -> str:
    """The last assistant text, on one line, at most ``PREVIEW_CHARS`` characters."""
    for msg in reversed(session["messages"]):
        if msg.get("type") == "assistant" and (msg.get("text") or "").strip():
            text = " ".join(msg["text"][:2000].split())
            return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 3].rstrip() + "..."
    return ""


def summary(session: dict[str, Any]) -> dict[str, Any]:
    last = next((m for m in reversed(session["messages"])
                 if m.get("type") in {"user", "assistant"} and m.get("text")), None)
    return {
        "last_activity_at": session.get("last_activity_at") or session["updated_at"],
        "last_viewed_at": session.get("last_viewed_at"),
        "unread": bool(session.get("unread")),
        "attention": session.get("attention"),
        "preview": preview(session),
        "branch": session.get("branch"),
        "id": session["id"],
        "title": session["title"],
        "project_id": session.get("project_id"),
        "parent_id": session.get("parent_id"),
        "subagent_type": session.get("subagent_type"),
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "mode": session["mode"],
        "model": dict(session["model"]),
        "status": session["status"],
        "message_count": len(session["messages"]),
        "usage": dict(session["usage"]),
        "context": dict(session["context"]),
        "last_message": (last or {}).get("text", "")[:200],
        "forked_from": session.get("forked_from"),
    }


def public_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Message as sent to clients (internal prompt fields stripped)."""
    hidden = {"raw", "context", "tokens_est_prompt", "_prompt_item"}
    return {k: v for k, v in msg.items() if k not in hidden}


class Runtime:
    """Live state of one session: subscribers, the running turn, pending prompts."""

    def __init__(self, session: dict[str, Any], manager: Any, parent: "Runtime | None" = None,
                 parent_call_id: str | None = None) -> None:
        from ..agent.context import SpanStore

        self.session = session
        self.manager = manager
        self.parent = parent
        self.parent_call_id = parent_call_id
        self.subscribers: list[asyncio.Queue] = []
        self.task: asyncio.Task | None = None
        self.running = False  # a turn is executing (also for inline subagent turns)
        self.children: list[Runtime] = []
        # tool_call_id -> {"tool_call_id", "tool", "args", "reason", "suggested_rule", "future"}
        self.approvals: dict[str, dict[str, Any]] = {}
        self.pending_input: dict[str, Any] | None = None
        self.spans = SpanStore()
        self.seq = 0
        self.turn_tools: dict[str, Any] = {}
        self._last_inbox_key: tuple[bool, str | None] = (bool(session.get("unread")),
                                                         session.get("attention"))

    @property
    def id(self) -> str:
        return self.session["id"]

    @property
    def root(self) -> "Runtime":
        rt = self
        while rt.parent is not None:
            rt = rt.parent
        return rt

    def busy(self) -> bool:
        return self.running or (self.task is not None and not self.task.done())

    # -- events -------------------------------------------------------------------
    def emit(self, name: str, payload: dict[str, Any]) -> None:
        changed = self._track(name, payload) if self.parent is None else False
        self.seq += 1
        for q in list(self.subscribers):
            q.put_nowait((name, payload, self.seq))
        if self.parent is not None and name in FORWARDED:
            self.parent._forward(name, payload)
        if changed:
            self._inbox_changed()

    # -- inbox ----------------------------------------------------------------------
    def attention(self) -> str | None:
        """What the newest unseen event is. A pending question / approval wins
        (it blocks the thread); otherwise the stored ``done`` / ``error``."""
        if self.all_inputs():
            return "input"
        if self.all_approvals():
            return "approval"
        stored = self.session.get("attention")
        return stored if stored in ("error", "done") else None

    def _inbox_key(self) -> tuple[bool, str | None]:
        return bool(self.session.get("unread")), self.attention()

    def _track(self, name: str, payload: dict[str, Any]) -> bool:
        """Update ``last_activity_at`` / ``unread`` / ``attention`` for an event of
        this (root) session; True when the inbox has to be told."""
        before = self._last_inbox_key
        if name == "session.updated":
            # no activity by itself, but a subagent's approval may have been resolved
            self._last_inbox_key = self._inbox_key()
            return self._last_inbox_key != before
        if name not in ACTIVITY_EVENTS:
            return False
        session = self.session
        stamp = now()
        session["last_activity_at"] = stamp
        if name == "message.created" and payload.get("type") == "user":
            # the user wrote here: they have seen everything up to now
            session.update(unread=False, attention=None, last_viewed_at=stamp)
        elif name in {"approval.requested", "input.requested"}:
            session["unread"] = True
        elif name in {"message.created", "message.completed", "tool.updated"}:
            session["unread"] = True
        elif name == "error":
            session.update(unread=True, attention="error")
        elif name == "turn.completed":
            reason = payload.get("stop_reason")
            if reason == "error":
                session.update(unread=True, attention="error")
            elif reason in {"completed", "budget"}:
                session.update(unread=True, attention="done")
        self._last_inbox_key = self._inbox_key()
        return self._last_inbox_key != before

    def _inbox_changed(self) -> None:
        notify = getattr(self.manager, "inbox_changed", None)
        if notify is not None:
            try:
                notify()
            except Exception:
                logger.exception("could not publish the inbox")

    def mark_read(self) -> bool:
        """The user looked at this thread: clears ``unread`` and a non-blocking
        ``attention``. Returns True when something changed."""
        before = self._inbox_key()
        self.session.update(unread=False, attention=None, last_viewed_at=now())
        self._last_inbox_key = self._inbox_key()
        changed = self._last_inbox_key != before
        save(self.session)  # not persist(): viewing must not reorder by recency
        if changed:
            self.emit("session.updated", self.public())
            self._inbox_changed()
        return changed

    def _forward(self, name: str, payload: dict[str, Any]) -> None:
        self.emit(name, payload)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    def close_subscribers(self) -> None:
        for q in list(self.subscribers):
            q.put_nowait(None)
        self.subscribers.clear()

    def all_approvals(self) -> list[dict[str, Any]]:
        out = list(self.approvals.values())
        for child in self.children:
            out.extend(child.all_approvals())
        return out

    def all_inputs(self) -> list[dict[str, Any]]:
        out = [self.pending_input] if self.pending_input else []
        for child in self.children:
            out.extend(child.all_inputs())
        return out

    def replay(self) -> list[tuple[str, dict[str, Any]]]:
        """Current state as events (sent first to every new SSE subscriber)."""
        events: list[tuple[str, dict[str, Any]]] = [
            ("session.updated", self.public(with_messages=True)),
            ("todos.updated", {"todos": copy.deepcopy(self.session["todos"])}),
            ("context.updated", dict(self.session["context"])),
        ]
        for ap in self.all_approvals():
            events.append(("approval.requested", approval_payload(ap)))
        for inp in self.all_inputs():
            events.append(("input.requested", input_payload(inp)))
        return events

    # -- status -------------------------------------------------------------------
    def refresh_status(self) -> None:
        if not self.busy() and not self.approvals and not self.pending_input:
            status = "idle"
        elif self.all_inputs():
            status = "awaiting_input"
        elif self.all_approvals():
            status = "awaiting_approval"
        else:
            status = "running"
        if status != self.session["status"]:
            self.session["status"] = status
            self.emit("session.updated", self.public())
        if self.parent is not None:
            self.parent.refresh_status()

    def touch(self) -> None:
        self.session["updated_at"] = now()

    def persist(self) -> None:
        self.touch()
        save(self.session)

    def public(self, *, with_messages: bool = False) -> dict[str, Any]:
        out = summary(self.session)
        out["archived"] = bool(self.session.get("archived"))
        out["attention"] = self.attention()
        out["blocking"] = out["attention"] in BLOCKING
        ref = getattr(self.manager, "project_ref", None)
        out["project"] = ref(self.session.get("project_id")) if ref is not None else None
        out["todos"] = copy.deepcopy(self.session["todos"])
        out["scope_papers"] = list(self.session.get("scope_papers") or [])
        out["pending_approvals"] = [approval_payload(a) for a in self.all_approvals()]
        inputs = self.all_inputs()
        out["pending_input"] = input_payload(inputs[0]) if inputs else None
        try:
            from . import vcs
            from .workspace import key_for, root_for

            out["workspace"] = {"key": key_for(self.session.get("project_id")),
                                "path": str(root_for(self.session.get("project_id"))),
                                "vcs": vcs.brief(self.session.get("project_id"))}
        except Exception:
            out["workspace"] = None
        if with_messages:
            out["messages"] = self.public_messages()
        return out

    def public_messages(self) -> list[dict[str, Any]]:
        """Messages for clients. The tool calls of a subagent follow their ``task``
        call (as they do live, where they are forwarded as ``tool.updated``)."""
        lookup = getattr(self.manager, "session_data", None)
        out = []
        for msg in self.session["messages"]:
            out.append(public_message(msg))
            sub = msg.get("subagent") if msg.get("type") == "tool_call" else None
            child = lookup(sub.get("session_id")) if isinstance(sub, dict) and lookup else None
            if child:
                out.extend(public_message(m) for m in child.get("messages") or []
                           if m.get("type") == "tool_call")
        return out


def approval_payload(ap: dict[str, Any]) -> dict[str, Any]:
    return {
        **({"preview": ap["preview"]} if ap.get("preview") else {}),
        "tool_call_id": ap["tool_call_id"],
        "tool": ap["tool"],
        "args": ap["args"],
        "reason": ap.get("reason", ""),
        "session_id": ap.get("session_id"),
        "parent_id": ap.get("parent_id"),
        "suggested_rule": ap.get("suggested_rule"),
        "category": ap.get("category"),
    }


def input_payload(inp: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_call_id": inp["tool_call_id"],
        "question": inp["question"],
        "choices": list(inp.get("choices") or []),
        "session_id": inp.get("session_id"),
    }
