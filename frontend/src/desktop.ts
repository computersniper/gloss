import { useZoom } from "./zoom";

// The Electron shell's API (desktop/preload.js). Absent when GlossX runs in a
// regular browser, so every use is optional.
export type DesktopCommand = "undo" | "redo" | "cut" | "copy" | "paste" | "selectAll" | "newWindow" | "fullscreen" | "devtools" | "quit";

export type DesktopApi = {
  shell: "electron";
  platform: string;
  command: (action: DesktopCommand) => Promise<boolean>;
  openFolder?: () => Promise<string | null>;
  setTitleBar: (bar: { color: string; symbolColor: string; height: number }) => Promise<boolean>;
  /** Feeds the taskbar jump list's recent papers / projects (newer shells only). */
  recent?: (entry: { kind: "paper" | "project"; id: string; title: string }) => Promise<boolean>;
  cancelExit: () => Promise<boolean>;
  confirmExit: (dontAskAgain: boolean) => Promise<boolean>;
  browser: {
    show: (targetId: string, rect: { x: number; y: number; width: number; height: number }) => Promise<boolean>;
    hide: () => Promise<boolean>;
  };
};

declare global {
  interface Window { glossxDesktop?: DesktopApi }
}

export const desktop: DesktopApi | undefined = typeof window === "undefined" ? undefined : window.glossxDesktop;

/** Another GlossX window on the same local app, as in VS Code. */
export function newWindow() {
  if (desktop) void desktop.command("newWindow");
  else window.open(window.location.href, "_blank");
}

// Any CSS colour (including color-mix results) as #rrggbb.
function hex(color: string): string | null {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

const TITLEBAR_HEIGHT = 40;

/** Let the app's own top bar act as the window title bar. */
export function initDesktopChrome() {
  const api = desktop;
  if (!api) return;
  const root = document.documentElement;
  root.classList.add("gx-desktop", `gx-desktop-${api.platform}`);
  // The bar is TITLEBAR_HEIGHT CSS pixels tall and the app zoom (zoom.ts)
  // scales it, so the system buttons follow, and the room kept for them
  // shrinks back to CSS pixels.
  const zoom = () => useZoom.getState().app;
  const overlay = (navigator as Navigator & { windowControlsOverlay?: EventTarget & { getTitlebarAreaRect: () => DOMRect } }).windowControlsOverlay;
  const reserve = () => {
    const area = overlay?.getTitlebarAreaRect();
    const right = area && area.width > 0 ? Math.max(0, window.innerWidth - area.x - area.width) : api.platform === "darwin" ? 0 : 138;
    root.style.setProperty("--gx-controls-right", `${Math.round(right / zoom())}px`);
  };
  overlay?.addEventListener("geometrychange", reserve);
  window.addEventListener("resize", reserve);
  reserve();
  let last = "", queued = false;
  // The system window buttons sit on the bar, so they take its colours. The
  // bar changes with the theme and with the Gloss / GlossX mode.
  const sync = () => {
    queued = false;
    const bar = document.querySelector(".wb-titlebar, .topbar");
    if (!bar) return;
    const style = getComputedStyle(bar);
    const height = Math.round(TITLEBAR_HEIGHT * zoom());
    const raw = `${style.backgroundColor}|${style.color}|${height}`;
    if (raw === last) return;
    last = raw;
    const color = hex(style.backgroundColor);
    const symbol = hex(style.color);
    if (color && symbol) void api.setTitleBar({ color, symbolColor: symbol, height }).then(reserve);
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(sync); } };
  new MutationObserver(queue).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  new MutationObserver(queue).observe(document.body, { childList: true, subtree: true });
  useZoom.subscribe(() => { queue(); reserve(); });
  queue();
}
