"""Pluggable deciders: typed choices and claim judgements for the agent.

A decider answers two questions the loop keeps asking:

* ``decide(question, options, context) -> {"choice", "confidence", "reason"}``
  (routing, screening, picking the next ReAct action, stopping), and
* ``judge(claim, evidence) -> {"verdict", "confidence", "note"}`` with
  ``verdict`` in ``supported|partial|unsupported``.

``llm-json`` is the default implementation. Other deciders (e.g. Jev) can be
registered by id with :func:`register_decider`.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

from ..features.common import json_complete
from .context import UNTRUSTED_RULE, wrap_untrusted

VERDICTS = ("supported", "partial", "unsupported")

DECIDE_SYSTEM = (
    "You are the decider of GlossX, a research agent. Pick exactly one option "
    "id that best answers the question, given the context. Be calibrated: "
    "confidence is your probability that the choice is right."
)
DECIDE_SHAPE = '{"choice": "<one option id>", "confidence": 0.0, "reason": "one sentence"}'

JUDGE_SYSTEM = (
    "You are a strict judge. Decide whether the evidence supports the claim: "
    "'supported' (fully entailed), 'partial' (partly or loosely), or "
    "'unsupported'. Judge only from the evidence, not outside knowledge. "
    "Confidence is your calibrated probability that the verdict is right."
)
JUDGE_SHAPE = '{"verdict": "supported|partial|unsupported", "confidence": 0.0, "note": "short reason"}'


class Decider(Protocol):
    id: str

    async def decide(self, question: str, options: list[Any], context: str = "") -> dict[str, Any]:
        ...

    async def judge(self, claim: str, evidence: str) -> dict[str, Any]:
        ...


def _options(options: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for opt in options:
        if isinstance(opt, dict):
            oid = str(opt.get("id") or opt.get("name") or "").strip()
            card = str(opt.get("card") or opt.get("description") or "")
        else:
            oid, card = str(opt).strip(), ""
        if oid and oid not in seen:
            seen.add(oid)
            out.append({"id": oid, "card": " ".join(card.split())[:300]})
    return out


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class LLMJsonDecider:
    """Default decider: one JSON-mode LLM call per decision."""

    id = "llm-json"

    def __init__(self, provider: str | None = None) -> None:
        self.provider = provider

    async def decide(self, question: str, options: list[Any], context: str = "") -> dict[str, Any]:
        opts = _options(options)
        if not opts:
            raise ValueError("decide() needs at least one option")
        if len(opts) == 1:
            return {"choice": opts[0]["id"], "confidence": 1.0, "reason": "only option"}
        listing = "\n".join(f"- {o['id']}: {o['card']}" if o["card"] else f"- {o['id']}" for o in opts)
        user = f"QUESTION:\n{question}\n\nOPTIONS:\n{listing}"
        if context:
            user += f"\n\nCONTEXT:\n{wrap_untrusted('decider-context', context)}"
        out = await json_complete(
            f"{DECIDE_SYSTEM}\n\n{UNTRUSTED_RULE}", user, DECIDE_SHAPE, provider=self.provider
        )
        out = out if isinstance(out, dict) else {}
        choice = str(out.get("choice") or "").strip()
        ids = [o["id"] for o in opts]
        if choice not in ids:
            # tolerate decorated answers such as "workflow.x (best)"
            match = next((i for i in ids if i and i in choice), None)
            if match is None:
                return {"choice": ids[0], "confidence": 0.0,
                        "reason": f"invalid choice {choice!r}; defaulted to first option"}
            choice = match
        return {
            "choice": choice,
            "confidence": _confidence(out.get("confidence")),
            "reason": str(out.get("reason") or "")[:500],
        }

    async def judge(self, claim: str, evidence: str) -> dict[str, Any]:
        user = (
            f"CLAIM:\n{claim}\n\nEVIDENCE:\n{wrap_untrusted('evidence', evidence or '(none)')}"
        )
        out = await json_complete(
            f"{JUDGE_SYSTEM}\n\n{UNTRUSTED_RULE}", user, JUDGE_SHAPE, provider=self.provider
        )
        out = out if isinstance(out, dict) else {}
        verdict = str(out.get("verdict") or "").strip().lower()
        return {
            "verdict": verdict if verdict in VERDICTS else "partial",
            "confidence": _confidence(out.get("confidence")),
            "note": str(out.get("note") or "")[:300],
        }


_DECIDERS: dict[str, Callable[..., Decider]] = {"llm-json": LLMJsonDecider}
DEFAULT_DECIDER = "llm-json"


def register_decider(decider_id: str, factory: Callable[..., Decider]) -> None:
    _DECIDERS[decider_id] = factory


def available() -> list[str]:
    return list(_DECIDERS)


def get_decider(decider_id: str | None = None, *, provider: str | None = None) -> Decider:
    factory = _DECIDERS.get(decider_id or DEFAULT_DECIDER)
    if factory is None:
        raise ValueError(f"unknown decider: {decider_id}")
    return factory(provider=provider)
