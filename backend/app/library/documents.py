"""Non-PDF sources (Markdown, plain text, HTML pages, slides, Word and Excel
files, OpenDocument, GoodNotes notebooks) as PDFs.

Every source in the library is a PDF so that evidence spans keep a page and a
box and stay clickable in the reader. Other formats are rendered to a simple
PDF here (PyMuPDF ``Story``, no extra dependency) and then go through the
normal PDF pipeline. Slides become one page each, so "p. 3" means slide 3.
"""
from __future__ import annotations

import html as htmllib
import io
import re
import zipfile
from pathlib import PurePath

# read through app.office (blocks), then laid out like the other formats
OFFICE_SUFFIXES = {".docx", ".docm", ".xlsx", ".xlsm", ".odt", ".ods", ".odp", ".goodnotes"}
SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm", ".pptx"} | OFFICE_SUFFIXES
# what the upload endpoint converts; plain .txt stays an internal format (saved web pages)
UPLOAD_SUFFIXES = SUFFIXES - {".txt"}
MAX_BYTES = 30 * 1024 * 1024
MAX_SLIDES = 300

CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.35; }
h1 { font-size: 18pt; } h2 { font-size: 15pt; } h3 { font-size: 13pt; }
pre { font-family: monospace; font-size: 9pt; }
"""

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\w)"), r"<i>\1</i>"),
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),  # keep link and image text only
]


def is_supported(filename: str) -> bool:
    """Whether an uploaded file of this name is converted (Markdown, HTML, slides)."""
    return PurePath(filename or "").suffix.lower() in UPLOAD_SUFFIXES


def _inline(text: str) -> str:
    out = htmllib.escape(text, quote=False)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def markdown_to_html(text: str) -> tuple[str, str]:
    """A small Markdown subset: headings, lists, fences, quotes, paragraphs."""
    parts: list[str] = []
    title = ""
    para: list[str] = []
    list_tag: str | None = None
    fence: list[str] | None = None

    def flush() -> None:
        nonlocal list_tag
        if para:
            parts.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None

    for line in text.replace("\r\n", "\n").split("\n"):
        if fence is not None:
            if line.strip().startswith("```"):
                parts.append(f"<pre>{htmllib.escape(chr(10).join(fence))}</pre>")
                fence = None
            else:
                fence.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            fence = []
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        bullet = re.match(r"[-*+]\s+(.*)", stripped)
        number = re.match(r"\d+[.)]\s+(.*)", stripped)
        if heading:
            flush()
            level = min(3, len(heading.group(1)))
            title = title or heading.group(2).strip()
            parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet or number:
            if para:
                parts.append(f"<p>{_inline(' '.join(para))}</p>")
                para.clear()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                if list_tag:
                    parts.append(f"</{list_tag}>")
                parts.append(f"<{tag}>")
                list_tag = tag
            parts.append(f"<li>{_inline((bullet or number).group(1))}</li>")
        elif stripped.startswith(">"):
            flush()
            parts.append(f"<blockquote>{_inline(stripped.lstrip('> '))}</blockquote>")
        elif not stripped:
            flush()
        else:
            if list_tag:
                flush()
            para.append(stripped)
    if fence is not None:
        parts.append(f"<pre>{htmllib.escape(chr(10).join(fence))}</pre>")
    flush()
    return "\n".join(parts), title


def page_html(raw: str) -> tuple[str, str]:
    """Readable body of a saved web page: no scripts, styles, forms or media."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = htmllib.unescape(" ".join(title_match.group(1).split())) if title_match else ""
    body = re.search(r"<body[^>]*>(.*)</body>", raw, re.I | re.S)
    text = body.group(1) if body else raw
    text = re.sub(r"<(script|style|noscript|svg|form|iframe|template)\b.*?</\1\s*>", "", text, flags=re.I | re.S)
    text = re.sub(r"<(img|video|audio|source|input|link|meta)\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    # attributes can carry styles that hide text or break layout; keep tags only
    text = re.sub(r"<([a-zA-Z][a-zA-Z0-9]*)\s[^>]*?(/?)>", r"<\1\2>", text)
    return text, title


def pptx_slides(data: bytes) -> list[list[str]]:
    """Text paragraphs of each slide, in slide order."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        slides = []
        for name in names[:MAX_SLIDES]:
            xml = z.read(name).decode("utf-8", errors="replace")
            paras = []
            for p in re.findall(r"<a:p\b.*?</a:p>", xml, re.S):
                runs = re.findall(r"<a:t>(.*?)</a:t>", p, re.S)
                line = htmllib.unescape("".join(runs)).strip()
                if line:
                    paras.append(line)
            slides.append(paras)
    return slides


def render_pdf(chunks: list[str], title: str = "") -> bytes:
    """Render HTML chunks to a PDF; each chunk starts on a new page."""
    import fitz

    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (54, 54, -54, -54)
    for chunk in chunks or ["<p></p>"]:
        story = fitz.Story(html=f"<body>{chunk}</body>", user_css=CSS)
        more = 1
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    writer.close()
    if title:
        doc = fitz.open("pdf", buf.getvalue())
        doc.set_metadata({**(doc.metadata or {}), "title": title[:300]})
        out = doc.tobytes()
        doc.close()
        return out
    return buf.getvalue()


def blocks_to_chunks(blocks: list[dict]) -> list[str]:
    """HTML chunks (one per slide, sheet or page) from ``app.office`` blocks."""
    esc = lambda t: htmllib.escape(t, quote=False).replace("\n", "<br/>")  # noqa: E731
    chunks: list[list[str]] = [[]]
    items: list[str] = []

    def flush_list() -> None:
        if items:
            chunks[-1].append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for block in blocks:
        kind = block["t"]
        if kind == "p" and block.get("list"):
            items.append(esc(block["text"]))
            continue
        flush_list()
        if kind == "section":
            if any(chunks[-1]):
                chunks.append([])
            chunks[-1].append(f"<h2>{esc(block['text'])}</h2>")
        elif kind == "h":
            level = min(3, block["level"])
            chunks[-1].append(f"<h{level}>{esc(block['text'])}</h{level}>")
        elif kind == "note":
            chunks[-1].append(f"<blockquote><i>Notes:</i> {esc(block['text'])}</blockquote>")
        elif kind == "table":
            rows = block["rows"]
            if block.get("columns"):
                rows = [["", *block["columns"]]] + [[str(n), *r] for n, r in zip(block.get("row_numbers") or [], rows)]
            body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows[:2000])
            chunks[-1].append(f'<table border="1" cellpadding="3" style="border-collapse: collapse; font-size: 8pt">{body}</table>')
        elif kind == "p":
            chunks[-1].append(f"<p>{esc(block['text'])}</p>")
    flush_list()
    return ["".join(c) for c in chunks if c]


def _office_pdf(filename: str, data: bytes) -> tuple[bytes, str]:
    import fitz

    from .. import office

    suffix = PurePath(filename or "").suffix.lower()
    stem = PurePath(filename or "document").stem
    try:
        if suffix in (".docx", ".docm"):
            doc = office.read_docx(data)
        elif suffix in (".xlsx", ".xlsm"):
            doc = office.read_xlsx(data)
        elif suffix == ".goodnotes":
            doc = office.read_goodnotes(data)
        else:
            doc = office.read_odf(data, suffix)
    except (KeyError, IndexError, zipfile.BadZipFile) as error:
        raise ValueError(f"not a valid {suffix} file") from error
    title = doc.get("title") or stem
    if suffix == ".goodnotes":
        # the notebook's own PDFs keep their layout; recognized handwriting goes first
        text = [b for b in doc["blocks"] if b["t"] == "p" and not b.get("embedded")]
        out = fitz.open()
        if text:
            out.insert_pdf(fitz.open("pdf", render_pdf(blocks_to_chunks([{"t": "section", "text": "Recognized handwriting"}, *text]))))
        for item in office.goodnotes_members(data):
            raw, mime = office.goodnotes_member(data, item["member"])
            if mime == "application/pdf":
                out.insert_pdf(fitz.open("pdf", raw))
        if not out.page_count:
            raise ValueError("the notebook has no readable text or PDF pages")
        out.set_metadata({"title": title[:300]})
        return out.tobytes(), title
    chunks = blocks_to_chunks(doc["blocks"])
    if not chunks:
        raise ValueError("the document contains no text")
    return render_pdf(chunks, title), title


def to_pdf(filename: str, data: bytes) -> tuple[bytes, str]:
    """Render a supported non-PDF source; returns (pdf bytes, title)."""
    if len(data) > MAX_BYTES:
        raise ValueError("document is too large")
    suffix = PurePath(filename or "").suffix.lower()
    stem = PurePath(filename or "document").stem
    if suffix not in SUFFIXES:
        raise ValueError(f"unsupported document type: {suffix or 'none'}")
    if suffix in OFFICE_SUFFIXES:
        from .. import office

        try:
            return _office_pdf(filename, data)
        except office.DocumentError as error:
            raise ValueError(str(error)) from error
    if suffix == ".pptx":
        try:
            slides = pptx_slides(data)
        except (zipfile.BadZipFile, KeyError) as error:
            raise ValueError("not a valid .pptx file") from error
        if not any(slides):
            raise ValueError("the slides contain no text")
        chunks = []
        for i, paras in enumerate(slides, 1):
            head, rest = (paras[0], paras[1:]) if paras else (f"Slide {i}", [])
            body = "".join(f"<p>{htmllib.escape(p)}</p>" for p in rest)
            chunks.append(f"<h2>{htmllib.escape(head)}</h2>{body}")
        title = (slides[0][0] if slides and slides[0] else stem)
        return render_pdf(chunks, title), title
    text = data.decode("utf-8-sig", errors="replace")
    if suffix in {".md", ".markdown"}:
        body, title = markdown_to_html(text)
    elif suffix in {".html", ".htm"}:
        body, title = page_html(text)
    else:
        body = "".join(f"<p>{htmllib.escape(p)}</p>" for p in re.split(r"\n\s*\n", text) if p.strip())
        title = ""
    if not re.sub(r"<[^>]+>", "", body).strip():
        raise ValueError("the document contains no text")
    title = title or stem
    return render_pdf([body], title), title
