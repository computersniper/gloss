"""Shared machinery for agent loops: run state, gated step execution, and the
internal planner / writer (synthesize) / verifier / reflect roles.

Loop plugins (``loops/``) and the workflow engine (``workflow.py``) compose
these pieces. Every step passes the permission gate and the budget meter and
is traced as events; step failures become observations.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import output_language
from ..features.common import json_complete
from ..library import store
from ..providers import registry as providers
from .capabilities import CapabilityRegistry, RunContext
from .context import UNTRUSTED_RULE, SpanStore, WorkingContext, wrap_untrusted
from .gate import BudgetExceeded, BudgetMeter, Gate

MAX_PLAN_STEPS = 8
MAX_STEP_EXECUTIONS = 16
STEP_TIMEOUT = 180.0
MAX_CLAIMS = 10
MAX_EVIDENCE_SPANS = 24
EVIDENCE_TOKENS = 3500
VERDICTS = {"supported", "partial", "unsupported"}
STOP_REASONS = (
    "goal_met", "max_rounds", "budget", "no_progress", "approval_needed", "error", "cancelled",
)
# capability kinds the planner never calls directly
PLANNER_EXCLUDED_KINDS = {"workflow", "loop", "decider", "provider", "view"}

CITE = re.compile(r"\[S:([^\]\s]+)\]")

Emit = Callable[[dict[str, Any]], None]

PLAN_SYSTEM = (
    "You are the planner of GlossX, a research agent over a local library of "
    "academic papers. Break the goal into sub-questions and choose a short plan "
    "of capability calls that gathers the evidence needed to answer it. Use only "
    "the listed capabilities and their argument names. Prefer cheap capabilities "
    "(cost free) and paper.search_spans for factual questions; use LLM or network "
    "capabilities only when they add something the evidence cannot. Capabilities "
    "marked 'needs approval' pause for the user; use them only when necessary. "
    "Steps without dependencies run in parallel; arguments are static (no "
    "references to other steps' outputs)."
)
PLAN_SHAPE = (
    '{"sub_questions": ["..."], "steps": [{"id": "s1", "capability": "<name>", '
    '"args": {}, "purpose": "why this step", "depends_on": []}]}'
)

SYNTH_SYSTEM = (
    "You are GlossX, a careful research agent. Answer the goal using ONLY the "
    "evidence spans provided. Cite inline after each supported statement with the "
    "exact markers given, e.g. [S:abc#12]. Cite only span ids that appear in the "
    "EVIDENCE section; never invent ids. If the evidence is insufficient, say so "
    "plainly rather than guessing. List the answer's key factual claims separately, "
    "each with the span ids that support it."
)
SYNTH_SHAPE = (
    '{"answer": "<markdown with inline citations like [S:<span_id>]>", '
    '"claims": [{"text": "...", "span_ids": ["..."]}]}'
)

VERIFY_SYSTEM = (
    "You are a strict fact checker. For each numbered claim decide whether its "
    "cited evidence spans support it: 'supported' (fully entailed), 'partial' "
    "(some parts supported, or only loosely), or 'unsupported'. Judge only from "
    "the given spans, not from outside knowledge."
)
VERIFY_SHAPE = (
    '{"verdicts": [{"i": 0, "verdict": "supported|partial|unsupported", '
    '"confidence": 0.0, "note": "short reason"}]}'
)

REPAIR_SYSTEM = (
    "You repair a research answer after fact checking. Some of its claims were "
    "judged unsupported by the cited evidence. Rewrite the answer so that it no "
    "longer states those claims: delete them, or say plainly that the evidence "
    "does not establish them. Keep every supported statement and its citation "
    "markers exactly as written; never add new facts or new citation markers."
)
REPAIR_SHAPE = '{"answer": "<the repaired markdown answer>"}'

REFLECT_SYSTEM = (
    "You are the reflection step of GlossX, a research agent. Decide whether the "
    "current answer adequately covers every sub-question with supported claims. "
    "Choose 'stop' if it does, or if more work is unlikely to help. Choose "
    "'replan' only when specific additional capability calls would close a "
    "concrete gap (uncovered sub-question, unsupported claim, failed step), and "
    "give those calls as new_steps."
)
REFLECT_SHAPE = (
    '{"decision": "stop|replan", "reason": "...", "new_steps": [{"id": "r1", '
    '"capability": "<name>", "args": {}, "purpose": "...", "depends_on": []}]}'
)


def system_prompt(base: str) -> str:
    return f"{base}\n\n{UNTRUSTED_RULE}"


@dataclass
class RunState:
    """Everything one run's loop needs; loops mutate it in place."""

    run: Any
    registry: CapabilityRegistry
    spans: SpanStore
    ctx: RunContext
    wc: WorkingContext
    meter: BudgetMeter
    gate: Gate
    decider: Any
    emit: Emit
    max_rounds: int = 2
    stop: dict[str, Any] = field(default_factory=dict)
    draft: dict[str, Any] | None = None
    claims: list[dict] = field(default_factory=list)
    rounds: int = 0
    stop_reason: str | None = None
    workflow_ran: bool = False
    # set by a workflow's ``agent.writer`` step: how this kind of answer is written
    writer_instructions: str = ""
    # claims the verifier rejected and :func:`repair` removed from the answer
    rejected_claims: list[dict] = field(default_factory=list)

    @property
    def provider(self) -> str | None:
        return self.ctx.provider

    def publish_plan(self) -> None:
        self.run.plan = {
            "sub_questions": list(self.wc.sub_questions),
            "steps": [dict(s) for s in self.wc.plan],
        }


