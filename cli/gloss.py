#!/usr/bin/env python
"""GlossX (旁注) CLI — a thin terminal wrapper over the same backend.

Usage:
  gloss serve                 # start the web app (prints URL)
  gloss open <pdf|arxiv|doi>  # import + serve + open browser
  gloss import <pdf|arxiv|doi>
  gloss ls                    # list library
  gloss summarize <id|pdf|arxiv>
  gloss chat <id>             # interactive terminal chat (claude-style)
  gloss skills                # list discovered Claude/Codex skills
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from pathlib import Path

# make the backend importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend"))

from app import config  # noqa: E402
from app.features import chat as chat_feat  # noqa: E402
from app.features import summarize as sum_feat  # noqa: E402
from app.library import importers, service, store  # noqa: E402
from app.skills import loader as skill_loader  # noqa: E402


def _add_from_source(src: str) -> dict:
    """Add a paper from a local PDF path, arXiv id/URL, or DOI. Returns paper."""
    store.init_db()
    p = Path(src)
    if p.exists() and p.suffix.lower() == ".pdf":
        return service.create_from_pdf_bytes(p.read_bytes(), {"source": "upload", "title": p.stem})
    meta, pdf = asyncio.run(importers.import_source({"query": src}))
    return service.create_from_pdf_bytes(pdf, meta)


def _resolve_paper(idish: str) -> dict:
    """Accept an existing paper id, or import from a path/arxiv/doi."""
    store.init_db()
    p = store.get_paper(idish)
    if p:
        return p
    return _add_from_source(idish)


def cmd_serve(args):
    import uvicorn

    print(f"💡 GlossX 旁注 → http://{config.HOST}:{config.PORT}")
    print("   (open in a browser; Ctrl-C to stop)")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, app_dir=str(_ROOT / "backend"))


def cmd_open(args):
    paper = _resolve_paper(args.source)
    url = f"http://127.0.0.1:{config.PORT}/"
    print(f"opened paper: {paper['title']!r} ({paper['id']})")
    print(f"starting server at {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    cmd_serve(args)


def cmd_import(args):
    paper = _add_from_source(args.source)
    print(f"imported: {paper['id']}  {paper['title']!r}  ({paper['n_pages']}p)")


def cmd_ls(args):
    store.init_db()
    for p in store.list_papers():
        print(f"{p['id']}  {p['n_pages']:>3}p  {p['title'][:70]}")


def cmd_summarize(args):
    paper = _resolve_paper(args.source)
    print(f"# {paper['title']}\n")
    s = asyncio.run(sum_feat.summarize_paper(paper["id"], language=args.lang, refresh=True))
    print("TL;DR:", s.get("tldr", ""), "\n")
    print("Problem:", s.get("problem", ""))
    print("Method: ", s.get("method", ""))
    print("Results:", s.get("results", ""))
    if s.get("contributions"):
        print("\nContributions:")
        for c in s["contributions"]:
            print("  •", c)
    if s.get("key_points"):
        print("\nKey points:")
        for c in s["key_points"]:
            print("  •", c)


def cmd_chat(args):
    paper = _resolve_paper(args.source)
    print(f"💬 Chatting about: {paper['title']}  (blank line to quit)\n")

    async def ask(history):
        acc = []
        async for d in chat_feat.chat_stream(paper["id"], history):
            acc.append(d)
            sys.stdout.write(d)
            sys.stdout.flush()
        print("\n")
        return "".join(acc)

    history = []
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        history.append({"role": "user", "content": q})
        print("gloss> ", end="")
        reply = asyncio.run(ask(history))
        history.append({"role": "assistant", "content": reply})


def cmd_skills(args):
    for s in skill_loader.discover_skills():
        print(f"[{s['source']}] {s['name']:<24} {s['description'][:70]}")


def main():
    ap = argparse.ArgumentParser(prog="gloss", description="GlossX - local agent-harness paper reader")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve").set_defaults(fn=cmd_serve)

    o = sub.add_parser("open"); o.add_argument("source"); o.set_defaults(fn=cmd_open)
    i = sub.add_parser("import"); i.add_argument("source"); i.set_defaults(fn=cmd_import)
    sub.add_parser("ls").set_defaults(fn=cmd_ls)

    s = sub.add_parser("summarize"); s.add_argument("source")
    s.add_argument("--lang", default=None); s.set_defaults(fn=cmd_summarize)

    c = sub.add_parser("chat"); c.add_argument("source"); c.set_defaults(fn=cmd_chat)
    sub.add_parser("skills").set_defaults(fn=cmd_skills)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
