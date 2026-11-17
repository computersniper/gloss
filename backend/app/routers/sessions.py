"""GlossX harness API: agent sessions (docs/glossx-harness.md section 7)."""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..harness import vcs
from ..harness.manager import NotPendingError, SessionBusyError, SessionNotFoundError

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

Mode = Literal["ask", "agent", "auto", "plan"]


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=120)
    effort: str | None = Field(default=None, max_length=16)


class SessionCreateBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, max_length=64)
    mode: Mode | None = None
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=120)
    effort: str | None = Field(default=None, max_length=16)
    # work on this workspace branch (created from the current commit when new)
    branch: str | None = Field(default=None, max_length=100)
    # the paper in the active tab: scopes the session to it and, without a
    # project_id, selects the paper's own project
    paper_id: str | None = Field(default=None, max_length=64)


class SessionPatchBody(BaseModel):
    archived: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Mode | None = None
    provider: str | None = Field(default=None, max_length=64)
    model: str | ModelBody | None = None


class ForkBody(BaseModel):
    message_id: str | None = Field(default=None, max_length=64)


class MoveProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(max_length=64)


class AttachmentBody(BaseModel):
    kind: Literal["paper", "span", "file", "run"]
    ref: str = Field(min_length=1, max_length=1000)
    label: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=8000)


class MessageBody(BaseModel):
    text: str = Field(default="", max_length=100_000)
    attachments: list[AttachmentBody] | None = Field(default=None, max_length=32)


class ApprovalBody(BaseModel):
    decision: Literal["once", "always", "deny"]
    feedback: str | None = Field(default=None, max_length=4000)


class InputBody(BaseModel):
    answer: str = Field(min_length=1, max_length=20_000)
    tool_call_id: str | None = Field(default=None, max_length=64)


class CompactBody(BaseModel):
    focus: str | None = Field(default=None, max_length=2000)


def _manager(request: Request):
    manager = getattr(request.app.state, "sessions", None)
    if manager is None:
        raise HTTPException(503, "the session manager is not running")
    return manager


def _fail(error: Exception) -> HTTPException:
    if isinstance(error, SessionNotFoundError):
        return HTTPException(404, "session not found")
    if isinstance(error, (SessionBusyError, NotPendingError)):
        return HTTPException(409, str(error))
    if isinstance(error, LookupError):
        return HTTPException(404, str(error).strip("'\""))
    return HTTPException(400, str(error))


_HANDLED = (LookupError, ValueError, SessionBusyError, NotPendingError)


def _sse(events, named: bool) -> StreamingResponse:
    async def stream():
        yield ": connected\n\n"
        async for name, payload, seq in events:
            if name is None:
                yield ": ping\n\n"
                continue
            data = json.dumps({**payload, "event": name, "seq": seq},
                              ensure_ascii=False, default=str)
            yield (f"event: {name}\n" if named else "") + f"data: {data}\n\n"

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
async def list_sessions(request: Request, project_id: str | None = None,
                        include_subagents: bool = False,
                        archived: bool = False,
                        view: Literal["inbox"] | None = None):
    """Sessions, most recently updated first. ``?view=inbox`` orders them for the
    inbox: blocking attention, then unread, then the rest, each newest first."""
    return _manager(request).list(project_id=project_id, include_subagents=include_subagents,
                                  view=view, archived=archived)


@router.post("")
async def create_session(request: Request, body: SessionCreateBody | None = None):
    body = body or SessionCreateBody()
    manager = _manager(request)
    try:
        project_id, paper_id = manager.resolve_new_session(body.project_id, body.paper_id)
        branch = await manager.prepare_branch(project_id, body.branch)
        return manager.create(title=body.title, project_id=project_id,
                              mode=body.mode, provider=body.provider, model=body.model,
                              effort=body.effort, branch=branch, paper_id=paper_id)
    except vcs.VcsError as error:
        return JSONResponse({"detail": str(error), "code": error.code, **error.extra},
                            status_code=409)
    except _HANDLED as error:
        raise _fail(error) from error


# -- inbox (declared before /{session_id} so "inbox" is never taken for an id) ---------------
@router.get("/inbox/summary")
async def inbox_summary(request: Request):
    """``{unread, blocking, total, items}``; ``items`` are the first 20 threads in
    inbox order."""
    return _manager(request).inbox_summary()


@router.get("/inbox/events")
async def inbox_events(request: Request, named: bool = True):
    """SSE: ``inbox.updated`` with the summary whenever a thread's unread /
    attention changes (also for threads whose own stream is closed). The
    summary is replayed on connect."""
    return _sse(_manager(request).subscribe_inbox(), named)


