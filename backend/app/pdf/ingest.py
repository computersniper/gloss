"""PDF ingestion with PyMuPDF (fitz).

Extracts, per page, text blocks with bounding boxes + dominant font size (used to
anchor highlights/annotations to PDF.js coordinates and to detect section
headers). Produces a ``parsed.json`` document consumed by the reader UI and the
feature endpoints.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz  # PyMuPDF


def ingest_pdf(pdf_path: str | Path) -> dict[str, Any]:
    try:
        doc = fitz.open(str(pdf_path))
    except (fitz.FileDataError, RuntimeError) as error:
        raise ValueError(f"The PDF is damaged or unreadable: {error}") from error
    pages: list[dict] = []
    full_parts: list[str] = []
    block_uid = 0
    try:
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            rect = page.rect
            pd = page.get_text("dict")
            blocks = []
            for b in pd.get("blocks", []):
                if b.get("type") != 0:  # 0 = text block; 1 = image
                    continue
                btext_lines = []
                max_size = 0.0
                font = ""
                bold = False
                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    line_txt = "".join(s.get("text", "") for s in spans)
                    if line_txt.strip():
                        btext_lines.append(line_txt)
                    for s in spans:
                        sz = float(s.get("size", 0))
                        if sz > max_size:
                            max_size = sz
                            font = s.get("font", "")
                        if s.get("flags", 0) & 16:  # bold flag
                            bold = True
                text = "\n".join(btext_lines).strip()
                if not text:
                    continue
                blocks.append({
                    "id": block_uid,
                    "bbox": [round(v, 2) for v in b.get("bbox", [0, 0, 0, 0])],
                    "text": text,
                    "size": round(max_size, 2),
                    "font": font,
                    "bold": bold,
                })
                full_parts.append(text)
                block_uid += 1

            # image/figure rectangles on the page (for figure preview)
            images = []
            for img in page.get_image_info():
                bb = img.get("bbox")
                if bb:
                    images.append([round(v, 2) for v in bb])

            pages.append({
                "index": pno,
                "width": round(rect.width, 2),
                "height": round(rect.height, 2),
                "blocks": blocks,
                "images": images,
            })

        toc = doc.get_toc(simple=True) or []
        meta = doc.metadata or {}
    finally:
        doc.close()

    return {
        "n_pages": len(pages),
        "pages": pages,
        "toc": toc,
        "full_text": "\n\n".join(full_parts),
        "meta": {
            "title": (meta.get("title") or "").strip(),
            "author": (meta.get("author") or "").strip(),
        },
    }


def search_page_rects(pdf_path: str | Path, page_index: int, needle: str) -> list[list[float]]:
    """Return bounding rects for ``needle`` on a page (best-effort, for highlights)."""
    needle = " ".join(needle.split())
    if not needle:
        return []
    doc = fitz.open(str(pdf_path))
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return []
        page = doc.load_page(page_index)
        rects = page.search_for(needle, quads=False)
        if not rects and len(needle) > 60:
            rects = page.search_for(needle[:60], quads=False)
        return [[round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)] for r in rects]
    finally:
        doc.close()


def locate_text(pdf_path: str | Path, needle: str, max_pages: int | None = None) -> dict | None:
    """Find the first page containing ``needle`` and return {page, rects}."""
    snippet = " ".join(needle.split())
    if not snippet:
        return None
    probe = snippet if len(snippet) <= 80 else snippet[:80]
    doc = fitz.open(str(pdf_path))
    try:
        n = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        for pno in range(n):
            page = doc.load_page(pno)
            rects = page.search_for(probe, quads=False)
            if rects:
                return {
                    "page": pno,
                    "rects": [[round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)] for r in rects],
                }
    finally:
        doc.close()
    return None
