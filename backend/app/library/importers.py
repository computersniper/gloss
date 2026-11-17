"""Fetch papers from arXiv, a DOI (Crossref), or a direct PDF URL.

All network calls degrade gracefully — if a host is unreachable the caller gets a
clear error and can fall back to uploading a PDF.
"""
from __future__ import annotations

import asyncio
import contextlib
import re

from ..net import UA, external_client  # noqa: F401  (UA re-exported for callers)

_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
_ARXIV_OLD = re.compile(r"([a-z\-]+/\d{7})(v\d+)?", re.IGNORECASE)


def normalize_arxiv_id(s: str) -> str | None:
    s = s.strip()
    m = _ARXIV_RE.search(s) or _ARXIV_OLD.search(s)
    return m.group(0) if m else None


async def _fetch_arxiv_metadata(client, arxiv_id: str) -> dict:
    """Best-effort Atom metadata. Cancellation must propagate to the caller."""
    import feedparser

    try:
        r = await client.get(
            "http://export.arxiv.org/api/query",
            params={"id_list": arxiv_id, "max_results": 1},
        )
        feed = feedparser.parse(r.text)
        if not feed.entries:
            return {}
        e = feed.entries[0]
        return {
            "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
            "authors": [a.get("name", "") for a in e.get("authors", [])],
            "abstract": re.sub(r"\s+", " ", e.get("summary", "")).strip(),
            "year": e.get("published", "")[:4],
            "doi": e.get("arxiv_doi", "") or "",
        }
    except asyncio.CancelledError:
        raise
    except Exception:
        return {}


async def fetch_arxiv(arxiv_id: str) -> tuple[dict, bytes]:
    """Return (metadata, pdf_bytes) without making metadata a PDF dependency."""
    meta: dict = {"source": "arxiv", "arxiv_id": arxiv_id}
    async with external_client(timeout=60) as client:
        metadata_task = asyncio.create_task(_fetch_arxiv_metadata(client, arxiv_id))
        try:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            resp = await client.get(pdf_url)
            resp.raise_for_status()
            if metadata_task.done() and not metadata_task.cancelled():
                meta.update(metadata_task.result())
            return meta, resp.content
        finally:
            if not metadata_task.done():
                metadata_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await metadata_task


async def fetch_arxiv_tex(arxiv_id: str) -> dict:
    """Fetch the LaTeX source of an arXiv paper → {files: [{name, tex}], main}.

    arXiv `e-print` is usually a gzipped tar of the source; sometimes a single
    gzipped .tex, or (rare) raw text. We keep only the .tex/.bbl files.
    """
    import gzip
    import io
    import tarfile

    url = f"https://arxiv.org/e-print/{arxiv_id}"
    async with external_client(timeout=90) as client:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
        data = r.content

    files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for m in tar.getmembers():
                if m.isfile() and m.name.lower().endswith((".tex", ".bbl")):
                    f = tar.extractfile(m)
                    if f:
                        files[m.name.lstrip("./")] = f.read().decode("utf-8", "replace")
    except (tarfile.TarError, EOFError):
        try:
            files["main.tex"] = gzip.decompress(data).decode("utf-8", "replace")
        except OSError:
            try:
                files["main.tex"] = data.decode("utf-8", "replace")
            except Exception:
                files = {}

    if not files:
        return {"files": [], "main": None}
    # main = the file that has \documentclass or \begin{document}; else the largest
    main = None
    for name, tex in files.items():
        if "\\documentclass" in tex or "\\begin{document}" in tex:
            main = name
            break
    if not main:
        main = max(files, key=lambda n: len(files[n]))
    ordered = [{"name": main, "tex": files[main]}]
    ordered += [{"name": n, "tex": t} for n, t in files.items() if n != main]
    return {"files": ordered, "main": main}


async def fetch_pdf_url(url: str) -> tuple[dict, bytes]:
    async with external_client(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "pdf" not in ct and not resp.content[:5] == b"%PDF-":
            raise ValueError(f"URL did not return a PDF (content-type: {ct})")
        return {"source": "url"}, resp.content


async def fetch_doi(doi: str, *, client_factory=None) -> tuple[dict, bytes | None]:
    """Crossref metadata; try to grab an open-access PDF link if present."""
    doi = doi.strip().replace("https://doi.org/", "")
    meta: dict = {"source": "doi", "doi": doi}
    pdf_bytes: bytes | None = None
    async with (client_factory or external_client)(timeout=60) as client:
        try:
            r = await client.get(f"https://api.crossref.org/works/{doi}")
            r.raise_for_status()
            m = r.json().get("message", {})
            meta["title"] = (m.get("title") or [""])[0]
            meta["authors"] = [
                f"{a.get('given','')} {a.get('family','')}".strip() for a in m.get("author", [])
            ]
            dp = m.get("published-print") or m.get("published-online") or {}
            parts = dp.get("date-parts", [[None]])
            meta["year"] = str(parts[0][0]) if parts and parts[0] and parts[0][0] else ""
            meta["abstract"] = re.sub(r"<[^>]+>", "", m.get("abstract", "") or "")
            # try open-access pdf link
            for link in m.get("link", []):
                if "pdf" in (link.get("content-type", "") or ""):
                    try:
                        pr = await client.get(link["URL"])
                        if pr.status_code == 200 and pr.content[:5] == b"%PDF-":
                            pdf_bytes = pr.content
                            break
                    except Exception:
                        continue
        except Exception as e:
            raise ValueError(f"Crossref lookup failed for DOI {doi}: {e}")
    return meta, pdf_bytes


async def import_source(payload: dict) -> tuple[dict, bytes]:
    """Dispatch on payload keys. Returns (meta, pdf_bytes) or raises ValueError."""
    raw = (payload.get("query") or payload.get("arxiv") or payload.get("doi")
           or payload.get("url") or "").strip()
    if not raw:
        raise ValueError("empty import query")

    # explicit fields win
    if payload.get("arxiv"):
        aid = normalize_arxiv_id(payload["arxiv"]) or payload["arxiv"]
        return await fetch_arxiv(aid)
    if payload.get("doi"):
        meta, pdf = await fetch_doi(payload["doi"])
        if not pdf:
            raise ValueError("No open-access PDF found for this DOI — please upload the PDF.")
        return meta, pdf
    if payload.get("url") and payload["url"].lower().endswith(".pdf"):
        return await fetch_pdf_url(payload["url"])

    # infer from a free-form query
    aid = normalize_arxiv_id(raw)
    if aid and ("arxiv" in raw.lower() or re.fullmatch(r"(\d{4}\.\d{4,5})(v\d+)?", raw)):
        return await fetch_arxiv(aid)
    if raw.lower().startswith("http") and raw.lower().split("?")[0].endswith(".pdf"):
        return await fetch_pdf_url(raw)
    if re.match(r"10\.\d{4,9}/", raw):
        meta, pdf = await fetch_doi(raw)
        if not pdf:
            raise ValueError("No open-access PDF found for this DOI — please upload the PDF.")
        return meta, pdf
    if aid:
        return await fetch_arxiv(aid)
    raise ValueError(f"Could not interpret import query: {raw!r}")
