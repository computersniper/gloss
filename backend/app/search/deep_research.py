"""Bounded, provenance-preserving network research for analysis plugins."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Awaitable

from ..library import store
from ..pdf import structure
from . import scholar

NETWORK_RESEARCH_REVISION = "network-research-v1"
MAX_SOURCES = 24


class DeepResearchNetworkError(ValueError):
    """No trustworthy external evidence could be retrieved."""


def _looks_like_identifier_title(value: str) -> bool:
    title = (value or "").strip().lower()
    return (
        not title
        or title.startswith(("arxiv:", "doi:", "http://", "https://"))
        or title in {"untitled", "unknown"}
        or title.endswith(".pdf")
    )


def _title_from_text(text: str) -> str:
    skip = re.compile(
        r"arxiv:|copyright|permission|attribution|reproduce|journalistic|"
        r"scholarly works|all rights reserved|https?://|@",
        re.IGNORECASE,
    )
    for block in re.split(r"\n\s*\n", (text or "")[:8000]):
        candidate = " ".join(block.split()).strip(" -–—")
        words = candidate.split()
        if 3 <= len(words) <= 30 and 12 <= len(candidate) <= 220 and not skip.search(candidate):
            return candidate
    return ""


def _abstract_from_text(text: str) -> str:
    match = re.search(
        r"\bAbstract\b\s*(.{80,5000}?)\s*(?:\b1\s*\.?\s*Introduction\b|"
        r"\bIntroduction\b|\bKeywords?\b)",
        text or "",
        re.IGNORECASE | re.DOTALL,
    )
    return " ".join(match.group(1).split())[:1800] if match else ""


def _effective_paper(paper: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """Repair importer header titles for search without changing library metadata."""
    effective = dict(paper)
    full_text = str(parsed.get("full_text") or "")
    if _looks_like_identifier_title(str(effective.get("title") or "")):
        recovered = _title_from_text(full_text)
        if recovered:
            effective["title"] = recovered
    if not effective.get("abstract"):
        effective["abstract"] = _abstract_from_text(full_text)
    return effective


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (value or "").lower()).strip()


def _abstract_keywords(text: str, limit: int = 10) -> list[str]:
    stopwords = {
        "about", "after", "before", "between", "could", "from", "have",
        "into", "paper", "research", "study", "that", "their", "these",
        "this", "through", "using", "were", "which", "with",
    }
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}|[\u4e00-\u9fff]{2,6}", text or "")
    output = []
    seen = set()
    for word in words:
        normalized = word.lower()
        if normalized in stopwords or normalized in seen:
            continue
        seen.add(normalized)
        output.append(word)
        if len(output) >= limit:
            break
    return output


def build_queries(paper: dict[str, Any], *, depth: str = "exhaustive") -> list[str]:
    title = " ".join(str(paper.get("title") or "").split())[:220]
    if not title:
        raise DeepResearchNetworkError("Deep Research needs a paper title for network search")
    queries = [title]
    keywords = _abstract_keywords(str(paper.get("abstract") or ""))
    tags = [str(tag).strip() for tag in paper.get("tags") or [] if str(tag).strip()]
    topical = " ".join([*tags[:3], *keywords[:8]]).strip()
    if topical and _normalized_title(topical) != _normalized_title(title):
        queries.append(topical[:220])
    else:
        queries.append(f"{title[:180]} review")
    if depth == "exhaustive":
        authors = [str(author).strip() for author in paper.get("authors") or []]
        author = authors[0] if authors else ""
        title_terms = " ".join(title.split()[:10])
        third = " ".join(part for part in (author, title_terms, "related work") if part)
        if _normalized_title(third) not in {_normalized_title(item) for item in queries}:
            queries.append(third[:220])
    return queries[:3]


async def _attempt(
    provider: str, query: str, request: Awaitable[list[dict[str, Any]]]
) -> dict[str, Any]:
    try:
        results = await request
        return {
            "provider": provider,
            "query": query,
            "ok": True,
            "results": results,
            "error": "",
        }
    except asyncio.CancelledError:
        raise
    except Exception as error:  # provider/network errors are reported, never fabricated
        return {
            "provider": provider,
            "query": query,
            "ok": False,
            "results": [],
            "error": f"{type(error).__name__}: {str(error)[:240]}",
        }


def _source_url(item: dict[str, Any]) -> str:
    if item.get("url"):
        return str(item["url"])
    if item.get("doi"):
        return f"https://doi.org/{item['doi']}"
    if item.get("arxiv_id"):
        return f"https://arxiv.org/abs/{item['arxiv_id']}"
    return ""


def _deduplicate(
    rows: list[dict[str, Any]], target_title: str
) -> list[dict[str, Any]]:
    target = _normalized_title(target_title)
    output = []
    seen = set()
    for item in rows:
        title = " ".join(str(item.get("title") or "").split())
        normalized = _normalized_title(title)
        if not normalized or normalized == target:
            continue
        doi = str(item.get("doi") or "").lower().removeprefix("https://doi.org/")
        key = f"doi:{doi}" if doi else f"title:{normalized}"
        if key in seen:
            continue
        seen.add(key)
        output.append({
            "title": title,
            "authors": [str(author) for author in item.get("authors") or [] if author][:8],
            "year": item.get("year"),
            "abstract": " ".join(str(item.get("abstract") or "").split())[:1000],
            "url": _source_url(item),
            "doi": doi,
            "citations": item.get("citations"),
            "database": item.get("source", "unknown"),
            "relation": item.get("relation", "search"),
        })
        if len(output) >= MAX_SOURCES:
            break
    for index, item in enumerate(output, 1):
        item["source_id"] = f"S{index}"
    return output


def _paper_reference_excerpts(parsed: dict[str, Any]) -> list[str]:
    raw = structure.find_references_text(parsed)
    if not raw:
        return []
    return [" ".join(item.split())[:500] for item in structure.split_reference_entries(raw)[:12]]


async def collect_external_evidence(
    paper_id: str, *, depth: str = "exhaustive"
) -> dict[str, Any]:
    """Search two live indexes, then expand the paper's citation neighborhood."""
    paper = store.get_paper(paper_id)
    parsed = store.load_parsed(paper_id)
    if not paper or not parsed:
        raise ValueError("paper not parsed")
    effective_paper = _effective_paper(paper, parsed)
    queries = build_queries(effective_paper, depth=depth)
    per_query = 7 if depth == "exhaustive" else 5

    first = await asyncio.gather(
        _attempt(
            "Semantic Scholar", queries[0],
            scholar.search_semantic_scholar(queries[0], per_query),
        ),
        _attempt("OpenAlex", queries[0], scholar.search_openalex(queries[0], per_query)),
    )
    first_rows = [row for attempt in first for row in attempt["results"]]
    target = _normalized_title(str(effective_paper.get("title") or ""))
    semantic_matches = [
        item for item in first[0]["results"]
        if item.get("paper_id") and _normalized_title(str(item.get("title") or "")) == target
    ]
    semantic_target = semantic_matches[0] if semantic_matches else None
    openalex_target = next(
        (
            item for item in first[1]["results"]
            if item.get("paper_id")
            and _normalized_title(str(item.get("title") or "")) == target
        ),
        None,
    )

    followup_requests: list[Awaitable[dict[str, Any]]] = []
    for query in queries[1:]:
        followup_requests.extend([
            _attempt(
                "Semantic Scholar", query,
                scholar.search_semantic_scholar(query, per_query),
            ),
            _attempt("OpenAlex", query, scholar.search_openalex(query, per_query)),
        ])
    if semantic_target:
        followup_requests.append(_attempt(
            "Semantic Scholar citation graph",
            str(semantic_target.get("title") or queries[0]),
            scholar.semantic_scholar_connections(
                str(semantic_target["paper_id"]), 7 if depth == "exhaustive" else 4
            ),
        ))
    if openalex_target:
        followup_requests.append(_attempt(
            "OpenAlex citation graph",
            str(openalex_target.get("title") or queries[0]),
            scholar.openalex_connections(
                str(openalex_target["paper_id"]), 7 if depth == "exhaustive" else 4
            ),
        ))
    followups = await asyncio.gather(*followup_requests) if followup_requests else []
    attempts = [*first, *followups]
    rows = [*first_rows, *(row for attempt in followups for row in attempt["results"])]
    sources = _deduplicate(rows, str(effective_paper.get("title") or ""))
    successful = [attempt for attempt in attempts if attempt["ok"]]
    if not successful:
        details = "; ".join(
            f"{attempt['provider']}: {attempt['error']}" for attempt in attempts
        )
        raise DeepResearchNetworkError(
            "Deep Research could not reach scholarly search services. " + details[:600]
        )
    if not sources:
        raise DeepResearchNetworkError(
            "Scholarly search completed but returned no usable external sources"
        )

    providers = list(dict.fromkeys(attempt["provider"] for attempt in successful))
    return {
        "revision": NETWORK_RESEARCH_REVISION,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "providers": providers,
        "source_count": len(sources),
        "target": {
            "title": effective_paper.get("title", ""),
            "authors": effective_paper.get("authors", []),
            "year": effective_paper.get("year", ""),
            "doi": effective_paper.get("doi", ""),
            "arxiv_id": effective_paper.get("arxiv_id", ""),
        },
        "sources": sources,
        "paper_reference_excerpts": _paper_reference_excerpts(parsed),
        "provider_status": [
            {
                "provider": attempt["provider"],
                "query": attempt["query"],
                "ok": attempt["ok"],
                "result_count": len(attempt["results"]),
                "error": attempt["error"],
            }
            for attempt in attempts
        ],
    }


def prompt_bundle(evidence: dict[str, Any]) -> str:
    """Serialize only retrieved records; every external claim must point here."""
    payload = {
        "searched_at": evidence["searched_at"],
        "queries": evidence["queries"],
        "providers": evidence["providers"],
        "provider_status": evidence["provider_status"],
        "target": evidence["target"],
        "sources": evidence["sources"],
        "paper_reference_excerpts": evidence["paper_reference_excerpts"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def public_metadata(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "searched_at": evidence["searched_at"],
        "queries": evidence["queries"],
        "providers": evidence["providers"],
        "source_count": evidence["source_count"],
        "partial": any(not item["ok"] for item in evidence["provider_status"]),
        "failed_providers": list(dict.fromkeys(
            item["provider"] for item in evidence["provider_status"] if not item["ok"]
        )),
    }
