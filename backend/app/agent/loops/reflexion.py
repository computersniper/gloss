"""``loop:reflexion``: plan-execute-verify with verbal self-reflection.

After each round the answer's claims and verdicts are critiqued into a few
short lessons (Reflexion, Shinn et al. 2023). The lessons stay in the working
context, so the next plan is made knowing what went wrong. The loop stops when
the critique says the goal is met, the coverage threshold is reached, rounds or
budget run out, or coverage has not improved for ``stop.patience`` rounds.
"""
from __future__ import annotations

import asyncio

from ...features.common import json_complete
from .. import core
from ..context import wrap_untrusted
from ..core import RunState, event

DEFAULT_PATIENCE = 1
MAX_LESSONS = 3

CRITIQUE_SYSTEM = (
    "You are the self-reflection step of GlossX, a research agent. Look at the "
    "goal, the answer's claims with their verifier verdicts and the failed steps. "
    "Write at most three short lessons: what went wrong or is still missing, and "
    "what to do differently next round (other queries, other papers, other "
    "capabilities). Set done to true only if every sub-question is answered by "
    "supported claims."
)
CRITIQUE_SHAPE = '{"done": false, "lessons": ["..."]}'


async def critique(state: RunState) -> tuple[bool, list[str]]:
    claim_lines = "\n".join(f"- [{c['verdict']}] {c['text']}" for c in state.claims) or "(no claims)"
    user = (
        f"{state.wc.render(max_tokens=4000)}\n\nCURRENT CLAIMS AND VERDICTS:\n"
        f"{wrap_untrusted('draft-claims', claim_lines)}"
    )
    out = await json_complete(core.system_prompt(CRITIQUE_SYSTEM), user, CRITIQUE_SHAPE,
                              provider=state.provider)
    out = out if isinstance(out, dict) else {}
    lessons = out.get("lessons") if isinstance(out.get("lessons"), list) else []
    lessons = [" ".join(str(x).split())[:300] for x in lessons if str(x).strip()][:MAX_LESSONS]
    return out.get("done") is True, lessons


async def run(state: RunState) -> None:
    wc, emit, meter = state.wc, state.emit, state.meter

    if not state.workflow_ran:
        await core.gather_evidence(state)
    await core.write_and_verify(state, 1, first=True)
    state.rounds = 1
    threshold = state.stop.get("coverage")
    patience = int(state.stop.get("patience") or DEFAULT_PATIENCE)
    best = core.coverage(state.claims)
    stale = 0

    while True:
        if isinstance(threshold, (int, float)) and state.claims and best >= threshold:
            state.stop_reason = "goal_met"
            emit(event("reflect", "completed", f"coverage {best:.2f} >= {threshold}; stopping"))
            break
        if meter.exhausted or meter.steps_remaining <= 0:
            state.stop_reason = "budget"
            emit(event("reflect", "info", "budget exhausted; stopping"))
            break
        if state.rounds >= state.max_rounds:
            state.stop_reason = "max_rounds"
            emit(event("reflect", "info", f"reached max rounds ({state.max_rounds}); stopping"))
            break
        emit(event("reflect", "started", "self-reflection: what went wrong this round"))
        try:
            done, lessons = await critique(state)
        except (asyncio.CancelledError, core.BudgetExceeded):
            raise
        except Exception as error:
            state.stop_reason = "error"
            emit(event("reflect", "failed", f"reflection failed: {core.err(error)}"))
            break
        if done:
            state.stop_reason = "goal_met"
            emit(event("reflect", "completed", "self-reflection: the goal is met"))
            break
        wc.reflections.extend(lessons)
        emit(event("reflect", "completed",
                   f"{len(lessons)} lesson(s): " + " | ".join(lessons)[:500] if lessons else "no lessons"))

        steps = await core.plan(state)
        if not steps:
            state.stop_reason = "no_progress"
            emit(event("reflect", "info", "the new plan has no usable steps; stopping"))
            break
        state.rounds += 1
        for s in steps:
            s["round"] = state.rounds
        wc.plan.extend(steps)
        state.publish_plan()
        denied_before = state.gate.denied
        ok = await core.execute(state, steps)
        state.publish_plan()
        if ok == 0:
            state.stop_reason = "approval_needed" if state.gate.denied > denied_before else "no_progress"
            emit(event("reflect", "info", "no new step succeeded; keeping previous answer"))
            break
        if not await core.write_and_verify(state, state.rounds, first=False):
            break
        cov = core.coverage(state.claims)
        if cov > best + 1e-9:
            best, stale = cov, 0
        else:
            stale += 1
            emit(event("reflect", "info", f"coverage did not improve ({cov:.2f}); "
                                          f"{stale}/{patience} round(s) without progress"))
            if stale >= patience:
                state.stop_reason = "no_progress"
                break
