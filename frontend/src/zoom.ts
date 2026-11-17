// Zoom, following VS Code: Ctrl+= / Ctrl+- / Ctrl+0 zoom the whole window,
// while Ctrl+wheel only enlarges the content under the pointer (the open
// file, diff or report; the PDF viewer handles its own wheel zoom). The
// browser's own page zoom is suppressed so the two never stack.
import { create } from "zustand";

const APP_KEY = "glossx.zoom.app";
const CONTENT_KEY = "glossx.zoom.content";
export const APP_ZOOM_MIN = 0.5;
export const APP_ZOOM_MAX = 2;
const APP_STEP = 0.1;
const CONTENT_MIN = 0.5;
const CONTENT_MAX = 3;
const CONTENT_STEP = 0.1;
/** Ctrl+wheel inside these zooms their content; elsewhere it does nothing. */
const CONTENT_SELECTOR = ".gx-file-body, .wb-report";

const read = (key: string) => {
  try { const v = Number(localStorage.getItem(key)); return Number.isFinite(v) && v > 0 ? v : 1; } catch { return 1; }
};
const write = (key: string, value: number) => { try { localStorage.setItem(key, String(value)); } catch { /* private mode */ } };
const round = (v: number) => Math.round(v * 100) / 100;
const clamp = (v: number, lo: number, hi: number) => round(Math.min(hi, Math.max(lo, v)));

interface ZoomState {
  app: number;
  content: number;
  setApp: (value: number) => void;
  setContent: (value: number) => void;
}

export const useZoom = create<ZoomState>((set) => ({
  app: clamp(read(APP_KEY), APP_ZOOM_MIN, APP_ZOOM_MAX),
  content: clamp(read(CONTENT_KEY), CONTENT_MIN, CONTENT_MAX),
  setApp: (value) => { const app = clamp(value, APP_ZOOM_MIN, APP_ZOOM_MAX); write(APP_KEY, app); set({ app }); },
  setContent: (value) => { const content = clamp(value, CONTENT_MIN, CONTENT_MAX); write(CONTENT_KEY, content); set({ content }); },
}));

export const zoomIn = () => useZoom.getState().setApp(useZoom.getState().app + APP_STEP);
export const zoomOut = () => useZoom.getState().setApp(useZoom.getState().app - APP_STEP);
export const zoomReset = () => useZoom.getState().setApp(1);
export const contentZoomReset = () => useZoom.getState().setContent(1);

function apply({ app, content }: ZoomState) {
  const root = document.documentElement;
  root.style.zoom = app === 1 ? "" : String(app);
  root.style.setProperty("--gx-content-zoom", String(content));
}

/** Which zoom a key press asks for (null when it is not a zoom shortcut). */
export function zoomKey(e: Pick<KeyboardEvent, "ctrlKey" | "metaKey" | "altKey" | "key" | "code">): "in" | "out" | "reset" | null {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  if (e.key === "=" || e.key === "+" || e.code === "NumpadAdd") return "in";
  if (e.key === "-" || e.key === "_" || e.code === "NumpadSubtract") return "out";
  if (e.key === "0" || e.code === "Numpad0") return "reset";
  return null;
}

let installed = false;
/** Install the global listeners once (idempotent). */
export function installZoom() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  apply(useZoom.getState());
  useZoom.subscribe(apply);
  window.addEventListener("keydown", (e) => {
    const action = zoomKey(e);
    if (!action) return;
    e.preventDefault();
    e.stopPropagation();
    if (action === "in") zoomIn(); else if (action === "out") zoomOut(); else zoomReset();
  }, true);
  let wheel = 0;
  window.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); // never the browser's page zoom (this is also a trackpad pinch)
    const target = e.target instanceof Element ? e.target : null;
    if (!target?.closest(CONTENT_SELECTOR)) return;
    // One step per mouse notch; trackpads send many small deltas, so add those up.
    let step: number;
    if (e.deltaMode !== 0 || Math.abs(e.deltaY) >= 50) { wheel = 0; step = Math.sign(e.deltaY); }
    else { wheel += e.deltaY; if (Math.abs(wheel) < 50) return; step = Math.sign(wheel); wheel = 0; }
    const { content, setContent } = useZoom.getState();
    setContent(content - step * CONTENT_STEP);
  }, { passive: false });
}
