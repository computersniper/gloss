"""Citation support: does a located quote actually back its sentence?

``metrics.citation_report`` only checks that a quote exists in the paper. This
module asks a judge model, the same for every system, whether each located
quote supports the sentence it is attached to (SUPPORTS, PARTIAL or
UNSUPPORTED); fabricated citations count as UNSUPPORTED without a call. The
judge is any CLI agent given as a command template, ideally a different model
family from the one under test. A human audit of a random sample gives Cohen's
kappa between judge and human labels.

    python -m glossx_eval.judge run results/results-X.jsonl --tasks data/pilot/pilot.jsonl \\
        --judge-cmd "claude -p {prompt}" [--execute]
    python -m glossx_eval.judge sample results/results-X.judged.jsonl --frac 0.2 --out audit.csv
    python -m glossx_eval.judge agree audit.csv results/results-X.judged.jsonl

``sample`` writes a blind CSV (no system names, no judge labels); fill in the
``human_label`` column, then ``agree`` prints kappa.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import metrics
from .tasks import load_tasks

LABELS = ("SUPPORTS", "PARTIAL", "UNSUPPORTED")
BATCH = 12

JUDGE_PROMPT = (
    "You check citations in research answers. For each item decide whether the QUOTE, taken from "
    "the cited paper, supports the SENTENCE. SUPPORTS: the quote states or directly implies every "
    "factual part of the sentence. PARTIAL: it supports some of it, or only with an extra unstated "
    "step. UNSUPPORTED: it does not support the sentence or contradicts it. Judge only the quote, "
    "not what you know about the topic. Reply with one JSON object and nothing else: "
    '{"verdicts": [{"i": 0, "label": "SUPPORTS"}]}\n\nITEMS:\n'
)

Judge = Callable[[str], str]


def command_judge(template: str, timeout_s: float = 300.0) -> Judge:
    """A judge backed by a CLI agent; ``{prompt}`` is replaced by the prompt (no shell)."""

    def call(prompt: str) -> str:
        argv = [prompt if word == "{prompt}" else word for word in shlex.split(template)]
        argv[0] = shutil.which(argv[0]) or argv[0]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s, env=dict(os.environ))
        return proc.stdout

    return call


def parse_verdicts(text: str, n: int) -> list[str | None]:
    """Labels by item index from the judge's last ``{"verdicts": [...]}`` object."""
    labels: list[str | None] = [None] * n
    decoder = json.JSONDecoder()
    for start in reversed([i for i, ch in enumerate(text or "") if ch == "{"]):
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("verdicts"), list):
            for v in obj["verdicts"]:
                if not isinstance(v, dict) or not isinstance(v.get("i"), int) or not 0 <= v["i"] < n:
                    continue
                label = str(v.get("label") or "").strip().upper()
                labels[v["i"]] = label if label in LABELS else None
            break
    return labels


def judge_items(items: list[dict[str, str]], judge: Judge) -> list[str | None]:
    """Label (sentence, quote) pairs in batches; an unparsable reply leaves None."""
    out: list[str | None] = []
    for k in range(0, len(items), BATCH):
        chunk = items[k:k + BATCH]
        body = "\n".join(
            json.dumps({"i": i, "sentence": it["claim"][:600], "quote": it["quote"][:1200]}, ensure_ascii=False)
            for i, it in enumerate(chunk))
        try:
            out.extend(parse_verdicts(judge(JUDGE_PROMPT + body), len(chunk)))
        except (OSError, subprocess.SubprocessError):
            out.extend([None] * len(chunk))
    return out


