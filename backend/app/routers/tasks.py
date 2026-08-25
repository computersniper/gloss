"""Observable and cancellable background AI tasks."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..tasks import TaskNotFoundError, TaskValidationError

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreateBody(BaseModel):
    feature_id: str = Field(min_length=1, max_length=128)
    paper_id: str | None = None
    refresh: bool = False
    language: str | None = None
    input: dict[str, Any] | str | None = Field(default_factory=dict)


def _manager(request: Request):
    return request.app.state.ai_tasks


@router.post("")
async def create_task(body: TaskCreateBody, request: Request):
    try:
        return await _manager(request).submit(
            feature_id=body.feature_id,
            paper_id=body.paper_id,
            refresh=body.refresh,
            language=body.language,
            input=(
                body.input
                if isinstance(body.input, dict)
                else {"text": body.input}
                if isinstance(body.input, str)
                else {}
            ),
        )
    except TaskValidationError as error:
        status = 404 if str(error) in {"paper not found", "plugin is not installed"} else 400
        raise HTTPException(status, str(error)) from error


@router.get("/artifact")
async def get_cached_artifact(
    request: Request,
    feature_id: str,
    paper_id: str,
    language: str | None = None,
    configuration: str | None = None,
):
    """Return a saved result only; this endpoint never starts an AI call."""
    try:
        parsed_configuration: dict[str, Any] = {}
        if configuration:
            decoded = json.loads(configuration)
            if not isinstance(decoded, dict):
                raise TaskValidationError("configuration must be a JSON object")
            parsed_configuration = decoded
        result = _manager(request).cached_result(
            feature_id=feature_id,
            paper_id=paper_id,
            language=language,
            input={"configuration": parsed_configuration},
        )
    except TaskValidationError as error:
        status = 404 if str(error) in {
            "paper not found", "plugin is not installed",
        } else 400
        raise HTTPException(status, str(error)) from error
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(400, str(error)) from error
    return {"found": result is not None, "result": result}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request):
    try:
        return _manager(request).get(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(404, "task not found") from error


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request):
    manager = _manager(request)
    try:
        manager.get(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(404, "task not found") from error

    async def stream():
        async for snapshot in manager.events(task_id):
            yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/{task_id}")
async def cancel_task(task_id: str, request: Request):
    try:
        return await _manager(request).cancel(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(404, "task not found") from error
