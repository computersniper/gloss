import { useEffect, useRef, useState } from "react";
import { pdfjsLib, TextLayer } from "./pdfSetup";
import { api, type ChatAttachment, type DrawingTool } from "../api/client";
import { useStore } from "../store";
import { SelectionPopover } from "../components/SelectionPopover";
import { getPdfDoc } from "./docCache";

// Per-paper scroll offsets so switching back to a tab lands where you left off.
const scrollMemo = new Map<string, number>();

const DRAW_COLORS = ["#ef6b6b", "#ffd54f", "#7aa2f7", "#4fb8a0", "#ce93d8"];
const BRUSHES: Record<DrawingTool, { widths: number[]; defaultWidth: number }> = {
  pencil: { widths: [1, 2, 4], defaultWidth: 2 },
  pen: { widths: [2, 4, 7], defaultWidth: 4 },
  highlighter: { widths: [10, 18, 28], defaultWidth: 18 },
};

function savedBrush(): DrawingTool {
  const value = localStorage.getItem("gloss.drawing.brush");
  return value === "pencil" || value === "highlighter" ? value : "pen";
}

interface DraftStroke {
  page: number;
  points: [number, number][];
  color: string;
  width: number;
  tool: DrawingTool;
}

type DrawTool = "select" | "pen" | "eraser" | "lasso";

interface LassoDraft {
  page: number;
  points: [number, number][];
}

interface LassoCapture extends ChatAttachment {
  points: [number, number][];
}

function pointInPolygon(point: [number, number], polygon: [number, number][]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const crosses = (yi > point[1]) !== (yj > point[1])
      && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi || Number.EPSILON) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

// Use PDF.js's OFFICIAL text layer (v4 `TextLayer`) rather than a hand-rolled one,
// so selection matches a real browser PDF viewer — this uses the PDF's embedded
// text (not OCR); it's just rendered/positioned by pdf.js's own builder.
async function renderTextLayer(page: any, viewport: any, layer: HTMLDivElement) {
  layer.innerHTML = "";
  // v4 positions spans relative to this CSS variable
  layer.style.setProperty("--scale-factor", String(viewport.scale));
  if (TextLayer) {
    const tl = new TextLayer({
      textContentSource: page.streamTextContent({ includeMarkedContent: true }),
      container: layer,
      viewport,
    });
    await tl.render();
    return;
  }
  // fallback (older pdf.js): manual span positioning
  const tc = await page.getTextContent();
  const frag = document.createDocumentFragment();
  for (const item of tc.items as any[]) {
    if (!item.str) continue;
    const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
    const fontSize = Math.hypot(tx[2], tx[3]);
    const span = document.createElement("span");
    span.textContent = item.str;
    span.style.left = `${tx[4]}px`;
    span.style.top = `${tx[5] - fontSize}px`;
    span.style.fontSize = `${fontSize}px`;
    frag.appendChild(span);
  }
  layer.appendChild(frag);
}

