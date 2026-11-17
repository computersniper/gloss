import { useEffect, useState } from "react";

// The GlossX phone / tablet app (mobile/) opens this same frontend from the
// computer's backend as  /?token=…&client=mobile&shell=<connect page URL>.
// The backend turned the token into a cookie, so it is dropped from the URL.

const SHELL_KEY = "glossx.mobileShell";

type CapacitorApp = { addListener: (event: "appUrlOpen", cb: (e: { url: string }) => void) => unknown };
declare global {
  interface Window { Capacitor?: { Plugins?: { App?: CapacitorApp } } }
}

function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function mobileShell(): string | null {
  return read(SHELL_KEY);
}

/** Inside the GlossX phone / tablet app. */
export const isMobileClient = () => !!mobileShell();

/** A phone or tablet: the app, or Safari / Chrome (also from the home screen) after scanning the QR code. */
export const isTouchClient = () =>
  isMobileClient() || (window.matchMedia("(pointer: coarse)").matches && !(window as { glossxDesktop?: unknown }).glossxDesktop);

/** Back to the app's own "pick a computer" page. */
export function leaveComputer(reason = "switch") {
  const shell = mobileShell();
  if (shell) window.location.href = `${shell}#${reason}`;
}

export function initMobileClient() {
  const url = new URL(window.location.href);
  const params = url.searchParams;
  if (params.get("client") === "mobile") {
    const shell = params.get("shell");
    try {
      if (shell) localStorage.setItem(SHELL_KEY, shell);
    } catch {
      /* storage unavailable */
    }
  }
  if (params.has("token") || params.has("client") || params.has("shell")) {
    for (const key of ["token", "client", "shell"]) params.delete(key);
    window.history.replaceState(null, "", url.pathname + (params.toString() ? `?${params}` : "") + url.hash);
  }
  if (!isTouchClient()) return;
  document.documentElement.classList.add("gx-mobile");
  // pinch zooms the PDF (PdfViewer), never the whole app
  document.querySelector('meta[name="viewport"]')?.setAttribute("content", "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");
  // pairing code rotated on the computer: ask for the new one
  void fetch("/api/health").then((r) => {
    if (r.status === 401) leaveComputer("expired");
  }).catch(() => {});
  // a glossx://pair link (QR code) opened while this page is showing
  window.Capacitor?.Plugins?.App?.addListener("appUrlOpen", (e) => {
    if (e.url.startsWith("glossx:")) leaveComputer(`pair=${encodeURIComponent(e.url)}`);
  });
}

/** iPad, iPhone and Mac keyboards say ⌘ where others say Ctrl. */
export const MOD_KEY = /Mac|iPhone|iPad|iPod/.test(navigator.platform) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ? "⌘" : "Ctrl";

const COMPACT = "(max-width: 720px)";

/** Phone-sized window: one pane at a time, tabs at the bottom. */
export function useCompact() {
  const [compact, setCompact] = useState(() => window.matchMedia(COMPACT).matches);
  useEffect(() => {
    const query = window.matchMedia(COMPACT);
    const on = () => setCompact(query.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, []);
  return compact;
}
