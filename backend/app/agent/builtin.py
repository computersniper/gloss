"""Builtin capabilities: thin, defensive wrappers over existing Gloss features.

Every handler validates its own args (raising ``ValueError`` with a short
message the planner can learn from), returns JSON-serialisable data and
truncates long outputs so observations stay cheap.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..features.highlight import CATEGORY_COLORS as HIGHLIGHT_COLORS
from ..features.citations import _ensure_raw_refs, resolve_references
from ..features.explain import explain_selection
from ..features.translate import translate_text
from ..features.summarize import summarize_paper
from ..library import store
from ..pdf import ingest, structure
from ..features.common import json_complete
from ..providers import registry as providers
from ..search import scholar
from . import projects
from .capabilities import Capability, CapabilityRegistry, RunContext, bm25_rank
from .context import UNTRUSTED_RULE, wrap_untrusted

IMPORT_WAIT_SECONDS = 170.0  # stays under the loop's per-step timeout


def _str_arg(args: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"'{key}' is required")
        return ""
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"'{key}' must be a string")
    return str(value).strip()


def _int_arg(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _paper_arg(args: dict[str, Any], ctx: RunContext) -> str:
    paper_id = _str_arg(args, "paper_id", required=False)
    if not paper_id:
        if not ctx.paper_ids:
            raise ValueError("'paper_id' is required (run has no scope papers)")
        paper_id = ctx.paper_ids[0]
    if not store.get_paper(paper_id):
        raise ValueError(f"paper not found: {paper_id}")
    return paper_id


def _brief(paper: dict) -> dict:
    return {
        "id": paper.get("id"),
        "title": paper.get("title", ""),
        "year": paper.get("year", ""),
        "authors": list(paper.get("authors") or [])[:3],
    }


async def _library_list(args: dict[str, Any], ctx: RunContext) -> Any:
    papers = store.list_papers()
    return {"count": len(papers), "papers": [_brief(p) for p in papers[:100]]}


async def _paper_read(args: dict[str, Any], ctx: RunContext) -> Any:
    paper_id = _paper_arg(args, ctx)
    max_chars = _int_arg(args, "max_chars", 3000, 200, 12000)
    paper = store.get_paper(paper_id) or {}
    parsed = store.load_parsed(paper_id) or {}
    sections = parsed.get("sections")
    if not sections and parsed.get("pages"):
        try:
            sections = structure.detect_sections(parsed)
        except Exception:
            sections = []
    outline = [
        {"title": s.get("title", ""), "page": s.get("page"), "level": s.get("level")}
        for s in (sections or [])[:60]
    ]
    return {
        "paper_id": paper_id,
        "title": paper.get("title", ""),
        "authors": list(paper.get("authors") or [])[:8],
        "year": paper.get("year", ""),
        "abstract": (paper.get("abstract") or "")[:2000],
        "n_pages": parsed.get("n_pages") or paper.get("n_pages"),
        "outline": outline,
        "opening_text": (parsed.get("full_text") or "")[:max_chars],
    }


async def _paper_search_spans(args: dict[str, Any], ctx: RunContext) -> Any:
    query = _str_arg(args, "query")
    k = _int_arg(args, "k", 6, 1, 12)
    paper_ids = args.get("paper_ids")
    if isinstance(paper_ids, str):
        paper_ids = [paper_ids]
    if isinstance(paper_ids, list):
        # workflow templates may project ids that are missing (None) for some items
        paper_ids = [str(p) for p in paper_ids if isinstance(p, (str, int)) and str(p)]
    if not isinstance(paper_ids, list) or not paper_ids:
        paper_ids = list(ctx.paper_ids)
    if not paper_ids:
        # no run scope: search the whole library
        paper_ids = [p["id"] for p in store.list_papers()[:50]]
    spans = ctx.spans.search(query, paper_ids, k=k)
    return {"query": query, "spans": spans}


async def _paper_summary(args: dict[str, Any], ctx: RunContext) -> Any:
    paper_id = _paper_arg(args, ctx)
    # summarize_paper consults its own cache before calling a provider
    return await summarize_paper(paper_id, provider=ctx.provider)


async def _paper_explain(args: dict[str, Any], ctx: RunContext) -> Any:
    paper_id = _paper_arg(args, ctx)
    text = _str_arg(args, "text")[:4000]
    nearby = ctx.spans.search(text, [paper_id], k=3)
    context = "\n\n".join(s["text"] for s in nearby) or None
    explanation = await explain_selection(
        paper_id, text, context=context, provider=ctx.provider
    )
    return {"paper_id": paper_id, "explanation": explanation[:5000]}


def _ref_brief(r: dict) -> dict:
    return {
        "idx": r.get("idx"),
        "title": r.get("title", ""),
        "authors": list(r.get("authors") or [])[:3],
        "year": r.get("year", ""),
        "doi": r.get("doi", ""),
        "arxiv_id": r.get("arxiv_id", ""),
        "raw": (r.get("raw") or "")[:300],
    }


async def _paper_references(args: dict[str, Any], ctx: RunContext) -> Any:
    paper_id = _paper_arg(args, ctx)
    refs = store.get_refs(paper_id)
    resolved = bool(refs) and all(r.get("resolved") for r in refs)
    note = None
    if not resolved:
        try:
            refs = await resolve_references(paper_id, provider=ctx.provider)
            resolved = True
        except Exception as error:  # network / provider failure: fall back to raw refs
            note = f"resolution failed, raw references only: {error}"[:300]
            try:
                refs = await _ensure_raw_refs(paper_id)
            except Exception:
                refs = []
    return {
        "paper_id": paper_id,
        "resolved": resolved,
        "count": len(refs),
        "references": [_ref_brief(r) for r in refs[:60]],
        "note": note,
    }


async def _scholar_search(args: dict[str, Any], ctx: RunContext) -> Any:
    query = _str_arg(args, "query")
    k = _int_arg(args, "k", 8, 1, 10)
    results = await scholar.search(query, k)
    return {
        "query": query,
        "results": [
            {
                "title": r.get("title", ""),
                "year": r.get("year"),
                "authors": list(r.get("authors") or [])[:3],
                "abstract": (r.get("abstract") or "")[:400],
                "arxiv_id": r.get("arxiv_id", ""),
                "doi": r.get("doi", ""),
                "url": r.get("url", ""),
                "citations": r.get("citations"),
            }
            for r in results[:k]
        ],
    }


async def _library_import(args: dict[str, Any], ctx: RunContext) -> Any:
    source = _str_arg(args, "source")[:500]
    if ctx.import_jobs is None:
        raise ValueError("import queue is not available in this process")
    job = await ctx.import_jobs.submit({"query": source})
    deadline = time.monotonic() + IMPORT_WAIT_SECONDS
    while job.get("status") not in {"completed", "failed", "cancelled"}:
        if time.monotonic() > deadline:
            return {"status": "pending", "job_id": job["id"], "detail": job.get("stage_detail")}
        await asyncio.sleep(1.0)
        job = await ctx.import_jobs.get(job["id"]) or {**job, "status": "failed",
                                                       "error": "import job disappeared"}
    paper_id = job.get("paper_id")
    if job["status"] == "completed" and paper_id:
        ctx.imported[source] = paper_id
        if paper_id not in ctx.paper_ids:
            # newly imported papers join the run scope so later steps can cite them
            ctx.paper_ids.append(paper_id)
    return {
        "status": job["status"],
        "job_id": job.get("id"),
        "paper_id": paper_id,
        "title": job.get("title", ""),
        "error": job.get("error"),
    }


async def _library_import_web(args: dict[str, Any], ctx: RunContext) -> Any:
    from ..harness.web import web_fetch
    from ..library import documents, service

    url = _str_arg(args, "url")[:2000]
    page = await web_fetch(url, max_chars=200_000)  # refuses local and private addresses
    text = str(page.get("text") or "").strip()
    if not text:
        raise ValueError(page.get("note") or "the page has no readable text")
    title = str(page.get("title") or "").strip() or url
    body = f"# {title}\n\nSource: {page.get('final_url') or url}\n\n{text}"
    pdf, _ = await asyncio.to_thread(documents.to_pdf, "page.txt", body.encode("utf-8"))
    paper = await asyncio.to_thread(service.create_from_pdf_bytes, pdf,
                                    {"source": "web", "url": page.get("final_url") or url, "title": title[:300]})
    ctx.imported[url] = paper["id"]
    if paper["id"] not in ctx.paper_ids:
        ctx.paper_ids.append(paper["id"])
    return {"status": "completed", "paper_id": paper["id"], "title": paper.get("title", title),
            "truncated": bool(page.get("truncated"))}


async def _library_search(args: dict[str, Any], ctx: RunContext) -> Any:
    query = _str_arg(args, "query")
    k = _int_arg(args, "k", 8, 1, 20)
    papers = store.list_papers()
    ranked = bm25_rank(query, [f"{p.get('title') or ''} {p.get('abstract') or ''}" for p in papers])
    hits = [papers[i] for i, score in ranked if score > 0][:k]
    return {
        "query": query,
        "papers": [{**_brief(p), "abstract": (p.get("abstract") or "")[:400]} for p in hits],
    }


READER_SYSTEM = (
    "You are a reader sub-agent of GlossX. You see only excerpts of ONE paper. "
    "Extract the findings that bear on the question, each as a short factual "
    "claim citing the excerpt ids ([S:<id>]) that support it, and rate how "
    "relevant the paper is to the question (0 = unrelated, 1 = central)."
)
READER_SHAPE = (
    '{"relevance": 0.0, "findings": [{"claim": "...", "span_ids": ["<paper_id>#<n>"]}]}'
)


def _resolve_reader_paper(args: dict[str, Any], ctx: RunContext) -> str:
    paper_id = _str_arg(args, "paper_id", required=False)
    source = _str_arg(args, "source", required=False)
    if not paper_id and source:
        paper_id = ctx.imported.get(source, "")
    if not paper_id:
        raise ValueError("paper is not in the library (import denied, failed or still pending)")
    if not store.get_paper(paper_id):
        raise ValueError(f"paper not found: {paper_id}")
    return paper_id


async def _agent_reader(args: dict[str, Any], ctx: RunContext) -> Any:
    paper_id = _resolve_reader_paper(args, ctx)
    question = _str_arg(args, "question", required=False) or ctx.goal
    if not question:
        raise ValueError("'question' is required")
    k = _int_arg(args, "k", 8, 2, 12)
    paper = store.get_paper(paper_id) or {}
    spans = ctx.spans.search(question, [paper_id], k=k)
    if not spans:
        return {"paper_id": paper_id, "title": paper.get("title", ""), "relevance": 0.0,
                "findings": [], "note": "no parsed text for this paper"}
    excerpt = ctx.spans.materialize([s["id"] for s in spans], max_tokens=2500)
    provided = {s["id"] for s in spans}
    user = (
        f"QUESTION:\n{question}\n\nPAPER: {paper.get('title') or paper_id}\n\n"
        f"EXCERPTS:\n{wrap_untrusted(f'paper:{paper_id}', excerpt)}"
    )
    provider = providers.get_provider(ctx.provider)
    shared = callable(getattr(provider, "complete_session", None))
    with providers.feature_context(
        paper_id=paper_id, context_mode="shared_session" if shared else "full"
    ):
        out = await json_complete(
            f"{READER_SYSTEM}\n\n{UNTRUSTED_RULE}", user, READER_SHAPE, provider=ctx.provider
        )
    out = out if isinstance(out, dict) else {}
    try:
        relevance = max(0.0, min(1.0, float(out.get("relevance", 0.0))))
    except (TypeError, ValueError):
        relevance = 0.0
    findings = []
    for f in out.get("findings") if isinstance(out.get("findings"), list) else []:
        if not isinstance(f, dict) or not str(f.get("claim") or "").strip():
            continue
        ids = f.get("span_ids") if isinstance(f.get("span_ids"), list) else []
        findings.append({
            "claim": str(f["claim"]).strip()[:600],
            "span_ids": [str(i) for i in dict.fromkeys(ids) if str(i) in provided],
        })
    cited = {sid for f in findings for sid in f["span_ids"]}
    evidence = [s for s in spans if s["id"] in cited] or spans[:3]
    return {
        "paper_id": paper_id,
        "title": paper.get("title", ""),
        "relevance": relevance,
        "findings": findings[:12],
        "evidence": evidence,
    }


def _project_of(ctx: RunContext) -> str:
    if not ctx.project_id or not projects.exists(ctx.project_id):
        raise ValueError("this run does not belong to a project")
    return ctx.project_id


async def _paper_translate(args: dict[str, Any], ctx: RunContext) -> Any:
    span_ids = args.get("span_ids")
    span_ids = [str(s) for s in span_ids] if isinstance(span_ids, list) else []
    text = _str_arg(args, "text", required=False)[:6000]
    spans = [s for s in (ctx.spans.get(sid) for sid in span_ids[:12]) if s]
    if not text and not spans:
        raise ValueError("give 'text' or 'span_ids' to translate")
    language = _str_arg(args, "language", required=False) or None
    out: dict[str, Any] = {"language": language or "output language"}
    if text:
        out["translation"] = await translate_text(text, language=language,
                                                  provider=ctx.provider)
    if spans:
        # one call for all spans; markers keep each translation tied to its span id
        joined = "\n".join(f"[S:{s['id']}] {' '.join(s['text'].split())}" for s in spans)
        out["span_translations"] = await translate_text(joined, language=language, provider=ctx.provider)
        out["span_ids"] = [s["id"] for s in spans]
    return out


def _highlight_rects(paper_id: str, span: dict) -> tuple[int, list] | None:
    bbox = span.get("bbox")
    if isinstance(span.get("page"), int) and isinstance(bbox, list) and len(bbox) == 4:
        return span["page"], [bbox]
    loc = ingest.locate_text(store.pdf_path(paper_id), span.get("text", "")[:200])
    return (loc["page"], loc["rects"]) if loc else None


async def _paper_highlight(args: dict[str, Any], ctx: RunContext) -> Any:
    span_ids = args.get("span_ids")
    if not isinstance(span_ids, list) or not span_ids:
        raise ValueError("'span_ids' must be a non-empty list of evidence span ids")
    note = _str_arg(args, "note", required=False)[:500]
    category = (_str_arg(args, "category", required=False) or "result").lower()
    if category not in HIGHLIGHT_COLORS:
        category = "result"
    saved, skipped = [], []
    for sid in [str(x) for x in span_ids][:20]:
        span = ctx.spans.get(sid)
        if not span or not store.get_paper(span["paper_id"]):
            skipped.append(sid)
            continue
        where = await asyncio.to_thread(_highlight_rects, span["paper_id"], span)
        if not where:
            skipped.append(sid)
            continue
        page, rects = where
        h = store.add_highlight(span["paper_id"], {
            "page": page, "rects": rects, "color": HIGHLIGHT_COLORS[category],
            "category": category, "text": span["text"][:2000],
            "note": note or f"GlossX run {ctx.run_id}", "kind": "agent",
        })
        saved.append({"id": h["id"], "span_id": sid, "paper_id": span["paper_id"], "page": page})
    return {"highlights": saved, "skipped": skipped}


async def _learner_profile(args: dict[str, Any], ctx: RunContext) -> Any:
    from . import learner

    query = _str_arg(args, "query", required=False) or ctx.goal
    concepts = learner.profile(query, k=_int_arg(args, "k", 8, 1, 30))
    return {"query": query, "concepts": concepts,
            "note": "mastery is 0.5 for untested concepts; below 0.4 means the learner struggles"}


async def _learner_record(args: dict[str, Any], ctx: RunContext) -> Any:
    from . import learner

    entry = learner.record(_str_arg(args, "concept"), _str_arg(args, "outcome"),
                           _str_arg(args, "note", required=False))
    return {"status": "saved", "concept": entry["name"], "mastery": entry["mastery"]}


CRITIC_SYSTEM = (
    "You are the critic of a GlossX reading team. Several reader sub-agents each "
    "read one paper and reported findings with cited excerpts. Cross-check them: "
    "find findings that conflict across papers, findings their cited excerpts do "
    "not support, and important gaps for the question. For each problem worth "
    "settling, address a challenge to the reader of one paper: a precise question "
    "that rereading that paper could answer."
)
CRITIC_SHAPE = (
    '{"conflicts": [{"finding_ids": ["r0.f0", "r1.f0"], "issue": "..."}], '
    '"unsupported": [{"finding_id": "r0.f1", "issue": "..."}], '
    '"challenges": [{"paper_id": "<paper id>", "question": "..."}]}'
)
MAX_CHALLENGES = 6


async def _agent_critic(args: dict[str, Any], ctx: RunContext) -> Any:
    readings = args.get("readings")
    if isinstance(readings, dict):
        readings = [readings]
    readings = [r for r in readings if isinstance(r, dict)] if isinstance(readings, list) else []
    if not readings:
        raise ValueError("'readings' must be a list of reader results")
    question = _str_arg(args, "question", required=False) or ctx.goal
    lines: list[str] = []
    finding_ids: set[str] = set()
    cited: list[str] = []
    papers: dict[str, str] = {}
    for i, reading in enumerate(readings[:8]):
        pid = str(reading.get("paper_id") or "")
        papers[pid] = str(reading.get("title") or pid)
        lines.append(f"READER r{i} on paper {pid} ({papers[pid]}):")
        for j, f in enumerate((reading.get("findings") or [])[:12]):
            if not isinstance(f, dict):
                continue
            ids = [str(x) for x in f.get("span_ids") or []]
            cited.extend(ids)
            finding_ids.add(f"r{i}.f{j}")
            lines.append(f"  [r{i}.f{j}] {f.get('claim', '')} (cites: {', '.join(ids) or 'nothing'})")
    evidence = ctx.spans.materialize(list(dict.fromkeys(cited)), max_tokens=3000)
    user = (
        f"QUESTION:\n{question}\n\nFINDINGS:\n{wrap_untrusted('reader-findings', chr(10).join(lines))}\n\n"
        f"CITED EXCERPTS:\n{wrap_untrusted('evidence-spans', evidence or '(none)')}"
    )
    out = await json_complete(f"{CRITIC_SYSTEM}\n\n{UNTRUSTED_RULE}", user, CRITIC_SHAPE, provider=ctx.provider)
    out = out if isinstance(out, dict) else {}

    def rows(key: str) -> list[dict]:
        value = out.get(key)
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    conflicts = [
        {"finding_ids": [str(x) for x in r.get("finding_ids") or [] if str(x) in finding_ids],
         "issue": str(r.get("issue") or "")[:400]}
        for r in rows("conflicts")
    ]
    unsupported = [
        {"finding_id": str(r.get("finding_id")), "issue": str(r.get("issue") or "")[:400]}
        for r in rows("unsupported") if str(r.get("finding_id")) in finding_ids
    ]
    # challenges go back to readers, so they must name a paper the team actually read
    challenges = [
        {"paper_id": str(r.get("paper_id")), "question": " ".join(str(r.get("question") or "").split())[:500]}
        for r in rows("challenges") if str(r.get("paper_id")) in papers and str(r.get("question") or "").strip()
    ][:MAX_CHALLENGES]
    return {
        "conflicts": [c for c in conflicts if c["finding_ids"]],
        "unsupported": unsupported,
        "challenges": challenges,
    }


async def _memory_recall(args: dict[str, Any], ctx: RunContext) -> Any:
    project_id = _project_of(ctx)
    query = _str_arg(args, "query")
    k = _int_arg(args, "k", 5, 1, 20)
    notes = projects.recall(project_id, query, k=k)
    return {"query": query, "notes": [
        {"id": n["id"], "text": n["text"], "span_ids": n.get("span_ids") or []} for n in notes
    ]}


async def _memory_note(args: dict[str, Any], ctx: RunContext) -> Any:
    project_id = _project_of(ctx)
    text = _str_arg(args, "text")[:2000]
    note = projects.add_memory(project_id, text, source_run=ctx.run_id)
    return {"status": "saved", "id": note["id"]}


BUILTINS: list[Capability] = [
    Capability(
        name="library.list",
        kind="source",
        card="List papers in the local library with id, title, year and first authors.",
        input_schema={},
        handler=_library_list,
        permissions=["library:read"],
    ),
    Capability(
        name="paper.read",
        kind="tool",
        card="Read a paper's metadata, abstract, section outline and opening text.",
        input_schema={
            "paper_id": "str: library paper id",
            "max_chars": "int (optional, default 3000): opening text length",
        },
        handler=_paper_read,
        permissions=["library:read"],
    ),
    Capability(
        name="paper.search_spans",
        kind="tool",
        card="Find citable evidence passages (with page) in scope papers by keyword search; use for any factual question about paper content.",
        input_schema={
            "query": "str: keywords or question",
            "paper_ids": "list[str] (optional): defaults to the run scope",
            "k": "int (optional, default 6, max 12)",
        },
        handler=_paper_search_spans,
        permissions=["library:read"],
    ),
    Capability(
        name="paper.summary",
        kind="agent",
        card="Structured summary of one paper: TL;DR, problem, method, results, contributions, limitations.",
        input_schema={"paper_id": "str: library paper id"},
        handler=_paper_summary,
        permissions=["library:read", "llm"],
        cost="llm",
    ),
    Capability(
        name="paper.explain",
        kind="agent",
        card="Explain a passage, equation, table or term from a paper in plain language.",
        input_schema={
            "paper_id": "str: library paper id",
            "text": "str: the passage or term to explain",
        },
        handler=_paper_explain,
        permissions=["library:read", "llm"],
        cost="llm",
    ),
    Capability(
        name="paper.references",
        kind="tool",
        card="List a paper's bibliography, resolved to title/authors/year/DOI/arXiv when possible.",
        input_schema={"paper_id": "str: library paper id"},
        handler=_paper_references,
        permissions=["library:read", "network"],
        cost="network",
    ),
    Capability(
        name="scholar.search",
        kind="source",
        card="Search Semantic Scholar / arXiv for papers outside the library.",
        input_schema={"query": "str: search query", "k": "int (optional, default 8, max 10)"},
        handler=_scholar_search,
        permissions=["network"],
        cost="network",
    ),
    Capability(
        name="library.import",
        kind="tool",
        card="Import a paper into the library by arXiv id, DOI or PDF URL and add it to the run scope.",
        input_schema={"source": "str: arXiv id, DOI or URL"},
        handler=_library_import,
        permissions=["library:write", "network"],
        cost="network",
        side_effects="adds a paper to the library",
    ),
    Capability(
        name="library.import_web",
        kind="source",
        card="Save a web page (blog post, documentation, news) into the library as a citable source and add it to the run scope.",
        input_schema={"url": "str: http(s) URL of the page"},
        handler=_library_import_web,
        permissions=["library:write", "network"],
        cost="network",
        side_effects="adds the page to the library",
    ),
    Capability(
        name="library.search",
        kind="source",
        card="Search the local library's papers by title and abstract keywords; returns paper ids.",
        input_schema={"query": "str: keywords", "k": "int (optional, default 8)"},
        handler=_library_search,
        permissions=["library:read"],
    ),
    Capability(
        name="agent.reader",
        kind="agent",
        card="Reader sub-agent: read one library paper in isolation and return relevance plus findings with cited span ids.",
        input_schema={
            "paper_id": "str: library paper id",
            "question": "str: what to look for (defaults to the goal)",
        },
        output_schema={"relevance": "number 0..1", "findings": "[{claim, span_ids}]"},
        handler=_agent_reader,
        permissions=["library:read", "llm"],
        cost="llm",
    ),
    Capability(
        name="paper.translate",
        kind="tool",
        card="Translate a passage or evidence spans (by span id) into another language, keeping span ids attached.",
        input_schema={
            "text": "str (optional): text to translate",
            "span_ids": "list[str] (optional): evidence span ids to translate",
            "language": "str (optional): target language, defaults to the output language",
        },
        handler=_paper_translate,
        permissions=["library:read", "llm"],
        cost="llm",
    ),
    Capability(
        name="paper.highlight",
        kind="tool",
        card="Highlight evidence spans in the PDF reader so the user can see where a claim comes from.",
        input_schema={
            "span_ids": "list[str]: evidence span ids to highlight",
            "category": "str (optional): contribution|method|result|limitation|definition|background",
            "note": "str (optional): note shown with the highlight",
        },
        handler=_paper_highlight,
        permissions=["library:read", "highlight:write"],
        side_effects="writes highlights to the library",
    ),
    Capability(
        name="agent.critic",
        kind="agent",
        card="Critic sub-agent: cross-check several readers' findings for conflicts and unsupported claims, and return challenges for readers to settle by rereading.",
        input_schema={
            "readings": "list: agent.reader results to cross-check",
            "question": "str (optional): the question the team works on (defaults to the goal)",
        },
        output_schema={"conflicts": "[{finding_ids, issue}]", "unsupported": "[{finding_id, issue}]",
                       "challenges": "[{paper_id, question}]"},
        handler=_agent_critic,
        permissions=["library:read", "llm"],
        cost="llm",
    ),
    Capability(
        name="learner.profile",
        kind="memory",
        card="Learner model: which concepts the user has mastered or struggles with (quiz history), to adapt explanations.",
        input_schema={"query": "str (optional): topic to look up, defaults to the goal",
                      "k": "int (optional, default 8)"},
        handler=_learner_profile,
        permissions=["memory:read"],
    ),
    Capability(
        name="learner.record",
        kind="memory",
        card="Record how the learner did on a concept: a quiz answer correct or wrong, or that it was explained.",
        input_schema={"concept": "str: the concept", "outcome": "str: correct|wrong|explained",
                      "note": "str (optional): what went wrong or right"},
        handler=_learner_record,
        permissions=["memory:write"],
        side_effects="updates the learner model",
    ),
    Capability(
        name="memory.recall",
        kind="memory",
        card="Recall earlier findings and notes saved in this run's research project.",
        input_schema={"query": "str: what to recall", "k": "int (optional, default 5)"},
        handler=_memory_recall,
        permissions=["memory:read"],
    ),
    Capability(
        name="memory.note",
        kind="memory",
        card="Save a short note or decision to this run's research project memory.",
        input_schema={"text": "str: the note to remember"},
        handler=_memory_note,
        permissions=["memory:write"],
        side_effects="writes a project memory note",
    ),
]


def register_builtins(registry: CapabilityRegistry) -> None:
    for cap in BUILTINS:
        registry.register(cap)
