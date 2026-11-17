"""Reading Word, PowerPoint, Excel, OpenDocument, PDF and GoodNotes files."""
from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from backend.app import office
from backend.app.harness import workspace

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def _zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_docx() -> bytes:
    body = f"""<w:document {W}><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Methods</w:t></w:r></w:p>
      <w:p><w:r><w:t xml:space="preserve">We sampled </w:t></w:r><w:r><w:t>120 &lt;patients&gt;.</w:t></w:r></w:p>
      <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>first point</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Group</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>n</w:t></w:r></w:p></w:tc></w:tr>
             <w:tr><w:tc><w:p><w:r><w:t>A|B</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>60</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      <w:p><w:r><w:delText>deleted</w:delText></w:r></w:p>
    </w:body></w:document>"""
    styles = f'<w:styles {W}><w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>'
    return _zip({"word/document.xml": body, "word/styles.xml": styles})


def make_pptx() -> bytes:
    pres = f'<p:presentation {P} {R}><p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>'
    rels = (f'<Relationships {REL}><Relationship Id="rId1" Type="x/slide" Target="slides/slide1.xml"/>'
            f'<Relationship Id="rId2" Type="x/slide" Target="slides/slide2.xml"/></Relationships>')

    def slide(*paras: str) -> str:
        ps = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paras)
        return f"<p:sld {P} {A}><p:cSld><p:spTree><p:sp><p:txBody>{ps}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"

    notes = (f'<p:notes {P} {A}><p:cSld><p:spTree>'
             f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Say this</a:t></a:r></a:p></p:txBody></p:sp>'
             f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:fld><a:t>2</a:t></a:fld></a:p></p:txBody></p:sp>'
             f'</p:spTree></p:cSld></p:notes>')
    return _zip({
        "ppt/presentation.xml": pres, "ppt/_rels/presentation.xml.rels": rels,
        "ppt/slides/slide1.xml": slide("Results", "Accuracy 91%"),
        "ppt/slides/slide2.xml": slide("Intro", "Why it matters"),
        "ppt/slides/_rels/slide2.xml.rels": f'<Relationships {REL}><Relationship Id="rId9" Type="http://x/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        "ppt/notesSlides/notesSlide1.xml": notes,
    })


def make_xlsx() -> bytes:
    book = (f'<workbook {S} {R}><sheets><sheet name="Data" sheetId="1" r:id="rId1"/>'
            f'<sheet name="Hidden" sheetId="2" state="hidden" r:id="rId2"/></sheets></workbook>')
    rels = (f'<Relationships {REL}><Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="x/worksheet" Target="/xl/worksheets/sheet2.xml"/></Relationships>')
    sst = f'<sst {S}><si><t>name</t></si><si><t>date</t></si><si><r><t>Al</t></r><r><t>ice</t></r></si></sst>'
    styles = f'<styleSheet {S}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'
    sheet1 = (f'<worksheet {S}><sheetData>'
              '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="inlineStr"><is><t>score</t></is></c></row>'
              '<row r="3"><c r="A3" t="s"><v>2</v></c><c r="B3" s="1"><v>45292</v></c><c r="C3"><v>0.5</v></c><c r="E3" t="b"><v>1</v></c></row>'
              '</sheetData></worksheet>')
    sheet2 = f'<worksheet {S}><sheetData/></worksheet>'
    return _zip({"xl/workbook.xml": book, "xl/_rels/workbook.xml.rels": rels, "xl/sharedStrings.xml": sst,
                 "xl/styles.xml": styles, "xl/worksheets/sheet1.xml": sheet1, "xl/worksheets/sheet2.xml": sheet2})


def make_odt() -> bytes:
    content = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"><office:body><office:text>'
               '<text:h text:outline-level="1">Notes</text:h><text:p>Hello<text:s text:c="2"/>world</text:p>'
               '<table:table table:name="T"><table:table-row><table:table-cell><text:p>x</text:p></table:table-cell>'
               '<table:table-cell><text:p>y</text:p></table:table-cell></table:table-row></table:table>'
               '</office:text></office:body></office:document-content>')
    return _zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": content})


def make_pdf(text: str = "Hello PDF page") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _pb_field(number: int, payload: bytes) -> bytes:
    def varint(n: int) -> bytes:
        out = b""
        while True:
            b = n & 0x7F
            n >>= 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def make_goodnotes() -> bytes:
    recognized = _pb_field(1, _pb_field(2, "手写的笔记 Lecture 3".encode()) + _pb_field(3, b"0F8B8C1A-1111-2222-3333-444455556666"))
    return _zip({"index.notes.pb": b"\x08\x01", "search/page-1": recognized,
                 "attachments/ABCD": make_pdf("Imported slides"), "notes/0001": b"\x08\x02\x10\x03"})


def test_docx_headings_paragraphs_lists_and_tables():
    doc = office.read_docx(make_docx())
    md = office.to_markdown(doc)
    assert "## Methods" in md
    assert "We sampled 120 <patients>." in md
    assert "- first point" in md
    assert "| Group | n |" in md and "| A\\|B | 60 |" in md
    assert "deleted" not in md


def test_pptx_slides_follow_presentation_order_with_notes():
    md = office.to_markdown(office.read_pptx(make_pptx()))
    assert md.index("Slide 1: Intro") < md.index("Slide 2: Results")
    assert "> Notes: Say this" in md
    assert "Notes: Say this\n> 2" not in md  # the slide number field is dropped
    assert "Accuracy 91%" in md


