"""Markdown, HTML and slides become PDFs whose text is citable like a paper's."""
from __future__ import annotations

import io
import zipfile

import fitz
import pytest

from app.library import documents


def _text(pdf: bytes) -> list[str]:
    with fitz.open("pdf", pdf) as doc:
        return [page.get_text() for page in doc]


def test_markdown_renders_headings_lists_code_and_cjk():
    md = ("# 注意力机制笔记\n\nSelf-attention is **cheap** to parallelise.\n\n"
          "- first point\n- second point\n\n```\nx = softmax(q @ k.T)\n```\n")
    pdf, title = documents.to_pdf("notes.md", md.encode())
    text = _text(pdf)[0]
    assert title == "注意力机制笔记"
    for piece in ("注意力机制笔记", "Self-attention is cheap", "rst point", "softmax(q @ k.T)"):
        assert piece in text  # MuPDF sets "fi" as a ligature; retrieval normalises it
    with fitz.open("pdf", pdf) as doc:
        assert doc.metadata["title"] == "注意力机制笔记"


def test_html_drops_scripts_and_hidden_markup():
    page = ("<html><head><title>Blog post</title><style>p{display:none}</style></head><body>"
            "<p style='display:none'>Visible after cleaning.</p><script>alert('x')</script>"
            "<img src=x onerror=alert(1)></body></html>")
    pdf, title = documents.to_pdf("page.html", page.encode())
    text = _text(pdf)[0]
    assert title == "Blog post" and "Visible after cleaning." in text and "alert" not in text


def test_pptx_gives_one_page_per_slide():
    def slide(*paras):
        body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paras)
        return f'<p:sld xmlns:a="a" xmlns:p="p"><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/slides/slide10.xml", slide("Results", "BLEU 28.4"))
        z.writestr("ppt/slides/slide2.xml", slide("Method", "Multi-head attention &amp; FFN"))
        z.writestr("ppt/slides/slide1.xml", slide("Lecture 5: Transformers"))
    pdf, title = documents.to_pdf("lecture.pptx", buf.getvalue())
    pages = _text(pdf)
    assert title == "Lecture 5: Transformers" and len(pages) == 3
    assert "Multi-head attention & FFN" in pages[1] and "BLEU 28.4" in pages[2]


def test_rejects_unsupported_and_empty_documents():
    with pytest.raises(ValueError):
        documents.to_pdf("a.docx", b"x")
    with pytest.raises(ValueError):
        documents.to_pdf("a.md", b"\n\n   \n")
    with pytest.raises(ValueError):
        documents.to_pdf("a.pptx", b"not a zip")
    assert documents.is_supported("x.MD") and not documents.is_supported("x.txt")


def test_markdown_upload_becomes_a_searchable_library_paper(client):
    md = b"# Reading notes\n\nThe Transformer reaches 28.4 BLEU on WMT 2014 English-German.\n"
    r = client.post("/api/papers/upload", files={"file": ("notes.md", md, "text/markdown")})
    assert r.status_code == 200, r.text
    paper = r.json()
    assert paper["title"] == "Reading notes" and paper["source"] == "document"
    from app.agent.context import SpanStore

    spans = SpanStore().search("BLEU WMT", [paper["id"]], k=1)
    assert spans and "28.4 BLEU" in spans[0]["text"] and spans[0]["page"] == 0


def test_retrieval_tokens_survive_ligatures_and_cjk():
    from app.features.retrieval import _tok

    assert _tok("\ufb01rst o\ufb03cial") == ["first", "official"]
    assert _tok("注意力 attention") == ["注", "意", "力", "attention"]


def test_agent_saves_a_web_page_as_a_citable_source(client, monkeypatch):
    import asyncio

    from app.agent.capabilities import RunContext, build_registry
    from app.agent.context import SpanStore
    from app.harness import web

    async def fake_fetch(url, **kwargs):
        return {"url": url, "final_url": url, "title": "Release notes",
                "text": "Version 2 cuts inference latency by 40 percent on long inputs."}

    monkeypatch.setattr(web, "web_fetch", fake_fetch)
    cap = build_registry(connections={}).get("library.import_web")
    assert cap.approval_permissions() == ["library:write"]
    ctx = RunContext(run_id="r", paper_ids=[], spans=SpanStore())
    out = asyncio.run(cap.handler({"url": "https://example.org/notes"}, ctx))
    assert out["status"] == "completed" and ctx.paper_ids == [out["paper_id"]]
    spans = ctx.spans.search("inference latency", ctx.paper_ids, k=1)
    assert spans and "40 percent" in spans[0]["text"]
