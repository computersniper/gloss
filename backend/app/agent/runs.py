"""Agent run lifecycle: start, observe (SSE), cancel, persist.

Each run is one asyncio task driving :func:`loop.run_agent`. Every event the
loop emits is appended to the run's trace and fanned out to live subscribers.
Finished runs are written to ``<data dir>/agent_runs/<id>.json`` and reloaded
on startup, which gives the agent a durable run memory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .. import config
from ..library import store
from ..providers import registry as providers
from . import connections, projects
from .capabilities import abuild_registry
from .context import SpanStore
from .gate import BudgetMeter, Gate, normalize_budget
from .loop import run_agent
from .loops import normalize_loop_id

logger = logging.getLogger(__name__)

RUNS_DIR = config.DATA_DIR / "agent_runs"
TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"running", "awaiting_approval"}
MAX_LOADED_RUNS = 500


class RunNotFoundError(KeyError):
    pass


class ApprovalNotFoundError(LookupError):
    pass


@dataclass
class AgentRun:
    id: str
    goal: str
    paper_ids: list[str]
    max_rounds: int = 2
    provider: str | None = None
    status: str = "running"
    phase: str = "plan"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    project_id: str | None = None
    workflow: str = "auto"
    loop: str = "auto"
    max_rounds_explicit: bool = True
    route: dict[str, Any] | None = None
    pending_approval: dict[str, Any] | None = None
    budget_limits: dict[str, int] | None = None
    budget_final: dict[str, int] | None = None
    import_jobs: Any | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    subscribers: list[asyncio.Queue] = field(default_factory=list, repr=False)
    meter: BudgetMeter | None = field(default=None, repr=False)
    gate: Gate | None = field(default=None, repr=False)

    def budget(self) -> dict[str, int] | None:
        if self.meter is not None:
            return self.meter.snapshot()
        return self.budget_final

    def snapshot(self, *, trace: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "goal": self.goal,
            "paper_ids": list(self.paper_ids),
            "status": self.status,
            "phase": self.phase,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "plan": self.plan,
            "result": self.result,
            "error": self.error,
            "route": self.route,
            "project_id": self.project_id,
            "workflow": self.workflow,
            "loop": self.loop,
            "pending_approval": self.pending_approval,
            "budget": self.budget(),
        }
        if trace:
            out["trace"] = list(self.trace)
        return out

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> "AgentRun":
        status = data.get("status") if data.get("status") in TERMINAL else "failed"
        return cls(
            id=str(data["id"]),
            goal=str(data.get("goal") or ""),
            paper_ids=[str(p) for p in data.get("paper_ids") or []],
            status=status,
            phase=str(data.get("phase") or "final"),
            created_at=float(data.get("created_at") or 0.0),
            finished_at=data.get("finished_at"),
            trace=list(data.get("trace") or []),
            plan=data.get("plan"),
            result=data.get("result"),
            error=data.get("error"),
            project_id=data.get("project_id"),
            workflow=str(data.get("workflow") or "auto"),
            loop=str(data.get("loop") or "auto"),
            route=data.get("route"),
            budget_final=data.get("budget") if isinstance(data.get("budget"), dict) else None,
        )


class AgentRunManager:
    """Own agent runs for one local Gloss process."""

    def __init__(self, *, import_jobs: Any | None = None, runs_dir: Path | None = None) -> None:
        self.import_jobs = import_jobs
        self.runs_dir = runs_dir or RUNS_DIR
        self._runs: dict[str, AgentRun] = {}
        self._accepting = True
        self._load_persisted()

    # -- persistence -------------------------------------------------------
    def _load_persisted(self) -> None:
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(
                self.runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )[:MAX_LOADED_RUNS]
        except OSError:
            logger.exception("could not read agent run directory")
            return
        for path in files:
            try:
                run = AgentRun.from_snapshot(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("skipping unreadable agent run %s", path.name)
                continue
            self._runs[run.id] = run

    def _persist(self, run: AgentRun) -> None:
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            target = self.runs_dir / f"{run.id}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(run.snapshot(), ensure_ascii=False, default=str), encoding="utf-8"
            )
            os.replace(tmp, target)
        except OSError:
            logger.exception("could not persist agent run %s", run.id)

    # -- public API ---------------------------------------------------------
    def start(
        self,
        goal: str,
        paper_ids: list[str],
        max_rounds: int = 2,
        provider: str | None = None,
        *,
        project_id: str | None = None,
        workflow: str | None = None,
        loop: str | None = None,
        budget: dict[str, Any] | None = None,
        max_rounds_explicit: bool = True,
        authorizer: Any | None = None,
    ) -> dict[str, Any]:
        """Start a run. ``authorizer`` (optional async ``(approval) -> (bool, msg)``)
        answers the run's approvals instead of the HTTP approval endpoint."""
        if not self._accepting:
            raise RuntimeError("agent is shutting down")
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal must not be empty")
        if not 1 <= int(max_rounds) <= 3:
            raise ValueError("max_rounds must be between 1 and 3")
        if provider and provider not in providers.available():
            raise ValueError(f"unknown provider: {provider}")
        workflow = str(workflow or "auto").strip()
        if workflow not in {"auto", "generic"} and not workflow.startswith("workflow."):
            raise ValueError("workflow must be 'auto', 'generic' or 'workflow.<id>'")
        loop_id = normalize_loop_id(loop)  # raises ValueError for unknown loops
        limits = normalize_budget(budget)
        if project_id and not projects.exists(project_id):
            raise LookupError(f"project not found: {project_id}")
        if project_id and projects.get(project_id).get("archived"):
            raise ValueError("restore the archived project before starting a run")
        scope = list(dict.fromkeys(str(p) for p in paper_ids))
        for pid in scope:
            if not store.get_paper(pid):
                raise LookupError(f"paper not found: {pid}")
        if workflow.startswith("workflow."):
            from .workflow import workflow_manifests

            known = {f"workflow.{m['id']}" for m in workflow_manifests()}
            if workflow not in known:
                raise ValueError(f"unknown workflow: {workflow}")

        run = AgentRun(
            id=uuid.uuid4().hex,
            goal=goal[:4000],
            paper_ids=scope,
            max_rounds=int(max_rounds),
            provider=provider or None,
            import_jobs=self.import_jobs,
            project_id=project_id or None,
            workflow=workflow,
            loop=loop_id or "auto",
            max_rounds_explicit=max_rounds_explicit,
            budget_limits=limits,
        )
        emit = lambda ev, _run=run: self._emit(_run, ev)  # noqa: E731
        run.meter = BudgetMeter(limits, emit=emit)
        run.gate = Gate(
            emit,
            auto_approve=connections.auto_approve,
            on_wait=lambda approval, _run=run: self._awaiting(_run, approval),
            on_resume=lambda _run=run: self._resumed(_run),
            delegate=authorizer,
        )
        if run.project_id:
            projects.add_run(run.project_id, run.id)
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._drive(run), name=f"glossx-run-{run.id[:8]}")
        return run.snapshot()

    def resolve_approval(self, run_id: str, approval_id: str, approve: bool) -> dict[str, Any]:
        run = self._get(run_id)
        pending = run.pending_approval
        if run.gate is None or not pending or pending.get("id") != approval_id:
            raise ApprovalNotFoundError(approval_id)
        if not run.gate.resolve(approval_id, approve):
            raise ApprovalNotFoundError(approval_id)
        # reflect the decision immediately; the gate resumes the loop shortly
        self._resumed(run)
        return run.snapshot()

    def get(self, run_id: str) -> dict[str, Any]:
        return self._get(run_id).snapshot()

    def list(self) -> list[dict[str, Any]]:
        runs = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
        return [r.snapshot(trace=False) for r in runs]

    def summaries(self, run_ids: list[str]) -> list[dict[str, Any]]:
        out = []
        for rid in run_ids:
            run = self._runs.get(rid)
            if run is not None:
                out.append({"id": run.id, "goal": run.goal, "status": run.status,
                            "created_at": run.created_at, "finished_at": run.finished_at,
                            "stop_reason": (run.result or {}).get("stop_reason")})
        return out

    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        task = run.task
        if run.status in ACTIVE and task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except (asyncio.CancelledError, TimeoutError, asyncio.TimeoutError):
                pass
            self._mark_cancelled_if_stuck(run)
        return run.snapshot()

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Replay the trace so far, then stream live events until the run ends."""
        run = self._get(run_id)
        queue: asyncio.Queue = asyncio.Queue()
        # no await between copying the trace and registering: no gaps, no dupes
        backlog = list(run.trace)
        finished = run.status in TERMINAL
        if not finished:
            run.subscribers.append(queue)
        try:
            for event in backlog:
                yield event
            if finished:
                return
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if queue in run.subscribers:
                run.subscribers.remove(queue)

    async def wait(self, run_id: str) -> dict[str, Any]:
        """Wait until the run finishes and return its snapshot."""
        run = self._get(run_id)
        task = run.task
        if task is not None and not task.done():
            await asyncio.shield(task)
        return run.snapshot()

    def status_of(self, run_id: str) -> str:
        return self._get(run_id).status

    async def shutdown(self) -> None:
        self._accepting = False
        tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=15)
        for run in self._runs.values():
            self._mark_cancelled_if_stuck(run)

    # -- internals ----------------------------------------------------------
    def _get(self, run_id: str) -> AgentRun:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    def _awaiting(self, run: AgentRun, approval: dict[str, Any]) -> None:
        run.pending_approval = approval
        if run.status == "running":
            run.status = "awaiting_approval"

    def _resumed(self, run: AgentRun) -> None:
        run.pending_approval = None
        if run.status == "awaiting_approval":
            run.status = "running"

    def _emit(self, run: AgentRun, event: dict[str, Any]) -> None:
        run.trace.append(event)
        if event.get("phase") in {
            "route", "plan", "execute", "synthesize", "verify", "reflect", "final", "error",
        }:
            run.phase = event["phase"]
        for q in list(run.subscribers):
            q.put_nowait(event)

    async def _drive(self, run: AgentRun) -> None:
        try:
            spans = SpanStore()
            registry = await abuild_registry()
            run.result = await run_agent(run, registry, spans, lambda ev: self._emit(run, ev))
            run.status = "completed"
            run.phase = "final"
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.error = "cancelled"
            self._emit(run, {"ts": time.time(), "phase": "final", "status": "info",
                             "detail": "run cancelled"})
        except Exception as error:  # the loop already degrades; this is a last resort
            logger.exception("agent run %s failed", run.id)
            run.status = "failed"
            run.error = (str(error) or type(error).__name__)[:1000]
            self._emit(run, {"ts": time.time(), "phase": "error", "status": "failed",
                             "detail": run.error})
        finally:
            self._finish(run)

    def _finish(self, run: AgentRun) -> None:
        run.finished_at = time.time()
        run.pending_approval = None
        if run.meter is not None:
            run.budget_final = run.meter.snapshot()
            run.meter = None
        run.gate = None
        self._persist(run)
        for q in list(run.subscribers):
            q.put_nowait(None)
        run.subscribers.clear()

    def _mark_cancelled_if_stuck(self, run: AgentRun) -> None:
        # A task cancelled before its first step never reaches _drive's handlers.
        if run.status in ACTIVE and run.task is not None and run.task.done():
            run.status = "cancelled"
            run.error = "cancelled"
            self._emit(run, {"ts": time.time(), "phase": "final", "status": "info",
                             "detail": "run cancelled"})
            self._finish(run)
