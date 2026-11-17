// Read-only views for workspace files that are not plain text: office
// documents (blocks from the backend), PDFs and images. GoodNotes notebooks
// arrive as documents whose blocks embed the PDFs and images inside them.
import { useEffect, useMemo, useRef, useState } from "react";
import { Info, StickyNote } from "lucide-react";
import { useZoom } from "../../zoom";
import { pdfjsLib } from "../../pdf/pdfSetup";

export type DocBlock =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string; list?: boolean; embedded?: boolean }
  | { t: "section"; text: string; kind?: "slide" | "sheet" | "page"; number?: number; range?: string; embedded?: boolean }
  | { t: "note"; text: string }
  | { t: "table"; rows: string[][]; columns?: string[]; row_numbers?: number[] }
  | { t: "embed"; kind: "pdf" | "image"; member: string; text: string };

export type DocPayload = { format?: string; title?: string; blocks?: DocBlock[]; notice?: string };

export const rawUrl = (project: string, path: string, member?: string) =>
  `/api/workspace/${encodeURIComponent(project || "default")}/raw?path=${encodeURIComponent(path)}${member ? `&member=${encodeURIComponent(member)}` : ""}`;

const FORMAT_LABEL: Record<string, string> = {
  docx: "Word", pptx: "PowerPoint", xlsx: "Excel", odt: "OpenDocument Text", ods: "OpenDocument Spreadsheet",
  odp: "OpenDocument Presentation", pdf: "PDF", goodnotes: "GoodNotes",
};
export const formatLabel = (format?: string) => (format && FORMAT_LABEL[format]) || (format || "").toUpperCase();

