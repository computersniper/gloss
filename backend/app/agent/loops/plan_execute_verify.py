"""``loop:plan-execute-verify``: plan -> execute -> synthesize -> verify -> reflect.

This is the original GlossX loop. One round plans a step graph, executes it in
dependency waves, writes a cited answer and verifies each claim. Reflection may
replan (new steps) until the goal is met, the workflow's coverage threshold is
reached, rounds or budget run out, or no progress is made.
"""
from __future__ import annotations

import asyncio

from .. import core
from ..core import RunState, event


async def run(state: RunState) -> None:
    wc, emit, meter = state.wc, state.emit, state.meter

    if not state.workflow_ran:
        await core.gather_evidence(state)

    await core.write_and_verify(state, 1, first=True)
    state.rounds = 1
    threshold = state.stop.get("coverage")

    while True:
        if isinstance(threshold, (int, float)) and state.claims:
            cov = core.coverage(state.claims)
            if cov >= threshold:
                state.stop_reason = "goal_met"
                emit(event("reflect", "completed", f"coverage {cov:.2f} >= {threshold}; stopping"))
                break
        if meter.exhausted or meter.steps_remaining <= 0:
            state.stop_reason = "budget"
            emit(event("reflect", "info", "budget exhausted; stopping"))
            break
        if state.rounds >= state.max_rounds:
            state.stop_reason = "max_rounds"
            emit(event("reflect", "info", f"reached max rounds ({state.max_rounds}); stopping"))
            break
        try:
            decision, reason, raw_steps = await core.reflect(state, state.max_rounds - state.rounds)
        except (asyncio.CancelledError, core.BudgetExceeded):
            raise
        except Exception as error:
            state.stop_reason = "error"
            emit(event("reflect", "failed", f"reflection failed: {core.err(error)}"))
            break
        if decision == "stop":
            state.stop_reason = "goal_met"
            emit(event("reflect", "completed", f"stop: {reason}"))
            break
        taken = {s["id"] for s in wc.plan}
        new_steps = core.normalize_steps(raw_steps, state, prefix=f"r{state.rounds + 1}-",
                                         taken=taken, phase="reflect")
        if not new_steps:
            state.stop_reason = "no_progress"
            emit(event("reflect", "completed", f"replan requested without usable steps: {reason}"))
            break
        state.rounds += 1
        emit(event("reflect", "completed", f"replan (round {state.rounds}): {reason}"))
        for s in new_steps:
            s["round"] = state.rounds
        wc.plan.extend(new_steps)
        state.publish_plan()
        evidence_before = len(wc.evidence_ids)
        denied_before = state.gate.denied
        ok = await core.execute(state, new_steps)
        state.publish_plan()
        if ok == 0:
            if state.gate.denied > denied_before:
                state.stop_reason = "approval_needed"
                emit(event("reflect", "info", "needed steps were not approved; stopping"))
            else:
                state.stop_reason = "no_progress"
                emit(event("reflect", "info", "no new step succeeded; keeping previous answer"))
            break
        if not await core.write_and_verify(state, state.rounds, first=False):
            break
        if len(wc.evidence_ids) == evidence_before:
            emit(event("reflect", "info", "replan added no new evidence spans"))