@router.post("/inbox/read")
async def inbox_mark_all_read(request: Request, project_id: str | None = None):
    """Mark every thread (of one project, with ``?project_id=``) as read."""
    return _manager(request).mark_all_read(project_id)


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    try:
        return _manager(request).get(session_id)
    except _HANDLED as error:
        raise _fail(error) from error


@router.patch("/{session_id}")
async def patch_session(session_id: str, body: SessionPatchBody, request: Request):
    fields: dict[str, Any] = {}
    for key in body.model_fields_set:
        value = getattr(body, key)
        fields[key] = value.model_dump(exclude_unset=True) if isinstance(value, ModelBody) else value
    if not fields:
        raise HTTPException(400, "nothing to update (title, mode, model)")
    try:
        return _manager(request).patch(session_id, fields)
    except _HANDLED as error:
        raise _fail(error) from error


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    try:
        await _manager(request).delete(session_id)
        await request.app.state.browser.close_session(session_id)
    except _HANDLED as error:
        raise _fail(error) from error
    return {"status": "deleted", "id": session_id}


@router.post("/{session_id}/fork")
async def fork_session(session_id: str, request: Request, body: ForkBody | None = None):
    try:
        return _manager(request).fork(session_id, body.message_id if body else None)
    except _HANDLED as error:
        raise _fail(error) from error


@router.post("/{session_id}/project")
async def move_session_project(session_id: str, body: MoveProjectBody, request: Request):
    try:
        return _manager(request).move_project(session_id, body.project_id)
    except _HANDLED as error:
        raise _fail(error) from error


class RewindBody(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    include: bool = False
    restore_files: bool = False


@router.post("/{session_id}/rewind")
async def rewind_session(session_id: str, body: RewindBody, request: Request):
    """Go back to a message in place (Claude Code's rewind): later messages are
    dropped, and ``restore_files`` also reverts the file edits made since."""
    try:
        return await _manager(request).rewind(session_id, body.message_id, include=body.include,
                                              restore_files=body.restore_files)
    except _HANDLED as error:
        raise _fail(error) from error


@router.post("/{session_id}/messages")
async def post_message(session_id: str, body: MessageBody, request: Request):
    attachments = [a.model_dump(exclude_none=True) for a in body.attachments or []]
    try:
        return await _manager(request).send_message(session_id, body.text, attachments or None)
    except _HANDLED as error:
        raise _fail(error) from error


@router.get("/{session_id}/events")
async def session_events(session_id: str, request: Request, named: bool = True):
    """SSE stream. Each event is ``event: <name>`` + ``data: <payload JSON>``; the
    payload also carries ``"event"`` and ``"seq"``. ``?named=0`` drops the
    ``event:`` line so ``EventSource.onmessage`` receives everything."""
    manager = _manager(request)
    try:
        manager.get(session_id)
    except _HANDLED as error:
        raise _fail(error) from error

    return _sse(manager.subscribe(session_id), named)


@router.post("/{session_id}/read")
async def mark_session_read(session_id: str, request: Request):
    """The thread is shown and focused: clears ``unread`` and a non-blocking
    ``attention``. Returns the session summary."""
    try:
        return _manager(request).mark_read(session_id)
    except _HANDLED as error:
        raise _fail(error) from error


@router.post("/{session_id}/interrupt")
async def interrupt_session(session_id: str, request: Request):
    try:
        return await _manager(request).interrupt(session_id)
    except _HANDLED as error:
        raise _fail(error) from error


@router.post("/{session_id}/approvals/{tool_call_id}")
async def answer_approval(session_id: str, tool_call_id: str, body: ApprovalBody, request: Request):
    try:
        return _manager(request).resolve_approval(session_id, tool_call_id, body.decision,
                                                  body.feedback)
    except _HANDLED as error:
        raise _fail(error) from error


@router.post("/{session_id}/input")
async def answer_input(session_id: str, body: InputBody, request: Request):
    try:
        return _manager(request).answer_input(session_id, body.answer, body.tool_call_id)
    except _HANDLED as error:
        raise _fail(error) from error


@router.post("/{session_id}/compact")
async def compact_session(session_id: str, request: Request, body: CompactBody | None = None):
    try:
        return await _manager(request).compact(session_id, (body.focus if body else None) or "")
    except _HANDLED as error:
        raise _fail(error) from error


@router.get("/{session_id}/results/{ref}")
async def get_result(session_id: str, ref: str, request: Request):
    """Full text of a stored tool result (``tool_call.result_ref``)."""
    try:
        text = _manager(request).result(session_id, ref)
    except _HANDLED as error:
        raise _fail(error) from error
    return {"ref": ref, "chars": len(text), "text": text}
