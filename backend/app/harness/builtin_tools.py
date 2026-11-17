"""First-party harness tools, registered in the capability registry as ``harness.<tool>``.

Workspace tools are sandboxed to the session's project workspace; every write
records a checkpoint. Execution tools run inside the workspace with a timeout
and kill their whole process group on timeout or interrupt.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from types import SimpleNamespace
from typing import Any

from ..agent import builtin as kernel_builtins
from ..agent import projects
from .. import office
from ..agent.capabilities import Capability
from ..library import store
from ..pdf import structure
from . import execution, workspace
from .tools import HarnessContext, ToolOutput
from .workspace import WorkspaceError

MAX_GREP_FILE = 2_000_000
TODO_STATUSES = ("pending", "in_progress", "completed")


def _s(args: dict[str, Any], key: str, *, required: bool = True, limit: int = 100_000) -> str:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"'{key}' is required")
        return ""
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"'{key}' must be a string")
    text = str(value)
    if len(text) > limit:
        raise ValueError(f"'{key}' is too long (max {limit} characters)")
    return text


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _root(ctx: HarnessContext):
    if ctx.workspace_root is None:
        raise WorkspaceError("this session has no workspace")
    return ctx.workspace_root


def _session_id(ctx: HarnessContext) -> str | None:
    return ctx.runtime.id if ctx.runtime is not None else None


def _call_id(ctx: HarnessContext) -> str | None:
    return (ctx.call or {}).get("id")


# -- workspace files -------------------------------------------------------------
async def _fs_list(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    start = workspace.resolve(root, args.get("path") or ".")
    if not start.is_dir() or workspace.is_checkpoint_rel(workspace.relpath(root, start)):
        raise WorkspaceError(f"not a directory: {args.get('path')}")
    depth = _clamp(args.get("depth"), 2, 1, 8)
    entries, truncated = workspace.walk(root, start, max_entries=500, max_depth=depth - 1)
    lines = [e["path"] + "/" if e["type"] == "dir" else f"{e['path']} ({e['size']} B)"
             for e in entries]
    where = workspace.relpath(root, start)
    head = f"Listing of {where} ({len(entries)} entries{', truncated' if truncated else ''})"
    return ToolOutput("\n".join([head, *lines]) if lines else f"{head}\n(empty)",
                      meta={"path": where})


async def _fs_read(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    target = workspace.resolve(root, _s(args, "path"))
    text = await asyncio.to_thread(workspace.read_document, target)
    lines = text.splitlines(keepends=True)
    offset = _clamp(args.get("offset"), 1, 1, max(1, len(lines)))
    limit = _clamp(args.get("limit"), 2000, 1, 5000)
    chunk = lines[offset - 1: offset - 1 + limit]
    content = "".join(chunk)
    cut = len(content) > 100_000
    if cut:
        content = content[:100_000]
    rel = workspace.relpath(root, target)
    end = offset + len(chunk) - 1
    head = f"{rel} (lines {offset}-{end} of {len(lines)}{'; truncated' if cut or end < len(lines) else ''})"
    return ToolOutput(f"{head}\n{content}" if content else f"{head}\n(empty file)",
                      meta={"path": rel})


async def _fs_glob(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    pattern = _s(args, "pattern", limit=500).strip().replace("\\", "/")
    if pattern.startswith("/") or ".." in pattern.split("/"):
        raise WorkspaceError("glob patterns must be relative to the workspace")
    base = workspace.resolve(root, args.get("path") or ".")
    found = []
    for path in base.glob(pattern):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in workspace.SKIP_DIRS for part in rel_parts):
            continue
        if workspace.is_checkpoint_rel("/".join(rel_parts)):
            continue
        found.append(path)
        if len(found) >= 5000:
            break

    def mtime(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0

    found.sort(key=mtime, reverse=True)
    rels = [workspace.relpath(root, p) + ("/" if p.is_dir() else "") for p in found[:500]]
    head = f"{len(found)} match(es) for {pattern}" + (" (showing 500)" if len(found) > 500 else "")
    return ToolOutput("\n".join([head, *rels]))


async def _fs_grep(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    pattern = _s(args, "pattern", limit=1000)
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as error:
        raise ValueError(f"invalid regular expression: {error}") from None
    base = workspace.resolve(root, args.get("path") or ".")
    file_glob = _s(args, "glob", required=False, limit=200)
    max_results = _clamp(args.get("max_results"), 100, 1, 500)

    def scan() -> tuple[list[str], int]:
        hits: list[str] = []
        scanned = 0
        if base.is_file():
            files = [base]
        else:
            files = []
            for dirpath, dirs, names in os.walk(base):
                dirs[:] = sorted(
                    d for d in dirs if d not in workspace.SKIP_DIRS
                    and not workspace.is_checkpoint_rel(
                        workspace.relpath(root, type(base)(dirpath) / d)))
                for name in sorted(names):
                    files.append(type(base)(dirpath) / name)
        for path in files:
            rel = workspace.relpath(root, path)
            if file_glob and not (fnmatch.fnmatch(path.name, file_glob) or fnmatch.fnmatch(rel, file_glob)):
                continue
            if office.is_document(path):
                # converted documents are cached; old binary formats need LibreOffice, too slow here
                try:
                    if path.suffix.lower() in office.LEGACY or path.stat().st_size > 20 * 1024 * 1024:
                        continue
                    data = office.read(path)["markdown"].encode()
                except (office.DocumentError, OSError):
                    continue
            else:
                try:
                    if path.stat().st_size > MAX_GREP_FILE:
                        continue
                    data = path.read_bytes()
                except OSError:
                    continue
                if b"\0" in data[:8192]:
                    continue
            scanned += 1
            for no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{rel}:{no}: {line.strip()[:300]}")
                    if len(hits) >= max_results:
                        return hits, scanned
        return hits, scanned

    hits, scanned = await asyncio.to_thread(scan)
    head = f"{len(hits)} match(es) in {scanned} file(s)" + (" (limit reached)" if len(hits) >= max_results else "")
    return ToolOutput("\n".join([head, *hits]))


def _checkpoint_meta(cps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"checkpoints": [{"id": c["id"], "path": c["path"], "lines_added": c["lines_added"],
                             "lines_removed": c["lines_removed"], "existed_before": c["existed_before"]}
                            for c in cps]}


async def _fs_write(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    path = _s(args, "path", limit=1000)
    content = args.get("content")
    if not isinstance(content, str):
        raise ValueError("'content' must be a string")
    cp = workspace.write_text(ctx.workspace_key, root, path, content, source="fs_write",
                              session_id=_session_id(ctx), tool_call_id=_call_id(ctx))
    verb = "Updated" if cp["existed_before"] else "Created"
    return ToolOutput(
        f"{verb} {cp['path']} ({len(content.encode('utf-8'))} bytes, +{cp['lines_added']} "
        f"-{cp['lines_removed']} lines). Checkpoint {cp['id']}.",
        meta=_checkpoint_meta([cp]),
    )


async def _fs_edit(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    target = workspace.resolve(root, _s(args, "path", limit=1000))
    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not old:
        raise ValueError("'old_string' must be a non-empty string")
    if not isinstance(new, str):
        raise ValueError("'new_string' must be a string")
    if old == new:
        raise ValueError("old_string and new_string are identical")
    text = workspace.read_text(target)
    count = text.count(old)
    if count == 0:
        raise ValueError("old_string was not found in the file (it must match exactly, "
                         "including whitespace; read the file first)")
    replace_all = bool(args.get("replace_all"))
    if count > 1 and not replace_all:
        raise ValueError(f"old_string occurs {count} times; include more context to make it "
                         "unique or set replace_all to true")
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    cp = workspace.write_text(ctx.workspace_key, root, workspace.relpath(root, target), updated,
                              source="fs_edit", session_id=_session_id(ctx),
                              tool_call_id=_call_id(ctx))
    idx = updated.find(new) if new else -1
    snippet = ""
    if idx >= 0:
        start = updated.rfind("\n", 0, max(0, idx - 200)) + 1
        end = updated.find("\n", min(len(updated), idx + len(new) + 200))
        snippet = updated[start: end if end >= 0 else len(updated)][:1500]
    msg = (f"Edited {cp['path']}: replaced {count if replace_all else 1} occurrence(s) "
           f"(+{cp['lines_added']} -{cp['lines_removed']} lines). Checkpoint {cp['id']}.")
    if snippet:
        msg += f"\nUpdated region:\n{snippet}"
    return ToolOutput(msg, meta=_checkpoint_meta([cp]))


# -- execution --------------------------------------------------------------------
async def _run_in_workspace(ctx: HarnessContext, argv: list[str], *, cwd, timeout: float,
                            header: str, source: str) -> ToolOutput:
    root = _root(ctx)
    before = await asyncio.to_thread(workspace.snapshot, root)
    env = os.environ.copy()
    env["GLOSSX_WORKSPACE"] = str(root)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        result = await execution.run_process(argv, cwd=cwd, timeout=timeout, env=env)
    except asyncio.CancelledError:
        workspace.record_changes(ctx.workspace_key, root, before, source=source,
                                 session_id=_session_id(ctx), tool_call_id=_call_id(ctx))
        raise
    cps, untracked = await asyncio.to_thread(
        workspace.record_changes, ctx.workspace_key, root, before, source=source,
        session_id=_session_id(ctx), tool_call_id=_call_id(ctx))
    text = execution.format_run(result, header)
    if cps:
        text += "\nFiles changed (checkpointed): " + ", ".join(c["path"] for c in cps)
    if untracked:
        text += "\nFiles changed without checkpoint (binary/large): " + ", ".join(untracked[:20])
    failed = result["timed_out"] or result["exit_code"] != 0
    meta = {"exit_code": result["exit_code"], "timed_out": result["timed_out"],
            "duration_s": result["duration_s"], **_checkpoint_meta(cps)}
    return ToolOutput(text, meta=meta, is_error=failed)


async def _shell_run(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    command = _s(args, "command", limit=20_000).strip()
    timeout = _clamp(args.get("timeout"), 120, 1, 600)
    cwd = workspace.resolve(root, args.get("cwd") or ".")
    if not cwd.is_dir():
        raise WorkspaceError("cwd must be a directory inside the workspace")
    return await _run_in_workspace(ctx, execution.shell_argv(command), cwd=cwd, timeout=timeout,
                                   header=f"$ {command}", source="shell_run")


async def _python_run(args: dict[str, Any], ctx: HarnessContext) -> Any:
    root = _root(ctx)
    target = workspace.resolve(root, _s(args, "path", limit=1000))
    if not target.is_file() or target.suffix.lower() != ".py":
        raise WorkspaceError("path must be an existing .py file in the workspace")
    extra = args.get("args") or []
    if not isinstance(extra, list) or len(extra) > 64:
        raise ValueError("'args' must be a list of at most 64 strings")
    extra = [str(a) for a in extra]
    timeout = _clamp(args.get("timeout"), 120, 1, 600)
    argv = [execution.python_interpreter(), str(target), *extra]
    rel = workspace.relpath(root, target)
    return await _run_in_workspace(ctx, argv, cwd=target.parent, timeout=timeout,
                                   header=f"$ python {rel} {' '.join(extra)}".rstrip(),
                                   source="python_run")


# -- web ----------------------------------------------------------------------------
async def _web_search(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .web import web_search

    query = _s(args, "query", limit=500)
    source = str(args.get("source") or "all")
    if source not in {"all", "papers", "web"}:
        raise ValueError("source must be all, papers or web")
    return await web_search(query, source=source, k=_clamp(args.get("k"), 8, 1, 10))


async def _web_fetch(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .web import web_fetch

    out = await web_fetch(_s(args, "url", limit=4000),
                          max_chars=_clamp(args.get("max_chars"), 20_000, 500, 100_000),
                          allow_private=bool(ctx.settings.get("allow_private_network")))
    head = [f"URL: {out.get('final_url') or out.get('url')}", f"HTTP {out.get('status')}"
            f" {out.get('content_type') or ''}".rstrip()]
    if out.get("title"):
        head.append(f"Title: {out['title']}")
    if out.get("note"):
        head.append(f"Note: {out['note']}")
    if out.get("truncated"):
        head.append(f"(truncated to {len(out['text'])} of {out.get('total_chars')} characters)")
    return ToolOutput("\n".join(head) + "\n\n" + (out.get("text") or ""),
                      meta={"url": out.get("final_url") or out.get("url")})


# -- agent utilities ----------------------------------------------------------------
async def _todo_write(args: dict[str, Any], ctx: HarnessContext) -> Any:
    raw = args.get("todos")
    if not isinstance(raw, list) or len(raw) > 50:
        raise ValueError("'todos' must be a list of at most 50 items")
    todos = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            item = {"content": item}
        if not isinstance(item, dict):
            raise ValueError("each todo must be an object {content, status}")
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            raise ValueError("each todo needs content")
        status = str(item.get("status") or "pending").strip()
        if status not in TODO_STATUSES:
            raise ValueError(f"todo status must be one of: {', '.join(TODO_STATUSES)}")
        todos.append({
            "id": str(item.get("id") or f"t{i + 1}")[:40],
            "content": content[:500],
            "status": status,
            "active_form": str(item.get("activeForm") or item.get("active_form") or "")[:300] or None,
        })
    rt = ctx.runtime
    rt.session["todos"] = todos
    rt.emit("todos.updated", {"todos": [dict(t) for t in todos]})
    rt.persist()
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    lines = [f"{marks[t['status']]} {t['content']}" for t in todos]
    return ToolOutput("Todo list updated:\n" + ("\n".join(lines) or "(empty)"), meta={"todos": len(todos)})


async def _ask_user(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .engine import ask_user

    question = _s(args, "question", limit=4000).strip()
    choices = args.get("choices") or []
    if not isinstance(choices, list) or len(choices) > 12:
        raise ValueError("'choices' must be a list of at most 12 strings")
    choices = [str(c)[:300] for c in choices if str(c).strip()]
    answer = await ask_user(ctx, question, choices)
    return ToolOutput(f"The user answered: {answer}", meta={"answer": answer}, trusted=True)


async def _task(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .engine import run_subagent

    prompt = _s(args, "prompt", limit=40_000)
    description = _s(args, "description", required=False, limit=200) or prompt[:60]
    agent_type = _s(args, "subagent_type", required=False, limit=64) or "general"
    return await run_subagent(ctx, description=description, prompt=prompt, agent_type=agent_type)


def _project(ctx: HarnessContext) -> str:
    if not ctx.project_id or not projects.exists(ctx.project_id):
        raise ValueError("this session does not belong to a project")
    return ctx.project_id


async def _memory_recall(args: dict[str, Any], ctx: HarnessContext) -> Any:
    project_id = _project(ctx)
    query = _s(args, "query", limit=2000)
    notes = projects.recall(project_id, query, k=_clamp(args.get("k"), 5, 1, 20))
    return {"query": query, "notes": [
        {"id": n["id"], "text": n["text"], "span_ids": n.get("span_ids") or []} for n in notes]}


async def _memory_note(args: dict[str, Any], ctx: HarnessContext) -> Any:
    project_id = _project(ctx)
    text = _s(args, "text", limit=4000)
    span_ids = args.get("span_ids") if isinstance(args.get("span_ids"), list) else None
    note = projects.add_memory(project_id, text, source_run=f"session:{_session_id(ctx)}",
                               span_ids=span_ids)
    return {"status": "saved", "id": note["id"]}


async def _result_read(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .session import read_result

    ref = _s(args, "ref", limit=64).strip()
    offset = _clamp(args.get("offset"), 0, 0, 100_000_000)
    limit = _clamp(args.get("limit"), 8000, 200, 30_000)
    rt = ctx.runtime
    text = None
    while rt is not None and text is None:
        text = read_result(rt.id, ref)
        rt = rt.parent
    if text is None:
        raise ValueError(f"unknown result ref: {ref}")
    part = text[offset: offset + limit]
    more = offset + limit < len(text)
    tail = (f"\n[... {len(text) - offset - limit} more characters; continue with offset "
            f"{offset + limit}]") if more else ""
    return ToolOutput(f"{ref} characters {offset}-{offset + len(part)} of {len(text)}:\n{part}{tail}")


async def _plan_submit(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .engine import set_mode

    plan = _s(args, "plan", limit=40_000)
    set_mode(ctx.runtime, "agent", note="Plan accepted: switched to agent mode.")
    return ToolOutput(
        "The user approved the plan. The session is now in agent mode: carry out the plan "
        "step by step (track it with todo_write).", meta={"plan_chars": len(plan)})


# -- research ---------------------------------------------------------------------
async def _run_workflow(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from .engine import nested_authorizer, update_call

    rt = ctx.runtime
    runs = rt.manager.agent_runs
    if runs is None:
        raise RuntimeError("the workflow engine is not available in this process")
    workflow = _s(args, "workflow", required=False, limit=120).strip() or "literature-review"
    workflow = workflow.removeprefix("workflow.")
    question = _s(args, "question", limit=4000).strip()
    paper_ids = args.get("paper_ids")
    if paper_ids is None:
        paper_ids = list(rt.session.get("scope_papers") or [])
    if not isinstance(paper_ids, list):
        raise ValueError("'paper_ids' must be a list")
    snap = runs.start(
        question, [str(p) for p in paper_ids][:50],
        max_rounds=_clamp(args.get("max_rounds"), 2, 1, 3),
        provider=ctx.provider, project_id=ctx.project_id, workflow=f"workflow.{workflow}",
        max_rounds_explicit="max_rounds" in args, authorizer=nested_authorizer(ctx),
    )
    run_id = snap["id"]
    if ctx.call is not None:
        update_call(rt, ctx.call, run_id=run_id)
    try:
        final = await runs.wait(run_id)
    except asyncio.CancelledError:
        await runs.cancel(run_id)
        raise
    result = final.get("result") or {}
    lines = [f"Workflow {workflow}: run {run_id} {final.get('status')} "
             f"(stop_reason={result.get('stop_reason')}, rounds={result.get('rounds')})."]
    if final.get("error"):
        lines.append(f"Error: {final['error']}")
    if result.get("answer"):
        lines.append("\n## Report\n" + result["answer"])
    claims = result.get("claims") or []
    if claims:
        lines.append("\n## Claims")
        for c in claims[:20]:
            ids = " ".join(f"[S:{s}]" for s in c.get("span_ids") or [])
            lines.append(f"- [{c.get('verdict', '?')}] {c.get('text', '')} {ids}".rstrip())
    cites = result.get("citations") or []
    if cites:
        lines.append("\n## Citations")
        for s in cites[:30]:
            page = s["page"] + 1 if isinstance(s.get("page"), int) else "?"
            lines.append(f"- [S:{s['id']}] {s.get('title') or s.get('paper_id')} (p.{page})")
    return ToolOutput("\n".join(lines), meta={"run_id": run_id},
                      is_error=final.get("status") != "completed")


async def _verify_claims(args: dict[str, Any], ctx: HarnessContext) -> Any:
    from ..agent.core import verify

    raw = args.get("claims")
    if not isinstance(raw, list) or not raw or len(raw) > 12:
        raise ValueError("'claims' must be a list of 1-12 {text, span_ids} objects")
    claims = []
    for c in raw:
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            raise ValueError("each claim needs text")
        ids = c.get("span_ids") if isinstance(c.get("span_ids"), list) else []
        claims.append({"text": str(c["text"]).strip()[:1000],
                       "span_ids": [str(s).removeprefix("S:") for s in ids][:12]})
    shim = SimpleNamespace(spans=ctx.spans, emit=lambda ev: None, provider=ctx.provider)
    verdicts = await verify(shim, {"answer": "", "claims": claims})
    return {"claims": [{k: c.get(k) for k in ("text", "span_ids", "verdict", "confidence", "note")}
                       for c in verdicts]}


def _page_text(page: dict) -> str:
    return "\n".join(b.get("text", "") for b in page.get("blocks") or [] if b.get("text"))


def _parse_pages(value: Any, n_pages: int) -> tuple[int, int]:
    if isinstance(value, int):
        lo = hi = value
    elif isinstance(value, list) and value:
        lo, hi = int(value[0]), int(value[-1])
    else:
        text = str(value).strip()
        m = re.fullmatch(r"(\d+)\s*(?:-|to|\.\.)\s*(\d+)", text) or re.fullmatch(r"(\d+)", text)
        if not m:
            raise ValueError("pages must look like 3 or 3-5 (1-based)")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.lastindex and m.lastindex > 1 else lo
    lo, hi = max(1, min(lo, hi)), max(lo, hi)
    if lo > n_pages:
        raise ValueError(f"the paper has {n_pages} pages")
    return lo, min(hi, n_pages, lo + 9)


async def _paper_read(args: dict[str, Any], ctx: HarnessContext) -> Any:
    section = _s(args, "section", required=False, limit=300).strip()
    pages = args.get("pages")
    if not section and pages in (None, "", []):
        out = await kernel_builtins._paper_read(args, ctx)
        out["hint"] = "pass section (title) or pages (e.g. '3-5') to read more"
        return out
    paper_id = kernel_builtins._paper_arg(args, ctx)
    parsed = store.load_parsed(paper_id) or {}
    page_list = parsed.get("pages") or []
    if not page_list:
        raise ValueError("this paper has no parsed text")
    max_chars = _clamp(args.get("max_chars"), 8000, 500, 30_000)
    paper = store.get_paper(paper_id) or {}
    if pages not in (None, "", []):
        lo, hi = _parse_pages(pages, len(page_list))
        text = "\n\n".join(f"--- page {i} ---\n{_page_text(page_list[i - 1])}" for i in range(lo, hi + 1))
        return {"paper_id": paper_id, "title": paper.get("title", ""), "pages": f"{lo}-{hi}",
                "text": text[:max_chars], "truncated": len(text) > max_chars}
    sections = parsed.get("sections")
    if not sections:
        try:
            sections = structure.detect_sections(parsed)
        except Exception:
            sections = []
    want = section.lower()
    idx = next((i for i, s in enumerate(sections) if s.get("title", "").lower() == want), None)
    if idx is None:
        idx = next((i for i, s in enumerate(sections)
                    if want in s.get("title", "").lower() or s.get("title", "").lower() in want), None)
    if idx is None:
        return {"paper_id": paper_id, "error": f"section not found: {section}",
                "available_sections": [s.get("title") for s in sections[:60]]}
    sec = sections[idx]
    start = int(sec.get("page") or 0)
    nxt = sections[idx + 1] if idx + 1 < len(sections) else None
    end = int(nxt.get("page") or start) if nxt else min(len(page_list) - 1, start + 3)
    end = max(start, min(end, start + 6, len(page_list) - 1))
    text = "\n\n".join(_page_text(page_list[i]) for i in range(start, end + 1))
    pos = text.find(sec.get("title", ""))
    if pos > 0:
        text = text[pos:]
    if nxt and nxt.get("title"):
        cut = text.find(nxt["title"], len(sec.get("title", "")))
        if cut > 0:
            text = text[:cut]
    return {"paper_id": paper_id, "title": paper.get("title", ""), "section": sec.get("title"),
            "pages": f"{start + 1}-{end + 1}", "text": text[:max_chars],
            "truncated": len(text) > max_chars}


# -- version control -----------------------------------------------------------------
def _vcs_handler(operation: str):
    async def run(args: dict[str, Any], ctx: HarnessContext) -> Any:
        from . import vcs
        if ctx.runtime is not None and (operation in {"checkout", "merge"} or (operation == "branch" and args.get("checkout"))):
            manager = ctx.runtime.manager
            busy = [r for r in manager.busy_sessions(ctx.project_id) if r["id"] != ctx.runtime.root.id]
            if busy:
                raise vcs.VcsError("Another thread is working in this workspace; wait before changing its tree", "busy")
        key = ctx.workspace_key
        if operation == "status":
            return await asyncio.to_thread(vcs.status, key)
        if operation == "log":
            return await asyncio.to_thread(vcs.log, key, branch=args.get("branch"), limit=_clamp(args.get("limit"), 50, 1, 500), path=args.get("path"))
        if operation == "diff":
            return await asyncio.to_thread(vcs.diff, key, from_rev=args.get("from"), to_rev=args.get("to"), path=args.get("path"))
        if operation == "commit":
            return await asyncio.to_thread(vcs.commit, key, _s(args, "message", limit=5000), args.get("paths"))
        if operation == "branch":
            result = await asyncio.to_thread(vcs.create_branch, key, _s(args, "name"), args.get("from"), checkout=bool(args.get("checkout")))
        elif operation == "checkout":
            result = await asyncio.to_thread(vcs.checkout, key, _s(args, "branch"), stash=bool(args.get("stash")), discard=bool(args.get("discard")))
        else:
            result = await asyncio.to_thread(vcs.merge, key, _s(args, "branch"))
        if ctx.runtime:
            ctx.runtime.session["branch"] = vcs.head_branch(ctx.workspace_root)
            ctx.runtime.manager.workspace_changed(ctx.project_id)
        return result
    return run


# -- registration ---------------------------------------------------------------------
def _p(kind: str, desc: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": desc, **extra}


def _cap(name: str, description: str, handler, category: str, props: dict[str, Any],
         required: list[str], *, permissions: list[str] | None = None, kind: str = "tool",
         cost: str = "free", **meta: Any) -> Capability:
    parameters = {"type": "object", "properties": props, "required": required}
    input_schema = {
        key: f"{spec.get('type', 'any')}{'' if key in required else ' (optional)'}: {spec.get('description', '')}"
        for key, spec in props.items()
    }
    return Capability(
        name=f"harness.{name}", kind=kind, card=description, input_schema=input_schema,
        handler=handler, permissions=permissions or [], cost=cost, source="harness",
        trust="first-party", timeout=float(meta.get("timeout") or 180.0),
        meta={"harness": True, "tool": name, "parameters": parameters, "category": category, **meta},
    )


def harness_capabilities() -> list[Capability]:
    path = _p("string", "path relative to the workspace root")
    vcs_caps = []
    schemas = {
        "status": ({}, []),
        "log": ({"branch": _p("string", "branch or '*' for all branches"), "limit": _p("integer", "max commits"), "path": path}, []),
        "diff": ({"from": _p("string", "base revision"), "to": _p("string", "target revision, omitted for working tree"), "path": path}, []),
        "commit": ({"message": _p("string", "commit message"), "paths": _p("array", "paths to commit", items={"type": "string"})}, ["message"]),
        "branch": ({"name": _p("string", "new branch name"), "from": _p("string", "base revision"), "checkout": _p("boolean", "also switch to new branch")}, ["name"]),
        "checkout": ({"branch": _p("string", "existing branch"), "stash": _p("boolean", "stash changes before switching"), "discard": _p("boolean", "discard changes before switching")}, ["branch"]),
        "merge": ({"branch": _p("string", "branch to merge into current")}, ["branch"]),
    }
    for name, (props, required) in schemas.items():
        read = name in {"status", "log", "diff"}
        vcs_caps.append(_cap("vcs_" + name, "Workspace git: " + name + ".", _vcs_handler(name), "read" if read else "exec", props, required,
                             permissions=["workspace:read" if read else "code:execute"], always_ask=not read, parallel_safe=read))
    from .browser.tools import capabilities as browser_capabilities
    return vcs_caps + browser_capabilities() + [
        _cap("paper_read", "Read a library paper: metadata/outline/opening text, or a section by title, or pages (e.g. '3-5').",
             _paper_read, "read",
             {"paper_id": _p("string", "library paper id"),
              "section": _p("string", "section title to read"),
              "pages": _p("string", "1-based page or range, e.g. '3' or '3-5'"),
              "max_chars": _p("integer", "max characters (default 8000)")},
             [], permissions=["library:read"]),
        _cap("fs_list", "List files and folders in the project workspace.", _fs_list, "read",
             {"path": _p("string", "directory (default '.')"), "depth": _p("integer", "levels (default 2)")},
             [], permissions=["workspace:read"]),
        _cap("fs_read", "Read a workspace file (optionally a line range). Text files as is; Word, PowerPoint, Excel, OpenDocument, PDF and GoodNotes files as Markdown (headings, slides with notes, sheets as tables, pages).", _fs_read, "read",
             {"path": path, "offset": _p("integer", "first line, 1-based"),
              "limit": _p("integer", "number of lines (default 2000)")},
             ["path"], permissions=["workspace:read"], primary_arg="path"),
        _cap("fs_glob", "Find workspace files by glob pattern, e.g. '**/*.md' (newest first).", _fs_glob, "read",
             {"pattern": _p("string", "glob pattern"), "path": _p("string", "base directory")},
             ["pattern"], permissions=["workspace:read"]),
        _cap("fs_grep", "Search workspace file contents with a regular expression; returns path:line matches.",
             _fs_grep, "read",
             {"pattern": _p("string", "regular expression"), "path": _p("string", "file or directory"),
              "glob": _p("string", "file name filter, e.g. '*.py'"),
              "ignore_case": _p("boolean", "case-insensitive"),
              "max_results": _p("integer", "default 100")},
             ["pattern"], permissions=["workspace:read"]),
        _cap("fs_write", "Create or overwrite a text file in the workspace (checkpointed).", _fs_write, "write",
             {"path": path, "content": _p("string", "full new file content")},
             ["path", "content"], permissions=["workspace:write"], primary_arg="path",
             workspace_write=True, parallel_safe=False),
        _cap("fs_edit", "Edit a workspace file by exact string replacement (old_string must be unique unless replace_all).",
             _fs_edit, "write",
             {"path": path, "old_string": _p("string", "exact text to replace"),
              "new_string": _p("string", "replacement text"),
              "replace_all": _p("boolean", "replace every occurrence")},
             ["path", "old_string", "new_string"], permissions=["workspace:write"], primary_arg="path",
             workspace_write=True, parallel_safe=False),
        _cap("shell_run", "Run a shell command in the workspace directory (timeout, default 120 s).", _shell_run, "exec",
             {"command": _p("string", "shell command"), "timeout": _p("integer", "seconds, max 600"),
              "cwd": _p("string", "subdirectory to run in")},
             ["command"], permissions=["code:execute"], primary_arg="command", always_ask=True,
             parallel_safe=False, timeout=660.0),
        _cap("python_run", "Run a Python script from the workspace with the backend's Python.", _python_run, "exec",
             {"path": _p("string", "workspace .py file"), "args": _p("array", "command-line arguments", items={"type": "string"}),
              "timeout": _p("integer", "seconds, max 600")},
             ["path"], permissions=["code:execute"], primary_arg="path", always_ask=True,
             parallel_safe=False, timeout=660.0),
        _cap("web_search", "Search papers (Semantic Scholar/arXiv) and the open web (DuckDuckGo).", _web_search, "read",
             {"query": _p("string", "search query"),
              "source": _p("string", "all | papers | web", enum=["all", "papers", "web"]),
              "k": _p("integer", "results per source (max 10)")},
             ["query"], permissions=["network"], cost="network"),
        _cap("web_fetch", "Fetch a web page or text URL and return its readable text (size-limited).", _web_fetch, "read",
             {"url": _p("string", "http(s) URL"), "max_chars": _p("integer", "default 20000")},
             ["url"], permissions=["network"], cost="network", primary_arg="url"),
        _cap("todo_write", "Replace the session todo list shown to the user (track multi-step work).", _todo_write, "meta",
             {"todos": _p("array", "[{content, status: pending|in_progress|completed, activeForm?}]",
                          items={"type": "object"})},
             ["todos"], parallel_safe=False),
        _cap("ask_user", "Ask the user a question (optionally with choices) and wait for the answer.", _ask_user, "meta",
             {"question": _p("string", "the question"),
              "choices": _p("array", "suggested answers", items={"type": "string"})},
             ["question"], parallel_safe=False, timeout=None),
        _cap("task", "Delegate a self-contained task to a subagent with its own context; returns its final answer.",
             _task, "agent",
             {"description": _p("string", "3-6 word label"), "prompt": _p("string", "complete instructions"),
              "subagent_type": _p("string", "general | reader | reviewer | coder | searcher | custom type")},
             ["prompt"], kind="agent", cost="llm", timeout=None),
        _cap("memory_recall", "Recall notes and verified findings saved in this project's memory.", _memory_recall, "read",
             {"query": _p("string", "what to recall"), "k": _p("integer", "default 5")},
             ["query"], permissions=["memory:read"], kind="memory"),
        _cap("memory_note", "Save a short note, decision or finding to this project's memory.", _memory_note, "write",
             {"text": _p("string", "the note"), "span_ids": _p("array", "supporting span ids", items={"type": "string"})},
             ["text"], permissions=["memory:write"], kind="memory", parallel_safe=False),
        _cap("result_read", "Read more of a large stored tool result by its ref id.", _result_read, "read",
             {"ref": _p("string", "result ref, e.g. r_abc123"), "offset": _p("integer", "start character"),
              "limit": _p("integer", "characters (default 8000)")},
             ["ref"]),
        _cap("run_workflow", "Run a GlossX workflow (default literature-review) on a question; returns the cited, verified report.",
             _run_workflow, "agent",
             {"question": _p("string", "the research question"),
              "workflow": _p("string", "workflow id (default literature-review)"),
              "paper_ids": _p("array", "scope papers", items={"type": "string"}),
              "max_rounds": _p("integer", "1-3")},
             ["question"], kind="agent", cost="llm", timeout=None),
        _cap("verify_claims", "Check claims against their cited evidence spans; returns supported/partial/unsupported verdicts.",
             _verify_claims, "read",
             {"claims": _p("array", "[{text, span_ids: [paper_id#n]}]", items={"type": "object"})},
             ["claims"], permissions=["library:read", "llm"], kind="agent", cost="llm"),
        _cap("plan_submit", "Plan mode only: submit the final plan for the user to accept (switches to agent mode).",
             _plan_submit, "plan", {"plan": _p("string", "the plan in Markdown")}, ["plan"],
             parallel_safe=False),
    ]