function Table({ block }: { block: Extract<DocBlock, { t: "table" }> }) {
  const sheet = !!block.columns;
  const [head, ...body] = sheet ? [block.columns!, ...block.rows] : block.rows;
  return (
    <div className="gx-docv-table">
      <table className={sheet ? "sheet" : ""}>
        <thead><tr>{sheet && <th className="rn" />}{head.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r}>
              {sheet && <th className="rn">{block.row_numbers?.[r] ?? r + 1}</th>}
              {row.map((c, i) => <td key={i}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SECTION_ZH: Record<string, string> = { "Recognized text": "识别出的文字", Footnotes: "脚注", Endnotes: "尾注", Comments: "批注" };

function Blocks({ blocks, project, path, zh }: { blocks: DocBlock[]; project: string; path: string; zh: boolean }) {
  const out: JSX.Element[] = [];
  let list: string[] = [];
  const flush = (key: number) => {
    if (list.length) out.push(<ul key={`l${key}`}>{list.map((t, i) => <li key={i}>{t}</li>)}</ul>);
    list = [];
  };
  blocks.forEach((b, i) => {
    if (b.t === "p" && b.list) { list.push(b.text); return; }
    flush(i);
    if (b.t === "h") {
      const H = `h${Math.min(6, Math.max(1, b.level))}` as keyof JSX.IntrinsicElements;
      out.push(<H key={i}>{b.text}</H>);
    } else if (b.t === "p") {
      out.push(<p key={i}>{b.text}</p>);
    } else if (b.t === "section") {
      out.push(<div key={i} className={`gx-docv-section ${b.kind || ""}`}>{b.kind === "slide" && b.number ? <b>{b.number}</b> : null}<span>{b.kind === "slide" ? b.text.replace(/^Slide \d+:?\s*/, "") || (zh ? `幻灯片 ${b.number}` : b.text) : b.kind === "page" ? (zh ? `第 ${b.number} 页` : b.text) : (zh && SECTION_ZH[b.text]) || b.text}</span>{b.range && <small>{b.range}</small>}</div>);
    } else if (b.t === "note") {
      out.push(<aside key={i} className="gx-docv-note"><StickyNote size={13} /><div><b>{zh ? "备注" : "Notes"}</b><p>{b.text}</p></div></aside>);
    } else if (b.t === "table") {
      out.push(<Table key={i} block={b} />);
    } else if (b.t === "embed") {
      out.push(
        <figure key={i} className="gx-docv-embed">
          <figcaption>{zh ? b.text.replace(/^Attachment (\d+) \((\w+)\): /, (_m, n, k) => `附件 ${n}（${k === "pdf" ? "PDF" : "图片"}）：`) : b.text}</figcaption>
          {b.kind === "image" ? <img src={rawUrl(project, path, b.member)} alt={b.text} /> : <PdfPages url={rawUrl(project, path, b.member)} zh={zh} followZoom={false} />}
        </figure>,
      );
    }
  });
  flush(blocks.length);
  return <>{out}</>;
}

/** Office documents; workbooks get Excel-style sheet tabs. */
export function DocumentView({ doc, project, path, zh }: { doc: DocPayload; project: string; path: string; zh: boolean }) {
  const blocks = doc.blocks || [];
  const sheets = useMemo(() => {
    if (!blocks.some((b) => b.t === "section" && b.kind === "sheet")) return null;
    const groups: { name: string; range?: string; blocks: DocBlock[] }[] = [];
    for (const b of blocks) {
      if (b.t === "section" && b.kind === "sheet") groups.push({ name: b.text.replace(/^Sheet:\s*/, ""), range: b.range, blocks: [] });
      else if (groups.length) groups[groups.length - 1].blocks.push(b);
    }
    return groups;
  }, [blocks]);
  const [sheet, setSheet] = useState(0);
  useEffect(() => setSheet(0), [path]);
  // GoodNotes shows the embedded PDF itself; its extracted text would only repeat it
  const visible = blocks.filter((b) => !(b as { embedded?: boolean }).embedded);
  return (
    <div className={`gx-docv${sheets ? " workbook" : ""}`}>
      <div className="gx-docv-scroll">
        <article className={`gx-docv-page ${doc.format || ""}`}>
          {doc.notice && <p className="gx-docv-notice"><Info size={13} /> {zh && doc.format === "goodnotes" ? "手写笔迹无法在这里绘制，下面是识别出的文字和笔记里嵌入的 PDF、图片。" : doc.notice}</p>}
          {sheets ? (
            sheets[sheet] ? <Blocks blocks={sheets[sheet].blocks} project={project} path={path} zh={zh} /> : null
          ) : visible.length ? <Blocks blocks={visible} project={project} path={path} zh={zh} /> : <p className="gx-docv-empty">{zh ? "文档里没有文字。" : "The document contains no text."}</p>}
        </article>
      </div>
      {sheets && (
        <div className="gx-docv-sheets" role="tablist">
          {sheets.map((s, i) => (
            <button key={i} type="button" role="tab" aria-selected={i === sheet} className={i === sheet ? "on" : ""} title={s.range} onClick={() => setSheet(i)}>{s.name}</button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A PDF as a column of pages, rendered when scrolled into view. */
export function PdfPages({ url, zh, followZoom = true }: { url: string; zh: boolean; followZoom?: boolean }) {
  // inside a document page the CSS zoom already applies
  const contentZoom = useZoom((s) => s.content);
  const zoom = followZoom ? contentZoom : 1;
  const [pdf, setPdf] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [sizes, setSizes] = useState<{ w: number; h: number }[]>([]);
  const box = useRef<HTMLDivElement>(null);
  const [avail, setAvail] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setAvail(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [pdf]);
  useEffect(() => {
    let live = true;
    let task: any = null;
    setPdf(null); setError(null); setSizes([]);
    void (async () => {
      task = pdfjsLib.getDocument({ url });
      const doc = await task.promise;
      const first = await doc.getPage(1);
      const vp = first.getViewport({ scale: 1 });
      if (!live) return;
      setSizes(Array.from({ length: doc.numPages }, () => ({ w: vp.width, h: vp.height })));
      setPdf(doc);
    })().catch((e) => live && setError(String(e?.message || e)));
    return () => { live = false; task?.destroy?.(); };
  }, [url]);
  if (error) return <p className="gx-docv-empty">{zh ? "PDF 打不开：" : "Could not open the PDF: "}{error}</p>;
  if (!pdf) return <div className="gx-chat-loading"><div className="gx-skel w90" /><div className="gx-skel w75" /></div>;
  // fit the page width to the pane (at most 125%), then apply Ctrl+wheel zoom
  const base = sizes[0] && avail ? Math.min(1.25, (avail - 8) / sizes[0].w) : 1;
  const scale = Math.max(0.2, base) * zoom;
  return (
    <div className="gx-pdfpages" ref={box}>
      {sizes.map((s, i) => <PdfPage key={i} pdf={pdf} number={i + 1} width={s.w * scale} height={s.h * scale} scale={scale} />)}
    </div>
  );
}

function PdfPage({ pdf, number, width, height, scale }: { pdf: any; number: number; width: number; height: number; scale: number }) {
  const holder = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setSeen(true), { rootMargin: "600px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!seen || !canvas.current) return;
    let task: any = null;
    let live = true;
    void pdf.getPage(number).then((page: any) => {
      if (!live || !canvas.current) return;
      const ratio = window.devicePixelRatio || 1;
      const vp = page.getViewport({ scale: scale * ratio });
      const c = canvas.current;
      c.width = vp.width; c.height = vp.height;
      task = page.render({ canvasContext: c.getContext("2d"), viewport: vp });
      return task.promise;
    }).catch(() => undefined);
    return () => { live = false; task?.cancel?.(); };
  }, [seen, pdf, number, scale]);
  return (
    <div ref={holder} className="gx-pdfpage" style={{ width, height }}>
      <canvas ref={canvas} style={{ width, height }} />
      <span>{number}</span>
    </div>
  );
}
