"""``loop:react``: one capability per turn, chosen by the decider from the
top-k cards, until the decider says done (or budget / turns run out); then the
answer is written and verified like in plan-execute-verify.
"""
from __future__ import annotations

import asyncio
import json

from ...features.common import json_complete
from .. import core
from ..core import RunState, event

TOP_K = 8
TURNS_PER_ROUND = 6
MAX_FAILURES = 3
DONE = "done"

ARGS_SYSTEM = (
    "You fill in the arguments for one capability call of GlossX, a research "
    "agent. Use only the argument names in the schema; use paper ids exactly as "
    "listed in the scope or observations. Return only the JSON object."
)
ARGS_SHAPE = '{"args": {}, "purpose": "why this call helps"}'


async def _fill_args(state: RunState, cap) -> tuple[dict, str]:
    if not cap.input_schema:
        return {}, cap.card
    user = (
        f"GOAL:\n{state.wc.goal}\n\nSCOPE PAPERS:\n{core.scope_text(state.ctx.paper_ids)}\n\n"
        f"CAPABILITY: {cap.name}: {cap.card}\n"
        f"ARGUMENT SCHEMA: {json.dumps(cap.input_schema, ensure_ascii=False)}\n\n"
        f"WHAT IS KNOWN SO FAR:\n{state.wc.render(max_tokens=2500)}"
    )
    out = await json_complete(core.system_prompt(ARGS_SYSTEM), user, ARGS_SHAPE, provider=state.provider)
    out = out if isinstance(out, dict) else {}
    args = out.get("args") if isinstance(out.get("args"), dict) else {}
    return args, str(out.get("purpose") or "")[:300]


async def run(state: RunState) -> None:
    wc, emit, meter, registry = state.wc, state.emit, state.meter, state.registry

    if not state.workflow_ran:
        seed = core.seed_step(state)
        if seed:
            wc.plan.append(seed)
            state.publish_plan()
            await core.execute(state, [seed])
            state.publish_plan()

    max_turns = TURNS_PER_ROUND * max(1, state.max_rounds)
    failures = 0
    last_denied = False
    stop_reason = "max_rounds"
    for turn in range(1, max_turns + 1):
        if meter.exhausted or meter.steps_remaining <= 0:
            stop_reason = "budget"
            emit(event("reflect", "info", "budget exhausted; stopping"))
            break
        last = wc.observations[-1]["summary"][:300] if wc.observations else ""
        candidates = core.planner_ranked(state, f"{wc.goal} {last}", TOP_K)
        options = [{"id": c.name, "card": c.card} for c in candidates]
        options.append({"id": DONE, "card": "The observations already answer the goal; stop and write the answer."})
        emit(event("plan", "started", f"turn {turn}: choosing the next action"))
        try:
            decision = await state.decider.decide(
                "Which single capability should run next to make progress on the goal? "
                "Choose 'done' when the evidence gathered is enough.",
                options,
                wc.render(max_tokens=3000),
            )
        except (asyncio.CancelledError, core.BudgetExceeded):
            raise
        except Exception as error:
            stop_reason = "error"
            emit(event("plan", "failed", f"decider failed: {core.err(error)}"))
            break
        choice = decision["choice"]
        emit(event("plan", "completed",
                   f"turn {turn}: {choice} (confidence {decision['confidence']:.2f}) {decision['reason']}"))
        if choice == DONE:
            stop_reason = "goal_met"
            break
        cap = registry.get(choice)
        if cap is None:
            failures += 1
            continue
        try:
            args, purpose = await _fill_args(state, cap)
        except (asyncio.CancelledError, core.BudgetExceeded):
            raise
        except Exception as error:
            emit(event("plan", "failed", f"could not fill arguments: {core.err(error)}",
                       capability=cap.name))
            failures += 1
            if failures >= MAX_FAILURES:
                stop_reason = "no_progress"
                break
            continue
        step = {
            "id": f"t{turn}",
            "capability": cap.name,
            "args": args,
            "purpose": purpose,
            "depends_on": [],
            "status": "pending",
            "round": 1 + (turn - 1) // TURNS_PER_ROUND,
        }
        wc.plan.append(step)
        state.publish_plan()
        ok = await core.execute(state, [step])
        state.publish_plan()
        last_denied = bool(step.get("denied"))
        failures = 0 if ok else failures + 1
        if failures >= MAX_FAILURES:
            stop_reason = "approval_needed" if last_denied else "no_progress"
            emit(event("reflect", "info", f"{failures} failed turns in a row; stopping"))
            break

    turns_run = sum(1 for s in wc.plan if s["id"].startswith("t"))
    state.rounds = max(1, min(state.max_rounds, 1 + max(0, turns_run - 1) // TURNS_PER_ROUND))
    await core.write_and_verify(state, state.rounds, first=True)
    if state.stop_reason is None:
        state.stop_reason = stop_reason
