"""Loop plugins, registered by id ``loop:<name>``.

A loop drives a :class:`~app.agent.core.RunState` to a verified answer. If a
workflow already ran its steps (``state.workflow_ran``), the loop continues
with synthesize -> verify -> reflect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from . import plan_execute_verify, react, reflexion


@dataclass(frozen=True)
class LoopPlugin:
    id: str  # short id, e.g. "react"; the registry id is "loop:<id>"
    card: str
    run: Callable[..., Awaitable[None]]


LOOPS: dict[str, LoopPlugin] = {
    "plan-execute-verify": LoopPlugin(
        id="plan-execute-verify",
        card=(
            "Plan a parallel step graph, execute it, write a cited answer, verify every "
            "claim, then reflect and replan until coverage is met or rounds run out."
        ),
        run=plan_execute_verify.run,
    ),
    "react": LoopPlugin(
        id="react",
        card=(
            "Iterative ReAct: pick one capability per turn from the top cards, observe "
            "the result, repeat until the decider says done; then write and verify."
        ),
        run=react.run,
    ),
    "reflexion": LoopPlugin(
        id="reflexion",
        card=(
            "Plan-execute-verify with self-reflection: after each round, critique the "
            "verdicts into lessons and replan with them; stop when coverage stops improving."
        ),
        run=reflexion.run,
    ),
}
DEFAULT_LOOP = "plan-execute-verify"


def normalize_loop_id(value: str | None) -> str | None:
    """Map ``"auto"``/``None`` to None, ``"loop:react"`` to ``"react"``; validate."""
    text = str(value or "auto").strip()
    if text == "auto":
        return None
    if text.startswith("loop:"):
        text = text[5:]
    if text not in LOOPS:
        raise ValueError(f"unknown loop: {value}")
    return text


def list_loops() -> list[dict[str, str]]:
    return [{"id": loop.id, "card": loop.card} for loop in LOOPS.values()]