def citation_key(row: dict[str, Any], index: int) -> str:
    """Stable id of one citation that does not reveal the system."""
    raw = f"{row['task']}|{row['system']}|{row['repeat']}|{index}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def judge_row(row: dict[str, Any], papers: list[str], paper_text, judge: Judge) -> dict[str, Any]:
    """Add ``judge`` labels to the row's citations and support rates to its scores."""
    cites = row["output"].get("citations") or []
    answer = row["output"].get("answer") or ""
    report = metrics.citation_report(cites, papers, paper_text)
    todo = [i for i, r in enumerate(report["rows"]) if r["located"]]
    items = [{"claim": str(cites[i].get("claim") or answer), "quote": str(cites[i].get("quote") or "")}
             for i in todo]
    labels = dict(zip(todo, judge_items(items, judge))) if items else {}
    for i, c in enumerate(cites):
        c["judge"] = labels.get(i) if i in labels else "UNSUPPORTED"
        c["key"] = citation_key(row, i)
    judged = [c["judge"] for c in cites if c["judge"]]
    scores = row["scores"]
    if judged:
        scores["support_rate"] = sum(x == "SUPPORTS" for x in judged) / len(judged)
        scores["partial_rate"] = sum(x == "PARTIAL" for x in judged) / len(judged)
    scores["judge_failed"] = sum(c["judge"] is None for c in cites)
    return row


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sample_audit(rows: list[dict[str, Any]], frac: float, seed: int = 0) -> list[dict[str, str]]:
    """A blind random sample of judged citations for human labelling."""
    pool = []
    for row in rows:
        for c in row["output"].get("citations") or []:
            if c.get("key") and c.get("judge"):
                pool.append({"key": c["key"], "claim": str(c.get("claim") or ""),
                             "quote": str(c.get("quote") or ""), "human_label": ""})
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[: max(1, round(len(pool) * frac))] if pool else []


def agreement(audit: list[dict[str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    judge = {c["key"]: c.get("judge") for row in rows for c in row["output"].get("citations") or []
             if c.get("key")}
    pairs = [(a["human_label"].strip().upper(), judge.get(a["key"])) for a in audit
             if a.get("human_label", "").strip()]
    pairs = [(h, j) for h, j in pairs if h in LABELS and j in LABELS]
    return {"n": len(pairs), "kappa": metrics.cohen_kappa(pairs),
            "raw_agreement": sum(h == j for h, j in pairs) / len(pairs) if pairs else float("nan")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="glossx_eval.judge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("results")
    r.add_argument("--tasks", required=True)
    r.add_argument("--judge-cmd", default="claude -p {prompt}")
    r.add_argument("--out", default=None)
    r.add_argument("--execute", action="store_true", help="call the judge (default: dry run)")
    s = sub.add_parser("sample")
    s.add_argument("judged")
    s.add_argument("--frac", type=float, default=0.2)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", default="audit.csv")
    g = sub.add_parser("agree")
    g.add_argument("audit")
    g.add_argument("judged")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        from .run import pdf_text_reader

        rows = read_rows(Path(args.results))
        tasks = {t.id: t for t in load_tasks(args.tasks)}
        cites = sum(len(r["output"].get("citations") or []) for r in rows)
        print(json.dumps({"runs": len(rows), "citations": cites,
                          "judge_calls_at_most": sum(-(-len(r["output"].get("citations") or []) // BATCH)
                                                     for r in rows)}, indent=2))
        if not args.execute:
            print("dry run: the judge was not called (pass --execute)", file=sys.stderr)
            return 0
        judge = command_judge(args.judge_cmd)
        paper_text = pdf_text_reader(Path(args.tasks).parent)
        out = Path(args.out or Path(args.results).with_suffix(".judged.jsonl"))
        with out.open("w", encoding="utf-8") as fh:
            for row in rows:
                task = tasks.get(row["task"])
                if task is not None:
                    judge_row(row, task.papers, paper_text, judge)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote {out}")
    elif args.cmd == "sample":
        audit = sample_audit(read_rows(Path(args.judged)), args.frac, args.seed)
        with open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["key", "claim", "quote", "human_label"])
            writer.writeheader()
            writer.writerows(audit)
        print(f"wrote {len(audit)} citations to {args.out}; fill human_label with "
              f"{' / '.join(LABELS)}")
    else:
        with open(args.audit, encoding="utf-8-sig", newline="") as fh:
            audit = list(csv.DictReader(fh))
        print(json.dumps(agreement(audit, read_rows(Path(args.judged))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
