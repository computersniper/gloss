"""The agent turn loop: stream, parse tool calls, run them under permissions, repeat.

One *turn* = everything between a user message and the model's final reply
without tool calls. Per round the engine

1. builds the prompt (system prompt with the tool list + derived messages),
   auto-compacting first when the context is >= 80% full;
2. streams the assistant message through ``providers.registry.stream`` and
   forwards the visible text (``<tool_call>`` blocks are filtered out);
3. parses the tool calls, runs them (adjacent parallel-safe calls
   concurrently) through mode / permission rules / hooks / approvals, and
   records each result as a ``tool_call`` message.

The loop ends when the model answers without tool calls, the tool-round budget
is hit, the provider fails, or the turn is interrupted (task cancellation).
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import time
import hashlib
from typing import Any

from ..agent import connections as conn_mod
from ..agent.context import span_ids_in
from ..providers import registry as providers
from ..providers.base import progress_sink
from . import context as ctx_mod
from . import execution, extensions, permissions, protocol, workspace
from . import settings as settings_mod
from .session import (
    TERMINAL_TOOL,
    Runtime,
    approval_payload,
    input_payload,
    new_id,
    new_session,
    now,
    public_message,
    store_result,
    empty_usage,
    add_usage,
)
from .tools import (
    HarnessContext,
    ToolOutput,
    ToolSpec,
    build_table,
    harness_registry,
    tool_name_for,
    validate_args,
)

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 2000          # result_preview shown on the tool card
MAX_THINKING_CHARS = 40_000    # reasoning kept per assistant message
MAX_STEPS = 80
INLINE_CHARS = 12_000         # larger results are truncated for the model ...
INLINE_HEAD = 9_000           # ... to this many characters plus a result_read ref
ARG_PREVIEW_CHARS = 20_000
_SPAN_MARK = re.compile(r"\[S:([^\]\s]+#\d+)\]")
RESERVED_CALL_KEYS = {"id", "type", "tool", "args", "status", "raw", "created_at",
                      "started_at", "finished_at", "session_id", "assistant_id"}


class UsageMeter:
    """``providers.call_meter`` hook: counts model calls made by nested tools."""

    def __init__(self, session: dict[str, Any], rt: Runtime | None = None) -> None:
        self.session = session
        self.rt = rt
        self.turn = empty_usage()
        self.main = False  # the loop's own stream call is accounted separately

    def before_llm_call(self) -> None:
        self.session["usage"]["llm_calls"] += 1
        self.turn["llm_calls"] += 1

    def after_llm_call(self, tokens_est: int) -> None:
        if not self.main:
            self.session["usage"]["input_tokens_est"] += max(0, int(tokens_est or 0))

    def after_usage(self, usage: dict) -> None:
        add_usage(self.session["usage"], usage)
        add_usage(self.turn, usage)
        if self.rt:
            self.rt.emit("usage.updated", dict(self.session["usage"]))

    def usage_unavailable(self) -> None:
        self.session["usage"]["exact"] = False
        self.turn["exact"] = False
        if self.rt:
            self.rt.emit("usage.updated", dict(self.session["usage"]))


def err(error: BaseException) -> str:
    text = str(error).strip()
    return text[:1000] if text else type(error).__name__


# -- small state helpers -------------------------------------------------------------------
def add_message(rt: Runtime, msg: dict[str, Any], *, emit: bool = True) -> dict[str, Any]:
    rt.session["messages"].append(msg)
    if emit:
        rt.emit("message.created", public_message(msg))
    return msg


def add_note(rt: Runtime, kind: str, text: str, **extra: Any) -> dict[str, Any]:
    note = {"id": new_id("m_"), "type": "system_note", "kind": kind, "text": text,
            "created_at": now(), **extra}
    ctx_mod.stamp(note)
    return add_message(rt, note)


def update_call(rt: Runtime, call: dict[str, Any], **fields: Any) -> None:
    """Merge ``fields`` into a tool_call message and publish ``tool.updated``."""
    for key, value in fields.items():
        call[key] = value
    rt.emit("tool.updated", public_message(call))


def set_mode(rt: Runtime, mode: str, note: str | None = None) -> None:
    if mode not in settings_mod.MODES:
        raise ValueError(f"mode must be one of: {', '.join(settings_mod.MODES)}")
    if rt.session["mode"] == mode:
        return
    rt.session["mode"] = mode
    add_note(rt, "mode", note or f"Mode changed to {mode}.", mode=mode)
    rt.emit("session.updated", rt.public())
    rt.persist()


def _workspace(rt: Runtime) -> tuple[str, Any]:
    project_id = rt.session.get("project_id")
    return workspace.key_for(project_id), workspace.root_for(project_id)


def _args_preview(args: Any) -> Any:
    if not isinstance(args, dict):
        return args
    out = {}
    for key, value in list(args.items())[:40]:
        if isinstance(value, str) and len(value) > ARG_PREVIEW_CHARS:
            value = value[:ARG_PREVIEW_CHARS] + "...[truncated]"
        out[str(key)] = value
    return out


def _edit_preview(spec: ToolSpec, args: dict[str, Any], root: Any) -> dict[str, Any] | None:
    """Proposed diff of an ``fs_write`` / ``fs_edit`` call (shown before approval)."""
    if spec.name not in {"fs_write", "fs_edit"}:
        return None
    try:
        target = workspace.resolve(root, args.get("path"))
        before = workspace.read_text(target) if target.is_file() else None
        if spec.name == "fs_write":
            after = args.get("content")
        else:
            old, new = args.get("old_string"), args.get("new_string")
            if before is None or not isinstance(old, str) or not isinstance(new, str) or old not in before:
                return None
            after = before.replace(old, new) if args.get("replace_all") else before.replace(old, new, 1)
        if not isinstance(after, str):
            return None
        rel = workspace.relpath(root, target)
        diff = "\n".join(difflib.unified_diff(
            (before or "").splitlines(), after.splitlines(),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
        return {"path": rel, "existed_before": before is not None, "diff": diff[:60_000]}
    except Exception:
        return None


def sync_status(rt: Runtime) -> None:
    """Refresh the status up the parent chain and publish ``session.updated`` on
    every level (the pending approvals / input changed even if the status did not)."""
    chain = []
    node: Runtime | None = rt
    while node is not None:
        chain.append((node, node.session["status"]))
        node = node.parent
    rt.refresh_status()
    for node, before in chain:
        if node.session["status"] == before:
            node.emit("session.updated", node.public())


def _human_pages(value: Any) -> Any:
    """Copy of a tool result with evidence-span pages 1-based for the model.

    Kernel spans carry the parsed 0-based page index (the UI needs that to jump),
    but the model writes page numbers into answers and notes for the reader.
    """
    if isinstance(value, list):
        return [_human_pages(item) for item in value]
    if isinstance(value, dict):
        out = {key: _human_pages(item) for key, item in value.items()}
        span_id = value.get("id")
        if isinstance(span_id, str) and "#" in span_id and isinstance(value.get("page"), int):
            out["page"] = value["page"] + 1
        return out
    return value


def _span_refs(rt: Runtime, result: Any, text: str) -> list[dict[str, Any]]:
    """Evidence spans a tool result refers to (for clickable citations in the UI)."""
    ids: list[str] = []
    try:
        ids.extend(span_ids_in(result))

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "span_ids" and isinstance(item, list):
                        ids.extend(str(s) for s in item)
                    elif isinstance(item, (dict, list)):
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(result)
        ids.extend(_SPAN_MARK.findall(text or ""))
        out = []
        for sid in list(dict.fromkeys(ids))[:20]:
            span = rt.root.spans.get(sid.removeprefix("S:"))
            if span:
                out.append({**span, "text": (span.get("text") or "")[:400]})
        return out
    except Exception:
        return []


# -- approvals and user input ---------------------------------------------------------------
async def await_approval(
    rt: Runtime,
    key: str,
    *,
    tool: str,
    args: dict[str, Any],
    reason: str,
    suggested_rule: str | None,
    category: str,
    parent_id: str | None,
    preview: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Park an approval request on the session; returns ``(decision, feedback)``."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    entry = {
        "tool_call_id": key, "tool": tool, "args": _args_preview(args), "reason": reason[:500],
        "session_id": rt.id, "parent_id": parent_id, "suggested_rule": suggested_rule,
        "category": category, "future": future, "requested_at": now(),
    }
    rt.approvals[key] = entry
    payload = approval_payload(entry)
    if preview:
        payload["preview"] = preview
        entry["preview"] = preview
    rt.emit("approval.requested", payload)
    sync_status(rt)
    decision, feedback, resolved = "deny", "", "cancelled"
    try:
        decision, feedback = await future
        resolved = decision
    finally:
        rt.approvals.pop(key, None)
        rt.emit("approval.resolved", {"tool_call_id": key, "session_id": rt.id,
                                      "decision": resolved})
        sync_status(rt)
    if decision == "always" and suggested_rule:
        try:
            settings_mod.add_rule("allow", suggested_rule)
        except Exception:
            logger.exception("could not save allow rule %s", suggested_rule)
    return decision, feedback


async def ask_user(ctx: HarnessContext, question: str, choices: list[str]) -> str:
    rt: Runtime = ctx.runtime
    call = ctx.call or {"id": new_id("c_")}
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    entry = {"tool_call_id": call["id"], "question": question, "choices": list(choices),
             "session_id": rt.id, "future": future}
    rt.pending_input = entry
    rt.emit("input.requested", input_payload(entry))
    sync_status(rt)
    try:
        answer = await future
    finally:
        rt.pending_input = None
        rt.emit("input.resolved", {"tool_call_id": call["id"], "session_id": rt.id})
        sync_status(rt)
    return str(answer)


def nested_authorizer(ctx: HarnessContext):
    """Approval delegate for a workflow run started by ``run_workflow``."""
    rt: Runtime = ctx.runtime
    parent_call = ctx.call or {}

    async def authorize(approval: dict[str, Any]) -> tuple[bool, str]:
        tool = tool_name_for(str(approval.get("capability") or ""))
        args = approval.get("args") if isinstance(approval.get("args"), dict) else {}
        spec = rt.turn_tools.get(tool)
        reason = str(approval.get("reason") or "")
        if spec is not None:
            decision, why = permissions.decide(spec, args, rt.session["mode"], settings_mod.load(_workspace(rt)[1]),
                                               conn_mod.auto_approve())
            if decision == "allow":
                return True, ""
            if decision in {"deny", "unavailable"}:
                return False, why
            reason = reason or why
        key = f"{parent_call.get('id') or 'wf'}-{approval.get('id') or new_id()}"
        decision, feedback = await await_approval(
            rt, key, tool=tool, args=args, reason=reason or "workflow step needs approval",
            suggested_rule=tool if spec is not None else None,
            category=spec.category if spec is not None else "exec", parent_id=parent_call.get("id"))
        if decision in {"once", "always"}:
            return True, ""
        return False, ("approval denied" + (f": {feedback}" if feedback else ""))

    return authorize


# -- hooks ------------------------------------------------------------------------------------
async def run_hooks(rt: Runtime, event: str, settings: dict[str, Any], payload: dict[str, Any],
                    *, tool: str | None = None, primary: str | None = None) -> list[dict[str, Any]]:
    hooks = extensions.hooks_for(event, settings)
    if not hooks:
        return []
    _, root = _workspace(rt)
    body = json.dumps({"event": event, "session_id": rt.id,
                       "project_id": rt.session.get("project_id"), "workspace": str(root),
                       "mode": rt.session["mode"], **payload},
                      ensure_ascii=False, default=str).encode("utf-8")
    env = os.environ.copy()
    env["GLOSSX_WORKSPACE"] = str(root)
    env["GLOSSX_HOOK_EVENT"] = event
    out = []
    for hook in hooks:
        matcher = hook.get("matcher") or "*"
        if tool is not None and matcher != "*" and not permissions.matches(matcher, tool, primary):
            continue
        try:
            res = await execution.run_process(
                execution.shell_argv(hook["command"]), cwd=root,
                timeout=float(hook.get("timeout") or 30), stdin=body, env=env)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            res = {"exit_code": None, "timed_out": False, "stdout": "", "stderr": err(error)}
        out.append({"command": hook["command"], "matcher": matcher,
                    "source": hook.get("source", "settings"), **res})
    return out


def _hook_failure(res: dict[str, Any]) -> str | None:
    if res.get("timed_out"):
        return "hook timed out"
    if res.get("exit_code") != 0:
        return (res.get("stderr") or res.get("stdout") or "").strip()[:2000] or (
            f"hook exited with code {res.get('exit_code')}")
    return None


# -- running tool calls ---------------------------------------------------------------------
def _harness_context(rt: Runtime, spec: ToolSpec | None, call: dict[str, Any],
                     settings: dict[str, Any]) -> HarnessContext:
    session = rt.session
    key, root = _workspace(rt)
    goal = next((m.get("text") or "" for m in reversed(session["messages"])
                 if m.get("type") == "user"), "")
    run_id = rt.id
    if spec is not None and str(spec.capability or "").startswith("delegate."):
        run_id = key  # CLI delegates work inside the project workspace
    return HarnessContext(
        run_id=run_id, paper_ids=session["scope_papers"], spans=rt.root.spans,
        import_jobs=getattr(rt.manager, "import_jobs", None),
        provider=(session.get("model") or {}).get("provider") or None,
        project_id=session.get("project_id"), goal=goal[:4000],
        imported=rt.root.__dict__.setdefault("imported", {}),
        runtime=rt, call=call, workspace_key=key, workspace_root=root,
        mode=session["mode"], settings=settings,
    )


def _finish_call(rt: Runtime, call: dict[str, Any], status: str, text: str, *,
                 meta: dict[str, Any] | None = None, trusted: bool = False) -> None:
    text = text if isinstance(text, str) else protocol.to_text(text)
    fields: dict[str, Any] = {k: v for k, v in (meta or {}).items() if k not in RESERVED_CALL_KEYS}
    ref = None
    if len(text) > PREVIEW_CHARS:
        try:
            ref = store_result(rt.id, text)
        except OSError:
            logger.exception("could not store a tool result")
    model_text = text
    if len(text) > INLINE_CHARS and ref:
        model_text = (text[:INLINE_HEAD] + f"\n[... truncated: {len(text) - INLINE_HEAD} more "
                      f"characters. The full result is stored as ref {ref}; read more with "
                      f"result_read(ref=\"{ref}\", offset={INLINE_HEAD}).]")
    elif len(text) > INLINE_CHARS:
        model_text = text[:INLINE_HEAD] + "\n[... truncated]"
    call["raw"] = model_text
    if trusted:
        call["trusted"] = True
    fields.update(status=status, result_preview=text[:PREVIEW_CHARS], result_ref=ref,
                  result_chars=len(text), finished_at=now())
    if status == "error":
        fields["error"] = text[:500]
    call["tokens_est"] = ctx_mod.est_tokens(ctx_mod.tool_result_block({**call, **fields}))
    update_call(rt, call, **fields)


async def _invoke(rt: Runtime, spec: ToolSpec, call: dict[str, Any], args: dict[str, Any],
                  settings: dict[str, Any]) -> tuple[str, str, dict[str, Any], bool]:
    """Run the handler; returns ``(status, text, meta, trusted)``."""
    hctx = _harness_context(rt, spec, call, settings)
    delegate = str(spec.capability or "").startswith("delegate.")
    before = None
    if delegate:
        before = await asyncio.to_thread(workspace.snapshot, hctx.workspace_root)
    try:
        coro = spec.handler(args, hctx)
        result = await (asyncio.wait_for(coro, spec.timeout) if spec.timeout else coro)
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        return "error", f"timed out after {int(spec.timeout or 0)}s", {}, False
    except (ValueError, LookupError, workspace.WorkspaceError, PermissionError) as error:
        return "error", err(error).strip("'\""), {}, False
    except Exception as error:
        logger.exception("tool %s failed", spec.name)
        return "error", f"{type(error).__name__}: {err(error)}", {}, False
    meta: dict[str, Any] = {}
    if before is not None:
        cps, _ = await asyncio.to_thread(
            workspace.record_changes, hctx.workspace_key, hctx.workspace_root, before,
            source=spec.name, session_id=rt.id, tool_call_id=call["id"])
        if cps:
            meta["checkpoints"] = [{k: c[k] for k in ("id", "path", "lines_added", "lines_removed",
                                                       "existed_before")} for c in cps]
    if isinstance(result, ToolOutput):
        meta.update(result.meta or {})
        status, text, trusted = ("error" if result.is_error else "done"), result.text, result.trusted
    else:
        status, text, trusted = "done", protocol.to_text(_human_pages(result)), False
    if not trusted:
        spans = _span_refs(rt, None if isinstance(result, ToolOutput) else result, text)
        if spans:
            meta["spans"] = spans
    return status, text, meta, trusted


async def run_call(rt: Runtime, table: dict[str, ToolSpec], call: dict[str, Any],
                   settings: dict[str, Any]) -> None:
    """Gate and run one tool call. Never raises except on cancellation."""
    try:
        await _run_call(rt, table, call, settings)
    except asyncio.CancelledError:
        if call.get("status") not in TERMINAL_TOOL:
            _finish_call(rt, call, "error", "interrupted by the user before it finished")
        raise
    except Exception as error:  # a harness bug must not kill the turn
        logger.exception("tool call %s crashed", call.get("tool"))
        if call.get("status") not in TERMINAL_TOOL:
            _finish_call(rt, call, "error", f"internal error: {err(error)}")


async def _run_call(rt: Runtime, table: dict[str, ToolSpec], call: dict[str, Any],
                    settings: dict[str, Any]) -> None:
    mode = rt.session["mode"]
    spec = table.get(call["tool"])
    if spec is None:
        near = difflib.get_close_matches(call["tool"], list(table), n=4, cutoff=0.5)
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        _finish_call(rt, call, "error", f"unknown tool: {call['tool']}.{hint} Use only the tools "
                     "listed in the system prompt.")
        return
    update_call(rt, call, category=spec.category, source=spec.source)
    args, problem = validate_args(spec, call.get("args") or {})
    if problem:
        _finish_call(rt, call, "error", f"invalid arguments: {problem}. Expected: "
                     + json.dumps(spec.parameters.get("properties") or {}, ensure_ascii=False)[:1500])
        return
    key, root = _workspace(rt)
    if spec.workspace_write and spec.primary_arg == "path":
        try:
            args["path"] = workspace.relpath(root, workspace.resolve(root, args.get("path")))
        except workspace.WorkspaceError as error:
            _finish_call(rt, call, "error", str(error))
            return
    call["args"] = args
    decision, reason = permissions.decide(spec, args, mode, settings, conn_mod.auto_approve())
    if decision == "unavailable":
        _finish_call(rt, call, "error", reason + ". Tell the user what you would do, or ask them "
                     "to switch to agent mode.")
        return
    if decision == "deny":
        _finish_call(rt, call, "denied", f"Denied: {reason}. Do not retry this call.")
        return
    primary = spec.primary_value(args)
    key, root = _workspace(rt)
    hook_payload = {"tool": spec.name, "args": args, "tool_call_id": call["id"]}
    for res in await run_hooks(rt, "pre_tool_use", settings, hook_payload, tool=spec.name,
                               primary=primary):
        failure = _hook_failure(res)
        if failure is not None:
            _finish_call(rt, call, "denied",
                         f"Blocked by a pre_tool_use hook ({res['command'][:120]}): {failure}",
                         meta={"blocked_by_hook": True})
            return
    if decision == "ask":
        preview = _edit_preview(spec, args, root)
        suggested = None if spec.category == "plan" or permissions.protects_config(spec, primary) else permissions.suggest_rule(spec.name, primary)
        update_call(rt, call, status="awaiting_approval", approval_reason=reason,
                    **({"preview": preview} if preview else {}))
        verdict, feedback = await await_approval(
            rt, call["id"], tool=spec.name, args=args, reason=reason, suggested_rule=suggested,
            category=spec.category, parent_id=rt.parent_call_id, preview=preview)
        if verdict not in {"once", "always"}:
            text = "The user denied this tool call."
            if feedback:
                text += f" Their feedback: {feedback}"
            _finish_call(rt, call, "denied", text + " Do not retry the same call; adapt.",
                         meta={"feedback": feedback or None}, trusted=True)
            return
        call["approved"] = verdict
    update_call(rt, call, status="running", started_at=now())
    status, text, meta, trusted = await _invoke(rt, spec, call, args, settings)
    post = await run_hooks(rt, "post_tool_use", settings,
                           {**hook_payload, "status": status, "result": text[:4000]},
                           tool=spec.name, primary=primary)
    notes = [f"[post_tool_use hook {r['command'][:80]}]: {_hook_failure(r)}"
             for r in post if _hook_failure(r) is not None]
    if notes:
        text += "\n" + "\n".join(notes)
    _finish_call(rt, call, status, text, meta=meta, trusted=trusted)


async def execute_calls(rt: Runtime, table: dict[str, ToolSpec], assistant: dict[str, Any],
                        parsed: list[protocol.ParsedCall], settings: dict[str, Any]) -> None:
    calls: list[tuple[dict[str, Any], protocol.ParsedCall]] = []
    for i, item in enumerate(parsed):
        call = {
            "id": new_id("c_"), "type": "tool_call", "tool": item.name or "unknown",
            "args": item.arguments, "status": "pending", "result_preview": None,
            "result_ref": None, "created_at": now(), "started_at": None, "finished_at": None,
            "session_id": rt.id, "assistant_id": assistant["id"],
            "parent_tool_call_id": rt.parent_call_id,
        }
        add_message(rt, call)
        if item.error:
            _finish_call(rt, call, "error",
                         f"malformed tool call ({item.error}). Write exactly: <tool_call>{{\"name\": "
                         f"\"<tool>\", \"arguments\": {{...}}}}</tool_call> with valid JSON.")
        elif i >= protocol.MAX_CALLS_PER_MESSAGE:
            _finish_call(rt, call, "error", f"too many tool calls in one message (max "
                         f"{protocol.MAX_CALLS_PER_MESSAGE}); send the rest in the next message")
        else:
            calls.append((call, item))
    rt.persist()
    # adjacent parallel-safe calls run together; everything else runs in order
    batch: list[dict[str, Any]] = []

    async def flush() -> None:
        if not batch:
            return
        group = list(batch)
        batch.clear()
        if len(group) == 1:
            await run_call(rt, table, group[0], settings)
            return
        tasks = [asyncio.create_task(run_call(rt, table, c, settings)) for c in group]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    try:
        for call, _ in calls:
            spec = table.get(call["tool"])
            if spec is not None and spec.parallel_safe:
                batch.append(call)
                continue
            await flush()
            await run_call(rt, table, call, settings)
        await flush()
    except asyncio.CancelledError:
        for call, _ in calls:
            if call.get("status") not in TERMINAL_TOOL:
                _finish_call(rt, call, "error", "interrupted by the user before it ran")
        raise
    finally:
        rt.persist()


# -- the model call ---------------------------------------------------------------------------
async def stream_assistant(rt: Runtime, system: str, meter: UsageMeter
                           ) -> tuple[dict[str, Any], list[protocol.ParsedCall]]:
    session = rt.session
    messages = ctx_mod.provider_messages(session)
    model = session.get("model") or {}
    msg = {"id": new_id("m_"), "type": "assistant", "text": "", "created_at": now(),
           "streaming": True}
    add_message(rt, msg)
    raw = ""
    visible = protocol.VisibleStream()

    def push(piece: str) -> None:
        if piece:
            offset = len(msg["text"])
            msg["text"] += piece
            rt.emit("message.delta", {"id": msg["id"], "text": piece, "offset": offset})

    started = time.monotonic()

    def progress(event: dict[str, Any]) -> None:
        # What the model/CLI is doing while it works: shown live under
        # "Thinking", and kept on the message as "Thought for N s".
        # ``at`` (offset / index) makes a replayed event idempotent on the client.
        kind, text = event.get("kind"), str(event.get("text") or "")
        if kind == "thinking":
            at = len(msg.get("thinking", ""))
            if at >= MAX_THINKING_CHARS:
                return
            text = text[: MAX_THINKING_CHARS - at]
            msg["thinking"] = msg.get("thinking", "") + text
        elif kind == "step":
            steps = msg.setdefault("steps", [])
            at = len(steps)
            if at >= MAX_STEPS:
                return
            text = text[:300]
            steps.append(text)
        else:
            return
        rt.emit("message.progress", {"id": msg["id"], "kind": kind, "text": text, "at": at})

    meter.main = True
    try:
        with providers.harness_session(session, lambda reason: add_note(rt, "cache_reset", f"Prompt cache reset: {reason}.", reason=reason)), \
                progress_sink(progress):
            async for chunk in providers.stream(system, messages, provider=model.get("provider") or None,
                                                model=model.get("model") or None,
                                                effort=model.get("effort") or None):
                if not chunk:
                    continue
                raw += chunk
                push(visible.feed(chunk))
        push(visible.flush())
    except BaseException as error:
        msg["streaming"] = False
        msg["thought_ms"] = int((time.monotonic() - started) * 1000)
        if isinstance(error, asyncio.CancelledError):
            msg["interrupted"] = True
        else:
            msg["error"] = err(error)
        text, _ = protocol.parse_message(raw)
        msg["text"] = text
        ctx_mod.stamp(msg)
        rt.emit("message.completed", public_message(msg))
        raise
    finally:
        meter.main = False
    raw = protocol.cut_fake_results(raw)
    text, calls = protocol.parse_message(raw)
    msg["text"] = text
    msg["streaming"] = False
    msg["thought_ms"] = int((time.monotonic() - started) * 1000)
    # Keep model history byte-stable: UI parsing collapses blank lines, so use
    # the original protocol-safe reply for future prompts even without tools.
    msg["raw"] = raw.strip()
    if calls:
        msg["tool_calls"] = len(calls)
    ctx_mod.stamp(msg)
    usage = session["usage"]
    usage["input_tokens_est"] += ctx_mod.est_tokens(system) + sum(
        ctx_mod.est_tokens(m["content"]) for m in messages)
    usage["output_tokens_est"] += ctx_mod.est_tokens(raw)
    rt.emit("message.completed", public_message(msg))
    return msg, calls


# -- the turn ---------------------------------------------------------------------------------
async def run_turn(rt: Runtime, *, agent: dict[str, Any] | None = None,
                   max_rounds: int | None = None) -> dict[str, Any]:
    """Run one turn on ``rt`` (whose last message is the user's). Returns
    ``{stop_reason, rounds, message_id, cancelled}``; cleanup is done here but
    the caller decides whether to re-raise a cancellation."""
    session = rt.session
    meter = UsageMeter(session, rt)
    started = time.monotonic()
    info: dict[str, Any] = {"stop_reason": "completed", "rounds": 0, "message_id": None,
                            "cancelled": False}
    last_assistant: dict[str, Any] | None = None
    settings = settings_mod.load(_workspace(rt)[1])
    try:
        with providers.call_meter(meter), providers.usage_context("agent"):
            session["context"]["limit"] = int(settings.get("context_limit") or 120_000)
            registry = await harness_registry(settings)
            key, root = _workspace(rt)
            if sum(1 for m in session["messages"] if m.get("type") == "user") <= 1 and rt.parent is None:
                for res in await run_hooks(rt, "session_start", settings, {}):
                    out = (res.get("stdout") or "").strip()
                    if out:
                        add_note(rt, "hook", f"session_start hook output:\n{out[:4000]}")
            budget = int(max_rounds or settings.get("max_tool_rounds") or 25)
            compacted_at = -1
            while True:
                settings = settings_mod.load(_workspace(rt)[1])
                table = build_table(
                    registry, mode=session["mode"], has_project=bool(session.get("project_id")),
                    allowlist=None if agent is None or "*" in (agent.get("tools") or ["*"])
                    else list(agent.get("tools") or []),
                    subagent=rt.parent is not None, include_unavailable=True)
                rt.turn_tools = table
                system = ctx_mod.system_prompt(session, table, workspace_root=root, agent=agent)
                signature = hashlib.sha256(system.encode()).hexdigest()
                previous_signature = session.get("prompt_signature")
                if previous_signature and previous_signature != signature:
                    session.pop("provider_thread", None)
                    add_note(rt, "cache_reset", "Prompt cache reset: instructions or available tools changed.", reason="prompt_changed")
                session["prompt_signature"] = signature
                ctx_mod.recount(session, ctx_mod.est_tokens(system))
                if ctx_mod.needs_compaction(session) and compacted_at != info["rounds"]:
                    compacted_at = info["rounds"]
                    await ctx_mod.compact(rt, auto=True)
                rt.emit("context.updated", dict(session["context"]))
                last_assistant, calls = await stream_assistant(rt, system, meter)
                info["message_id"] = last_assistant["id"]
                ctx_mod.recount(session)
                rt.emit("context.updated", dict(session["context"]))
                rt.persist()
                if not calls:
                    break
                if info["rounds"] >= budget:
                    for item in calls:
                        call = {
                            "id": new_id("c_"), "type": "tool_call", "tool": item.name or "unknown",
                            "args": item.arguments, "status": "pending", "result_preview": None,
                            "result_ref": None, "created_at": now(), "started_at": None,
                            "finished_at": None, "session_id": rt.id,
                            "assistant_id": last_assistant["id"],
                            "parent_tool_call_id": rt.parent_call_id,
                        }
                        add_message(rt, call)
                        _finish_call(rt, call, "error",
                                     f"not run: the turn budget of {budget} tool rounds is used up")
                    add_note(rt, "budget", f"Stopped after {budget} tool rounds (turn budget). "
                             "Send a message to continue.")
                    info["stop_reason"] = "budget"
                    break
                await execute_calls(rt, table, last_assistant, calls, settings)
                info["rounds"] += 1
                ctx_mod.recount(session)
            if rt.parent is None:
                await run_hooks(rt, "stop", settings, {"stop_reason": info["stop_reason"],
                                                       "rounds": info["rounds"]})
    except asyncio.CancelledError:
        info["stop_reason"] = "interrupted"
        info["cancelled"] = True
        _abort_pending(rt)
        add_note(rt, "interrupt", "The user interrupted this turn.")
    except Exception as error:
        logger.exception("turn failed in session %s", rt.id)
        info["stop_reason"] = "error"
        info["error"] = err(error)
        _abort_pending(rt)
        add_note(rt, "error", f"The model call failed: {err(error)}")
        rt.emit("error", {"message": err(error), "session_id": rt.id,
                          "message_id": (last_assistant or {}).get("id")})
    ctx_mod.recount(session)
    meter.turn["duration_seconds"] = round(time.monotonic() - started, 3)
    info["usage"] = meter.turn
    return info


def _abort_pending(rt: Runtime) -> None:
    for msg in rt.session["messages"]:
        if msg.get("type") == "assistant" and msg.get("streaming"):
            msg["streaming"] = False
            msg["interrupted"] = True
            ctx_mod.stamp(msg)
            rt.emit("message.completed", public_message(msg))
        if msg.get("type") == "tool_call" and msg.get("status") not in TERMINAL_TOOL:
            _finish_call(rt, msg, "error", "interrupted before it finished")
    for entry in list(rt.approvals.values()):
        future = entry.get("future")
        if future is not None and not future.done():
            future.cancel()
    rt.approvals.clear()
    if rt.pending_input:
        future = rt.pending_input.get("future")
        if future is not None and not future.done():
            future.cancel()
        rt.pending_input = None


def finish_turn(rt: Runtime, info: dict[str, Any]) -> None:
    """Return the session to idle and publish ``turn.completed``."""
    rt.running = False
    if rt.parent is None:
        rt.task = None
    rt.refresh_status()
    if info.get("usage"):
        add_note(rt, "turn_usage", "", usage=info["usage"], turn_id=info.get("message_id"))
    rt.emit("context.updated", dict(rt.session["context"]))
    rt.emit("turn.completed", {
        "session_id": rt.id, "stop_reason": info.get("stop_reason"),
        "rounds": info.get("rounds", 0), "message_id": info.get("message_id"),
        "error": info.get("error"), "status": rt.session["status"], "commit": info.get("commit"),
        "usage": info.get("usage", empty_usage()), "context": dict(rt.session["context"]),
    })
    rt.persist()


async def drive_turn(rt: Runtime) -> dict[str, Any]:
    """Task body of a root session turn (never raises)."""
    info: dict[str, Any] = {"stop_reason": "error", "rounds": 0}
    try:
        from . import vcs
        await asyncio.to_thread(vcs.ensure_repo_quietly, _workspace(rt)[0])
        info = await run_turn(rt)
    except asyncio.CancelledError:  # cancelled before run_turn's own handler was armed
        info = {"stop_reason": "interrupted", "rounds": 0, "cancelled": True}
        _abort_pending(rt)
    except Exception as error:
        logger.exception("turn crashed in session %s", rt.id)
        info = {"stop_reason": "error", "rounds": 0, "error": err(error)}
        rt.emit("error", {"message": err(error), "session_id": rt.id, "message_id": None})
    finally:
        try:
            from . import vcs
            key, root = _workspace(rt)
            if settings_mod.load(root).get("vcs", {}).get("auto_commit", True):
                users = [m for m in rt.session["messages"] if m.get("type") == "user"]
                message = vcs.turn_message(rt.session["title"], (users[-1] if users else {}).get("text", ""), rt.id, len(users))
                committing = asyncio.create_task(asyncio.to_thread(vcs.auto_commit, key, message))
                # Cancelling the await cannot stop a git subprocess in its worker
                # thread. Keep the workspace busy until that worker finishes.
                while not committing.done():
                    try:
                        await asyncio.shield(committing)
                    except asyncio.CancelledError:
                        info.update(stop_reason="interrupted", cancelled=True)
                made = committing.result()
                if made:
                    info["commit"] = made
                    add_note(rt, "commit", f"Committed {made['short']}", commit=made)
            rt.session["branch"] = vcs.head_branch(root) or rt.session.get("branch")
        except Exception:
            logger.exception("auto-commit failed in session %s", rt.id)
        finish_turn(rt, info)
    return info


async def drive_compaction(rt: Runtime, focus: str = "") -> dict[str, Any]:
    """Task body of a manual compaction (``/compact`` or the compact endpoint)."""
    result: dict[str, Any] = {"compacted": False, "reason": "failed"}
    info = {"stop_reason": "compacted", "rounds": 0, "message_id": None}
    meter = UsageMeter(rt.session, rt)
    started = time.monotonic()
    try:
        with providers.call_meter(meter):
            result = await ctx_mod.compact(rt, focus=focus, auto=False)
        info["message_id"] = result.get("note_id")
        if not result.get("compacted"):
            info["stop_reason"] = "noop"
    except asyncio.CancelledError:
        info["stop_reason"] = "interrupted"
        result = {"compacted": False, "reason": "interrupted"}
    except Exception as error:
        logger.exception("compaction failed in session %s", rt.id)
        info.update(stop_reason="error", error=err(error))
        result = {"compacted": False, "reason": err(error)}
    finally:
        if meter.turn["llm_calls"]:
            meter.turn["duration_seconds"] = round(time.monotonic() - started, 3)
            info["usage"] = meter.turn
        finish_turn(rt, info)
    return result


# -- subagents --------------------------------------------------------------------------------
async def run_subagent(ctx: HarnessContext, *, description: str, prompt: str,
                       agent_type: str) -> ToolOutput:
    parent: Runtime = ctx.runtime
    if parent.parent is not None:
        raise ValueError("subagents cannot spawn subagents")
    agent = extensions.find_agent(agent_type, ctx.workspace_root)
    if agent is None:
        names = ", ".join(a["name"] for a in extensions.list_agents(ctx.workspace_root))
        raise ValueError(f"unknown subagent_type: {agent_type}. Available: {names}")
    psession = parent.session
    provider = (psession.get("model") or {}).get("provider")
    model = (psession.get("model") or {}).get("model")
    wanted = str(agent.get("model") or "").strip()
    if wanted and wanted != "inherit":
        head, sep, tail = wanted.partition(":")
        if head in providers.available():
            provider, model = head, (tail or None)
        elif not sep:
            model = wanted
    child_session = new_session(
        title=description, project_id=psession.get("project_id"), mode=psession["mode"],
        provider=provider, model=model, context_limit=int(psession["context"].get("limit") or 120_000),
        parent_id=parent.id, subagent_type=agent["name"])
    child_session["scope_papers"] = list(psession.get("scope_papers") or [])
    call = ctx.call or {}
    child = Runtime(child_session, parent.manager, parent=parent, parent_call_id=call.get("id"))
    parent.children.append(child)
    register = getattr(parent.manager, "register_child", None)
    if register is not None:
        register(child)
    if call:
        update_call(parent, call, subagent={"session_id": child.id, "type": agent["name"],
                                            "description": description, "status": "running"})
    user = {"id": new_id("m_"), "type": "user", "text": prompt, "attachments": [],
            "created_at": now()}
    ctx_mod.stamp(user)
    add_message(child, user)
    child.running = True
    child.refresh_status()
    info: dict[str, Any] = {"stop_reason": "error", "rounds": 0}
    try:
        info = await run_turn(child, agent=agent, max_rounds=int(agent.get("max_turns") or 15))
    finally:
        if child in parent.children:
            parent.children.remove(child)
        finish_turn(child, info)
        add_usage(psession["usage"], child_session["usage"])
        for key in ("input_tokens_est", "output_tokens_est", "llm_calls"):
            psession["usage"][key] += int(child_session["usage"].get(key) or 0)
        meter = providers.current_call_meter()
        if isinstance(meter, UsageMeter):
            add_usage(meter.turn, child_session["usage"])
            meter.turn["llm_calls"] += int(child_session["usage"].get("llm_calls") or 0)
        parent.emit("usage.updated", dict(psession["usage"]))
        for pid in child_session.get("scope_papers") or []:
            if pid not in psession["scope_papers"]:
                psession["scope_papers"].append(pid)
    if info.get("cancelled"):
        raise asyncio.CancelledError()
    answer = next((m.get("text") or "" for m in reversed(child_session["messages"])
                   if m.get("type") == "assistant" and (m.get("text") or "").strip()), "")
    tool_calls = sum(1 for m in child_session["messages"] if m.get("type") == "tool_call")
    meta = {"subagent": {"session_id": child.id, "type": agent["name"], "description": description,
                         "status": info.get("stop_reason"), "tool_calls": tool_calls,
                         "rounds": info.get("rounds", 0)}}
    if info.get("stop_reason") == "error":
        return ToolOutput(f"The {agent['name']} subagent failed: {info.get('error') or 'error'}"
                          + (f"\nPartial answer:\n{answer}" if answer else ""),
                          meta=meta, is_error=True)
    head = f"Subagent {agent['name']} finished ({tool_calls} tool call(s))."
    if info.get("stop_reason") == "budget":
        head = f"Subagent {agent['name']} stopped at its turn budget ({tool_calls} tool call(s))."
    return ToolOutput(f"{head}\n\n{answer or '(the subagent returned no text)'}", meta=meta)


__all__ = [
    "add_message", "add_note", "ask_user", "await_approval", "drive_compaction", "drive_turn",
    "execute_calls", "finish_turn", "nested_authorizer", "run_subagent", "run_turn", "set_mode",
    "update_call",
]