export function PdfViewer() {
  const current = useStore((s) => s.current);
  const uiLang = useStore((s) => s.uiLang);
  const pages = useStore((s) => s.pages);
  const highlights = useStore((s) => s.highlights);
  const drawings = useStore((s) => s.drawings);
  const refreshDrawings = useStore((s) => s.refreshDrawings);
  const notify = useStore((s) => s.notify);
  const gotoPage = useStore((s) => s.gotoPage);
  const setGoto = useStore((s) => s.setGoto);
  const setSelection = useStore((s) => s.setSelection);
  const flash = useStore((s) => s.flash);
  const clearFlash = useStore((s) => s.clearFlash);
  const addChatAttachment = useStore((s) => s.addChatAttachment);

  const [scale, setScale] = useState(1.35);
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  const zoomAnchorRef = useRef<{ ratio: number; top: number; left: number; anchorY: number; anchorX: number } | null>(null);
  const [loaded, setLoaded] = useState<{ id: string; doc: any } | null>(null);
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [popover, setPopover] = useState<{ x: number; y: number } | null>(null);
  const [showHighlights, setShowHighlights] = useState(() => localStorage.getItem("gloss.layer.highlights") !== "0");
  const [showNotes, setShowNotes] = useState(() => localStorage.getItem("gloss.layer.notes") !== "0");
  const [showInk, setShowInk] = useState(() => localStorage.getItem("gloss.layer.ink") !== "0");
  const [dispOpen, setDispOpen] = useState(false);
  const restoreRef = useRef<string | null>(null);
  const [drawTool, setDrawTool] = useState<DrawTool>("select");
  const [brushType, setBrushType] = useState<DrawingTool>(savedBrush);
  const [drawColor, setDrawColor] = useState(DRAW_COLORS[0]);
  const [drawWidth, setDrawWidth] = useState(() => BRUSHES[savedBrush()].defaultWidth);
  const [draft, setDraft] = useState<DraftStroke | null>(null);
  const draftRef = useRef<DraftStroke | null>(null);
  const [lassoDraft, setLassoDraft] = useState<LassoDraft | null>(null);
  const lassoDraftRef = useRef<LassoDraft | null>(null);
  const [lassoCapture, setLassoCapture] = useState<LassoCapture | null>(null);

  // Load the PDF document once per paper.
  useEffect(() => {
    if (!current) return;
    let cancelled = false;
    setLoaded(null);
    setLoadError("");
    restoreRef.current = current.id;
    // documents are owned by the shared cache (evicted when the tab closes)
    getPdfDoc(current.id).then(async (doc: any) => {
      if (cancelled) return;
      // on a touch screen, open at page width when the default would overflow
      const el = containerRef.current;
      if (el && window.matchMedia("(pointer: coarse)").matches) {
        try {
          const width = (await doc.getPage(1)).getViewport({ scale: 1 }).width;
          const fit = Math.floor(((el.clientWidth - 32) / width) * 100) / 100;
          if (!cancelled && fit > 0.3 && fit < 1.35) setScale(Math.max(0.6, fit));
        } catch { /* keep the default */ }
      }
      if (cancelled) return;
      setLoaded({ id: current.id, doc });
    }).catch((error) => {
      if (!cancelled) setLoadError(String(error?.message || error));
    });
    return () => { cancelled = true; };
  }, [current?.id, retry]);

  // Ctrl+wheel zooms the PDF (not the app). Pages re-render once the wheel
  // settles; the scroll position keeps the same spot under the pointer.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let pending: number | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let wheel = 0;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      e.stopPropagation();
      let step: number;
      if (e.deltaMode !== 0 || Math.abs(e.deltaY) >= 50) { wheel = 0; step = Math.sign(e.deltaY); }
      else { wheel += e.deltaY; if (Math.abs(wheel) < 50) return; step = Math.sign(wheel); wheel = 0; }
      const base = pending ?? scaleRef.current;
      pending = Math.round(Math.min(3, Math.max(0.6, base * (step < 0 ? 1.1 : 1 / 1.1))) * 100) / 100;
      const rect = el.getBoundingClientRect();
      const anchorY = e.clientY - rect.top;
      const anchorX = e.clientX - rect.left;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const next = pending!;
        const ratio = next / scaleRef.current;
        pending = null;
        zoomAnchorRef.current = { ratio, top: el.scrollTop + anchorY, left: el.scrollLeft + anchorX, anchorY, anchorX };
        setScale(next);
      }, 120);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => { el.removeEventListener("wheel", onWheel); if (timer) clearTimeout(timer); };
  }, [current?.id]);

  // Two-finger pinch zooms the PDF on a tablet or phone, the touch twin of
  // Ctrl+wheel: the view stretches while the fingers move, then pages re-render
  // at the new scale with the pinched spot kept in place.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let start: { dist: number; midX: number; midY: number } | null = null;
    let ratio = 1;
    const span = (t: TouchList) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 2) return;
      const rect = el.getBoundingClientRect();
      start = {
        dist: span(e.touches),
        midX: (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left,
        midY: (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top,
      };
      ratio = 1;
      el.style.transformOrigin = `${start.midX + el.scrollLeft}px ${start.midY + el.scrollTop}px`;
    };
    const onMove = (e: TouchEvent) => {
      if (!start || e.touches.length !== 2) return;
      e.preventDefault();
      const next = Math.min(3, Math.max(0.6, scaleRef.current * (span(e.touches) / start.dist)));
      ratio = next / scaleRef.current;
      el.style.transform = `scale(${ratio})`;
    };
    const onEnd = (e: TouchEvent) => {
      if (!start || e.touches.length >= 2) return;
      const { midX, midY } = start;
      start = null;
      el.style.transform = "";
      el.style.transformOrigin = "";
      if (Math.abs(ratio - 1) < 0.03) return;
      const next = Math.round(scaleRef.current * ratio * 100) / 100;
      zoomAnchorRef.current = { ratio: next / scaleRef.current, top: el.scrollTop + midY, left: el.scrollLeft + midX, anchorY: midY, anchorX: midX };
      setScale(next);
    };
    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [current?.id]);

  useEffect(() => {
    setDrawTool("select");
    setDraft(null);
    draftRef.current = null;
    setLassoDraft(null);
    lassoDraftRef.current = null;
    setLassoCapture(null);
  }, [current?.id]);

  // Render all pages whenever the doc is ready or the scale changes.
  useEffect(() => {
    if (!loaded || loaded.id !== current?.id) return;
    const doc = loaded.doc;
    let cancelled = false;
    const tasks: any[] = [];
    // supersample: render the bitmap at >=2x the display size so text is crisp
    // even on 1x monitors, then let CSS downscale it to the layout size.
    const quality = Math.max(2, window.devicePixelRatio || 1);

    (async () => {
      const items: ({ page: any; vp: any } | null)[] = [];
      // pass 1 — size every page holder first so a zoom applies to ALL pages at once
      for (let i = 1; i <= doc.numPages; i++) {
        if (cancelled) return;
        const holder = pageRefs.current[i - 1];
        if (!holder) {
          items[i] = null;
          continue;
        }
        const page = await doc.getPage(i);
        if (cancelled) return;
        const vp = page.getViewport({ scale });
        holder.style.width = `${vp.width}px`;
        holder.style.height = `${vp.height}px`;
        items[i] = { page, vp };
      }
      // all holders are sized now: restore this paper's previous scroll offset
      if (restoreRef.current && restoreRef.current === current?.id && containerRef.current) {
        containerRef.current.scrollTop = scrollMemo.get(restoreRef.current) || 0;
      }
      restoreRef.current = null;
      // after a Ctrl+wheel zoom, keep the point under the pointer in place
      const anchor = zoomAnchorRef.current;
      zoomAnchorRef.current = null;
      if (anchor && containerRef.current) {
        containerRef.current.scrollTop = anchor.top * anchor.ratio - anchor.anchorY;
        containerRef.current.scrollLeft = anchor.left * anchor.ratio - anchor.anchorX;
      }
      // pass 2 — render each page's bitmap + text layer (a failure on one page
      // must not abort the others)
      for (let i = 1; i <= doc.numPages; i++) {
        if (cancelled) return;
        const it = items[i];
        const holder = pageRefs.current[i - 1];
        if (!it || !holder) continue;
        const { page, vp } = it;
        const canvas = holder.querySelector("canvas") as HTMLCanvasElement;
        const textLayer = holder.querySelector(".textLayer") as HTMLDivElement;
        const rvp = page.getViewport({ scale: scale * quality });
        canvas.width = Math.floor(rvp.width);
        canvas.height = Math.floor(rvp.height);
        canvas.style.width = `${vp.width}px`;
        canvas.style.height = `${vp.height}px`;
        // Render offscreen: an old paper/zoom must never paint into a reused canvas.
        const bitmap = document.createElement("canvas");
        bitmap.width = canvas.width;
        bitmap.height = canvas.height;
        const ctx = bitmap.getContext("2d")!;
        try {
          const task = page.render({ canvasContext: ctx, viewport: rvp });
          tasks.push(task);
          try { await task.promise; } finally { tasks.pop(); }
          if (cancelled) return;
          canvas.getContext("2d")!.drawImage(bitmap, 0, 0);
          if (textLayer) {
            const text = document.createElement("div");
            text.className = "textLayer";
            await renderTextLayer(page, vp, text);
            if (cancelled) return;
            textLayer.style.cssText = text.style.cssText;
            const rotation = text.getAttribute("data-main-rotation");
            if (rotation !== null) textLayer.setAttribute("data-main-rotation", rotation);
            textLayer.replaceChildren(...Array.from(text.childNodes));
          }
        } catch (error) {
          if (!cancelled) setLoadError(String((error as any)?.message || error));
        }
      }
    })().catch((error) => {
      if (!cancelled) setLoadError(String(error?.message || error));
    });
    return () => {
      cancelled = true;
      tasks.forEach((task) => { try { task.cancel(); } catch {} });
    };
  }, [loaded, scale, current?.id]);

  // Scroll to a requested page/section.
  useEffect(() => {
    if (gotoPage == null) return;
    restoreRef.current = null; // an explicit jump wins over scroll restore
    const el = pageRefs.current[gotoPage];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    setGoto(null);
  }, [gotoPage]);

  // Auto-clear a transient flash highlight (from click-to-locate) after a moment.
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => clearFlash(), 1700);
    return () => clearTimeout(t);
  }, [flash?.id]);

  function onMouseUp() {
    if (drawTool !== "select") return;
    const sel = window.getSelection();
    const text = sel?.toString().trim() || "";
    if (!text || !sel || sel.rangeCount === 0) {
      setPopover(null);
      return;
    }
    // find the page container of the selection
    let node: Node | null = sel.anchorNode;
    let pageEl: HTMLElement | null = null;
    while (node) {
      if (node instanceof HTMLElement && node.dataset.pageIndex != null) {
        pageEl = node;
        break;
      }
      node = node.parentNode;
    }
    if (!pageEl) {
      // maybe anchorNode is a text node inside .text-layer
      const anc = sel.anchorNode?.parentElement?.closest("[data-page-index]") as HTMLElement | null;
      pageEl = anc;
    }
    if (!pageEl) {
      setPopover(null);
      return;
    }
    const pageIndex = parseInt(pageEl.dataset.pageIndex!, 10);
    const pr = pageEl.getBoundingClientRect();
    const range = sel.getRangeAt(0);
    const clientRects = Array.from(range.getClientRects());
    const rects = clientRects.map(
      (r) =>
        [
          (r.left - pr.left) / scale,
          (r.top - pr.top) / scale,
          (r.right - pr.left) / scale,
          (r.bottom - pr.top) / scale,
        ] as [number, number, number, number],
    );
    setSelection({ text, page: pageIndex, rects });
    const last = clientRects[clientRects.length - 1];
    if (last) {
      // position the popover in viewport coords, right beside the selection
      // (rendered position:fixed), clamped so it can't run off the right edge
      setPopover({
        x: Math.max(8, Math.min(last.left, window.innerWidth - 330)),
        y: last.bottom + 8,
      });
    }
  }

  function pointOnPage(event: React.PointerEvent<SVGSVGElement>, page: number): [number, number] {
    const holder = pageRefs.current[page];
    const rect = holder?.getBoundingClientRect() || event.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, (event.clientX - rect.left) / scale),
      Math.max(0, (event.clientY - rect.top) / scale),
    ];
  }

  function beginStroke(event: React.PointerEvent<SVGSVGElement>, page: number) {
    if ((drawTool !== "pen" && drawTool !== "lasso") || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    if (drawTool === "lasso") {
      const next = { page, points: [pointOnPage(event, page)] };
      lassoDraftRef.current = next;
      setLassoDraft(next);
      setLassoCapture(null);
      return;
    }
    const next: DraftStroke = {
      page,
      points: [pointOnPage(event, page)],
      color: drawColor,
      width: drawWidth,
      tool: brushType,
    };
    draftRef.current = next;
    setDraft(next);
  }

  function extendStroke(event: React.PointerEvent<SVGSVGElement>, page: number) {
    const currentLasso = lassoDraftRef.current;
    if (drawTool === "lasso" && currentLasso && currentLasso.page === page) {
      const point = pointOnPage(event, page);
      const previous = currentLasso.points[currentLasso.points.length - 1];
      if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) < 1.8) return;
      const next = { ...currentLasso, points: [...currentLasso.points, point] };
      lassoDraftRef.current = next;
      setLassoDraft(next);
      return;
    }
    const currentDraft = draftRef.current;
    if (drawTool !== "pen" || !currentDraft || currentDraft.page !== page) return;
    const point = pointOnPage(event, page);
    const previous = currentDraft.points[currentDraft.points.length - 1];
    if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) < 1.2) return;
    const next = { ...currentDraft, points: [...currentDraft.points, point] };
    draftRef.current = next;
    setDraft(next);
  }

  function finishStroke(event: React.PointerEvent<SVGSVGElement>) {
    const completedLasso = lassoDraftRef.current;
    if (completedLasso) {
      try { event.currentTarget.releasePointerCapture(event.pointerId); } catch {}
      lassoDraftRef.current = null;
      setLassoDraft(null);
      if (completedLasso.points.length >= 8) captureLasso(completedLasso);
      return;
    }
    const completed = draftRef.current;
    if (!completed) return;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch {}
    draftRef.current = null;
    setDraft(null);
    if (!current || completed.points.length < 2) return;
    void api.addDrawing(current.id, completed)
      .then(() => refreshDrawings())
      .catch((error) => notify(String(error?.message || error)));
  }

  function captureLasso(completed: LassoDraft) {
    const holder = pageRefs.current[completed.page];
    const canvas = holder?.querySelector("canvas") as HTMLCanvasElement | null;
    if (!holder || !canvas || !canvas.width || !canvas.height) return;

    const xs = completed.points.map((point) => point[0]);
    const ys = completed.points.map((point) => point[1]);
    const bounds: [number, number, number, number] = [
      Math.max(0, Math.min(...xs) - 3),
      Math.max(0, Math.min(...ys) - 3),
      Math.max(...xs) + 3,
      Math.max(...ys) + 3,
    ];
    if (bounds[2] - bounds[0] < 12 || bounds[3] - bounds[1] < 12) {
      notify(uiLang === "zh" ? "圈选区域太小" : "The selected region is too small");
      return;
    }

    const holderRect = holder.getBoundingClientRect();
    const pixelX = canvas.width / holderRect.width;
    const pixelY = canvas.height / holderRect.height;
    const sourceX = bounds[0] * scale * pixelX;
    const sourceY = bounds[1] * scale * pixelY;
    const sourceWidth = (bounds[2] - bounds[0]) * scale * pixelX;
    const sourceHeight = (bounds[3] - bounds[1]) * scale * pixelY;
    const outputScale = Math.min(1, 1200 / sourceWidth, 900 / sourceHeight);
    const output = document.createElement("canvas");
    output.width = Math.max(1, Math.round(sourceWidth * outputScale));
    output.height = Math.max(1, Math.round(sourceHeight * outputScale));
    const context = output.getContext("2d");
    if (!context) return;
    context.scale(outputScale, outputScale);
    context.beginPath();
    completed.points.forEach(([x, y], index) => {
      const px = x * scale * pixelX - sourceX;
      const py = y * scale * pixelY - sourceY;
      if (index === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    });
    context.closePath();
    context.clip();
    context.drawImage(
      canvas,
      sourceX, sourceY, sourceWidth, sourceHeight,
      0, 0, sourceWidth, sourceHeight,
    );

    const extractedText = Array.from(holder.querySelectorAll(".textLayer span"))
      .filter((span) => {
        const rect = span.getBoundingClientRect();
        const center: [number, number] = [
          (rect.left + rect.width / 2 - holderRect.left) / scale,
          (rect.top + rect.height / 2 - holderRect.top) / scale,
        ];
        return pointInPolygon(center, completed.points);
      })
      .map((span) => span.textContent?.trim() || "")
      .filter(Boolean)
      .join(" ")
      .slice(0, 30_000);
    const id = globalThis.crypto?.randomUUID?.() || `region-${Date.now()}`;
    setLassoCapture({
      id,
      kind: "pdf_region",
      page: completed.page,
      image_data_url: output.toDataURL("image/png"),
      extracted_text: extractedText,
      bounds,
      points: completed.points,
    });
  }

  function attachLassoToChat() {
    if (!lassoCapture) return;
    const { points: _points, ...attachment } = lassoCapture;
    setSelection(null);
    addChatAttachment(attachment);
    setLassoCapture(null);
    activateDrawing("select");
  }

  function activateDrawing(tool: DrawTool) {
    setDrawTool(tool);
    if ((tool === "pen" || tool === "eraser") && !showInk) {
      setShowInk(true);
      localStorage.setItem("gloss.layer.ink", "1");
    }
    if (tool !== "lasso") {
      setLassoDraft(null);
      lassoDraftRef.current = null;
    }
    setPopover(null);
    window.getSelection()?.removeAllRanges();
  }

  function selectBrush(tool: DrawingTool) {
    if (tool !== brushType) setDrawWidth(BRUSHES[tool].defaultWidth);
    setBrushType(tool);
    localStorage.setItem("gloss.drawing.brush", tool);
    activateDrawing("pen");
  }

  function eraseDrawing(event: React.PointerEvent<SVGPolylineElement>, drawingId: string) {
    if (drawTool !== "eraser") return;
    event.preventDefault();
    event.stopPropagation();
    void api.deleteDrawing(drawingId)
      .then(() => refreshDrawings())
      .catch((error) => notify(String(error?.message || error)));
  }

  const strokePoints = (points: [number, number][]) =>
    points.map(([x, y]) => `${x * scale},${y * scale}`).join(" ");

  function setLayer(
    key: "highlights" | "notes" | "ink",
    value: boolean,
    setter: (next: boolean) => void,
  ) {
    setter(value);
    localStorage.setItem(`gloss.layer.${key}`, value ? "1" : "0");
  }

  if (!current) return null;
  const n = current.n_pages;

  return (
    <div className="pdf-wrap">
      {loadError && <div role="alert" className="pdf-load-error">
        {uiLang === "zh" ? "PDF 打开失败：" : "Could not open PDF: "}{loadError}
        <button onClick={() => { setLoadError(""); setRetry((value) => value + 1); }}>
          {uiLang === "zh" ? "重试" : "Retry"}
        </button>
      </div>}
      <div className="pdf-toolbar">
        <span className="paper-title" title={current.title}>
          {current.title || "Untitled"}
        </span>
        <div className="zoom">
          <button onClick={() => setScale((s) => Math.max(0.6, s - 0.15))}>−</button>
          <span>{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale((s) => Math.min(3, s + 0.15))}>+</button>
        </div>
        <div className="pdf-annotate">
          <button
            className={`pdf-disp-btn ${drawTool !== "select" ? "active" : ""}`}
            title={uiLang === "zh" ? "自由画笔标注" : "Freehand annotation"}
            onClick={() => activateDrawing(drawTool === "select" ? "pen" : "select")}
          >
            ✎
          </button>
          {drawTool !== "select" && (
            <div className="draw-tools">
              {(["pencil", "pen", "highlighter"] as DrawingTool[]).map((tool) => (
                <button
                  key={tool}
                  className={`draw-brush ${drawTool === "pen" && brushType === tool ? "active" : ""}`}
                  title={{
                    pencil: uiLang === "zh" ? "铅笔：轻柔半透明笔迹" : "Pencil: soft translucent strokes",
                    pen: uiLang === "zh" ? "写字笔：清晰实色笔迹" : "Pen: crisp solid strokes",
                    highlighter: uiLang === "zh" ? "荧光笔：宽幅透明笔迹" : "Highlighter: wide translucent strokes",
                  }[tool]}
                  onClick={() => selectBrush(tool)}
                >
                  {{ pencil: "✎", pen: "✒", highlighter: "▰" }[tool]}
                  <span>{{
                    pencil: uiLang === "zh" ? "铅笔" : "Pencil",
                    pen: uiLang === "zh" ? "写字笔" : "Pen",
                    highlighter: uiLang === "zh" ? "荧光笔" : "Highlighter",
                  }[tool]}</span>
                </button>
              ))}
              <button
                className={`draw-lasso ${drawTool === "lasso" ? "active" : ""}`}
                title={uiLang === "zh" ? "套索截图：在 PDF 上画一个闭合区域" : "Lasso screenshot: draw a closed region on the PDF"}
                onClick={() => activateDrawing("lasso")}
              >
                ◯ <span>{uiLang === "zh" ? "套索截图" : "Lasso"}</span>
              </button>
              <button
                className={`draw-eraser ${drawTool === "eraser" ? "active" : ""}`}
                title={uiLang === "zh" ? "橡皮擦：点击笔迹整条擦除" : "Eraser: click a stroke to remove it"}
                onClick={() => activateDrawing("eraser")}
              >
                ⌫ <span>{uiLang === "zh" ? "橡皮擦" : "Eraser"}</span>
              </button>
              {drawTool === "pen" && (
                <>
                  <span className="draw-colors">
                    {DRAW_COLORS.map((color) => (
                      <button key={color} className={`draw-color ${drawColor === color ? "selected" : ""}`} style={{ background: color }} onClick={() => { setDrawColor(color); activateDrawing("pen"); }} />
                    ))}
                  </span>
                  <span className="draw-widths">
                    {BRUSHES[brushType].widths.map((width) => (
                      <button key={width} className={drawWidth === width ? "active" : ""} title={`${width}px`} onClick={() => { setDrawWidth(width); activateDrawing("pen"); }}>
                        <span style={{ width, height: width }} />
                      </button>
                    ))}
                  </span>
                </>
              )}
              <button onClick={() => activateDrawing("select")}>{uiLang === "zh" ? "完成" : "Done"}</button>
            </div>
          )}
        </div>
        <div className="pdf-disp">
          <button className="pdf-disp-btn" title={uiLang === "zh" ? "显示设置" : "Display settings"} onClick={() => setDispOpen((o) => !o)}>
            👁
          </button>
          {dispOpen && (
            <div className="pdf-disp-pop" onMouseLeave={() => setDispOpen(false)}>
              <div className="pdf-disp-title">{uiLang === "zh" ? "PDF 图层" : "PDF layers"}</div>
              <label>
                <input
                  type="checkbox"
                  checked={showHighlights}
                  onChange={(event) => setLayer("highlights", event.target.checked, setShowHighlights)}
                />
                <span>{uiLang === "zh" ? "荧光高亮" : "Highlights"}</span>
                <small>{highlights.length}</small>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={showNotes}
                  onChange={(event) => setLayer("notes", event.target.checked, setShowNotes)}
                />
                <span>{uiLang === "zh" ? "批注笔记" : "Note pins"}</span>
                <small>{highlights.filter((highlight) => highlight.note).length}</small>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={showInk}
                  onChange={(event) => setLayer("ink", event.target.checked, setShowInk)}
                />
                <span>{uiLang === "zh" ? "自由画笔" : "Freehand ink"}</span>
                <small>{drawings.length}</small>
              </label>
            </div>
          )}
        </div>
      </div>
      {lassoCapture && (
        <div className="lasso-preview">
          <img src={lassoCapture.image_data_url} alt={uiLang === "zh" ? "PDF 圈选区域预览" : "Selected PDF region preview"} />
          <div>
            <strong>{uiLang === "zh" ? `第 ${lassoCapture.page + 1} 页圈选区域` : `Page ${lassoCapture.page + 1} region`}</strong>
            <small>{lassoCapture.extracted_text
              ? (uiLang === "zh" ? `已提取 ${lassoCapture.extracted_text.length} 个字符` : `${lassoCapture.extracted_text.length} text characters extracted`)
              : (uiLang === "zh" ? "图片区域" : "Image region")}</small>
            <span>
              <button onClick={() => setLassoCapture(null)}>{uiLang === "zh" ? "重新圈选" : "Retake"}</button>
              <button className="primary" onClick={attachLassoToChat}>{uiLang === "zh" ? "加入会话" : "Add to chat"}</button>
              <button className="x" onClick={() => { setLassoCapture(null); activateDrawing("select"); }}>×</button>
            </span>
          </div>
        </div>
      )}
      <div className={`pdf-scroll draw-${drawTool}`} ref={containerRef} onMouseUp={onMouseUp}
        onScroll={(e) => {
          if (current && !restoreRef.current) scrollMemo.set(current.id, e.currentTarget.scrollTop);
        }}>
        {Array.from({ length: n }).map((_, i) => {
          const pinfo = pages?.pages[i];
          const scaleFromPdf = pinfo ? scale : scale;
          return (
            <div
              key={`${current.id}:${i}`}
              className="pdf-page"
              data-page-index={i}
              ref={(el) => (pageRefs.current[i] = el)}
            >
              <canvas />
              {showHighlights && (
                <div className="highlight-layer">
                  {highlights
                    .filter((h) => h.page === i)
                    .map((h) =>
                      h.rects.map((r, ri) => (
                        <div
                          key={h.id + ri}
                          className="hl"
                          title={h.note || h.category}
                          style={{
                            left: r[0] * scaleFromPdf,
                            top: r[1] * scaleFromPdf,
                            width: (r[2] - r[0]) * scaleFromPdf,
                            height: (r[3] - r[1]) * scaleFromPdf,
                            background: h.color + (h.kind === "auto" ? "66" : "99"),
                          }}
                          onClick={() => useStore.getState().setTab("highlights")}
                        />
                      )),
                    )}
                </div>
              )}
              {showNotes && (
                <div className="note-pin-layer">
                  {highlights
                    .filter((highlight) => highlight.page === i && highlight.note && highlight.rects.length > 0)
                    .map((highlight) => {
                      const anchor = highlight.rects[highlight.rects.length - 1];
                      return (
                        <button
                          key={highlight.id}
                          className="pdf-note-pin"
                          style={{ left: anchor[2] * scale + 4, top: Math.max(2, anchor[1] * scale - 9) }}
                          title={highlight.note}
                          aria-label={uiLang === "zh" ? "打开批注笔记" : "Open note"}
                          onClick={() => useStore.getState().setTab("highlights")}
                        >
                          📝
                        </button>
                      );
                    })}
                </div>
              )}
              {(showInk || drawTool === "lasso") && (
                <svg
                  className={`ink-layer ${drawTool}`}
                  onPointerDown={(event) => beginStroke(event, i)}
                  onPointerMove={(event) => extendStroke(event, i)}
                  onPointerUp={finishStroke}
                  onPointerCancel={finishStroke}
                >
                  <defs>
                    <filter id={`pencil-texture-${i}`} x="-10%" y="-10%" width="120%" height="120%">
                      <feTurbulence type="fractalNoise" baseFrequency="0.035 0.7" numOctaves="2" seed={i + 7} result="grain" />
                      <feDisplacementMap in="SourceGraphic" in2="grain" scale="0.7" />
                    </filter>
                  </defs>
                  {showInk && drawings.filter((drawing) => drawing.page === i).map((drawing) => (
                    <g key={drawing.id}>
                      <polyline
                        className={`ink-stroke brush-${drawing.tool || "pen"}`}
                        points={strokePoints(drawing.points)}
                        stroke={drawing.color}
                        strokeWidth={drawing.width * scale}
                        filter={(drawing.tool || "pen") === "pencil" ? `url(#pencil-texture-${i})` : undefined}
                      />
                      {drawTool === "eraser" && (
                        <polyline className="ink-hit" points={strokePoints(drawing.points)} strokeWidth={Math.max(14, drawing.width * scale + 10)} onPointerDown={(event) => eraseDrawing(event, drawing.id)} />
                      )}
                    </g>
                  ))}
                  {showInk && draft && draft.page === i && (
                    <polyline
                      className={`ink-stroke brush-${draft.tool} draft`}
                      points={strokePoints(draft.points)}
                      stroke={draft.color}
                      strokeWidth={draft.width * scale}
                      filter={draft.tool === "pencil" ? `url(#pencil-texture-${i})` : undefined}
                    />
                  )}
                  {lassoDraft && lassoDraft.page === i && (
                    <polyline className="lasso-path draft" points={strokePoints(lassoDraft.points)} />
                  )}
                  {lassoCapture && lassoCapture.page === i && (
                    <polygon className="lasso-path captured" points={strokePoints(lassoCapture.points)} />
                  )}
                </svg>
              )}
              {flash && flash.page === i && (
                <div className="flash-layer">
                  {flash.rects.map((r, ri) => (
                    <div
                      key={ri}
                      className="flash-rect"
                      style={{
                        left: r[0] * scale,
                        top: r[1] * scale,
                        width: (r[2] - r[0]) * scale,
                        height: (r[3] - r[1]) * scale,
                      }}
                    />
                  ))}
                </div>
              )}
              <div className="textLayer" />
              <div className="page-num">{i + 1}</div>
            </div>
          );
        })}
      </div>
      {popover && (
        <SelectionPopover pos={popover} onClose={() => setPopover(null)} />
      )}
    </div>
  );
}