def event(
    phase: str,
    status: str,
    detail: str,
    *,
    step_id: str | None = None,
    capability: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    ev: dict[str, Any] = {"ts": time.time(), "phase": phase, "status": status, "detail": detail}
    if step_id is not None:
        ev["step_id"] = step_id
    if capability is not None:
        ev["capability"] = capability
    ev.update({k: v for k, v in extra.items() if v is not None})
    return ev


def err(error: BaseException) -> str:
    text = str(error) or type(error).__name__
    return text[:500]


def planner_kinds_excluded(state: RunState) -> set[str]:
    return set(PLANNER_EXCLUDED_KINDS)


FULL_SCHEMA_CARDS = 8


def planner_can_use(state: RunState, cap) -> bool:
    """Project memory needs a project; the learner model is global."""
    if cap.kind in PLANNER_EXCLUDED_KINDS:
        return False
    return state.ctx.project_id is not None or not cap.name.startswith("memory.")


def planner_ranked(state: RunState, query: str, k: int) -> list:
    """Capabilities the planner may use, best BM25 match first."""
    ranked = state.registry.ranked(query, k + 8, exclude_kinds=planner_kinds_excluded(state))
    return [cap for cap in ranked if planner_can_use(state, cap)][:k]


def cards_text(state: RunState, query: str, k: int = 16) -> str:
    """Planner-facing capability list, best BM25 match first.

    Progressive disclosure: the top ``FULL_SCHEMA_CARDS`` capabilities carry
    their full argument schema; the rest list only argument names, which keeps
    large external schemas (MCP tools) out of the planner's window.
    """
    auto = state.gate.auto_approved()
    lines = []
    ranked = planner_ranked(state, query, k)
    for n, cap in enumerate(ranked):
        if n < FULL_SCHEMA_CARDS:
            schema = json.dumps(cap.input_schema, ensure_ascii=False)
        else:
            schema = "[" + ", ".join(cap.input_schema) + "]"
        flags = ""
        if cap.approval_permissions(auto):
            flags += ", needs approval"
        if cap.side_effects:
            flags += f", side effect: {cap.side_effects}"
        lines.append(f"- {cap.name} ({cap.kind}, cost {cap.cost}{flags}): {cap.card} args: {schema}")
    return "\n".join(lines)


def scope_text(paper_ids: list[str]) -> str:
    if not paper_ids:
        return "(no scope papers selected; the whole library and external search are available)"
    lines = []
    for pid in paper_ids[:20]:
        paper = store.get_paper(pid) or {}
        lines.append(f"- {pid}: {paper.get('title') or '(untitled)'} ({paper.get('year') or 'n.d.'})")
    return wrap_untrusted("library-titles", "\n".join(lines))


def normalize_steps(
    raw: Any,
    state: RunState,
    *,
    prefix: str,
    taken: set[str],
    phase: str,
) -> list[dict]:
    """Validate planner output: known capabilities, unique ids, sane deps."""
    registry, emit = state.registry, state.emit
    steps: list[dict] = []
    if not isinstance(raw, list):
        return steps
    ids_in_plan: set[str] = set()
    for i, s in enumerate(raw):
        if len(steps) >= MAX_PLAN_STEPS:
            emit(event(phase, "info", f"plan truncated to {MAX_PLAN_STEPS} steps"))
            break
        if not isinstance(s, dict):
            continue
        name = str(s.get("capability") or "").strip()
        cap = registry.get(name)
        if not cap or not planner_can_use(state, cap):
            emit(event(phase, "info", f"dropped step with unknown capability '{name}'",
                       capability=name or None))
            continue
        sid = str(s.get("id") or "").strip()[:32]
        if not sid or sid in taken or sid in ids_in_plan or "[" in sid:
            n = i + 1
            sid = f"{prefix}{n}"
            while sid in taken or sid in ids_in_plan:
                n += 1
                sid = f"{prefix}{n}"
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        deps = s.get("depends_on") if isinstance(s.get("depends_on"), list) else []
        steps.append({
            "id": sid,
            "capability": name,
            "args": args,
            "purpose": str(s.get("purpose") or "")[:300],
            "depends_on": [str(d) for d in deps],
            "status": "pending",
        })
        ids_in_plan.add(sid)
    for st in steps:
        st["depends_on"] = [d for d in st["depends_on"] if d in ids_in_plan and d != st["id"]]
    return steps


def summarize_result(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("spans", "papers", "results", "references", "findings", "notes"):
            if isinstance(result.get(key), list):
                return f"{len(result[key])} {key}"
        if "status" in result:
            return f"status {result['status']}"
        return ", ".join(list(result.keys())[:6])
    if isinstance(result, list):
        return f"{len(result)} items"
    return " ".join(str(result).split())[:160]


async def run_step(state: RunState, step: dict, *, context_mode: str | None = None) -> Any:
    """Gate, run and observe one step. Never raises except on cancellation /
    LLM-budget exhaustion; returns the result or ``None`` on failure."""
    registry, ctx, wc, emit = state.registry, state.ctx, state.wc, state.emit
    cap = registry.resolve(step["capability"])
    step["status"] = "running"

    def fail(message: str) -> None:
        step["status"] = "failed"
        step["error"] = message
        wc.observe(step["id"], step["capability"], "failed", error=message)
        emit(event("execute", "failed", message, step_id=step["id"], capability=step["capability"]))

    if cap is None:
        fail("unknown capability")
        return None
    allowed, message = await state.gate.authorize(
        step["id"], cap, step.get("args") or {}, step.get("purpose") or ""
    )
    if not allowed:
        step["denied"] = True
        fail(message or "approval denied")
        return None
    emit(event("execute", "started", step.get("purpose") or cap.name,
               step_id=step["id"], capability=cap.name))
    if cap.trust == "external":
        emit(event("adapter", "call", f"{cap.source} adapter: {cap.name}",
                   step_id=step["id"], capability=cap.name))
    timeout = max(1.0, float(cap.timeout or STEP_TIMEOUT))
    try:
        paper_id = (step.get("args") or {}).get("paper_id")
        if context_mode == "shared_session" and isinstance(paper_id, str) and paper_id:
            with providers.feature_context(paper_id=paper_id, context_mode="shared_session"):
                result = await asyncio.wait_for(cap.handler(step.get("args") or {}, ctx), timeout)
        else:
            result = await asyncio.wait_for(cap.handler(step.get("args") or {}, ctx), timeout)
    except asyncio.CancelledError:
        raise
    except BudgetExceeded as error:
        fail(f"budget exceeded: {err(error)}")
        return None
    except Exception as error:
        message = (
            f"timed out after {int(timeout)}s"
            if isinstance(error, (asyncio.TimeoutError, TimeoutError))
            else err(error)
        )
        fail(message)
        return None
    step["status"] = "completed"
    wc.observe(step["id"], step["capability"], "completed", result=result)
    emit(event("execute", "completed", summarize_result(result),
               step_id=step["id"], capability=step["capability"]))
    return result


async def execute(state: RunState, steps: list[dict]) -> int:
    """Run steps in dependency waves; return how many completed successfully."""
    emit, meter = state.emit, state.meter
    pending = {s["id"]: s for s in steps}
    finished: set[str] = set()
    ok = 0
    while pending:
        ready = [s for s in pending.values() if all(d in finished for d in s["depends_on"])]
        if not ready:
            for s in pending.values():
                s["status"] = "skipped"
                emit(event("execute", "info", "skipped: unresolvable dependencies",
                           step_id=s["id"], capability=s["capability"]))
            break
        if meter.exhausted:
            for s in pending.values():
                s["status"] = "skipped"
            emit(event("execute", "info", f"budget exhausted; skipped {len(pending)} step(s)"))
            break
        granted = meter.take_steps(len(ready))
        if granted <= 0:
            for s in pending.values():
                s["status"] = "skipped"
            emit(event("execute", "info",
                       f"step budget exhausted ({meter.max_steps}); skipped {len(pending)} step(s)"))
            break
        ready = ready[:granted]
        await asyncio.gather(*(run_step(state, s) for s in ready))
        for s in ready:
            finished.add(s["id"])
            pending.pop(s["id"], None)
            ok += s["status"] == "completed"
    return ok


async def plan(state: RunState) -> list[dict]:
    wc, ctx, emit = state.wc, state.ctx, state.emit
    emit(event("plan", "started", "planning"))
    user = (
        f"GOAL:\n{wc.goal}\n\nSCOPE PAPERS:\n{scope_text(ctx.paper_ids)}\n\n"
        f"CAPABILITIES:\n{cards_text(state, wc.goal)}\n\n"
        f"WHAT IS KNOWN SO FAR:\n{wc.render(max_tokens=2000)}\n\n"
        f"Produce at most {MAX_PLAN_STEPS} steps. Use paper ids exactly as listed."
    )
    try:
        out = await json_complete(system_prompt(PLAN_SYSTEM), user, PLAN_SHAPE, provider=state.provider)
    except BudgetExceeded:
        raise
    except Exception as error:
        emit(event("plan", "failed", f"planner failed, continuing with seed evidence: {err(error)}"))
        return []
    out = out if isinstance(out, dict) else {}
    subs = out.get("sub_questions")
    wc.sub_questions = [str(q)[:300] for q in subs if str(q).strip()][:8] if isinstance(subs, list) else []
    taken = {s["id"] for s in wc.plan}
    steps = normalize_steps(out.get("steps"), state, prefix="s", taken=taken, phase="plan")
    emit(event("plan", "completed",
               f"{len(wc.sub_questions)} sub-question(s), {len(steps)} step(s)"))
    return steps


def provided_ids(evidence: str) -> list[str]:
    return list(dict.fromkeys(CITE.findall(evidence)))


def evidence_block(state: RunState) -> tuple[str, set[str]]:
    ids = state.wc.evidence_ids
    if len(ids) > MAX_EVIDENCE_SPANS:
        ids = ids[:8] + ids[-(MAX_EVIDENCE_SPANS - 8):]
    text = state.spans.materialize(ids, max_tokens=EVIDENCE_TOKENS)
    return text, set(provided_ids(text))


async def synthesize(state: RunState, round_no: int) -> dict:
    wc, emit = state.wc, state.emit
    emit(event("synthesize", "started", f"round {round_no}: writing grounded answer"))
    evidence, provided = evidence_block(state)
    user = (
        f"{wc.render(max_tokens=5000)}\n\n"
        f"EVIDENCE (cite only these ids):\n"
        f"{wrap_untrusted('evidence-spans', evidence) if evidence else '(no evidence spans were found)'}\n\n"
        f"Write the answer in: {output_language()}."
    )
    if state.writer_instructions:
        user += f"\n\nHOW TO WRITE THIS ANSWER (from the workflow):\n{state.writer_instructions}"
    out = await json_complete(system_prompt(SYNTH_SYSTEM), user, SYNTH_SHAPE, provider=state.provider)
    out = out if isinstance(out, dict) else {}
    answer = str(out.get("answer") or "").strip()

    answer = strip_citations(answer, provided)
    claims = []
    raw_claims = out.get("claims") if isinstance(out.get("claims"), list) else []
    for c in raw_claims[:MAX_CLAIMS]:
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            continue
        ids = c.get("span_ids") if isinstance(c.get("span_ids"), list) else []
        claims.append({
            "text": str(c["text"]).strip()[:1000],
            "span_ids": [str(s) for s in dict.fromkeys(ids) if str(s) in provided],
        })
    emit(event("synthesize", "completed",
               f"{len(claims)} claim(s) over {len(provided)} evidence span(s)"))
    return {"answer": answer, "claims": claims}


async def verify(state: RunState, draft: dict) -> list[dict]:
    spans, emit = state.spans, state.emit
    claims = [dict(c) for c in draft["claims"]]
    emit(event("verify", "started", f"checking {len(claims)} claim(s)"))
    for c in claims:
        c.update(verdict="unsupported", confidence=0.0, note="no valid citation")
    checkable = [i for i, c in enumerate(claims) if c["span_ids"]]
    if not checkable:
        emit(event("verify", "completed", "no cited claims to check"))
        return claims
    per_claim = max(300, 6000 // len(checkable))
    blocks = []
    for i in checkable:
        c = claims[i]
        blocks.append(
            f"CLAIM {i}: {c['text']}\nSPANS:\n"
            f"{wrap_untrusted('evidence-spans', spans.materialize(c['span_ids'], max_tokens=per_claim))}"
        )
    try:
        out = await json_complete(system_prompt(VERIFY_SYSTEM), "\n\n".join(blocks), VERIFY_SHAPE,
                                  provider=state.provider)
    except BudgetExceeded:
        raise
    except Exception as error:
        for i in checkable:
            claims[i].update(verdict="partial", confidence=0.0,
                             note=f"verification unavailable: {err(error)[:200]}")
        emit(event("verify", "failed", f"verifier failed: {err(error)}"))
        return claims
    rows = out.get("verdicts") if isinstance(out, dict) else None
    by_i: dict[int, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            by_i[int(row.get("i"))] = row
        except (TypeError, ValueError, AttributeError):
            continue
    for i in checkable:
        row = by_i.get(i)
        if not row:
            claims[i].update(verdict="partial", confidence=0.0, note="not graded by verifier")
            continue
        verdict = str(row.get("verdict") or "").strip().lower()
        try:
            confidence = max(0.0, min(1.0, float(row.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        claims[i].update(
            verdict=verdict if verdict in VERDICTS else "partial",
            confidence=confidence,
            note=str(row.get("note") or "")[:300],
        )
    tally = {v: sum(c["verdict"] == v for c in claims) for v in ("supported", "partial", "unsupported")}
    emit(event("verify", "completed",
               f"{tally['supported']} supported, {tally['partial']} partial, "
               f"{tally['unsupported']} unsupported"))
    return claims


def strip_citations(answer: str, allowed: set[str]) -> str:
    """Drop ``[S:id]`` markers whose id is not in ``allowed``."""

    def keep(match: re.Match) -> str:
        return match.group(0) if match.group(1) in allowed else ""

    cleaned = CITE.sub(keep, answer)
    if cleaned != answer:
        cleaned = re.sub(r"[ \t]+([.,;:!?)])", r"\1", cleaned)
    return cleaned


async def repair(state: RunState) -> None:
    """Remove claims the verifier judged unsupported from the final answer.

    The rejected claims leave ``state.claims`` (and so project memory) and are
    kept in ``state.rejected_claims``; the answer text is rewritten by the LLM so
    it no longer asserts them. If the rewrite is impossible (no budget, provider
    error) the answer text is kept and the event log says so.
    """
    bad = [c for c in state.claims if c.get("verdict") == "unsupported"]
    if not bad or state.draft is None:
        return
    emit = state.emit
    state.claims = [c for c in state.claims if c.get("verdict") != "unsupported"]
    state.rejected_claims = bad
    answer = str(state.draft.get("answer") or "")
    if not answer.strip():
        return
    emit(event("verify", "started", f"repairing the answer: removing {len(bad)} unsupported claim(s)"))
    if state.meter.exhausted:
        emit(event("verify", "info", "no budget left to rewrite the answer; unsupported claims are "
                                     "only dropped from the claim list"))
        return
    allowed = set(CITE.findall(answer))
    user = (
        f"ANSWER:\n{wrap_untrusted('draft-answer', answer)}\n\n"
        f"UNSUPPORTED CLAIMS (remove these):\n"
        f"{wrap_untrusted('unsupported-claims', chr(10).join('- ' + c['text'] for c in bad))}\n\n"
        f"Write the repaired answer in: {output_language()}."
    )
    try:
        out = await json_complete(system_prompt(REPAIR_SYSTEM), user, REPAIR_SHAPE, provider=state.provider)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # includes BudgetExceeded: repairing is best-effort
        emit(event("verify", "failed", f"could not rewrite the answer: {err(error)}"))
        return
    repaired = str((out or {}).get("answer") or "").strip() if isinstance(out, dict) else ""
    if not repaired:
        emit(event("verify", "failed", "repair returned an empty answer; keeping the original text"))
        return
    state.draft["answer"] = strip_citations(repaired, allowed)
    emit(event("verify", "completed", f"answer repaired; {len(bad)} unsupported claim(s) removed"))


def coverage(claims: list[dict]) -> float:
    """Share of claims backed by evidence (partial counts half)."""
    if not claims:
        return 0.0
    score = sum(1.0 if c.get("verdict") == "supported" else 0.5 if c.get("verdict") == "partial" else 0.0
                for c in claims)
    return score / len(claims)


async def reflect(state: RunState, rounds_left: int) -> tuple[str, str, Any]:
    wc, emit = state.wc, state.emit
    emit(event("reflect", "started", "checking coverage"))
    claim_lines = "\n".join(f"- [{c['verdict']}] {c['text']}" for c in state.claims) or "(no claims)"
    budget_left = state.meter.steps_remaining
    user = (
        f"{wc.render(max_tokens=4000)}\n\nCURRENT CLAIMS AND VERDICTS:\n"
        f"{wrap_untrusted('draft-claims', claim_lines)}\n\n"
        f"ROUNDS LEFT: {rounds_left}\nSTEP EXECUTIONS LEFT: {budget_left} "
        f"(at most {min(MAX_PLAN_STEPS, budget_left)} new steps).\n\n"
        f"CAPABILITIES:\n{cards_text(state, wc.goal)}"
    )
    out = await json_complete(system_prompt(REFLECT_SYSTEM), user, REFLECT_SHAPE, provider=state.provider)
    out = out if isinstance(out, dict) else {}
    decision = str(out.get("decision") or "stop").strip().lower()
    reason = str(out.get("reason") or "")[:500]
    return ("replan" if decision == "replan" else "stop"), reason, out.get("new_steps")


async def gather_evidence(state: RunState) -> None:
    """First round without a workflow: seed evidence, plan, execute the plan."""
    wc = state.wc
    seed = seed_step(state)
    if seed:
        wc.plan.append(seed)
        state.publish_plan()
        await execute(state, [seed])
    steps = await plan(state)
    for s in steps:
        s["round"] = 1
    wc.plan.extend(steps)
    state.publish_plan()
    await execute(state, steps)
    state.publish_plan()


def seed_step(state: RunState) -> dict | None:
    if not state.ctx.paper_ids:
        return None
    return {
        "id": "s0",
        "capability": "paper.search_spans",
        "args": {"query": state.wc.goal, "k": 8},
        "purpose": "seed evidence for the goal",
        "depends_on": [],
        "status": "pending",
        "round": 1,
    }


def final(state: RunState) -> dict:
    draft = state.draft or {
        "answer": "",
        "claims": [],
    }
    claims = state.claims
    cited = list(dict.fromkeys(
        [sid for c in claims for sid in c.get("span_ids", [])] + CITE.findall(draft["answer"])
    ))
    citations = [s for s in (state.spans.get(sid) for sid in cited) if s]
    return {
        "answer": draft["answer"],
        "claims": claims,
        "rejected_claims": list(state.rejected_claims),
        "citations": citations,
        "sub_questions": list(state.wc.sub_questions),
        "stop_reason": state.stop_reason or "goal_met",
        "rounds": state.rounds,
    }


async def write_and_verify(state: RunState, round_no: int, *, first: bool) -> bool:
    """Synthesize + verify into ``state``. In the first round a synthesis
    failure fails the run; later it keeps the previous answer (returns False)."""
    try:
        draft = await synthesize(state, round_no)
    except (asyncio.CancelledError, BudgetExceeded):
        raise
    except Exception as error:
        if first:
            state.emit(event("synthesize", "failed", err(error)))
            raise RuntimeError(f"synthesis failed: {err(error)}") from error
        state.stop_reason = "error"
        state.emit(event("synthesize", "failed", f"keeping previous answer: {err(error)}"))
        return False
    state.draft = draft
    state.claims = await verify(state, draft)
    return True
