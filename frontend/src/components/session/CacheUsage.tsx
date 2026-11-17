import { Database } from "lucide-react";
import { useActiveSession } from "../../sessionStore";
import { formatTokens } from "./toolMeta";
import type { SessionUsage } from "../../api/harness";

export const hitRate = (u?: Partial<SessionUsage> | null) => {
  if (!u) return null;
  if (typeof u.cache_hit_rate === "number") return Math.min(1, Math.max(0, u.cache_hit_rate));
  return u.input_tokens ? Math.min(1, Math.max(0, (u.cached_input_tokens || 0) / u.input_tokens)) : null;
};
const percent = (n: number | null) => n == null ? "—" : `${Math.round(n * 100)}%`;
export function usageHint(u: SessionUsage, zh: boolean) {
  const last = u.last_call;
  return [zh ? (u.exact ? "精确用量" : "估算用量") : u.exact ? "Exact usage" : "Estimated usage",
    `${zh ? "会话" : "Session"}: ${formatTokens(u.input_tokens || u.input_tokens_est || 0)} ${zh ? "输入" : "input"} · ${formatTokens(u.cached_input_tokens || 0)} ${zh ? "缓存" : "cached"} · ${formatTokens(u.output_tokens || u.output_tokens_est || 0)} ${zh ? "输出" : "output"} · ${percent(hitRate(u))}`,
    last ? `${zh ? "上次调用" : "Last call"}: ${formatTokens(last.input || 0)} / ${formatTokens(last.cached || 0)} / ${formatTokens(last.output || 0)} · ${percent(last.hit_rate ?? null)}${last.exact === false ? (zh ? "（估算）" : " (estimated)") : ""}` : "",
  ].filter(Boolean).join("\n");
}
export function CacheIndicator({ zh, status = false }: { zh: boolean; status?: boolean }) {
  const u = useActiveSession().session?.usage;
  if (!u?.llm_calls) return null;
  const last = u.last_call?.hit_rate ?? hitRate(u);
  return <span className={status ? "wb-status-item gx-cache" : "gx-cache"} title={usageHint(u, zh)}><Database size={12} /><span>{status ? `${zh ? "缓存" : "cache"} ${percent(hitRate(u))}${u.exact ? "" : "~"}` : `${percent(last)} / ${percent(hitRate(u))}${u.exact ? "" : "~"}`}</span></span>;
}
export function TurnUsage({ usage, zh }: { usage: SessionUsage; zh: boolean }) {
  return <div className="gx-turn-usage" title={usageHint(usage, zh)}><Database size={11} /><span>{usage.llm_calls || 0} {zh ? "次调用" : "calls"} · {formatTokens(usage.input_tokens || usage.input_tokens_est || 0)} {zh ? "输入" : "in"} · {percent(hitRate(usage))} {zh ? "缓存" : "cached"} · {formatTokens(usage.output_tokens || usage.output_tokens_est || 0)} {zh ? "输出" : "out"}{usage.duration_seconds != null ? ` · ${Math.round(usage.duration_seconds)}s` : ""}{usage.exact ? "" : zh ? " · 估算" : " · estimated"}</span></div>;
}
