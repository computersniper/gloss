"""Library / paper endpoints: import, list, read, delete."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agent import projects
from ..library import documents, importers, service, store
from ..library.import_jobs import (
    ImportCleanupError,
    ImportJobManager,
    ImportQueueFullError,
)
from ..pdf import ingest, structure

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _bounded_import_timeout(name: str, *, default: float = 120) -> float:
    try:
        configured = float(os.environ.get(name, str(default)))
    except ValueError:
        configured = 120
    return max(1, min(configured, 300))


IMPORT_DOWNLOAD_TIMEOUT = _bounded_import_timeout("GLOSS_IMPORT_TIMEOUT", default=300)
IMPORT_PARSE_TIMEOUT = _bounded_import_timeout("GLOSS_IMPORT_PARSE_TIMEOUT")
IMPORT_SAVE_TIMEOUT = _bounded_import_timeout("GLOSS_IMPORT_SAVE_TIMEOUT")


class ImportBody(BaseModel):
    query: str | None = None
    arxiv: str | None = None
    doi: str | None = None
    url: str | None = None


class PaperPatch(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    year: str | None = None
    reading_lens: Literal["auto", "humanities", "social_sciences", "stem"] | None = None


@router.get("")
async def list_papers():
    return {"papers": store.list_papers()}


@router.post("/upload")
async def upload_paper(file: UploadFile = File(...)):
    data = await file.read()
    meta = {"source": "upload", "title": ""}
    try:
        if data[:5] != b"%PDF-" and documents.is_supported(file.filename or ""):
            # Markdown, text, HTML and slides are rendered to a PDF so their spans stay clickable
            data, title = await run_in_threadpool(documents.to_pdf, file.filename or "", data)
            meta = {"source": "document", "title": title}
        paper = await run_in_threadpool(service.create_from_pdf_bytes, data, meta)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await run_in_threadpool(_seed_paper_project, paper["id"])
    return paper


@router.post("/import")
async def import_paper(body: ImportBody):
    try:
        meta, pdf = await asyncio.wait_for(
            importers.import_source(body.model_dump(exclude_none=True)),
            timeout=IMPORT_DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            f"Import timed out after {IMPORT_DOWNLOAD_TIMEOUT:g}s while downloading the paper",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # network / http errors
        raise HTTPException(502, f"Import failed: {e}")
    try:
        parsed = await run_in_threadpool(
            service.parse_pdf_bytes_with_timeout,
            pdf,
            IMPORT_PARSE_TIMEOUT,
        )
    except TimeoutError:
        raise HTTPException(
            504,
            f"Import timed out after {IMPORT_PARSE_TIMEOUT:g}s while parsing the PDF",
        )
    try:
        paper = await run_in_threadpool(
            service.create_from_pdf_bytes, pdf, meta, parsed
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await run_in_threadpool(_seed_paper_project, paper["id"])
    return paper


def _import_job_manager(request: Request) -> ImportJobManager:
    manager = getattr(request.app.state, "import_jobs", None)
    if manager is None:
        raise HTTPException(503, "import queue is not ready")
    return manager


@router.post("/import-jobs", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_import(body: ImportBody, request: Request):
    manager = _import_job_manager(request)
    try:
        return await manager.submit(body.model_dump(exclude_none=True))
    except ImportQueueFullError as error:
        raise HTTPException(429, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


@router.get("/import-jobs")
async def list_import_jobs(request: Request):
    return {"jobs": await _import_job_manager(request).list()}


@router.get("/import-jobs/{job_id}")
async def get_import_job(job_id: str, request: Request):
    job = await _import_job_manager(request).get(job_id)
    if job is None:
        raise HTTPException(404, "import job not found")
    return job


@router.delete("/import-jobs/{job_id}")
async def cancel_or_clear_import_job(job_id: str, request: Request):
    try:
        removed = await _import_job_manager(request).cancel_or_clear(job_id)
    except ImportCleanupError as error:
        raise HTTPException(500, str(error)) from error
    if not removed:
        raise HTTPException(404, "import job not found")
    return {"ok": True}


@router.get("/{paper_id}")
async def get_paper(paper_id: str):
    p = store.get_paper(paper_id)
    if not p:
        raise HTTPException(404, "paper not found")
    return p


@router.patch("/{paper_id}")
async def patch_paper(paper_id: str, body: PaperPatch):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    store.update_paper(paper_id, body.model_dump(exclude_none=True))
    return store.get_paper(paper_id)


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    projects.forget_paper(paper_id)
    store.delete_paper(paper_id)
    return {"ok": True}


@router.get("/{paper_id}/pdf")
async def get_pdf(paper_id: str):
    p = store.pdf_path(paper_id)
    if not p.exists():
        raise HTTPException(404, "pdf not found")
    return FileResponse(str(p), media_type="application/pdf", filename=f"{paper_id}.pdf")


@router.get("/{paper_id}/pages")
async def get_pages(paper_id: str):
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise HTTPException(404, "parsed data not found")
    # recompute sections from stored blocks so heuristic improvements apply to
    # already-ingested papers without re-parsing the PDF
    sections = structure.detect_sections(parsed)
    return {
        "n_pages": parsed["n_pages"],
        "pages": parsed["pages"],
        "sections": sections,
        "toc": parsed.get("toc", []),
    }


class LocateBody(BaseModel):
    text: str


@router.post("/{paper_id}/locate")
async def locate(paper_id: str, body: LocateBody):
    """Find where a snippet appears in the PDF → {page, rects} (for click-to-highlight)."""
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    loc = await run_in_threadpool(ingest.locate_text, store.pdf_path(paper_id), body.text)
    return loc or {"page": None, "rects": []}


@router.get("/{paper_id}/tex")
async def get_tex(paper_id: str):
    """LaTeX source for an arXiv paper (cached). {available, files:[{name,tex}], main}."""
    p = store.get_paper(paper_id)
    if not p:
        raise HTTPException(404, "paper not found")
    arxiv_id = p.get("arxiv_id")
    if not arxiv_id:
        return {"available": False, "reason": "not an arXiv paper", "files": []}
    cached = store.cache_get(paper_id, "tex")
    if cached:
        return cached
    try:
        res = await importers.fetch_arxiv_tex(arxiv_id)
    except Exception as e:  # noqa: BLE001 — network/parse errors surface to the UI
        return {"available": False, "reason": f"could not fetch arXiv source: {e}", "files": []}
    out = {"available": bool(res["files"]), "main": res.get("main"), "files": res["files"]}
    if out["available"]:
        store.cache_set(paper_id, "tex", out)
    return out


@router.get("/{paper_id}/fulltext")
async def get_fulltext(paper_id: str):
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise HTTPException(404, "parsed data not found")
    return {"full_text": parsed["full_text"]}


def _seed_paper_project(paper_id: str) -> None:
    try:
        projects.ensure_paper_project_quietly(store.get_paper(paper_id))
    except Exception:
        logging.getLogger(__name__).exception("Could not create workspace for paper %s", paper_id)


@router.get("/{paper_id}/project")
async def get_paper_project(paper_id: str):
    try:
        paper = store.get_paper(paper_id)
        if not paper:
            raise LookupError("paper not found")
        project, _ = await run_in_threadpool(projects.ensure_paper_project, paper)
        from ..harness import vcs
        vcs.ensure_repo_in_background(project["id"])
        return projects.workspace_detail(project)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
