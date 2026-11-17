"""Build the pilot task set and load its papers into GlossX.

    python -m glossx_eval.prepare build  --out data/pilot --qasper 20 --scifact 30
    python -m glossx_eval.prepare import --tasks data/pilot/pilot.jsonl --glossx-url http://127.0.0.1:8000

``build`` downloads the public QASPER and SciFact releases, samples tasks with a
fixed seed, fetches the QASPER papers from arXiv and renders each SciFact
abstract as a one-page PDF, then writes ``pilot.jsonl`` next to the files.
``import`` uploads every PDF to a running GlossX and writes ``paper_ids.json``
(file name -> library id) for ``glossx_eval.run --paper-ids``. Neither command
calls a model.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

from .tasks import Task, from_qasper, from_scifact, load_tasks, sample, write_tasks

QASPER_URL = "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz"
SCIFACT_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"
ARXIV_PDF = "https://arxiv.org/pdf/{id}"
USER_AGENT = "glossx-eval/0.1 (research benchmark preparation)"


def fetch(url: str, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def tar_member(blob: bytes, suffix: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith(suffix) and "/._" not in member.name:
                return tar.extractfile(member).read()
    raise FileNotFoundError(f"{suffix} not found in archive")


def abstract_pdf(title: str, sentences: list[str], path: Path) -> None:
    """One-page PDF of a SciFact abstract (PyMuPDF, already a backend dependency)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    text = title + "\n\n" + " ".join(s.strip() for s in sentences)
    for size in (10, 9, 8, 7, 6):  # insert_textbox writes nothing when the text overflows
        if page.insert_textbox(fitz.Rect(54, 54, 558, 738), text, fontsize=size) >= 0:
            break
    else:
        raise ValueError(f"abstract too long for one page: {title[:60]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def build_scifact(out: Path, n: int, seed: int, blob: bytes | None = None) -> list[Task]:
    blob = blob or fetch(SCIFACT_URL)
    claims = [json.loads(line) for line in tar_member(blob, "claims_dev.jsonl").decode().splitlines() if line.strip()]
    corpus = {}
    for line in tar_member(blob, "corpus.jsonl").decode().splitlines():
        if line.strip():
            row = json.loads(line)
            corpus[int(row["doc_id"])] = row
    # MIXED (support and contradiction across abstracts) is outside the 3-way label set
    pool = [t for t in from_scifact(claims, corpus) if t.gold["label"] != "MIXED"]
    tasks = sample(pool, n, seed)
    for task in tasks:
        for rel in task.papers:
            doc_id = int(Path(rel).stem)
            row = corpus.get(doc_id) or {"title": "", "abstract": []}
            abstract_pdf(row["title"], row["abstract"], out / rel)
    return tasks


def build_qasper(out: Path, n: int, seed: int, blob: bytes | None = None, delay: float = 3.0) -> list[Task]:
    blob = blob or fetch(QASPER_URL)
    data = json.loads(tar_member(blob, "qasper-dev-v0.3.json"))
    tasks = sample(list(from_qasper(data)), n, seed)
    kept = []
    for task in tasks:
        arxiv_id = task.gold["arxiv_id"]
        pdf = out / task.papers[0]
        if not pdf.exists():
            try:
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(fetch(ARXIV_PDF.format(id=arxiv_id)))
                time.sleep(delay)  # arXiv asks automated clients to go slowly
            except Exception as error:
                print(f"skip {task.id}: could not fetch arXiv {arxiv_id}: {error}", file=sys.stderr)
                continue
        kept.append(task)
    return kept


def import_papers(tasks_path: Path, base_url: str) -> dict[str, str | None]:
    import httpx  # a backend dependency

    ids: dict[str, str | None] = {}  # None: GlossX refused the file
    files = sorted({p for t in load_tasks(tasks_path) for p in t.papers})
    with httpx.Client(base_url=base_url, timeout=300) as client:
        for rel in files:
            path = tasks_path.parent / rel
            name = Path(rel).name
            if name in ids or not path.exists():
                continue
            resp = client.post("/api/papers/upload", files={"file": (name, path.read_bytes(), "application/pdf")})
            if 400 <= resp.status_code < 500:  # e.g. a corrupted PDF from glossx_eval.faults
                ids[name] = None
                print(f"rejected {name}: {resp.text[:200]}", file=sys.stderr)
                continue
            resp.raise_for_status()
            ids[name] = resp.json()["id"]
            print(f"imported {name} -> {ids[name]}", file=sys.stderr)
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="glossx_eval.prepare")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default="data/pilot")
    b.add_argument("--qasper", type=int, default=20)
    b.add_argument("--scifact", type=int, default=30)
    b.add_argument("--seed", type=int, default=0)
    i = sub.add_parser("import")
    i.add_argument("--tasks", default="data/pilot/pilot.jsonl")
    i.add_argument("--glossx-url", default="http://127.0.0.1:8000")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        out = Path(args.out)
        tasks: list[Task] = []
        if args.scifact:
            tasks += build_scifact(out, args.scifact, args.seed)
        if args.qasper:
            tasks += build_qasper(out, args.qasper, args.seed)
        n = write_tasks(tasks, out / "pilot.jsonl")
        print(json.dumps({"tasks": n, "file": str(out / "pilot.jsonl")}))
    else:
        tasks_path = Path(args.tasks)
        ids = import_papers(tasks_path, args.glossx_url)
        target = tasks_path.parent / "paper_ids.json"
        target.write_text(json.dumps(ids, indent=2), encoding="utf-8")
        print(json.dumps({"papers": len(ids), "file": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
