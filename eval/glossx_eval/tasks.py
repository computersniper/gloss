"""Benchmark tasks in one shape, plus loaders for the public suites.

Every suite is converted to JSONL rows of :class:`Task`. Benchmark files are
not downloaded here: put the original release files under ``eval/data/raw``
and convert them with the loaders below (or ``python -m glossx_eval.convert``).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

KINDS = {"qa", "claim", "review", "tutor", "route", "repro"}
CLAIM_LABELS = ("SUPPORTED", "REFUTED", "NOT ENOUGH EVIDENCE")


@dataclass
class Task:
    id: str
    suite: str
    kind: str
    question: str
    # PDFs (paths relative to the task file) or arXiv ids the systems may read
    papers: list[str] = field(default_factory=list)
    # suite-specific gold: answers, label, evidence passages, workflow, rubric ...
    gold: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"task {self.id}: unknown kind {self.kind!r}")
        if not self.question.strip():
            raise ValueError(f"task {self.id}: empty question")


def load_tasks(path: str | Path) -> list[Task]:
    tasks = []
    seen: set[str] = set()
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        row = json.loads(line)
        task = Task(**row)
        if task.id in seen:
            raise ValueError(f"{path}:{n}: duplicate task id {task.id}")
        seen.add(task.id)
        tasks.append(task)
    return tasks


def write_tasks(tasks: Iterable[Task], path: str | Path) -> int:
    rows = [json.dumps(asdict(t), ensure_ascii=False) for t in tasks]
    Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def sample(tasks: list[Task], n: int, seed: int = 0) -> list[Task]:
    """A fixed, seed-stable subsample (the proposal subsamples 50-100 per suite)."""
    import random

    if n >= len(tasks):
        return list(tasks)
    rng = random.Random(seed)
    return sorted(rng.sample(tasks, n), key=lambda t: t.id)


# -- converters -------------------------------------------------------------------
def from_qasper(data: dict[str, Any], pdf_dir: str = "pdfs") -> Iterator[Task]:
    """QASPER release JSON (``{paper_id: {title, qas: [...]}}``)."""
    for paper_id, paper in data.items():
        for qa in paper.get("qas", []):
            answers, evidence = [], []
            for ans in qa.get("answers", []):
                a = ans.get("answer", {})
                if a.get("unanswerable"):
                    answers.append("unanswerable")
                elif a.get("extractive_spans"):
                    answers.append(" ".join(a["extractive_spans"]))
                elif a.get("free_form_answer"):
                    answers.append(a["free_form_answer"])
                elif a.get("yes_no") is not None:
                    answers.append("yes" if a["yes_no"] else "no")
                evidence.extend(a.get("evidence", []))
            yield Task(
                id=f"qasper-{qa['question_id']}", suite="qasper", kind="qa",
                question=qa["question"], papers=[f"{pdf_dir}/{paper_id}.pdf"],
                gold={"answers": answers, "evidence": evidence, "arxiv_id": paper_id},
            )


def from_scifact(claims: Iterable[dict[str, Any]], corpus: dict[int, dict[str, Any]]) -> Iterator[Task]:
    """SciFact ``claims_*.jsonl`` rows + ``corpus.jsonl`` keyed by doc_id.

    The label is SUPPORTED / REFUTED from the evidence sets, else NOT ENOUGH
    EVIDENCE; cited abstracts become the task's documents (one small PDF each,
    so every system reads the same files; see ``prepare.abstract_pdf``).
    """
    for row in claims:
        labels = {e["label"] for sets in (row.get("evidence") or {}).values() for e in sets}
        label = ("SUPPORTED" if labels == {"SUPPORT"} else "REFUTED" if labels == {"CONTRADICT"}
                 else "NOT ENOUGH EVIDENCE" if not labels else "MIXED")
        docs = [str(d) for d in row.get("cited_doc_ids", [])]
        yield Task(
            id=f"scifact-{row['id']}", suite="scifact", kind="claim", question=row["claim"],
            papers=[f"abstracts/{d}.pdf" for d in docs],
            gold={"label": label, "evidence": row.get("evidence") or {},
                  "titles": {d: (corpus.get(int(d)) or {}).get("title", "") for d in docs}},
        )


def from_generic_qa(rows: Iterable[dict[str, Any]], suite: str) -> Iterator[Task]:
    """PaperArena / ScholarQABench style rows ``{id, question, answer, papers}``.

    The field names differ between releases; map them to these keys first.
    """
    for row in rows:
        yield Task(
            id=f"{suite}-{row['id']}", suite=suite, kind=row.get("kind", "qa"),
            question=row["question"], papers=list(row.get("papers", [])),
            gold={k: v for k, v in row.items() if k not in {"id", "question", "papers", "kind"}},
        )
