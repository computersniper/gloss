import { useCallback, useEffect, useState } from "react";
import { Gauge, RefreshCw } from "lucide-react";
import { useStore } from "../../store";
import { useActiveSession } from "../../sessionStore";
import { Popover, Spinner } from "../ui";

// Plan usage limits (5-hour / weekly) of the accounts the Claude Code and
// Codex CLIs are signed in to (backend: providers/quota.py).
type Window = { id: string; label: string; label_zh: string; used_percent: number; resets_at: number | null; window_minutes: number | null };
type Quota = { provider: string; available: boolean; plan: string | null; windows: Window[]; source: string | null; error: string | null; checked_at: number; observed_at?: number };

const CLI = ["local_claude", "local_codex"];
const NAME: Record<string, string> = { local_claude: "Claude Code", local_codex: "Codex" };
const REFRESH_MS = 5 * 60_000;

async function fetchQuotas(fresh = false): Promise<Quota[]> {
  const r = await fetch(`/api/settings/quota${fresh ? "?fresh=true" : ""}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()).quotas || [];
}

function until(epoch: number | null, zh: boolean) {
  if (!epoch) return "";
  const mins = Math.max(0, Math.round((epoch * 1000 - Date.now()) / 60000));
  const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
  const span = d ? (zh ? `${d} 天 ${h} 小时` : `${d}d ${h}h`) : h ? (zh ? `${h} 小时 ${m} 分` : `${h}h ${m}m`) : (zh ? `${m} 分钟` : `${m}m`);
  return zh ? `${span}后重置` : `resets in ${span}`;
}

const short = (w: Window, zh: boolean) => w.id === "five_hour" || w.window_minutes === 300 ? "5h" : w.window_minutes === 10080 ? (zh ? "周" : "wk") : w.label_zh.replace("额度", "");
const tone = (p: number) => (p >= 90 ? " bad" : p >= 75 ? " warn" : "");

export function QuotaIndicator({ compact = false }: { compact?: boolean }) {
  const defaultProvider = useStore((s) => s.provider);
  const session = useActiveSession();
  const provider = session.session?.model?.provider || defaultProvider;
  const zh = useStore((s) => s.uiLang) === "zh";
  const [quotas, setQuotas] = useState<Quota[]>([]);
  const [loading, setLoading] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const load = useCallback(async (fresh = false) => {
    setLoading(true);
    try { setQuotas(await fetchQuotas(fresh)); } catch { /* backend without the endpoint */ } finally { setLoading(false); }
  }, []);
  useEffect(() => {
    if (!CLI.includes(provider)) return;
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [provider, load]);
  if (!CLI.includes(provider)) return null;
  const mine = quotas.find((q) => q.provider === provider);
  const top = mine?.available ? mine.windows.slice(0, 2) : [];
  const worst = Math.max(0, ...top.map((w) => w.used_percent));
  return (
    <>
      <button
        ref={setAnchor}
        type="button"
        className={`wb-status-item gx-quota-chip${compact ? " compact" : ""}${tone(worst)}`}
        title={zh ? `${NAME[provider]} 账号额度` : `${NAME[provider]} plan usage`}
        onClick={() => { setOpen((o) => !o); if (!open) void load(); }}
        style={{ cursor: "pointer" }}
      >
        <Gauge size={13} />
        <span>{top.length ? (compact ? top.slice(0, 1) : top).map((w) => `${short(w, zh)} ${Math.round(w.used_percent)}%`).join(" · ") : zh ? "额度" : "Usage"}</span>
      </button>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} placement={compact ? "bottom-end" : "top-start"} width={320} className="gx-quota-pop">
        <div className="gx-quota-head">
          <strong>{zh ? "账号额度" : "Plan usage"}</strong>
          <button type="button" className="gx-icon-btn" title={zh ? "刷新" : "Refresh"} disabled={loading} onClick={() => void load(true)}>{loading ? <Spinner size={12} /> : <RefreshCw size={12} />}</button>
        </div>
        {CLI.map((p) => {
          const q = quotas.find((x) => x.provider === p);
          return (
            <section key={p} className={`gx-quota-sec${p === provider ? " current" : ""}`}>
              <div className="gx-quota-name"><b>{NAME[p]}</b>{q?.plan && <em>{q.plan}</em>}</div>
              {!q && <div className="gx-quota-note">{loading ? "…" : zh ? "暂无数据" : "No data"}</div>}
              {q && !q.available && <div className="gx-quota-note">{q.error || (zh ? "无法读取额度" : "Usage is not available")}</div>}
              {q?.available && q.windows.map((w) => (
                <div key={w.id} className="gx-quota-row">
                  <div className="gx-quota-label"><span>{zh ? w.label_zh : w.label}</span><b>{Math.round(w.used_percent)}%</b></div>
                  <div className={`gx-quota-bar${tone(w.used_percent)}`}><i style={{ width: `${Math.min(100, w.used_percent)}%` }} /></div>
                  {w.resets_at && <small>{until(w.resets_at, zh)}</small>}
                </div>
              ))}
              {q?.source === "session_log" && q.observed_at && (
                <div className="gx-quota-note">{zh ? `来自 Codex 上次运行的记录（${new Date(q.observed_at * 1000).toLocaleString()}）` : `From Codex's last run (${new Date(q.observed_at * 1000).toLocaleString()})`}</div>
              )}
            </section>
          );
        })}
      </Popover>
    </>
  );
}
