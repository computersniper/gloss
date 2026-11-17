"""Workflow engine: executes a v2 manifest's ``steps`` as a DAG.

* ``depends_on`` orders steps; independent steps run in parallel waves.
* ``map_over: "<step>.<field>"`` fans a step out (one task per item, parallel).
* ``fallback: [cap, ...]`` tries the next capability on error or empty result.
* ``context: "shared_session"`` runs the step inside the shared paper session.
* ``args`` may reference ``{{goal}}``, ``{{scope}}``, ``{{item}}``,
  ``{{item.<field>}}`` and ``{{<step>.<field>}}``.

Internal roles (not registry capabilities): ``agent.planner`` (sub-questions
and search queries), ``decider.screen`` (relevance screening with the
decider), and ``agent.verifier`` / ``agent.writer`` which are deferred to the
loop's synthesize -> verify -> reflect phases that run after the steps. An
``agent.writer`` step may carry ``args.instructions``: how the answer of this
workflow is written (a verdict first, a quiz at the end, ...).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from ..features.common import json_complete
from ..library import store
from ..platform_support import read_utf8_text
from .capabilities import Capability, RunContext, bm25_rank, clip_card
from .core import RunState, err, event, run_step, scope_text, system_prompt
from .gate import BudgetExceeded

logger = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).resolve().parent / "workflows"
MAX_FANOUT = 6
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-\[\]]+)\s*\}\}")

ROLE_ALIASES = {
    "agent.planner": "planner", "agent:planner": "planner",
    "decider.screen": "screen", "decider:screen": "screen",
    "decider.relevance": "screen", "decider:relevance": "screen",
    "agent.verifier": "verifier", "agent:verifier": "verifier",
    "agent.writer": "writer", "agent:writer": "writer",
}

PLANNER_SYSTEM = (
    "You plan a literature search for GlossX, a research agent. Break the goal "
    "into 2-5 sub-questions and give 1-3 short keyword search queries that would "
    "find the most relevant papers."
)
PLANNER_SHAPE = '{"sub_questions": ["..."], "queries": ["..."]}'


# ---------------------------------------------------------------------------
# Manifests -> capabilities
# ---------------------------------------------------------------------------
def builtin_manifests() -> list[dict[str, Any]]:
    from ..plugins import manager

    out = []
    for path in sorted(BUILTIN_DIR.glob("*.json")):
        try:
            manifest = manager.validate_manifest_v2(json.loads(read_utf8_text(path)))
        except (OSError, ValueError) as error:
            logger.error("invalid builtin workflow %s: %s", path.name, error)
            continue
        manifest["builtin"] = True
        out.append(manifest)
    return out


def workflow_manifests() -> list[dict[str, Any]]:
    """First-party workflows, then installed third-party ones (ids unique)."""
    from ..plugins import manager

    manifests = [m for m in builtin_manifests() if m.get("kind") == "workflow"]
    seen = {m["id"] for m in manifests}
    try:
        installed = manager.list_installed_v2()
    except Exception:
        logger.exception("could not list installed v2 manifests")
        installed = []
    for m in installed:
        if m.get("kind") == "workflow" and m["id"] not in seen:
            manifests.append(m)
            seen.add(m["id"])
    return manifests


async def _routed_only(args: dict[str, Any], ctx: RunContext) -> Any:
    raise ValueError("workflows are selected by the router, not called as steps")


def workflow_capabilities() -> list[Capability]:
    caps = []
    for m in workflow_manifests():
        meta = m.get("capability") or {}
        caps.append(Capability(
            name=f"workflow.{m['id']}",
            kind="workflow",
            card=clip_card(meta.get("card") or m.get("description") or m["name"]),
            input_schema=dict(meta.get("input") or {}),
            output_schema=dict(meta.get("output") or {}),
            handler=_routed_only,
            permissions=list(m.get("permissions") or []),
            cost="llm",
            source="builtin" if m.get("builtin") else "plugin",
            trust="first-party" if m.get("builtin") else "third-party",
            meta={"manifest": m},
        ))
    return caps


def list_workflows(caps: list[Capability] | None = None) -> list[dict[str, Any]]:
    """``[{id, name, card, source}]`` for ``GET /api/agent/workflows``."""
    caps = workflow_capabilities() if caps is None else caps
    return [
        {
            "id": cap.name,
            "name": (cap.meta.get("manifest") or {}).get("name") or cap.name,
            "card": cap.card,
            "source": cap.source,
        }
        for cap in caps
        if cap.kind == "workflow"
    ]


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------
def _lookup(path: str, scope: dict[str, Any]) -> Any:
    parts = path.split(".")
    value: Any = scope.get(parts[0])
    for part in parts[1:]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, list):
            # project a field over a list of objects: search.results.title
            value = [v.get(part) for v in value if isinstance(v, dict) and part in v]
        else:
            return None
    return value


def render_args(template: Any, scope: dict[str, Any]) -> Any:
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            rendered = render_args(v, scope)
            if rendered is not None and rendered != "":
                out[k] = rendered
        return out
    if isinstance(template, list):
        return [render_args(v, scope) for v in template]
    if isinstance(template, str):
        whole = _TEMPLATE.fullmatch(template.strip())
        if whole:
            return _lookup(whole.group(1), scope)

        def sub(match: re.Match) -> str:
            value = _lookup(match.group(1), scope)
            if value is None:
                return ""
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

        return _TEMPLATE.sub(sub, template)
    return template


def _is_empty(result: Any) -> bool:
    if result is None:
        return True
    if isinstance(result, (list, str)):
        return len(result) == 0
    if isinstance(result, dict):
        if result.get("is_error") is True:
            return True
        lists = [result[k] for k in ("results", "papers", "spans", "items", "findings", "content", "notes")
                 if k in result]
        return bool(lists) and all(not v for v in lists)
    return False


# ---------------------------------------------------------------------------
# Internal roles
# ---------------------------------------------------------------------------
async def _role_planner(state: RunState, spec: dict) -> dict:
    wc, emit = state.wc, state.emit
    emit(event("plan", "started", "workflow planner: sub-questions and search queries",
               step_id=spec["id"], capability=spec["use"]))
    user = (
        f"GOAL:\n{wc.goal}\n\nSCOPE PAPERS:\n{scope_text(state.ctx.paper_ids)}\n\n"
        f"WHAT IS KNOWN SO FAR:\n{wc.render(max_tokens=1500)}"
    )
    try:
        out = await json_complete(system_prompt(PLANNER_SYSTEM), user, PLANNER_SHAPE,
                                  provider=state.provider)
    except BudgetExceeded:
        raise
    except Exception as error:
        emit(event("plan", "failed", f"planner failed; searching for the goal: {err(error)}",
                   step_id=spec["id"]))
        out = {}
    out = out if isinstance(out, dict) else {}
    subs = out.get("sub_questions") if isinstance(out.get("sub_questions"), list) else []
    queries = out.get("queries") if isinstance(out.get("queries"), list) else []
    wc.sub_questions = [str(q)[:300] for q in subs if str(q).strip()][:8]
    queries = [" ".join(str(q).split())[:200] for q in queries if str(q).strip()][:3]
    if not queries:
        queries = [" ".join(wc.goal.split())[:200]]
    emit(event("plan", "completed",
               f"{len(wc.sub_questions)} sub-question(s), {len(queries)} search quer(ies)",
               step_id=spec["id"]))
    return {"sub_questions": wc.sub_questions, "queries": queries}


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _library_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for p in store.list_papers():
        pid = p.get("id")
        if not pid:
            continue
        for key in (p.get("arxiv_id"), (p.get("doi") or "").lower(), _norm_title(p.get("title") or "")):
            if key:
                index.setdefault(f"k:{key}", pid)
    return index


def _candidates_from(value: Any, out: list[dict]) -> None:
    if isinstance(value, list):
        for v in value:
            _candidates_from(v, out)
        return
    if not isinstance(value, dict):
        return
    for key in ("results", "papers"):
        items = value.get(key)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and (it.get("title") or it.get("id")):
                    out.append({
                        "paper_id": it.get("id") if key == "papers" else None,
                        "title": str(it.get("title") or "")[:300],
                        "year": it.get("year"),
                        "abstract": str(it.get("abstract") or "")[:600],
                        "arxiv_id": str(it.get("arxiv_id") or ""),
                        "doi": str(it.get("doi") or ""),
                        "url": str(it.get("url") or ""),
                    })


async def _role_screen(state: RunState, spec: dict, outputs: dict[str, Any]) -> dict:
    ctx, emit, wc = state.ctx, state.emit, state.wc
    args = spec.get("args") or {}
    max_candidates = max(1, min(12, int(args.get("max_candidates") or 8)))
    max_accept = max(1, min(MAX_FANOUT, int(args.get("max_accept") or 4)))
    emit(event("execute", "started", "screening candidate papers with the decider",
               step_id=spec["id"], capability=spec["use"]))

    raw: list[dict] = []
    for dep in spec.get("depends_on") or list(outputs):
        _candidates_from(outputs.get(dep), raw)
    index = _library_index()
    candidates: list[dict] = []
    seen: set[str] = set()
    for c in raw:
        pid = c.get("paper_id")
        if not pid:
            for key in (c["arxiv_id"], c["doi"].lower(), _norm_title(c["title"])):
                if key and f"k:{key}" in index:
                    pid = index[f"k:{key}"]
                    break
        c["paper_id"] = pid
        c["source"] = c["arxiv_id"] or c["doi"] or (c["url"] if c["url"].lower().endswith(".pdf") or "arxiv.org" in c["url"] else "")
        dedupe = pid or c["source"] or _norm_title(c["title"])
        if not dedupe or dedupe in seen or (pid and pid in ctx.paper_ids):
            continue
        seen.add(dedupe)
        candidates.append(c)

    accepted: list[dict] = []
    for pid in ctx.paper_ids:
        paper = store.get_paper(pid) or {}
        accepted.append({"paper_id": pid, "source": "", "title": paper.get("title") or "",
                         "reason": "in run scope", "confidence": 1.0})
    ranked = bm25_rank(wc.goal, [f"{c['title']} {c['abstract']}" for c in candidates])
    pool = [candidates[i] for i, _ in ranked][:max_candidates]

    async def judge(c: dict) -> tuple[dict, dict]:
        evidence = f"Title: {c['title']}\nYear: {c.get('year') or 'n.d.'}\nAbstract: {c['abstract'] or '(none)'}"
        try:
            verdict = await state.decider.judge(
                f"This paper is directly relevant to the research goal: {wc.goal}", evidence
            )
        except BudgetExceeded:
            raise
        except Exception as error:
            verdict = {"verdict": "unsupported", "confidence": 0.0, "note": f"screening failed: {err(error)}"}
        return c, verdict

    judged = await asyncio.gather(*(judge(c) for c in pool)) if pool else []
    rejected: list[dict] = []
    keep: list[tuple[float, dict]] = []
    for c, v in judged:
        relevant = v["verdict"] == "supported" or (v["verdict"] == "partial" and v["confidence"] >= 0.5)
        if relevant and (c["paper_id"] or c["source"]):
            score = v["confidence"] + (0.5 if v["verdict"] == "supported" else 0.0)
            keep.append((score, {"paper_id": c["paper_id"], "source": c["source"], "title": c["title"],
                                 "reason": v["note"], "confidence": v["confidence"]}))
        else:
            note = v["note"] if not relevant else "no importable identifier"
            rejected.append({"title": c["title"], "note": note})
    keep.sort(key=lambda x: x[0], reverse=True)
    room = max(0, min(max_accept, MAX_FANOUT - len(accepted)))
    accepted.extend(item for _, item in keep[:room])
    to_import = [{"source": a["source"], "title": a["title"]} for a in accepted if not a["paper_id"] and a["source"]]
    emit(event("execute", "completed",
               f"screened {len(pool)} candidate(s): {len(accepted)} accepted "
               f"({len(to_import)} to import), {len(rejected)} rejected",
               step_id=spec["id"], capability=spec["use"]))
    return {"accepted": accepted, "to_import": to_import, "rejected": rejected}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
async def _run_capability_step(state: RunState, spec: dict, scope: dict[str, Any]) -> Any:
    """Run one (possibly fanned-out) capability step with its fallback chain."""
    emit, wc = state.emit, state.wc
    items: list[Any] | None = None
    if spec.get("map_over"):
        value = _lookup(spec["map_over"], scope)
        items = value if isinstance(value, list) else ([] if value in (None, "") else [value])
        if len(items) > MAX_FANOUT:
            emit(event("execute", "info", f"fan-out capped at {MAX_FANOUT} of {len(items)} items",
                       step_id=spec["id"], capability=spec["use"]))
            items = items[:MAX_FANOUT]
        if not items:
            emit(event("execute", "info", f"skipped: nothing to map over ({spec['map_over']})",
                       step_id=spec["id"], capability=spec["use"]))
            return []

    chain = [spec["use"]] + list(spec.get("fallback") or [])

    async def one(sub_id: str, item: Any) -> Any:
        local = dict(scope)
        if item is not None:
            local["item"] = item
        args = render_args(spec.get("args") or {}, local)
        last = None
        for n, use in enumerate(chain):
            cap = state.registry.resolve(use)
            if cap is None:
                if n > 0:
                    emit(event("execute", "info", f"fallback {use} is not available",
                               step_id=sub_id, capability=use))
                    continue
                emit(event("execute", "info", f"capability {use} is not available; trying fallbacks",
                           step_id=sub_id, capability=use))
                continue
            if state.meter.exhausted or state.meter.take_steps(1) < 1:
                emit(event("execute", "info", "skipped: budget exhausted", step_id=sub_id, capability=cap.name))
                return last
            step = {
                "id": sub_id if n == 0 else f"{sub_id}~{cap.name}",
                "capability": cap.name,
                "args": args,
                "purpose": f"workflow step {spec['id']}" + (f" (fallback {n})" if n else ""),
                "depends_on": [],
                "status": "pending",
                "round": 1,
                "workflow_step": spec["id"],
            }
            wc.plan.append(step)
            state.publish_plan()
            result = await run_step(state, step, context_mode=spec.get("context"))
            state.publish_plan()
            last = result
            if step["status"] == "completed" and not _is_empty(result):
                return result
            if step.get("denied"):
                return None  # a denial is a decision, not a reason to try alternatives
            if n + 1 < len(chain):
                emit(event("execute", "info", "empty or failed result; trying fallback",
                           step_id=step["id"], capability=cap.name))
        return last

    if items is None:
        return await one(spec["id"], None)
    results = await asyncio.gather(*(one(f"{spec['id']}[{i}]", it) for i, it in enumerate(items)))
    return [r for r in results if r is not None]


def _implicit_deps(spec: dict, step_ids: set[str]) -> set[str]:
    """``depends_on`` plus steps referenced by ``map_over`` or ``{{step.field}}`` args."""
    deps = set(spec.get("depends_on") or [])
    if spec.get("map_over"):
        deps.add(str(spec["map_over"]).split(".", 1)[0])
    for ref in _TEMPLATE.findall(json.dumps(spec.get("args") or {})):
        head = ref.split(".", 1)[0]
        if head in step_ids:
            deps.add(head)
    deps.discard(spec["id"])
    return deps & step_ids


async def execute_workflow(state: RunState, manifest: dict[str, Any]) -> None:
    """Run every step of ``manifest``; outputs become observations/evidence."""
    emit = state.emit
    steps = list(manifest.get("steps") or [])
    emit(event("plan", "info", f"workflow {manifest['id']}: {len(steps)} step(s)"))
    scope: dict[str, Any] = {"goal": state.wc.goal, "scope": list(state.ctx.paper_ids)}
    outputs: dict[str, Any] = {}
    done: set[str] = set()
    pending = {s["id"]: s for s in steps}

    async def run_one(spec: dict) -> None:
        role = ROLE_ALIASES.get(spec["use"])
        entry = None
        if role:
            entry = {"id": spec["id"], "capability": spec["use"], "args": dict(spec.get("args") or {}),
                     "purpose": f"workflow role: {role}", "depends_on": list(spec.get("depends_on") or []),
                     "status": "running" if role in {"planner", "screen"} else "deferred",
                     "round": 1, "workflow_step": spec["id"]}
            state.wc.plan.append(entry)
            state.publish_plan()
        if role == "planner":
            outputs[spec["id"]] = await _role_planner(state, spec)
            state.wc.observe(spec["id"], spec["use"], "completed", result=outputs[spec["id"]])
            state.publish_plan()
        elif role == "screen":
            outputs[spec["id"]] = await _role_screen(state, spec, outputs)
            state.wc.observe(spec["id"], spec["use"], "completed", result=outputs[spec["id"]])
        elif role in {"verifier", "writer"}:
            outputs[spec["id"]] = None
            instructions = (spec.get("args") or {}).get("instructions")
            if role == "writer" and isinstance(instructions, str) and instructions.strip():
                state.writer_instructions = instructions.strip()[:2000]
            emit(event("execute", "info", f"{spec['use']} runs in the loop after the workflow steps",
                       step_id=spec["id"], capability=spec["use"]))
        else:
            outputs[spec["id"]] = await _run_capability_step(state, spec, scope)
        if entry is not None and entry["status"] == "running":
            entry["status"] = "completed"
            state.publish_plan()
        scope[spec["id"]] = outputs[spec["id"]]

    ids = set(pending)
    deps = {sid: _implicit_deps(spec, ids) for sid, spec in pending.items()}

    while pending:
        ready = [s for s in pending.values() if all(d in done for d in deps[s["id"]])]
        if not ready:  # validated manifests cannot get here; be defensive
            emit(event("execute", "info", f"skipped {len(pending)} step(s): unresolvable dependencies"))
            break
        await asyncio.gather(*(run_one(s) for s in ready))
        for s in ready:
            done.add(s["id"])
            pending.pop(s["id"], None)
        if state.meter.exhausted:
            emit(event("execute", "info", f"budget exhausted; skipped {len(pending)} workflow step(s)"))
            break
    state.workflow_ran = True
    state.publish_plan()


def manifest_for(registry, workflow_id: str) -> dict[str, Any] | None:
    cap = registry.get(workflow_id)
    if cap is None or cap.kind != "workflow":
        return None
    return cap.meta.get("manifest")


__all__ = [
    "builtin_manifests",
    "execute_workflow",
    "list_workflows",
    "manifest_for",
    "render_args",
    "workflow_capabilities",
    "workflow_manifests",
]