def test_xlsx_shared_strings_dates_and_row_numbers():
    doc = office.read_xlsx(make_xlsx())
    md = office.to_markdown(doc)
    assert "## Sheet: Data (A1:E3)" in md
    assert "| # | A | B | C | D | E |" in md
    assert "| 3 | Alice | 2024-01-01 | 0.5 |  | TRUE |" in md
    assert "Sheet: Hidden (hidden)" in md


def test_odt_and_pdf():
    md = office.to_markdown(office.read_odf(make_odt(), ".odt"))
    assert "## Notes" in md and "Hello world" in md and "| x | y |" in md
    pdf = office.read_pdf(make_pdf())
    assert pdf["viewer"] == "pdf"
    assert "## Page 1" in office.to_markdown(pdf) and "Hello PDF page" in office.to_markdown(pdf)


def test_goodnotes_recognized_text_and_embedded_pdf():
    data = make_goodnotes()
    doc = office.read_goodnotes(data)
    md = office.to_markdown(doc)
    assert "手写的笔记 Lecture 3" in md
    assert "0F8B8C1A" not in md
    assert "Imported slides" in md
    embeds = office.goodnotes_members(data)
    assert embeds == [{"member": "attachments/ABCD", "kind": "pdf", "size": len(make_pdf("Imported slides"))}]
    raw, mime = office.goodnotes_member(data, "attachments/ABCD")
    assert mime == "application/pdf" and raw.startswith(b"%PDF")
    with pytest.raises(office.DocumentError):
        office.goodnotes_member(data, "notes/0001")


def test_damaged_files_raise_document_errors(tmp_path: Path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(office.DocumentError):
        office.read(bad)
    with pytest.raises(workspace.WorkspaceError, match="broken.docx"):
        workspace.read_document(bad)


def test_legacy_formats_need_libreoffice(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(office, "soffice", lambda: None)
    old = tmp_path / "old.xls"
    old.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(office.DocumentError, match="LibreOffice"):
        office.read(old)


def test_workspace_read_document_and_cache(tmp_path: Path):
    path = tmp_path / "report.docx"
    path.write_bytes(make_docx())
    text = workspace.read_document(path)
    assert "## Methods" in text
    assert office.read(path) is office.read(path)
    plain = tmp_path / "a.txt"
    plain.write_text("plain", encoding="utf-8")
    assert workspace.read_document(plain) == "plain"


def test_fs_read_and_grep_see_office_files(tmp_path: Path, monkeypatch):
    from backend.app.harness import builtin_tools
    from backend.app.harness.tools import HarnessContext

    (tmp_path / "deck.pptx").write_bytes(make_pptx())
    (tmp_path / "data.xlsx").write_bytes(make_xlsx())
    ctx = HarnessContext.__new__(HarnessContext)
    monkeypatch.setattr(builtin_tools, "_root", lambda _ctx: tmp_path)
    out = asyncio.run(builtin_tools._fs_read({"path": "deck.pptx"}, ctx))
    assert "Slide 1: Intro" in out.text
    grep = asyncio.run(builtin_tools._fs_grep({"pattern": "Alice"}, ctx))
    assert "data.xlsx" in grep.text


def test_workspace_file_endpoint_previews_documents_and_serves_raw(client):
    project = client.post("/api/agent/projects", json={"name": "Office files"}).json()
    pid = project["id"]
    root = Path(project["workspace"]["path"])
    (root / "report.docx").write_bytes(make_docx())
    (root / "paper.pdf").write_bytes(make_pdf())
    (root / "notebook.goodnotes").write_bytes(make_goodnotes())
    (root / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 80)

    doc = client.get(f"/api/workspace/{pid}/file", params={"path": "report.docx"}).json()
    assert doc["readonly"] and doc["viewer"] == "document" and doc["document"]["format"] == "docx"
    assert {"t": "h", "level": 2, "text": "Methods"} in doc["document"]["blocks"]
    assert "## Methods" in doc["content"]

    pdf = client.get(f"/api/workspace/{pid}/file", params={"path": "paper.pdf"}).json()
    assert pdf["viewer"] == "pdf"
    raw = client.get(f"/api/workspace/{pid}/raw", params={"path": "paper.pdf"})
    assert raw.status_code == 200 and raw.headers["content-type"] == "application/pdf"

    image = client.get(f"/api/workspace/{pid}/file", params={"path": "figure.png"}).json()
    assert image["viewer"] == "image"
    assert client.get(f"/api/workspace/{pid}/raw", params={"path": "figure.png"}).headers["content-type"] == "image/png"

    note = client.get(f"/api/workspace/{pid}/file", params={"path": "notebook.goodnotes"}).json()
    embed = next(b for b in note["document"]["blocks"] if b["t"] == "embed")
    inner = client.get(f"/api/workspace/{pid}/raw", params={"path": "notebook.goodnotes", "member": embed["member"]})
    assert inner.status_code == 200 and inner.content.startswith(b"%PDF")

    assert client.get(f"/api/workspace/{pid}/raw", params={"path": "report.docx"}).status_code == 415
    assert client.get(f"/api/workspace/{pid}/raw", params={"path": "../x.pdf"}).status_code in (400, 403, 404)


@pytest.mark.parametrize("name,maker,needle", [
    ("report.docx", make_docx, "Methods"),
    ("data.xlsx", make_xlsx, "Alice"),
    ("notes.odt", make_odt, "Hello"),
    ("notebook.goodnotes", make_goodnotes, "Imported slides"),
])
def test_library_import_renders_office_files_to_pdf(name, maker, needle):
    import fitz

    from backend.app.library import documents

    assert documents.is_supported(name)
    pdf, title = documents.to_pdf(name, maker())
    with fitz.open("pdf", pdf) as doc:
        assert needle in "".join(page.get_text() for page in doc)
    assert title
