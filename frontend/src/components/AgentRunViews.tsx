import { useMemo } from "react";
import type { AgentEvent, AgentRun, AgentSpan } from "../api/agent";
import { useStore } from "../store";
import { Markdown } from "./Markdown";

// Shared pieces of an agent run, used by the AI pane, report tabs and the bottom panel.

// Trace groups in pipeline order; any phase the backend adds later is still shown at the end.
const PHASES = [
  "route", "plan", "gate", "approval", "execute", "adapter",
  "synthesize", "verify", "reflect", "budget", "final", "error",
] as const;

export function agentText(zh: boolean) {
  return zh
    ? {
        trace: "执行轨迹", plan: "计划", answer: "结论", claims: "论断核查", evidence: "证据",
        stop: "停止原因", rounds: "轮数", page: "第", pageSfx: "页", supported: "支持", partial: "部分支持",
        unsupported: "无依据", route: "路由", generic: "通用", approval: "需要审批", capability: "能力",
        permissions: "权限", approve: "批准", deny: "拒绝", awaiting: "等待审批", goal: "目标", status: "状态",
        steps: "步骤", llm: "LLM 调用", tokens: "Token", noTrace: "暂无轨迹事件。",
        status_: { running: "运行中", awaiting_approval: "等待审批", completed: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>,
        stops: { goal_met: "目标达成", max_rounds: "达到轮数上限", budget: "预算耗尽", no_progress: "无进展", approval_needed: "需要审批（被拒绝或超时）", error: "出错", cancelled: "已取消" } as Record<string, string>,
      }
    : {
        trace: "Trace", plan: "Plan", answer: "Answer", claims: "Claims", evidence: "Evidence",
        stop: "Stop reason", rounds: "Rounds", page: "p.", pageSfx: "", supported: "supported", partial: "partial",
        unsupported: "unsupported", route: "Route", generic: "Generic", approval: "Approval required", capability: "Capability",
        permissions: "Permissions", approve: "Approve", deny: "Deny", awaiting: "awaiting approval", goal: "Goal", status: "Status",
        steps: "Steps", llm: "LLM calls", tokens: "Tokens", noTrace: "No trace events yet.",
        status_: { running: "running", awaiting_approval: "awaiting approval", completed: "completed", failed: "failed", cancelled: "cancelled" } as Record<string, string>,
        stops: { goal_met: "goal met", max_rounds: "max rounds reached", budget: "budget exhausted", no_progress: "no progress", approval_needed: "approval needed (denied or timed out)", error: "error", cancelled: "cancelled" } as Record<string, string>,
      };
}

function statusIcon(status: string) {
  if (/fail|error/i.test(status)) return "✗";
  if (/complete|done|ok|success/i.test(status)) return "✓";
  return "…";
}

function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="agent-meter" title={`${value} / ${max}`}>
      <span className="agent-meter-label">{label}</span>
      <span className="agent-meter-bar"><i className={pct >= 90 ? "hot" : ""} style={{ width: `${pct}%` }} /></span>
      <span className="agent-meter-val">{value}/{max}</span>
    </div>
  );
}

export function BudgetMeter({ budget, zh }: { budget: NonNullable<AgentRun["budget"]>; zh: boolean }) {
  const T = agentText(zh);
  return (
    <div className="agent-budget">
      <Meter label={T.steps} value={budget.steps} max={budget.max_steps} />
      <Meter label={T.llm} value={budget.llm_calls} max={budget.max_llm_calls} />
      <Meter label={T.tokens} value={budget.tokens_est} max={budget.max_tokens_est} />
    </div>
  );
}

