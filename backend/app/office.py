"""Readable content of office documents and notebooks, without extra dependencies.

Word (.docx), PowerPoint (.pptx), Excel (.xlsx/.xlsm), OpenDocument
(.odt/.ods/.odp), PDF and GoodNotes (.goodnotes) files are turned into a list
of blocks (headings, paragraphs, tables, slide/sheet/page sections). The agent
reads them as Markdown (``to_markdown``); the file viewer renders the blocks.

The legacy binary formats (.doc/.xls/.ppt) are converted with LibreOffice when
``soffice`` is installed. GoodNotes keeps ink as vector data we cannot draw, so
a notebook shows its handwriting-recognition text and the PDFs and images
embedded in it (imported documents, page backgrounds).
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

OOXML = {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm"}
ODF = {".odt", ".ods", ".odp"}
LEGACY = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx", ".rtf": "docx"}
SUFFIXES = OOXML | ODF | set(LEGACY) | {".pdf", ".goodnotes"}

MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_MEMBER_BYTES = 120 * 1024 * 1024   # uncompressed XML part (zip bomb guard)
MAX_CHARS = 2_000_000
MAX_ROWS = 5000                        # per sheet
MAX_COLS = 200
MAX_PAGES = 2000

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
}


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


class DocumentError(ValueError):
    pass


def is_document(path: str | Path) -> bool:
    return PurePosixPath(str(path).replace("\\", "/")).suffix.lower() in SUFFIXES


# -- shared helpers ---------------------------------------------------------------
def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise DocumentError("the file is damaged or not in the expected format") from error


def _member(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        raise DocumentError(f"{name} is too large to read")
    return z.read(info)


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element | None:
    raw = _member(z, name)
    if raw is None:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise DocumentError(f"{name} is not valid XML") from error


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relationship id -> (type, absolute target) for a package part."""
    folder, name = part.rsplit("/", 1) if "/" in part else ("", part)
    root = _xml(z, f"{folder}/_rels/{name}.rels" if folder else f"_rels/{name}.rels")
    out: dict[str, tuple[str, str]] = {}
    for rel in root if root is not None else []:
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else str(PurePosixPath(folder, target))
        parts: list[str] = []
        for piece in path.split("/"):
            if piece == "..":
                parts and parts.pop()
            elif piece not in ("", "."):
                parts.append(piece)
        out[rel.get("Id") or ""] = (rel.get("Type") or "", "/".join(parts))
    return out


