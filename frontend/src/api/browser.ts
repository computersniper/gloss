export type BrowserTab = { id: string; url: string; title: string; active?: boolean };
export type BrowserState = { tabs: BrowserTab[]; active?: string; url?: string; title?: string; loading?: boolean; controlling?: "agent" | "user"; log?: { action?: string; note?: string; text?: string; url?: string; at?: number; time?: number }[] };
export type BrowserStatus = { available: boolean; browser?: string; path?: string; headed?: boolean; running?: boolean; reason?: string; restart_pending?: boolean };
export type BrowserFrame = { data: string; width: number; height: number; session_id?: string };
export type BrowserInput = { type: string; url?: string; x?: number; y?: number; delta_x?: number; delta_y?: number; key?: string; text?: string; id?: string; controlling?: "agent" | "user"; width?: number; height?: number };
async function call<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const raw = await r.text();
  let value: any;
  try { value = raw ? JSON.parse(raw) : {}; } catch { throw new Error("Browser service returned an invalid response"); }
  if (!r.ok) throw new Error(typeof value.detail === "string" ? value.detail : value.detail?.message || value.message || r.statusText);
  return value;
}
const base = (id: string) => `/api/sessions/${encodeURIComponent(id)}/browser`;
export const browserApi = {
  status: () => call<BrowserStatus>("/api/browser/status"),
  state: (id: string) => call<BrowserState>(base(id)),
  input: (id: string, body: BrowserInput) => call<any>(`${base(id)}/input`, body),
  read: (id: string) => call<{ title?: string; url?: string; text?: string; markdown?: string }>(`${base(id)}/read`),
  frames: (id: string, onFrame: (frame: BrowserFrame) => void, onState: (state: BrowserState) => void, onError: (message?: string) => void) => {
    const es = new EventSource(`${base(id)}/screencast`);
    const decode = (e: MessageEvent) => { try { return JSON.parse(e.data); } catch { return null; } };
    es.addEventListener("browser.frame", (e) => { const f = decode(e as MessageEvent); if (f?.data) onFrame(f); });
    es.addEventListener("browser.updated", (e) => { const s = decode(e as MessageEvent); if (s) onState(s); });
    es.addEventListener("browser.error", (e) => { const p = decode(e as MessageEvent); onError(p?.message); });
    es.onmessage = (e) => { const p = decode(e); if (p?.data && typeof p.data === "string") onFrame(p); else if (p?.type === "browser.frame") onFrame(p.data); };
    es.onerror = () => onError();
    return () => es.close();
  },
};
