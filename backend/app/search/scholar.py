"""Scholar deep search: related-paper recommendations + search (Semantic Scholar / arXiv)."""
from __future__ import annotations

import asyncio
import re

from ..library import store
from ..net import external_client

S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "paperId,title,abstract,year,authors,url,externalIds,citationCount"
S2_PAPER = "https://api.semanticscholar.org/graph/v1/paper"
OPENALEX_WORKS = "https://api.openalex.org/works"


def _semantic_scholar_item(it: dict, *, relation: str = "search") -> dict:
    ext = it.get("externalIds") or {}
    return {
        "paper_id": it.get("paperId", ""),
        "title": it.get("title", ""),
        "abstract": (it.get("abstract") or "")[:1200],
        "year": it.get("year"),
        "authors": [a.get("name", "") for a in it.get("authors", [])][:8],
        "url": it.get("url", ""),
        "arxiv_id": ext.get("ArXiv", ""),
        "doi": ext.get("DOI", ""),
        "citations": it.get("citationCount"),
        "source": "semanticscholar",
        "relation": relation,
    }


async def search_semantic_scholar(query: str, k: int = 10) -> list[dict]:
    async with external_client(timeout=25) as client:
        r = await client.get(
            S2_SEARCH, params={"query": query, "limit": k, "fields": S2_FIELDS}
        )
        r.raise_for_status()
        data = r.json().get("data", [])
    return [_semantic_scholar_item(it) for it in data]


def _openalex_abstract(inverted: dict | None) -> str:
    if not isinstance(inverted, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        positioned.extend((int(position), str(word)) for position in positions)
    return " ".join(word for _, word in sorted(positioned))[:1200]


def _openalex_item(item: dict, *, relation: str = "search") -> dict:
    location = item.get("primary_location") or {}
    authorships = item.get("authorships") or []
    doi_url = item.get("doi") or ""
    return {
        "paper_id": item.get("id", ""),
        "title": item.get("display_name", ""),
        "abstract": _openalex_abstract(item.get("abstract_inverted_index")),
        "year": item.get("publication_year"),
        "authors": [
            (entry.get("author") or {}).get("display_name", "")
            for entry in authorships[:8]
        ],
        "url": location.get("landing_page_url") or doi_url or item.get("id", ""),
        "arxiv_id": "",
        "doi": doi_url.removeprefix("https://doi.org/"),
        "citations": item.get("cited_by_count"),
        "source": "openalex",
        "relation": relation,
    }


async def search_openalex(query: str, k: int = 10) -> list[dict]:
    """Cross-disciplinary scholarly search with source URLs and abstracts."""
    async with external_client(timeout=25) as client:
        response = await client.get(
            OPENALEX_WORKS,
            params={
                "search": query,
                "per-page": max(1, min(k, 25)),
                "select": (
                    "id,display_name,publication_year,authorships,doi,"
                    "primary_location,cited_by_count,abstract_inverted_index"
                ),
            },
        )
        response.raise_for_status()
        data = response.json().get("results", [])
    return [_openalex_item(item) for item in data]


async def openalex_connections(work_id: str, k: int = 8) -> list[dict]:
    """Expand an exact OpenAlex work into referenced and citing works."""
    identifier = (work_id or "").rstrip("/").rsplit("/", 1)[-1]
    if not identifier.startswith("W"):
        return []
    fields = (
        "id,display_name,publication_year,authorships,doi,primary_location,"
        "cited_by_count,abstract_inverted_index"
    )
    async with external_client(timeout=25) as client:
        detail = await client.get(
            f"{OPENALEX_WORKS}/{identifier}",
            params={"select": "id,referenced_works"},
        )
        detail.raise_for_status()
        references = [
            value.rstrip("/").rsplit("/", 1)[-1]
            for value in detail.json().get("referenced_works", [])[:k]
        ]
        requests = []
        relations = []
        if references:
            requests.append(client.get(
                OPENALEX_WORKS,
                params={
                    "filter": "openalex_id:" + "|".join(references),
                    "per-page": len(references),
                    "select": fields,
                },
            ))
            relations.append("reference")
        requests.append(client.get(
            OPENALEX_WORKS,
            params={
                "filter": f"cites:{identifier}",
                "sort": "cited_by_count:desc",
                "per-page": max(1, min(k, 25)),
                "select": fields,
            },
        ))
        relations.append("citation")
        responses = await asyncio.gather(*requests)
        output = []
        for relation, response in zip(relations, responses):
            response.raise_for_status()
            output.extend(
                _openalex_item(item, relation=relation)
                for item in response.json().get("results", [])
            )
    return output


async def semantic_scholar_connections(paper_id: str, k: int = 8) -> list[dict]:
    """Expand one exact paper into references and citing papers."""
    if not paper_id:
        return []
    async with external_client(timeout=25) as client:
        references, citations = await asyncio.gather(
            client.get(
                f"{S2_PAPER}/{paper_id}/references",
                params={"limit": k, "fields": S2_FIELDS},
            ),
            client.get(
                f"{S2_PAPER}/{paper_id}/citations",
                params={"limit": k, "fields": S2_FIELDS},
            ),
        )
        references.raise_for_status()
        citations.raise_for_status()
    output = []
    for row in references.json().get("data", []):
        item = row.get("citedPaper") or {}
        if item.get("title"):
            output.append(_semantic_scholar_item(item, relation="reference"))
    for row in citations.json().get("data", []):
        item = row.get("citingPaper") or {}
        if item.get("title"):
            output.append(_semantic_scholar_item(item, relation="citation"))
    return output


async def search_arxiv(query: str, k: int = 10) -> list[dict]:
    import feedparser

    async with external_client(timeout=25) as client:
        r = await client.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "max_results": k},
        )
        feed = feedparser.parse(r.text)
    out = []
    for e in feed.entries:
        aid = ""
        m = re.search(r"(\d{4}\.\d{4,5})", e.get("id", ""))
        if m:
            aid = m.group(1)
        out.append({
            "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
            "abstract": re.sub(r"\s+", " ", e.get("summary", "")).strip()[:600],
            "year": e.get("published", "")[:4],
            "authors": [a.get("name", "") for a in e.get("authors", [])][:8],
            "url": e.get("id", ""),
            "arxiv_id": aid,
            "doi": "",
            "source": "arxiv",
        })
    return out


async def search(query: str, k: int = 10) -> list[dict]:
    """Prefer Semantic Scholar; fall back to arXiv on failure."""
    try:
        res = await search_semantic_scholar(query, k)
        if res:
            return res
    except Exception:
        pass
    try:
        return await search_arxiv(query, k)
    except Exception:
        return []


async def recommend(paper_id: str, k: int = 10) -> list[dict]:
    """Recommend related papers using this paper's title + abstract."""
    p = store.get_paper(paper_id)
    if not p:
        raise ValueError("paper not found")
    query = p.get("title", "")
    if not query:
        parsed = store.load_parsed(paper_id)
        query = (parsed["full_text"][:200] if parsed else "")
    results = await search(query, k + 3)
    title_l = (p.get("title") or "").lower().strip()
    filtered = [r for r in results if r.get("title", "").lower().strip() != title_l]
    return filtered[:k]
