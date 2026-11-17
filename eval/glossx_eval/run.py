"""Run systems over a task file and score them.

    python -m glossx_eval.run TASKS.jsonl --systems glossx,codex,pi --repeats 3 --out results/

Without ``--execute`` this is a dry run: it prints the plan (tasks x systems x
repeats) and a rough token estimate and calls nothing. Results are JSONL, one
row per (task, system, repeat), and ``python -m glossx_eval.report`` turns
them into the comparison tables.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import metrics
from .systems import CodexSystem, GlossXSystem, Output, PiSystem, System
from .tasks import Task, load_tasks

# rough tokens per agent run, by task kind (pilot runs should replace these)
TOKENS_PER_RUN = {"qa": 40_000, "claim": 25_000, "review": 80_000, "tutor": 30_000,
                  "repro": 200_000, "route": 5_000}


def score(task: Task, out: Output, paper_text) -> dict[str, Any]:
    """Per-run scores for the task's kind; citation grounding for every kind."""
    s: dict[str, Any] = {}
    gold = task.gold
    if task.kind == "qa" and gold.get("answers"):
        s["f1"] = metrics.best_f1(out.answer, gold["answers"])
        s["em"] = metrics.exact_match(out.answer, gold["answers"])
    if task.kind == "qa" and "correct" in gold:  # judged suites (PaperArena) fill this later
        s["judge_pending"] = True
    if task.kind == "claim":
        s["pred_label"] = metrics.parse_verdict(out.answer)
        s["gold_label"] = gold.get("label")
        s["label_correct"] = s["pred_label"] == s["gold_label"]
    if task.gold.get("fault"):
        from . import faults

        s.update(faults.score(task, out.answer))
        s["graceful"] = not out.error  # finished with an answer instead of crashing or timing out
    if task.kind == "route":
        s["route_correct"] = (out.raw.get("route") or {}).get("workflow") == gold.get("workflow")
    if paper_text is not None:
        rep = metrics.citation_report(out.citations, task.papers, paper_text)
        s.update(citations=rep["n"], located=rep["located"], fabricated=rep["fabricated"],
                 fabricated_rate=rep["fabricated_rate"])
    return s


def pdf_text_reader(task_dir: Path):
    """``paper_text(paper, page)`` for PDFs (PyMuPDF) and plain-text files."""
    cache: dict[str, list[str]] = {}

    def pages(paper: str) -> list[str]:
        if paper not in cache:
            path = task_dir / paper
            if path.suffix.lower() == ".pdf":
                import fitz  # PyMuPDF, already a backend dependency

                with fitz.open(path) as doc:
                    cache[paper] = [p.get_text() for p in doc]
            else:
                cache[paper] = [path.read_text(encoding="utf-8", errors="replace")] if path.exists() else []
        return cache[paper]

    def paper_text(paper: str, page: int | None) -> str:
        ps = pages(paper)
        if page is None:
            return "\n".join(ps)
        return ps[page - 1] if 1 <= page <= len(ps) else ""

    return paper_text


def build_systems(names: list[str], args: argparse.Namespace) -> list[System]:
    paper_ids = json.loads(Path(args.paper_ids).read_text()) if args.paper_ids else {}
    table = {
        "glossx": lambda: GlossXSystem(base_url=args.glossx_url, paper_ids=paper_ids),
        "codex": lambda: CodexSystem(model=args.model),
        "pi": lambda: PiSystem(provider=args.pi_provider, model=args.model),
    }
    unknown = [n for n in names if n not in table]
    if unknown:
        raise SystemExit(f"unknown systems: {', '.join(unknown)} (known: {', '.join(table)})")
    return [table[n]() for n in names]


def plan_summary(tasks: list[Task], systems: list[str], repeats: int) -> dict[str, Any]:
    runs = len(tasks) * len(systems) * repeats
    tokens = sum(TOKENS_PER_RUN[t.kind] for t in tasks) * len(systems) * repeats
    by_suite: dict[str, int] = {}
    for t in tasks:
        by_suite[t.suite] = by_suite.get(t.suite, 0) + 1
    return {"tasks": len(tasks), "by_suite": by_suite, "systems": systems, "repeats": repeats,
            "runs": runs, "tokens_estimate": tokens}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="glossx_eval.run")
    ap.add_argument("tasks")
    ap.add_argument("--systems", default="glossx,codex,pi")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="first N tasks only (pilot)")
    ap.add_argument("--timeout", type=float, default=900.0, help="wall-clock cap per run, seconds")
    ap.add_argument("--out", default="results")
    ap.add_argument("--model", default=None, help="same base model for every system")
    ap.add_argument("--pi-provider", default=None)
    ap.add_argument("--glossx-url", default="http://127.0.0.1:8000")
    ap.add_argument("--paper-ids", default=None, help="JSON {paper file name: GlossX library id}")
    ap.add_argument("--execute", action="store_true", help="actually run (default: dry run)")
    args = ap.parse_args(argv)

    task_path = Path(args.tasks)
    tasks = load_tasks(task_path)
    if args.limit:
        tasks = tasks[: args.limit]
    names = [n.strip() for n in args.systems.split(",") if n.strip()]
    summary = plan_summary(tasks, names, args.repeats)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not args.execute:
        print("dry run: nothing was executed (pass --execute to run)", file=sys.stderr)
        return 0

    systems = build_systems(names, args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / f"results-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    paper_text = pdf_text_reader(task_path.parent)
    from .systems import stage_papers

    with results.open("w", encoding="utf-8") as fh:
        for rep in range(args.repeats):
            for task in tasks:
                for system in systems:
                    workdir = out_dir / "work" / system.name / f"{task.id}-r{rep}"
                    stage_papers(task, task_path.parent, workdir)
                    try:
                        out = system.run(task, workdir, args.timeout)
                    except Exception as error:  # one broken run must not stop the sweep
                        out = Output(error=f"{type(error).__name__}: {error}")
                    row = {"task": task.id, "suite": task.suite, "kind": task.kind, "system": system.name,
                           "repeat": rep, "output": asdict(out), "scores": score(task, out, paper_text)}
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"{task.id} {system.name} r{rep}: {row['scores']}", file=sys.stderr)
    print(f"wrote {results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
