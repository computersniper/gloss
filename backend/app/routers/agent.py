"""GlossX agent API: capabilities, workflows, loops, runs (SSE + approvals),
research projects and external connections."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent import connections as conn_mod
from ..agent import projects
from ..agent.adapters import cli_delegate, mcp_client, ssh
from ..agent.capabilities import abuild_registry
from ..agent.loops import LOOPS, list_loops
from ..agent.runs import ApprovalNotFoundError, RunNotFoundError
from ..agent.workflow import list_workflows

router = APIRouter(prefix="/api/agent", tags=["agent"])


class BudgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int | None = Field(default=None, ge=1, le=64)
    max_llm_calls: int | None = Field(default=None, ge=1, le=200)
    max_tokens_est: int | None = Field(default=None, ge=1_000, le=4_000_000)


class RunCreateBody(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    paper_ids: list[str] = Field(default_factory=list, max_length=50)
    max_rounds: int = Field(default=2, ge=1, le=3)
    provider: str | None = None
    project_id: str | None = Field(default=None, max_length=64)
    workflow: str = Field(default="auto", max_length=120)
    loop: str = Field(default="auto", max_length=60)
    budget: BudgetBody | None = None

    @field_validator("goal")
    @classmethod
    def _goal_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be empty")
        return value.strip()

    @field_validator("workflow")
    @classmethod
    def _workflow_shape(cls, value: str) -> str:
        value = (value or "auto").strip()
        if value not in {"auto", "generic"} and not value.startswith("workflow."):
            raise ValueError("workflow must be 'auto', 'generic' or 'workflow.<id>'")
        return value

    @field_validator("loop")
    @classmethod
    def _loop_shape(cls, value: str) -> str:
        value = (value or "auto").strip()
        if value.removeprefix("loop:") not in {"auto", *LOOPS}:
            raise ValueError(f"loop must be 'auto' or one of: {', '.join(LOOPS)}")
        return value


class ApprovalBody(BaseModel):
    approve: bool


class ProjectCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return value.strip()


class MemoryBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value.strip()


def _manager(request: Request):
    return request.app.state.agent_runs


# -- capabilities, workflows, loops ---------------------------------------
@router.get("/capabilities")
async def list_capabilities():
    conns = conn_mod.load()
    registry = await abuild_registry(connections=conns)
    auto = set(conns.get("auto_approve") or [])
    return [cap.describe(auto) for cap in registry.all()]


@router.get("/workflows")
async def get_workflows():
    return list_workflows()


@router.get("/loops")
async def get_loops():
    return list_loops()


# -- runs -------------------------------------------------------------------
@router.post("/runs")
async def create_run(body: RunCreateBody, request: Request):
    try:
        return _manager(request).start(
            body.goal,
            body.paper_ids,
            max_rounds=body.max_rounds,
            provider=body.provider,
            project_id=body.project_id,
            workflow=body.workflow,
            loop=body.loop,
            budget=body.budget.model_dump(exclude_none=True) if body.budget else None,
            max_rounds_explicit="max_rounds" in body.model_fields_set,
        )
    except LookupError as error:
        raise HTTPException(404, str(error).strip("'\"")) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/runs")
async def list_runs(request: Request):
    return _manager(request).list()


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    try:
        return _manager(request).get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(404, "run not found") from error


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def answer_approval(run_id: str, approval_id: str, body: ApprovalBody, request: Request):
    try:
        return _manager(request).resolve_approval(run_id, approval_id, body.approve)
    except RunNotFoundError as error:
        raise HTTPException(404, "run not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(409, "approval is not pending for this run") from error


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    manager = _manager(request)
    try:
        manager.get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(404, "run not found") from error

    async def stream():
        async for event in manager.subscribe(run_id):
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        end = {"phase": "end", "status": manager.status_of(run_id)}
        yield f"data: {json.dumps(end)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/runs/{run_id}")
async def cancel_run(run_id: str, request: Request):
    try:
        return await _manager(request).cancel(run_id)
    except RunNotFoundError as error:
        raise HTTPException(404, "run not found") from error


# -- projects ---------------------------------------------------------------
def _project_detail(project_id: str, request: Request) -> dict[str, Any]:
    try:
        project = projects.get(project_id)
    except projects.ProjectNotFoundError as error:
        raise HTTPException(404, "project not found") from error
    project["runs"] = _manager(request).summaries(project.get("run_ids") or [])
    return project


@router.get("/projects")
async def list_projects(paper_id: str | None = None, archived: bool = False):
    return projects.list_projects(paper_id, archived=archived)


class LifecycleBody(BaseModel):
    action: Literal["archive", "restore", "remove"]


def _lifecycle(ids: list[str], action: str, request: Request):
    from ..harness import workspace
    manager = request.app.state.sessions
    for key in ids:
        project = projects.get(key)
        runs = _manager(request).summaries(project.get("run_ids") or [])
        if manager.busy_sessions(key) or workspace.is_relocating(key) or any(r["status"] not in ("completed", "failed", "cancelled") for r in runs):
            raise HTTPException(409, "wait for running threads or workspace relocation to finish")
        if action == "remove" and not project.get("archived"):
            raise HTTPException(400, "archive the project before removing it")
        if project.get("removed"):
            raise HTTPException(400, "project has been removed")
    for key in ids:
        projects.lifecycle(key, action)
        manager.project_archived(key, action != "restore")
    return {"changed": len(ids)}


@router.post("/projects/{project_id}/lifecycle")
async def project_lifecycle(project_id: str, body: LifecycleBody, request: Request):
    try:
        return _lifecycle([project_id], body.action, request)
    except projects.ProjectNotFoundError as error:
        raise HTTPException(404, "project not found") from error


class DirectionLifecycleBody(LifecycleBody):
    name: str = Field(min_length=1, max_length=200)


@router.post("/categories/lifecycle")
async def direction_lifecycle(body: DirectionLifecycleBody, request: Request):
    members = [p for p in projects.list_projects(archived=body.action != "archive")
               if p.get("category") == body.name or p.get("hub_category") == body.name]
    return _lifecycle([p["id"] for p in members], body.action, request)


@router.post("/projects")
async def create_project(body: ProjectCreateBody):
    try:
        project = projects.create(body.name, body.description or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {**projects.workspace_detail(project), "runs": []}


class OpenFolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


@router.post("/projects/open-folder")
async def open_folder(body: OpenFolderBody):
    try:
        project = projects.open_folder(body.path)
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
    return projects.workspace_detail(project)


class ProjectPatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)


class CategoryBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    new_name: str | None = Field(default=None, max_length=200)


@router.post("/categories/hub")
async def category_hub(body: CategoryBody, request: Request):
    """The research project gathering every paper of one direction."""
    try:
        hub = await asyncio.to_thread(projects.category_hub, body.name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _project_detail(hub["id"], request)


@router.post("/categories/rename")
async def rename_category(body: CategoryBody):
    try:
        changed = await asyncio.to_thread(projects.rename_category, body.name, body.new_name or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"changed": changed}


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, body: ProjectPatchBody, request: Request):
    try:
        await asyncio.to_thread(projects.update, project_id, name=body.name, category=body.category)
    except projects.ProjectNotFoundError as error:
        raise HTTPException(404, "project not found") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _project_detail(project_id, request)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    return _project_detail(project_id, request)


@router.post("/projects/{project_id}/memory")
async def add_project_memory(project_id: str, body: MemoryBody, request: Request):
    try:
        note = projects.add_memory(project_id, body.text)
    except projects.ProjectNotFoundError as error:
        raise HTTPException(404, "project not found") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {**_project_detail(project_id, request), "note": note}


@router.delete("/projects/{project_id}/memory/{memory_id}")
async def delete_project_memory(project_id: str, memory_id: str, request: Request):
    try:
        projects.delete_memory(project_id, memory_id)
    except projects.ProjectNotFoundError as error:
        raise HTTPException(404, "project not found") from error
    except KeyError as error:
        raise HTTPException(404, "memory note not found") from error
    return _project_detail(project_id, request)


# -- connections ----------------------------------------------------------------
@router.get("/connections")
async def get_connections():
    return conn_mod.load()


@router.put("/connections")
async def put_connections(body: dict[str, Any]):
    try:
        saved = conn_mod.save(body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    # stop MCP servers that were removed, disabled or reconfigured
    await mcp_client.MANAGER.prune(saved)
    return saved


@router.get("/connections/status")
async def connections_status():
    conns = conn_mod.load()
    mcp_status, ssh_status = await asyncio.gather(
        mcp_client.MANAGER.status(conns), ssh.status(conns)
    )
    cli = conns.get("cli_agents") or {}
    return {
        "mcp": mcp_status,
        "ssh": ssh_status,
        "cli": {
            agent: {
                "available": cli_delegate.cli_available(agent),
                "enabled": bool((cli.get(agent) or {}).get("enabled")),
            }
            for agent in ("claude", "codex")
        },
    }


class ProjectPaperBody(BaseModel):
    paper_id: str = Field(min_length=1, max_length=100)


@router.post("/projects/{project_id}/papers")
async def link_project_paper(project_id: str, body: ProjectPaperBody):
    try:
        return projects.link_paper(project_id, body.paper_id)
    except (LookupError, KeyError) as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.delete("/projects/{project_id}/papers/{paper_id}")
async def unlink_project_paper(project_id: str, paper_id: str):
    try:
        return projects.unlink_paper(project_id, paper_id)
    except (LookupError, KeyError) as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
