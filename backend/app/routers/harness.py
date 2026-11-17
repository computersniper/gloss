"""GlossX harness API: tools, commands, subagent types, settings, mentions and
project workspaces with checkpoints (docs/glossx-harness.md section 7)."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .. import office
from ..agent import connections as conn_mod
from ..agent import projects
from ..harness import extensions, home, vcs, workspace
from ..harness import settings as settings_mod
from ..harness.tools import build_table, harness_registry
from ..library import store

router = APIRouter(prefix="/api/harness", tags=["harness"])
workspace_router = APIRouter(prefix="/api/workspace", tags=["workspace"])
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp"}


# -- helpers ----------------------------------------------------------------------------
def _project(project_id: str | None) -> str | None:
    """``default`` / empty -> the default workspace; else an existing project id."""
    if project_id in (None, "", "default"):
        return None
    if not projects.exists(project_id):
        raise HTTPException(404, "project not found")
    return project_id


def _root(project_id: str | None):
    try:
        return workspace.root_for(_project(project_id))
    except workspace.WorkspaceBusyError as error:
        raise HTTPException(409, str(error)) from error
    except workspace.WorkspaceError as error:
        raise HTTPException(400, str(error)) from error


class WorkspaceLocationBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    confirm: bool = False


@workspace_router.post("/{project_id}/location")
async def relocate_workspace(project_id: str, body: WorkspaceLocationBody, request: Request):
    from ..harness import locations

    project = _project(project_id)
    if project is None:
        raise HTTPException(400, "Choose a project workspace to relocate")
    if not body.confirm:
        raise HTTPException(400, "Explicit confirmation is required; the old directory will be preserved")
    blocked = _refuse_while_running(request, project_id, "relocate the workspace")
    if blocked is not None:
        return blocked
    terminals = getattr(request.app.state, "terminals", None)
    if terminals is not None:
        # Detached shells keep their scrollback and reopen in the new location.
        await terminals.close_detached(project)
    try:
        destination = locations.normalize_path(body.path)
        source = _root(project_id)
        workspace.begin_relocation(project)
    except workspace.WorkspaceBusyError as error:
        return JSONResponse({"detail": str(error), "code": "busy"}, status_code=409)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    task = asyncio.create_task(asyncio.to_thread(locations.relocate, project, source, destination))
    try:
        # A disconnected client cannot stop a filesystem worker: keep the
        # workspace reserved until it has actually finished, just like git.
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
    finally:
        workspace.end_relocation(project)
        _sync_sessions(request, project_id)
    return result


def _session_scope(request: Request, session_id: str | None, project_id: str | None,
                   mode: str | None) -> tuple[str | None, str | None]:
    """Resolve ``(project_id, mode)`` from an optional session."""
    if session_id:
        manager = getattr(request.app.state, "sessions", None)
        try:
            session = manager.get(session_id) if manager else None
        except LookupError as error:
            raise HTTPException(404, "session not found") from error
        if session:
            return session.get("project_id"), mode or session.get("mode")
    return _project(project_id), mode


# -- tools / commands / agents ------------------------------------------------------------
@router.get("/tools")
async def list_tools(request: Request, mode: Literal["ask", "agent", "auto", "plan"] | None = None,
                     project_id: str | None = None, session_id: str | None = None):
    """Every harness tool with its permission state in ``mode`` (default: the
    session's mode, else ``default_mode``)."""
    project, mode = _session_scope(request, session_id, project_id, mode)
    settings = settings_mod.load(workspace.root_for(project))
    mode = mode or settings["default_mode"]
    registry = await harness_registry(settings)
    table = build_table(registry, mode=mode, has_project=bool(project), include_unavailable=True)
    auto = conn_mod.auto_approve()
    return {"mode": mode, "tools": [spec.describe(mode, settings, auto) for spec in table.values()]}


@router.get("/commands")
async def list_commands(request: Request, project_id: str | None = None,
                        session_id: str | None = None):
    project, _ = _session_scope(request, session_id, project_id, None)
    root = workspace.root_for(project)
    return [extensions.public_command(c) for c in extensions.list_commands(root)]


@router.get("/agents")
async def list_agents(request: Request, project_id: str | None = None,
                      session_id: str | None = None):
    project, _ = _session_scope(request, session_id, project_id, None)
    root = workspace.root_for(project)
    return [extensions.public_agent(a) for a in extensions.list_agents(root)]


# -- settings -----------------------------------------------------------------------------
def _settings_view(project_id: str | None) -> dict[str, Any]:
    """Global settings plus the read-only ``paths`` (and, for a project, the
    ``effective`` settings after its ``.glossx/settings.json`` is merged in)."""
    project = _project(project_id)
    root = workspace.root_for(project)
    out = settings_mod.load_global()
    out["paths"] = home.paths(root)
    if project_id not in (None, ""):
        out["project_id"] = project or "default"
        out["project_settings"] = home.read_workspace_settings(root)
        out["effective"] = settings_mod.load(root)
    return out


@router.get("/settings")
async def get_settings(project_id: str | None = None):
    """The user's settings (``~/.glossx/settings.json``). ``paths`` is read-only:
    ``{home, global_instructions, workspace_config, ...}``; ``workspace_config``
    is the ``.glossx`` folder of ``project_id`` (default: the default workspace)."""
    return _settings_view(project_id)


@router.put("/settings")
async def put_settings(body: dict[str, Any], project_id: str | None = None):
    """Partial update of the global settings: keys that are present replace the
    stored value. Read-only fields of the GET response are ignored."""
    try:
        body = {k: v for k, v in body.items()
                if k not in {"paths", "effective", "project_settings", "project_id"}}
        unknown = set(body) - set(settings_mod.DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"unknown settings field: {', '.join(sorted(unknown))}")
        settings_mod.save(body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _settings_view(project_id)


# -- mentions -----------------------------------------------------------------------------
@router.get("/mentions")
async def mentions(request: Request, q: str = "", project_id: str | None = None,
                   session_id: str | None = None, limit: int = 8):
    """Papers, workspace files and runs matching ``q`` (for the ``@`` picker)."""
    if len(q) > 200:
        raise HTTPException(400, "q is too long")
    limit = max(1, min(30, limit))
    project, _ = _session_scope(request, session_id, project_id, None)
    needle = q.strip().lower()
    papers = []
    for paper in store.list_papers():
        hay = " ".join([str(paper.get("title") or ""), " ".join(paper.get("authors") or []),
                        str(paper.get("id") or "")]).lower()
        if needle and needle not in hay:
            continue
        authors = list(paper.get("authors") or [])
        detail = ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else "")
        if paper.get("year"):
            detail = f"{detail} · {paper['year']}".strip(" ·")
        papers.append({"kind": "paper", "ref": paper["id"], "label": paper.get("title") or paper["id"],
                       "detail": detail, "insert": f"@paper:{paper['id']}"})
        if len(papers) >= limit:
            break
    root = workspace.root_for(project)
    entries, _ = await asyncio.to_thread(workspace.walk, root, None, max_entries=4000,
                                         include_hidden=False)
    files = []
    for entry in entries:
        if entry["type"] != "file" or (needle and needle not in entry["path"].lower()):
            continue
        files.append({"kind": "file", "ref": entry["path"], "label": entry["path"].rsplit("/", 1)[-1],
                      "detail": entry["path"], "insert": f"@file:{entry['path']}",
                      "mtime": entry["mtime"]})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    files = files[:limit]
    runs = []
    agent_runs = getattr(request.app.state, "agent_runs", None)
    for run in (agent_runs.list() if agent_runs else []):
        if project and run.get("project_id") != project:
            continue
        if needle and needle not in str(run.get("goal") or "").lower() and needle not in run["id"]:
            continue
        runs.append({"kind": "run", "ref": run["id"], "label": str(run.get("goal") or run["id"])[:120],
                     "detail": str(run.get("status") or ""), "insert": f"@run:{run['id']}"})
        if len(runs) >= limit:
            break
    return {"query": q, "papers": papers, "files": files, "runs": runs,
            "items": papers + files + runs}


# -- workspace ----------------------------------------------------------------------------
class FileBody(BaseModel):
    content: str = Field(max_length=4_000_000)


def _ws_error(error: Exception) -> HTTPException:
    text = str(error)
    if "not a file" in text or "not a directory" in text:
        return HTTPException(404, text)
    if "too large" in text or "larger than" in text:
        return HTTPException(413, text)
    return HTTPException(400, text)


@workspace_router.get("/{project_id}/tree")
async def workspace_tree(project_id: str, path: str = "", depth: int = 12, hidden: bool = True):
    root = _root(project_id)
    try:
        start = workspace.resolve(root, path or ".")
    except workspace.WorkspaceError as error:
        raise _ws_error(error) from error
    if not start.is_dir():
        raise HTTPException(404, "not a directory")
    entries, truncated = await asyncio.to_thread(
        workspace.walk, root, start, max_entries=5000, max_depth=max(0, min(32, depth)),
        include_hidden=hidden)
    return {"project_id": project_id, "key": workspace.key_for(_project(project_id)),
            "root": str(root), "path": workspace.relpath(root, start), "entries": entries,
            "truncated": truncated}


@workspace_router.get("/{project_id}/file")
async def read_workspace_file(project_id: str, path: str):
    root = _root(project_id)
    try:
        target = workspace.resolve(root, path)
        if not target.is_file():
            raise HTTPException(404, "file not found")
        stat = target.stat()
        rel = workspace.relpath(root, target)
        meta = {"path": rel, "size": stat.st_size, "mtime": stat.st_mtime}
        suffix = target.suffix.lower()
        if suffix in IMAGE_TYPES:
            return {**meta, "content": "", "readonly": True, "viewer": "image"}
        if office.is_document(target):
            try:
                doc = await asyncio.to_thread(office.read, target)
            except office.DocumentError as error:
                raise HTTPException(422, f"could not read {target.name}: {error}") from error
            return {**meta, "content": doc["markdown"], "readonly": True, "viewer": doc.get("viewer") or "document",
                    "document": {k: doc.get(k) for k in ("format", "title", "blocks", "notice")}}
        content = workspace.read_text(target)
    except workspace.WorkspaceError as error:
        raise _ws_error(error) from error
    return {**meta, "content": content}


@workspace_router.get("/{project_id}/raw")
async def raw_workspace_file(project_id: str, path: str, member: str | None = None):
    """Bytes of a PDF or image in the workspace (or one embedded in a GoodNotes notebook)."""
    root = _root(project_id)
    try:
        target = workspace.resolve(root, path)
    except workspace.WorkspaceError as error:
        raise _ws_error(error) from error
    if not target.is_file():
        raise HTTPException(404, "file not found")
    suffix = target.suffix.lower()
    headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    if member is not None:
        if suffix != ".goodnotes":
            raise HTTPException(400, "only GoodNotes notebooks have embedded files")
        try:
            data, mime = await asyncio.to_thread(lambda: office.goodnotes_member(target.read_bytes(), member))
        except office.DocumentError as error:
            raise HTTPException(404, str(error)) from error
        return Response(data, media_type=mime, headers=headers)
    mime = IMAGE_TYPES.get(suffix) or ("application/pdf" if suffix == ".pdf" else None)
    if not mime:
        raise HTTPException(415, "only PDFs and images are served raw")
    return FileResponse(target, media_type=mime, headers=headers)


@workspace_router.put("/{project_id}/file")
async def write_workspace_file(project_id: str, path: str, body: FileBody):
    """Create or overwrite a text file; records a checkpoint (source ``user``)."""
    root = _root(project_id)
    key = workspace.key_for(_project(project_id))
    try:
        checkpoint = workspace.write_text(key, root, path, body.content, source="user")
        target = workspace.resolve(root, path)
    except workspace.WorkspaceError as error:
        raise _ws_error(error) from error
    stat = target.stat()
    return {"path": checkpoint["path"], "size": stat.st_size, "mtime": stat.st_mtime,
            "checkpoint": checkpoint}


@workspace_router.get("/{project_id}/checkpoints")
async def list_checkpoints(project_id: str, session_id: str | None = None,
                           path: str | None = None, limit: int = 200):
    _root(project_id)
    key = workspace.key_for(_project(project_id))
    return workspace.list_checkpoints(key, session_id=session_id or None, path=path or None,
                                      limit=max(1, min(2000, limit)))


@workspace_router.get("/{project_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint(project_id: str, checkpoint_id: str):
    """One checkpoint with ``before`` / ``after`` content and a unified ``diff``."""
    _root(project_id)
    key = workspace.key_for(_project(project_id))
    try:
        return workspace.get_checkpoint(key, checkpoint_id)
    except KeyError as error:
        raise HTTPException(404, "checkpoint not found") from error


@workspace_router.post("/{project_id}/checkpoints/{checkpoint_id}/revert")
async def revert_checkpoint(project_id: str, checkpoint_id: str):
    _root(project_id)
    key = workspace.key_for(_project(project_id))
    try:
        return workspace.revert_checkpoint(key, checkpoint_id)
    except KeyError as error:
        raise HTTPException(404, "checkpoint not found") from error
    except workspace.WorkspaceError as error:
        raise _ws_error(error) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


# -- version control (docs/glossx-workspaces.md section 3) ---------------------------------
class CommitBody(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    paths: list[str] | None = Field(default=None, max_length=500)


class BranchBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    from_: str | None = Field(default=None, max_length=200, alias="from")
    checkout: bool = False

    model_config = {"populate_by_name": True}


class CheckoutBody(BaseModel):
    branch: str = Field(min_length=1, max_length=100)
    stash: bool = False
    discard: bool = False


class MergeBody(BaseModel):
    branch: str = Field(min_length=1, max_length=100)


class RestoreBody(BaseModel):
    rev: str = Field(min_length=1, max_length=200)
    paths: list[str] | None = Field(default=None, max_length=500)


class DiscardBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


def _vcs_key(project_id: str) -> str:
    _root(project_id)  # 404 for an unknown project, creates the workspace
    return workspace.key_for(_project(project_id))


def _vcs_failure(error: Exception) -> JSONResponse:
    """VCS errors as ``{"detail": <message>, "code": <reason>, ...}``: 409 when git
    is missing or refuses (``available: false`` / ``changes`` say why), 404 for an
    unknown branch / revision / path, 400 for invalid input."""
    if isinstance(error, vcs.VcsError):
        return JSONResponse({"detail": str(error), "code": error.code, **error.extra},
                            status_code=409)
    if isinstance(error, LookupError):
        return JSONResponse({"detail": str(error).strip("'\""), "code": "not_found"},
                            status_code=404)
    return JSONResponse({"detail": str(error), "code": "invalid"}, status_code=400)


_VCS_ERRORS = (vcs.VcsError, LookupError, ValueError)


async def _vcs(func, *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except _VCS_ERRORS as error:
        return _vcs_failure(error)


def _refuse_while_running(request: Request, project_id: str, action: str) -> JSONResponse | None:
    """Switching the tree under a running agent turn would confuse it."""
    manager = getattr(request.app.state, "sessions", None)
    terminals = getattr(request.app.state, "terminals", None)
    if terminals is not None and terminals.has_active(_project(project_id)):
        return JSONResponse({"detail": f"A terminal is open in this workspace. Close it before you {action}.",
                             "code": "busy"}, status_code=409)
    busy = manager.busy_sessions(_project(project_id)) if manager is not None else []
    if not busy:
        return None
    return JSONResponse(
        {"detail": f"A thread is working in this workspace ({busy[0]['title']}). Wait for it "
                   f"to finish or stop it before you {action}.",
         "code": "busy", "sessions": busy}, status_code=409)


def _sync_sessions(request: Request, project_id: str) -> None:
    manager = getattr(request.app.state, "sessions", None)
    if manager is not None:
        manager.workspace_changed(_project(project_id))


@workspace_router.get("/{project_id}/vcs/status")
async def vcs_status(project_id: str):
    """Always 200: ``available`` is false (with ``reason``) when git is missing."""
    return await _vcs(vcs.status, _vcs_key(project_id))


@workspace_router.get("/{project_id}/vcs/log")
async def vcs_log(project_id: str, branch: str | None = None, limit: int = 50,
                  path: str | None = None, skip: int = 0):
    """Commits, newest first. ``branch=*`` lists every branch (for the graph)."""
    return await _vcs(vcs.log, _vcs_key(project_id), branch=branch or None,
                      limit=max(1, min(500, limit)), path=path or None, skip=max(0, skip))


@workspace_router.get("/{project_id}/vcs/diff")
async def vcs_diff(project_id: str, request: Request, to: str | None = None,
                   path: str | None = None, format: str | None = None):
    """Unified diff as ``{diff, from, to, path, truncated}`` (``?format=text`` or
    ``Accept: text/plain`` returns the bare text)."""
    result = await _vcs(vcs.diff, _vcs_key(project_id),
                        from_rev=request.query_params.get("from") or None, to_rev=to or None,
                        path=path or None)
    accept = request.headers.get("accept", "")
    if isinstance(result, dict) and (format == "text" or (
            "text/plain" in accept and "application/json" not in accept)):
        return PlainTextResponse(result["diff"])
    return result


@workspace_router.get("/{project_id}/vcs/file")
async def vcs_file(project_id: str, path: str, rev: str | None = None):
    return await _vcs(vcs.file_at, _vcs_key(project_id), rev=rev or None, path=path)


@workspace_router.post("/{project_id}/vcs/commit")
async def vcs_commit(project_id: str, body: CommitBody):
    return await _vcs(vcs.commit, _vcs_key(project_id), body.message, body.paths)


@workspace_router.post("/{project_id}/vcs/branches")
async def vcs_create_branch(project_id: str, body: BranchBody, request: Request):
    key = _vcs_key(project_id)
    if body.checkout:
        refused = _refuse_while_running(request, project_id, "switch branches")
        if refused is not None:
            return refused
    result = await _vcs(vcs.create_branch, key, body.name, body.from_, checkout=body.checkout)
    _sync_sessions(request, project_id)
    return result


@workspace_router.post("/{project_id}/vcs/checkout")
async def vcs_checkout(project_id: str, body: CheckoutBody, request: Request):
    key = _vcs_key(project_id)
    if body.stash and body.discard:
        raise HTTPException(400, "choose either stash or discard, not both")
    refused = _refuse_while_running(request, project_id, "switch branches")
    if refused is not None:
        return refused
    result = await _vcs(vcs.checkout, key, body.branch, stash=body.stash, discard=body.discard)
    _sync_sessions(request, project_id)
    return result


@workspace_router.post("/{project_id}/vcs/merge")
async def vcs_merge(project_id: str, body: MergeBody, request: Request):
    key = _vcs_key(project_id)
    refused = _refuse_while_running(request, project_id, "merge")
    if refused is not None:
        return refused
    return await _vcs(vcs.merge, key, body.branch)


@workspace_router.delete("/{project_id}/vcs/branches/{name:path}")
async def vcs_delete_branch(project_id: str, name: str, force: bool = False):
    return await _vcs(vcs.delete_branch, _vcs_key(project_id), name, force=force)


@workspace_router.post("/{project_id}/vcs/restore")
async def vcs_restore(project_id: str, body: RestoreBody, request: Request):
    key = _vcs_key(project_id)
    refused = _refuse_while_running(request, project_id, "restore a version")
    if refused is not None:
        return refused
    return await _vcs(vcs.restore, key, body.rev, body.paths)


@workspace_router.post("/{project_id}/vcs/discard")
async def vcs_discard(project_id: str, body: DiscardBody):
    return await _vcs(vcs.discard, _vcs_key(project_id), body.paths)
