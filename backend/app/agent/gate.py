"""Kernel gate: permission/approval checks and the per-run budget meter.

Before a step runs, the loop asks :class:`Gate` to authorize it. Capabilities
holding a permission in ``APPROVAL_REQUIRED`` pause the run (status
``awaiting_approval``) until the user approves or denies through the HTTP API,
unless that permission is listed in ``connections.auto_approve``. A denial or a
10-minute timeout fails the step with "approval denied"; the loop continues.

:class:`BudgetMeter` counts step executions, LLM calls and estimated tokens.
Every provider call made inside a run's context is metered through
``providers.registry.call_meter``; exceeding any limit stops the run with
``stop_reason="budget"``.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable

from .capabilities import APPROVAL_REQUIRED, Capability

APPROVAL_TIMEOUT = 600.0
DEFAULT_BUDGET = {"max_steps": 16, "max_llm_calls": 40, "max_tokens_est": 400_000}
BUDGET_BOUNDS = {
    "max_steps": (1, 64),
    "max_llm_calls": (1, 200),
    "max_tokens_est": (1_000, 4_000_000),
}

Emit = Callable[[dict[str, Any]], None]

__all__ = [
    "APPROVAL_REQUIRED",
    "APPROVAL_TIMEOUT",
    "BudgetExceeded",
    "BudgetMeter",
    "DEFAULT_BUDGET",
    "Gate",
    "normalize_budget",
]


class BudgetExceeded(RuntimeError):
    """Raised when a run would exceed one of its budget limits."""


def _event(phase: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    ev: dict[str, Any] = {"ts": time.time(), "phase": phase, "status": status, "detail": detail}
    ev.update({k: v for k, v in extra.items() if v is not None})
    return ev


def normalize_budget(raw: dict[str, Any] | None) -> dict[str, int]:
    """Merge a partial request budget over the defaults; reject bad values."""
    out = dict(DEFAULT_BUDGET)
    for key, value in (raw or {}).items():
        if value is None:
            continue
        if key not in BUDGET_BOUNDS:
            raise ValueError(f"unknown budget field: {key}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"budget.{key} must be an integer")
        lo, hi = BUDGET_BOUNDS[key]
        if not lo <= value <= hi:
            raise ValueError(f"budget.{key} must be between {lo} and {hi}")
        out[key] = value
    return out


class BudgetMeter:
    """Per-run counters; also the provider-registry call meter."""

    def __init__(self, limits: dict[str, int] | None = None, emit: Emit | None = None) -> None:
        limits = normalize_budget(limits)
        self.max_steps = limits["max_steps"]
        self.max_llm_calls = limits["max_llm_calls"]
        self.max_tokens_est = limits["max_tokens_est"]
        self.steps = 0
        self.llm_calls = 0
        self.tokens_est = 0
        self.exhausted: str | None = None
        self._emit = emit

    # -- provider hook -----------------------------------------------------
    def before_llm_call(self) -> None:
        if self.llm_calls >= self.max_llm_calls:
            self._exhaust(f"LLM call budget reached ({self.max_llm_calls})")
        if self.tokens_est >= self.max_tokens_est:
            self._exhaust(f"token budget reached (~{self.max_tokens_est} tokens)")
        if self.exhausted:
            raise BudgetExceeded(self.exhausted)
        self.llm_calls += 1

    def after_llm_call(self, tokens_est: int) -> None:
        self.tokens_est += max(0, int(tokens_est or 0))
        if self.tokens_est >= self.max_tokens_est:
            self._exhaust(f"token budget reached (~{self.max_tokens_est} tokens)")

    # -- steps ---------------------------------------------------------------
    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self.steps)

    def take_steps(self, n: int) -> int:
        """Reserve up to ``n`` step executions; returns how many were granted."""
        granted = min(max(0, n), self.steps_remaining)
        self.steps += granted
        if granted < n:
            self._exhaust(f"step budget reached ({self.max_steps})")
        return granted

    def check(self) -> None:
        if self.exhausted:
            raise BudgetExceeded(self.exhausted)

    def _exhaust(self, reason: str) -> None:
        if self.exhausted:
            return
        self.exhausted = reason
        if self._emit:
            self._emit(_event("budget", "stopped", reason))

    def snapshot(self) -> dict[str, int]:
        return {
            "steps": self.steps,
            "max_steps": self.max_steps,
            "llm_calls": self.llm_calls,
            "max_llm_calls": self.max_llm_calls,
            "tokens_est": self.tokens_est,
            "max_tokens_est": self.max_tokens_est,
        }


def _args_preview(args: Any) -> Any:
    """Bound the size of args shown in an approval request."""
    if isinstance(args, dict):
        out = {}
        for k, v in list(args.items())[:20]:
            if isinstance(v, str) and len(v) > 2000:
                v = v[:2000] + "...[truncated]"
            elif isinstance(v, dict) and k == "files":
                v = {
                    str(name): (content[:400] + "...[truncated]"
                                if isinstance(content, str) and len(content) > 400 else content)
                    for name, content in list(v.items())[:20]
                }
            out[str(k)] = v
        return out
    return args


class Gate:
    """Permission & approval gate for one run."""

    def __init__(
        self,
        emit: Emit,
        *,
        auto_approve: Callable[[], set[str]] | None = None,
        on_wait: Callable[[dict[str, Any]], None] | None = None,
        on_resume: Callable[[], None] | None = None,
        timeout: float | None = None,
        delegate: Callable[..., Any] | None = None,
    ) -> None:
        self._emit = emit
        # optional async ``delegate(approval) -> (allowed, message)``: lets a
        # host (e.g. a harness session) answer approvals itself.
        self._delegate = delegate
        self._auto_approve = auto_approve or (lambda: set())
        self._on_wait = on_wait
        self._on_resume = on_resume
        self.timeout = APPROVAL_TIMEOUT if timeout is None else timeout
        self.pending: dict[str, Any] | None = None
        self._future: asyncio.Future | None = None
        self._lock = asyncio.Lock()
        self._approved: set[tuple[str, str]] = set()
        self.denied = 0

    def auto_approved(self) -> set[str]:
        try:
            return {p for p in self._auto_approve() if p in APPROVAL_REQUIRED}
        except Exception:
            return set()

    async def authorize(
        self, step_id: str, cap: Capability, args: dict[str, Any], reason: str = ""
    ) -> tuple[bool, str]:
        """Return ``(allowed, message)`` for running ``cap`` as step ``step_id``."""
        auto = self.auto_approved()
        need = cap.approval_permissions(auto)
        if not need:
            auto_used = [p for p in cap.permissions if p in APPROVAL_REQUIRED]
            detail = "allowed (auto-approved: " + ", ".join(auto_used) + ")" if auto_used else "allowed"
            self._emit(_event("gate", "allowed", detail, step_id=step_id, capability=cap.name))
            return True, ""
        # one approval covers every fan-out item of the same step
        key = (step_id.split("[", 1)[0], cap.name)
        if key in self._approved:
            self._emit(_event("gate", "allowed", "allowed (approved earlier for this step)",
                              step_id=step_id, capability=cap.name))
            return True, ""
        async with self._lock:
            if key in self._approved:
                self._emit(_event("gate", "allowed", "allowed (approved earlier for this step)",
                                  step_id=step_id, capability=cap.name))
                return True, ""
            approval = {
                "id": uuid.uuid4().hex[:12],
                "step_id": step_id,
                "capability": cap.name,
                "args": _args_preview(args),
                "permissions": need,
                "reason": (reason or cap.card)[:500],
                "requested_at": time.time(),
            }
            if self._delegate is not None:
                self._emit(_event("gate", "awaiting_approval",
                                  f"approval required for {', '.join(need)} (delegated)",
                                  step_id=step_id, capability=cap.name,
                                  approval_id=approval["id"]))
                approved, message = await self._delegate(approval)
                if approved:
                    self._approved.add(key)
                    self._emit(_event("approval", "approved", f"approved {cap.name}",
                                      step_id=step_id, capability=cap.name,
                                      approval_id=approval["id"]))
                    return True, ""
                self.denied += 1
                message = message or "approval denied"
                self._emit(_event("approval", "denied", message, step_id=step_id,
                                  capability=cap.name, approval_id=approval["id"]))
                return False, message
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self.pending = approval
            self._future = future
            if self._on_wait:
                self._on_wait(approval)
            self._emit(_event("gate", "awaiting_approval",
                              f"approval required for {', '.join(need)}",
                              step_id=step_id, capability=cap.name, approval_id=approval["id"]))
            timed_out = False
            try:
                approved = bool(await asyncio.wait_for(asyncio.shield(future), self.timeout))
            except (asyncio.TimeoutError, TimeoutError):
                approved, timed_out = False, True
            finally:
                self.pending = None
                self._future = None
                if self._on_resume:
                    self._on_resume()
        if approved:
            self._approved.add(key)
            self._emit(_event("approval", "approved", f"approved {cap.name}",
                              step_id=step_id, capability=cap.name, approval_id=approval["id"]))
            return True, ""
        self.denied += 1
        message = "approval denied (timed out)" if timed_out else "approval denied"
        self._emit(_event("approval", "denied", message, step_id=step_id, capability=cap.name,
                          approval_id=approval["id"]))
        return False, message

    def resolve(self, approval_id: str, approve: bool) -> bool:
        """Answer the pending approval; False if ``approval_id`` is not pending."""
        if not self.pending or self.pending.get("id") != approval_id:
            return False
        future = self._future
        if future is not None and not future.done():
            future.set_result(bool(approve))
        return True