export function RunTrace({ events }: { events: AgentEvent[] }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const extra = Array.from(
    new Set(events.map((e) => e.phase).filter((p) => p !== "end" && !(PHASES as readonly string[]).includes(p))),
  );
  const grouped = [...PHASES, ...extra]
    .map((p) => ({ phase: p, items: events.filter((e) => e.phase === p) }))
    .filter((g) => g.items.length);
  if (!grouped.length) return <div className="muted">{agentText(zh).noTrace}</div>;
  return (
    <>
      {grouped.map((g) => (
        <div key={g.phase} className="agent-phase">
          <div className="agent-phase-name">{g.phase}</div>
          {g.items.map((e, i) => (
            <div key={i} className={`agent-ev ${/fail|error/i.test(e.status) ? "bad" : ""}`}>
              <span className="agent-ev-icon">{statusIcon(e.status)}</span>
              <span>
                {e.capability && <code>{e.capability}</code>} {e.detail || e.status}
              </span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}

/** Route, budget, pending approval and plan of a run. */
export function RunStatus({ run, showPlan = true }: { run: AgentRun; showPlan?: boolean }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const decide = useStore((s) => s.decideApproval);
  const notify = useStore((s) => s.notify);
  const T = agentText(zh);
  const running = run.status === "running" || run.status === "awaiting_approval";
  const onDecide = (ok: boolean) => decide(run.id, ok).catch((e) => notify(String(e?.message || e)));
  return (
    <>
      {run.route && (
        <div className="agent-route">
          <span className="muted">{T.route}</span>{" "}
          <code>{run.route.workflow || T.generic}</code> · <code>{run.route.loop}</code>
          {run.route.reason && <div className="muted">{run.route.reason}</div>}
        </div>
      )}
      {run.budget && <BudgetMeter budget={run.budget} zh={zh} />}
      {run.status === "awaiting_approval" && !run.pending_approval && <div className="muted">{T.awaiting}…</div>}
      {run.pending_approval && (
        <div className="agent-approval">
          <strong>{T.approval}</strong>
          <div><span className="muted">{T.capability}: </span><code>{run.pending_approval.capability}</code></div>
          <div>
            <span className="muted">{T.permissions}: </span>
            {run.pending_approval.permissions.map((p) => <span key={p} className="agent-badge partial">{p}</span>)}
          </div>
          {run.pending_approval.reason && <div>{run.pending_approval.reason}</div>}
          <pre className="agent-args">{JSON.stringify(run.pending_approval.args, null, 2).slice(0, 1200)}</pre>
          <div className="panel-actions">
            <button className="primary" onClick={() => void onDecide(true)}>{T.approve}</button>
            <button onClick={() => void onDecide(false)}>{T.deny}</button>
          </div>
        </div>
      )}
      {showPlan && run.plan && (
        <details className="agent-section" open={running}>
          <summary>{T.plan}</summary>
          {!!run.plan.sub_questions?.length && (
            <ul className="agent-list">{run.plan.sub_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
          )}
          {!!run.plan.steps?.length && (
            <ol className="agent-list">
              {run.plan.steps.map((s) => (
                <li key={s.id}>
                  <code>{s.capability}</code> {s.purpose && <span className="muted">{s.purpose}</span>}
                </li>
              ))}
            </ol>
          )}
        </details>
      )}
      {run.error && <div className="error">{run.error}</div>}
    </>
  );
}

/** Answer with numbered citations, claims with verdicts and the evidence list. */
export function RunResult({ run }: { run: AgentRun }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const current = useStore((s) => s.current);
  const activeTabKey = useStore((s) => s.activeTabKey);
  const openCitation = useStore((s) => s.openCitation);
  const T = agentText(zh);
  const result = run.result;

  // Citation numbering in order of first appearance in the answer.
  const { answer, order } = useMemo(() => {
    const order: string[] = [];
    const text = result?.answer || "";
    const out = text.replace(/\[S:([^\]\s]+)\]/g, (_m, id: string) => {
      let n = order.indexOf(id);
      if (n < 0) n = order.push(id) - 1;
      return `[[${n + 1}]](#cite-${encodeURIComponent(id)})`;
    });
    return { answer: out, order };
  }, [result?.answer]);

  const spans = useMemo(() => {
    const m = new Map<string, AgentSpan>();
    result?.citations.forEach((c) => m.set(c.id, c));
    return m;
  }, [result]);

  if (!result) return null;
  const go = (span?: AgentSpan) => span && void openCitation(span);

  function onAnswerClick(e: React.MouseEvent) {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href") || "";
    if (!href.startsWith("#cite-")) return;
    e.preventDefault();
    go(spans.get(decodeURIComponent(href.slice(6))));
  }
  const verdictLabel = (v?: string) => (v ? (T as any)[v] || v : "");
  const here = (s: AgentSpan) => current?.id === s.paper_id && activeTabKey === `paper:${s.paper_id}`;

  return (
    <>
      <h4 className="agent-h">{T.answer}</h4>
      <div className="agent-answer" onClick={onAnswerClick}>
        <Markdown text={answer} />
      </div>
      {result.claims.length > 0 && (
        <>
          <h4 className="agent-h">{T.claims}</h4>
          {result.claims.map((c, i) => (
            <div key={i} className={`agent-claim ${c.verdict || ""}`}>
              {c.verdict && <span className={`agent-badge ${c.verdict}`}>{verdictLabel(c.verdict)}</span>}
              {c.confidence != null && <span className="muted"> {Math.round(c.confidence * 100)}%</span>}
              {c.span_ids.map((id) => {
                const n = order.indexOf(id);
                const sp = spans.get(id);
                return sp ? (
                  <button key={id} className="agent-chip as-button" title={`${sp.title} · ${T.page}${sp.page + 1}${T.pageSfx}`} onClick={() => go(sp)}>
                    {n >= 0 ? n + 1 : "·"}
                  </button>
                ) : null;
              })}
              <div>{c.text}</div>
              {c.note && <div className="muted">{c.note}</div>}
            </div>
          ))}
        </>
      )}
      {order.length > 0 && (
        <>
          <h4 className="agent-h">{T.evidence}</h4>
          {order.map((id, i) => {
            const s = spans.get(id);
            if (!s) return null;
            return (
              <div key={id} className="agent-cite clickable" onClick={() => go(s)} title={here(s) ? "" : s.title}>
                <span className="agent-chip">{i + 1}</span>
                <div>
                  <div className="muted">
                    {!here(s) && <>{s.title} · </>}
                    {T.page}{s.page + 1}{T.pageSfx}
                  </div>
                  <div className="agent-cite-text">{s.text}</div>
                </div>
              </div>
            );
          })}
        </>
      )}
      <div className="muted agent-foot">
        {T.stop}: {T.stops[result.stop_reason] || result.stop_reason} · {T.rounds}: {result.rounds}
      </div>
    </>
  );
}