def _clean(text: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", text).strip()


def _table(rows: list[list[str]]) -> dict | None:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    while width and all(len(r) < width or not r[width - 1].strip() for r in rows):
        width -= 1
    return {"t": "table", "rows": [(r + [""] * width)[:width] for r in rows]}


def _col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


# -- Word -------------------------------------------------------------------------
def _w_text(el: ET.Element) -> str:
    out = []
    for node in el.iter():
        tag = node.tag
        if tag == _q("w", "t"):
            out.append(node.text or "")
        elif tag == _q("w", "tab"):
            out.append("\t")
        elif tag in (_q("w", "br"), _q("w", "cr")):
            out.append("\n")
        elif tag == _q("w", "noBreakHyphen"):
            out.append("-")
    return "".join(out)


def _w_blocks(body: ET.Element, styles: dict[str, str]) -> list[dict]:
    blocks: list[dict] = []
    for el in body:
        if el.tag == _q("w", "p"):
            text = _clean(_w_text(el))
            if not text:
                continue
            ppr = el.find("w:pPr", NS)
            style = ""
            if ppr is not None and ppr.find("w:pStyle", NS) is not None:
                style = ppr.find("w:pStyle", NS).get(_q("w", "val")) or ""
            name = styles.get(style, style).lower().replace(" ", "")
            level = None
            if name == "title":
                level = 1
            elif m := re.match(r"(?:heading|标题)(\d)", name):
                level = min(6, int(m.group(1)) + 1)
            if ppr is not None and ppr.find("w:outlineLvl", NS) is not None and level is None:
                level = min(6, int(ppr.find("w:outlineLvl", NS).get(_q("w", "val")) or 0) + 2)
            if level:
                blocks.append({"t": "h", "level": level, "text": text})
            else:
                listed = ppr is not None and ppr.find("w:numPr", NS) is not None
                blocks.append({"t": "p", "text": text, **({"list": True} if listed else {})})
        elif el.tag == _q("w", "tbl"):
            rows = []
            for tr in el.findall("w:tr", NS):
                rows.append([_clean(" ".join(_w_text(p) for p in tc.iter(_q("w", "p"))))
                             for tc in tr.findall("w:tc", NS)])
            if table := _table(rows):
                blocks.append(table)
        elif el.tag in (_q("w", "sdt"), _q("w", "customXml")):
            content = el.find("w:sdtContent", NS) if el.tag == _q("w", "sdt") else el
            if content is not None:
                blocks.extend(_w_blocks(content, styles))
    return blocks


def read_docx(data: bytes) -> dict:
    z = _zip(data)
    root = _xml(z, "word/document.xml")
    if root is None:
        raise DocumentError("not a Word document (word/document.xml is missing)")
    styles: dict[str, str] = {}
    style_root = _xml(z, "word/styles.xml")
    for style in style_root.findall("w:style", NS) if style_root is not None else []:
        name = style.find("w:name", NS)
        if name is not None:
            styles[style.get(_q("w", "styleId")) or ""] = name.get(_q("w", "val")) or ""
    body = root.find("w:body", NS)
    blocks = _w_blocks(body, styles) if body is not None else []
    for part, label in (("word/footnotes.xml", "Footnotes"), ("word/endnotes.xml", "Endnotes"),
                        ("word/comments.xml", "Comments")):
        notes_root = _xml(z, part)
        if notes_root is None:
            continue
        notes = [_clean(_w_text(n)) for n in notes_root]
        notes = [n for n in notes if n]
        if notes:
            blocks.append({"t": "section", "text": label})
            blocks.extend({"t": "p", "text": n, "list": True} for n in notes)
    title = next((b["text"] for b in blocks if b["t"] == "h"), "")
    return {"format": "docx", "title": title, "blocks": blocks}


# -- PowerPoint -------------------------------------------------------------------
def _a_blocks(el: ET.Element, *, skip_fields: bool = False) -> list[dict]:
    """Paragraphs and tables of a DrawingML tree, in document order."""
    out: list[dict] = []
    if el.tag == _q("a", "tbl"):
        rows = [[_clean(" ".join(_a_para(p, skip_fields) for p in tc.iter(_q("a", "p"))))
                 for tc in tr.findall("a:tc", NS)] for tr in el.findall("a:tr", NS)]
        table = _table(rows)
        return [table] if table else []
    if el.tag == _q("a", "p"):
        text = _clean(_a_para(el, skip_fields))
        return [{"t": "p", "text": text}] if text else []
    for child in el:
        out.extend(_a_blocks(child, skip_fields=skip_fields))
    return out


def _a_para(p: ET.Element, skip_fields: bool) -> str:
    out = []
    for node in p:
        if node.tag == _q("a", "r"):
            t = node.find("a:t", NS)
            out.append(t.text or "" if t is not None else "")
        elif node.tag == _q("a", "fld") and not skip_fields:
            t = node.find("a:t", NS)
            out.append(t.text or "" if t is not None else "")
        elif node.tag == _q("a", "br"):
            out.append("\n")
    return "".join(out)


def read_pptx(data: bytes) -> dict:
    z = _zip(data)
    pres = _xml(z, "ppt/presentation.xml")
    if pres is None:
        raise DocumentError("not a PowerPoint file (ppt/presentation.xml is missing)")
    rels = _rels(z, "ppt/presentation.xml")
    order = []
    ids = pres.find("p:sldIdLst", NS)
    for sld in ids if ids is not None else []:
        rel = rels.get(sld.get(_q("r", "id")) or "")
        if rel:
            order.append(rel[1])
    if not order:
        order = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                       key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    blocks: list[dict] = []
    title = ""
    for number, part in enumerate(order[:MAX_PAGES], 1):
        slide = _xml(z, part)
        if slide is None:
            continue
        content = _a_blocks(slide)
        head = next((b["text"] for b in content if b["t"] == "p"), "")
        title = title or head
        blocks.append({"t": "section", "text": f"Slide {number}" + (f": {head}" if head else ""),
                       "kind": "slide", "number": number})
        blocks.extend(content[1:] if head else content)
        for rel_type, target in _rels(z, part).values():
            if rel_type.endswith("/notesSlide"):
                notes_root = _xml(z, target)
                if notes_root is None:
                    continue
                notes = []
                for shape in notes_root.iter(_q("p", "sp")):
                    ph = shape.find(".//p:nvPr/p:ph", NS)
                    if ph is not None and ph.get("type") in ("sldNum", "sldImg", "hdr", "ftr", "dt"):
                        continue
                    notes.extend(b["text"] for b in _a_blocks(shape, skip_fields=True) if b["t"] == "p")
                if notes:
                    blocks.append({"t": "note", "text": "\n".join(notes)})
    return {"format": "pptx", "title": title, "blocks": blocks}


# -- Excel ------------------------------------------------------------------------
_DATE_FORMATS = set(range(14, 23)) | {45, 46, 47}


def _excel_date(value: float, date1904: bool) -> str:
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    moment = base + timedelta(days=value)
    if value == int(value):
        return moment.strftime("%Y-%m-%d")
    if value < 1:
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _number(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def read_xlsx(data: bytes) -> dict:
    z = _zip(data)
    book = _xml(z, "xl/workbook.xml")
    if book is None:
        raise DocumentError("not an Excel workbook (xl/workbook.xml is missing)")
    rels = _rels(z, "xl/workbook.xml")
    pr = book.find("s:workbookPr", NS)
    date1904 = pr is not None and pr.get("date1904") in ("1", "true")
    shared: list[str] = []
    sst = _xml(z, "xl/sharedStrings.xml")
    for si in sst if sst is not None else []:
        shared.append("".join(t.text or "" for t in si.iter(_q("s", "t"))
                              if not any(t in rph.iter() for rph in si.findall("s:rPh", NS))))
    date_styles: set[int] = set()
    styles = _xml(z, "xl/styles.xml")
    if styles is not None:
        custom = set()
        fmts = styles.find("s:numFmts", NS)
        for fmt in fmts if fmts is not None else []:
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", fmt.get("formatCode") or "").lower()
            if re.search(r"[dy]|h+:|:s", code) or re.search(r"(^|[^a-z])m+([^a-z]|$)", code) and re.search(r"[dy]", code):
                custom.add(int(fmt.get("numFmtId") or 0))
        xfs = styles.find("s:cellXfs", NS)
        for i, xf in enumerate(xfs if xfs is not None else []):
            fmt_id = int(xf.get("numFmtId") or 0)
            if fmt_id in _DATE_FORMATS or fmt_id in custom:
                date_styles.add(i)
    blocks: list[dict] = []
    sheets_el = book.find("s:sheets", NS)
    for sheet in sheets_el if sheets_el is not None else []:
        name = sheet.get("name") or "Sheet"
        rel = rels.get(sheet.get(_q("r", "id")) or "")
        if not rel or _member(z, rel[1]) is None:
            continue
        rows: list[list[str]] = []
        numbers: list[int] = []
        truncated = False
        with z.open(rel[1]) as handle:
            for _, el in ET.iterparse(handle):
                if el.tag != _q("s", "row"):
                    continue
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                row_number = int(el.get("r") or len(rows) + 1)
                cells: dict[int, str] = {}
                for c in el.findall("s:c", NS):
                    col = _col_index(c.get("r") or "") if c.get("r") else len(cells)
                    if col >= MAX_COLS or col < 0:
                        continue
                    kind = c.get("t") or "n"
                    v = c.find("s:v", NS)
                    raw = v.text if v is not None and v.text is not None else ""
                    if kind == "s":
                        value = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(_q("s", "t")))
                    elif kind == "b":
                        value = "TRUE" if raw == "1" else "FALSE"
                    elif kind in ("str", "e"):
                        value = raw
                    elif raw and int(c.get("s") or 0) in date_styles:
                        try:
                            value = _excel_date(float(raw), date1904)
                        except (ValueError, OverflowError):
                            value = raw
                    else:
                        value = _number(raw) if raw else ""
                    if value != "":
                        cells[col] = value.replace("\r\n", "\n")
                el.clear()
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
                    numbers.append(row_number)
        hidden = sheet.get("state") in ("hidden", "veryHidden")
        width = max((len(r) for r in rows), default=0)
        span = f"A{numbers[0]}:{_col_name(width - 1)}{numbers[-1]}" if rows else ""
        blocks.append({"t": "section", "kind": "sheet", "text": f"Sheet: {name}" + (" (hidden)" if hidden else ""),
                       "range": span})
        table = _table(rows)
        if table:
            table["row_numbers"] = numbers[:len(table["rows"])]
            table["columns"] = [_col_name(i) for i in range(len(table["rows"][0]))]
            blocks.append(table)
        else:
            blocks.append({"t": "p", "text": "(empty sheet)"})
        if truncated:
            blocks.append({"t": "p", "text": f"(only the first {MAX_ROWS} rows are shown)"})
    return {"format": "xlsx", "title": "", "blocks": blocks}


# -- OpenDocument -----------------------------------------------------------------
def _odf_text(el: ET.Element) -> str:
    out = [el.text or ""]
    for child in el:
        if child.tag == _q("text", "s"):
            out.append(" " * int(child.get(_q("text", "c")) or 1))
        elif child.tag == _q("text", "tab"):
            out.append("\t")
        elif child.tag == _q("text", "line-break"):
            out.append("\n")
        elif child.tag not in (_q("text", "note"), _q("office", "annotation")):
            out.append(_odf_text(child))
        out.append(child.tail or "")
    return "".join(out)


def _odf_blocks(el: ET.Element, in_list: bool = False) -> list[dict]:
    out: list[dict] = []
    for child in el:
        tag = child.tag
        if tag == _q("text", "h"):
            text = _clean(_odf_text(child))
            if text:
                level = int(child.get(_q("text", "outline-level")) or 1)
                out.append({"t": "h", "level": min(6, level + 1), "text": text})
        elif tag == _q("text", "p"):
            text = _clean(_odf_text(child))
            if text:
                out.append({"t": "p", "text": text, **({"list": True} if in_list else {})})
        elif tag == _q("text", "list"):
            out.extend(_odf_blocks(child, True))
        elif tag == _q("table", "table"):
            rows: list[list[str]] = []
            for row in child.iter(_q("table", "table-row")):
                repeat_rows = min(int(row.get(_q("table", "number-rows-repeated")) or 1), 50)
                cells: list[str] = []
                for cell in row:
                    if cell.tag not in (_q("table", "table-cell"), _q("table", "covered-table-cell")):
                        continue
                    text = _clean("\n".join(_odf_text(p) for p in cell.iter(_q("text", "p"))))
                    repeat = int(cell.get(_q("table", "number-columns-repeated")) or 1)
                    cells.extend([text] * min(repeat, MAX_COLS))
                    if len(cells) >= MAX_COLS:
                        break
                if any(cells):
                    rows.extend([cells[:MAX_COLS]] * repeat_rows)
                if len(rows) >= MAX_ROWS:
                    break
            name = child.get(_q("table", "name"))
            if name and el.tag == _q("office", "spreadsheet"):
                out.append({"t": "section", "kind": "sheet", "text": f"Sheet: {name}"})
            if table := _table(rows):
                out.append(table)
        elif tag == _q("draw", "page"):
            number = len([b for b in out if b.get("kind") == "slide"]) + 1
            content = _odf_blocks(child)
            notes = child.find("presentation:notes", NS)
            if notes is not None:
                content = _odf_blocks_without(child, notes)
            head = next((b["text"] for b in content if b["t"] == "p"), "")
            out.append({"t": "section", "kind": "slide", "number": number,
                        "text": f"Slide {number}" + (f": {head}" if head else "")})
            out.extend(content[1:] if head else content)
            if notes is not None:
                text = "\n".join(b["text"] for b in _odf_blocks(notes) if b["t"] == "p")
                if text:
                    out.append({"t": "note", "text": text})
        elif tag not in (_q("office", "annotation"), _q("text", "note")):
            out.extend(_odf_blocks(child, in_list))
    return out


def _odf_blocks_without(el: ET.Element, skip: ET.Element) -> list[dict]:
    keep = ET.Element(el.tag)
    keep.extend(c for c in el if c is not skip)
    return _odf_blocks(keep)


def read_odf(data: bytes, suffix: str) -> dict:
    z = _zip(data)
    root = _xml(z, "content.xml")
    if root is None:
        raise DocumentError("not an OpenDocument file (content.xml is missing)")
    body = root.find("office:body", NS)
    blocks = _odf_blocks(body) if body is not None else []
    title = next((b["text"] for b in blocks if b["t"] == "h"), "")
    return {"format": suffix.lstrip("."), "title": title, "blocks": blocks}


# -- PDF --------------------------------------------------------------------------
def read_pdf(data: bytes) -> dict:
    import fitz

    try:
        doc = fitz.open("pdf", data)
    except Exception as error:  # noqa: BLE001 - PyMuPDF raises several types
        raise DocumentError("the PDF could not be opened") from error
    with doc:
        if doc.needs_pass:
            raise DocumentError("the PDF is password protected")
        blocks: list[dict] = []
        for number, page in enumerate(doc, 1):
            if number > MAX_PAGES:
                break
            blocks.append({"t": "section", "kind": "page", "number": number, "text": f"Page {number}"})
            for para in re.split(r"\n\s*\n", page.get_text("text")):
                text = _clean(para.replace("\n", " "))
                if text:
                    blocks.append({"t": "p", "text": text})
        title = (doc.metadata or {}).get("title") or ""
    return {"format": "pdf", "title": title, "blocks": blocks, "viewer": "pdf"}


# -- GoodNotes --------------------------------------------------------------------
def _pb_strings(data: bytes, depth: int = 0) -> list[str]:
    """Text found in a protobuf message: length-delimited fields that are
    readable UTF-8, recursing into nested messages."""
    out: list[str] = []
    i, n = 0, len(data)
    try:
        while i < n:
            key, i = _varint(data, i)
            wire = key & 7
            if key >> 3 == 0:
                return out
            if wire == 0:
                _, i = _varint(data, i)
            elif wire == 1:
                i += 8
            elif wire == 5:
                i += 4
            elif wire == 2:
                size, i = _varint(data, i)
                chunk = data[i:i + size]
                if len(chunk) < size:
                    return out
                i += size
                nested = _pb_strings(chunk, depth + 1) if depth < 12 and size > 1 else []
                if nested:
                    out.extend(nested)
                else:
                    text = _readable(chunk)
                    if text:
                        out.append(text)
            else:
                return out
    except IndexError:
        pass
    return out


def _varint(data: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            raise IndexError


_UUID = re.compile(r"^[0-9A-Fa-f-]{32,36}$")


def _readable(chunk: bytes) -> str | None:
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = text.strip()
    if len(text) < 2 or _UUID.match(text) or re.fullmatch(r"[\w.-]+\.(?:pdf|png|jpe?g|heic)", text, re.I):
        return None
    printable = sum(ch.isprintable() or ch in "\n\t" for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    if printable < len(text) * 0.97 or letters < max(1, len(text) * 0.3):
        return None
    return text


def _sniff(raw: bytes) -> str | None:
    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"\x89PNG") or raw.startswith(b"\xff\xd8\xff") or raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if raw[8:12] == b"WEBP":
        return "image"
    return None


def goodnotes_members(data: bytes) -> list[dict]:
    """Embedded PDFs and images of a GoodNotes notebook."""
    z = _zip(data)
    found = []
    for info in z.infolist():
        if info.is_dir() or info.file_size > MAX_MEMBER_BYTES or info.file_size < 64:
            continue
        with z.open(info) as handle:
            kind = _sniff(handle.read(16))
        if kind:
            found.append({"member": info.filename, "kind": kind, "size": info.file_size})
    return found


def goodnotes_member(data: bytes, member: str) -> tuple[bytes, str]:
    z = _zip(data)
    if member not in {m["member"] for m in goodnotes_members(data)}:
        raise DocumentError("no such attachment in the notebook")
    raw = z.read(member)
    kind = _sniff(raw[:16])
    if kind == "pdf":
        return raw, "application/pdf"
    mime = ("image/png" if raw.startswith(b"\x89PNG") else "image/jpeg" if raw.startswith(b"\xff\xd8")
            else "image/gif" if raw.startswith(b"GIF") else "image/webp")
    return raw, mime


def read_goodnotes(data: bytes) -> dict:
    z = _zip(data)
    names = z.namelist()
    text_parts = [n for n in names if re.search(r"(^|/)search(/|$)", n, re.I)] or \
                 [n for n in names if n.startswith("notes/") or n.endswith(".pb")]
    seen: set[str] = set()
    lines: list[str] = []
    for name in text_parts:
        raw = _member(z, name)
        if not raw or _sniff(raw[:16]):
            continue
        for text in _pb_strings(raw):
            key = " ".join(text.split())
            if key not in seen:
                seen.add(key)
                lines.append(text)
    embeds = goodnotes_members(data)
    blocks: list[dict] = []
    if lines:
        blocks.append({"t": "section", "text": "Recognized text"})
        blocks.extend({"t": "p", "text": _clean(t)} for t in lines if _clean(t))
    for number, item in enumerate(embeds, 1):
        label = PurePosixPath(item["member"]).name
        blocks.append({"t": "embed", "kind": item["kind"], "member": item["member"],
                       "text": f"Attachment {number} ({item['kind']}): {label}"})
        if item["kind"] == "pdf":
            try:
                inner = read_pdf(z.read(item["member"]))
            except DocumentError:
                continue
            if any(b["t"] == "p" for b in inner["blocks"]):
                blocks.extend({**b, "embedded": True} for b in inner["blocks"])
    if not blocks:
        raise DocumentError("no readable text or attachments were found in this notebook; "
                            "export it from GoodNotes as PDF for a faithful copy")
    return {"format": "goodnotes", "title": "", "blocks": blocks,
            "notice": "Handwriting strokes cannot be drawn here; shown are the recognized text and embedded files."}


# -- legacy formats ---------------------------------------------------------------
def soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(candidate).is_file():
            return candidate
    return None


def convert_legacy(path: Path) -> tuple[bytes, str]:
    suffix = path.suffix.lower()
    target = LEGACY[suffix]
    binary = soffice()
    if not binary:
        raise DocumentError(f"{suffix} is an old binary format; install LibreOffice to open it, "
                            f"or save it as .{target}")
    with tempfile.TemporaryDirectory(prefix="glossx-convert-") as out:
        try:
            subprocess.run([binary, "--headless", "--norestore", "-env:UserInstallation=" + (Path(out) / "profile").as_uri(),
                            "--convert-to", target, "--outdir", out, str(path)],
                           capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise DocumentError(f"LibreOffice could not convert the file: {error}") from error
        result = Path(out) / f"{path.stem}.{target}"
        if not result.is_file():
            raise DocumentError("LibreOffice could not convert the file")
        return result.read_bytes(), "." + target


# -- entry points -----------------------------------------------------------------
_cache: OrderedDict[tuple[str, float, int], dict] = OrderedDict()
_lock = threading.Lock()


def _parse(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise DocumentError(f"file is too large ({size} bytes, limit {MAX_FILE_BYTES})")
    suffix = path.suffix.lower()
    if suffix in LEGACY:
        data, suffix = convert_legacy(path)
    else:
        data = path.read_bytes()
    try:
        if suffix in (".docx", ".docm"):
            doc = read_docx(data)
        elif suffix in (".pptx", ".pptm"):
            doc = read_pptx(data)
        elif suffix in (".xlsx", ".xlsm"):
            doc = read_xlsx(data)
        elif suffix in ODF:
            doc = read_odf(data, suffix)
        elif suffix == ".pdf":
            doc = read_pdf(data)
        elif suffix == ".goodnotes":
            doc = read_goodnotes(data)
        else:
            raise DocumentError(f"unsupported document type: {suffix}")
    except (KeyError, IndexError, zipfile.BadZipFile, ET.ParseError) as error:
        raise DocumentError("the file is damaged or not in the expected format") from error
    doc["title"] = doc.get("title") or path.stem
    return doc


def read(path: Path) -> dict:
    """Blocks of a document (cached by path, mtime and size)."""
    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    doc = _parse(path)
    doc["markdown"] = to_markdown(doc)
    with _lock:
        _cache[key] = doc
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return doc


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def to_markdown(doc: dict) -> str:
    lines: list[str] = []
    total = 0
    for block in doc.get("blocks") or []:
        kind = block["t"]
        if kind == "h":
            chunk = f"{'#' * block['level']} {block['text']}"
        elif kind == "section":
            extra = f" ({block['range']})" if block.get("range") else ""
            chunk = f"## {block['text']}{extra}"
        elif kind == "note":
            chunk = "> Notes: " + block["text"].replace("\n", "\n> ")
        elif kind == "embed":
            chunk = f"[{block['text']}]"
        elif kind == "table":
            rows = block["rows"]
            if block.get("columns"):
                head = ["#", *block["columns"]]
                body = [[str(n), *r] for n, r in zip(block.get("row_numbers") or range(1, len(rows) + 1), rows)]
            else:
                head, body = rows[0], rows[1:]
            out = ["| " + " | ".join(_cell(c) for c in head) + " |",
                   "|" + "---|" * len(head)]
            out.extend("| " + " | ".join(_cell(c) for c in r) + " |" for r in body)
            chunk = "\n".join(out)
        else:
            chunk = ("- " if block.get("list") else "") + block["text"]
        total += len(chunk) + 2
        if total > MAX_CHARS:
            lines.append(f"[... truncated at {MAX_CHARS} characters]")
            break
        lines.append(chunk)
    if doc.get("notice"):
        lines.insert(0, f"({doc['notice']})")
    return "\n\n".join(lines) if lines else "(the document contains no text)"
