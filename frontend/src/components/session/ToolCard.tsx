import { useState } from "react";
import { Ban, Check, ChevronRight, CircleAlert, ExternalLink, GitCommitHorizontal, Quote, ShieldQuestion, Undo2 } from "lucide-react";
import { sessionsApi, type ApprovalRequest, type CheckpointRef, type ToolCallMessage } from "../../api/harness";
import { diffLines, parseUnifiedDiff } from "../../harness/diff.mjs";
import { splitDiff } from "../../harness/vcs.mjs";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { openBrowser } from "../../browserStore";
import { Markdown } from "../Markdown";
import { Spinner } from "../ui";
import { DiffView, revertCheckpoint, rowsForCheckpoint, statsOf, useCheckpoint, useCheckpointMarks, useDiffRows, type DiffRow } from "./DiffView";
import { argSummary, callCommit, callSpans, formatDuration, formatTokens, openSpan, spanLabel, toolMeta } from "./toolMeta";

const EDIT_TOOLS = new Set(["fs_edit", "fs_write"]);

function BrowserResult({ call, zh }: { call: ToolCallMessage; zh: boolean }) {
  const [expanded, setExpanded] = useState(false);
  let result: any = call.browser || {};
  try { result = { ...JSON.parse(call.result_preview || "{}"), ...result }; } catch { /* truncated preview is still shown below */ }
  const sid = call.session_id || useSessions.getState().activeId;
  const url = call.url || result.url || call.args?.url;
  const screenshot = call.screenshot || result.image_url;
  const safeImage = typeof screenshot === "string" && /^\/api\/sessions\/[^/]+\/browser\/files\//.test(screenshot);
  const body = result.text || result.snapshot;
  return <div className="gx-browser-result">
    {safeImage && <img src={screenshot} alt={result.title || (zh ? "页面截图" : "Page screenshot")} loading="lazy" />}
    {result.title && <strong>{result.title}</strong>}
    {url && <code title={url}>{url}</code>}
    {typeof body === "string" && <pre className="gx-result">{expanded ? body : body.slice(0, 2000)}</pre>}
    {typeof body === "string" && body.length > 2000 && <button type="button" className="gx-link" onClick={() => setExpanded(!expanded)}>{expanded ? (zh ? "收起正文" : "Show less") : (zh ? "显示完整正文" : "Show full text")}</button>}
    {result.path && !safeImage && <code>{result.path}</code>}
    {sid && <button type="button" className="gx-link" onClick={() => void openBrowser(undefined, sid)}><ExternalLink size={12} />{zh ? "在浏览器查看" : "View in browser"}</button>}
  </div>;
}

function argsDiff(call: ToolCallMessage): DiffRow[] | null {
  const a = call.args || {};
  // while the call waits for approval the server sends the real diff against the file on disk
  if (call.preview?.diff) return rowsForCheckpoint({ id: "", path: call.preview.path || "", diff: call.preview.diff });
  if (call.tool === "fs_edit" && (typeof a.old_string === "string" || typeof a.new_string === "string")) {
    return diffLines(a.old_string ?? a.old ?? "", a.new_string ?? a.new ?? "", 3) as DiffRow[];
  }
  if (call.tool === "fs_write" && typeof a.content === "string") return diffLines("", a.content, 0) as DiffRow[];
  return null;
}

function ArgsTable({ args, omit }: { args: Record<string, any>; omit?: string[] }) {
  const entries = Object.entries(args || {}).filter(([k]) => !omit?.includes(k));
  if (!entries.length) return null;
  return (
    <dl className="gx-kv">
      {entries.map(([k, v]) => {
        const text = typeof v === "string" ? v : JSON.stringify(v, null, 2);
        return (
          <div key={k}>
            <dt>{k}</dt>
            <dd className={text.includes("\n") || text.length > 80 ? "block" : ""}>{text}</dd>
          </div>
        );
      })}
    </dl>
  );
}

/** A `vcs_diff` result shown as a real diff (per file) instead of raw text. */
function VcsDiffResult({ text, zh }: { text: string; zh: boolean }) {
  const sections = (splitDiff(text) as { path: string; binary: boolean; text: string }[]).slice(0, 12);
  return (
    <div className="gx-vcs-diff">
      {sections.map((s, i) => (
        <div key={s.path + i}>
          {(sections.length > 1 || s.path) && <code className="gx-vcs-diff-path">{s.path}</code>}
          {s.binary ? <div className="gx-diff-empty">{zh ? "二进制文件" : "Binary file"}</div> : <DiffView rows={parseUnifiedDiff(s.text) as DiffRow[]} zh={zh} />}
        </div>
      ))}
    </div>
  );
}

function CheckpointActions({ project, cp, zh }: { project: string; cp: CheckpointRef; zh: boolean }) {
  const mark = useCheckpointMarks((s) => s.marks[cp.id]);
  const setMark = useCheckpointMarks((s) => s.mark);
  const openCheckpointTab = useStore((s) => s.openCheckpointTab);
  const notify = useStore((s) => s.notify);
  const [busy, setBusy] = useState(false);

  async function revert() {
    setBusy(true);
    try {
      const r = await revertCheckpoint(project, cp.id);
      if (r?.conflict) notify(zh ? "已还原（文件在此之后被改动过）" : "Reverted (the file had changed since this edit)");
    } catch (e: any) {
      notify(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gx-cp-actions">
      <button type="button" className="gx-link" onClick={() => openCheckpointTab(project, cp.id)}>
        <ExternalLink size={12} /> {zh ? "在编辑区查看" : "Open diff"}
      </button>
      <span className="spacer" />
      {mark === "reverted" ? (
        <span className="gx-tag muted"><Undo2 size={12} /> {zh ? "已还原" : "Reverted"}</span>
      ) : mark === "accepted" ? (
        <>
          <span className="gx-tag ok"><Check size={12} /> {zh ? "已接受" : "Accepted"}</span>
          <button type="button" className="gx-btn ghost sm" disabled={busy} onClick={() => void revert()}>{zh ? "还原" : "Revert"}</button>
        </>
      ) : (
        <>
          <button type="button" className="gx-btn ghost sm" disabled={busy} onClick={() => void revert()}>
            {busy ? <Spinner size={12} /> : <Undo2 size={12} />} {zh ? "还原" : "Revert"}
          </button>
          <button type="button" className="gx-btn sm" onClick={() => setMark(cp.id, "accepted")}>
            <Check size={12} /> {zh ? "接受" : "Accept"}
          </button>
        </>
      )}
    </div>
  );
}

function EditBody({ call, project, zh }: { call: ToolCallMessage; project: string; zh: boolean }) {
  const cps = call.checkpoints || [];
  const first = cps[0];
  const detail = useCheckpoint(project, first?.id);
  const rows = useDiffRows(detail, () => argsDiff(call), [call.args, call.tool]);
  return (
    <>
      {rows ? <DiffView rows={rows} zh={zh} /> : <ArgsTable args={call.args} />}
      {first && <CheckpointActions project={project} cp={first} zh={zh} />}
      {cps.slice(1).map((cp) => (
        <div key={cp.id} className="gx-cp-extra">
          <code>{cp.path}</code>
          <CheckpointActions project={project} cp={cp} zh={zh} />
        </div>
      ))}
    </>
  );
}

export function ApprovalBar({ approval, zh }: { approval: ApprovalRequest; zh: boolean }) {
  const approve = useSessions((s) => s.approve);
  const [denying, setDenying] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const isPlan = approval.tool === "plan_submit";
  const act = async (decision: "once" | "always" | "deny") => {
    setBusy(decision);
    await approve(approval.tool_call_id, decision, decision === "deny" ? feedback.trim() || undefined : undefined);
    setBusy(null);
  };
  const T = zh
    ? { need: isPlan ? "计划待确认" : "需要你的批准", once: isPlan ? "接受计划" : "允许一次", always: "始终允许", deny: isPlan ? "继续修改" : "拒绝", ph: "告诉智能体该怎么改（可选）", send: "拒绝并反馈", back: "返回" }
    : { need: isPlan ? "Plan ready for review" : "Needs your approval", once: isPlan ? "Accept plan" : "Approve once", always: "Always allow", deny: isPlan ? "Keep planning" : "Deny", ph: "Tell the agent what to do instead (optional)", send: "Deny with feedback", back: "Back" };
  return (
    <div className="gx-approval" role="group" aria-label={T.need}>
      <div className="gx-approval-head">
        <ShieldQuestion size={14} />
        <strong>{T.need}</strong>
        {approval.reason && <span>{approval.reason}</span>}
      </div>
      {denying ? (
        <div className="gx-approval-deny">
          <input
            autoFocus
            className="gx-input"
            value={feedback}
            placeholder={T.ph}
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) void act("deny");
              if (e.key === "Escape") setDenying(false);
            }}
          />
          <button type="button" className="gx-btn ghost sm" onClick={() => setDenying(false)}>{T.back}</button>
          <button type="button" className="gx-btn danger sm" disabled={!!busy} onClick={() => void act("deny")}>{T.send}</button>
        </div>
      ) : (
        <div className="gx-approval-actions">
          <button type="button" className="gx-btn primary sm" disabled={!!busy} onClick={() => void act("once")}>
            {busy === "once" ? <Spinner size={12} /> : <Check size={13} />} {T.once}
          </button>
          {!isPlan && (
            <button
              type="button"
              className="gx-btn sm"
              disabled={!!busy}
              title={approval.suggested_rule ? `${zh ? "添加允许规则" : "Adds allow rule"}: ${approval.suggested_rule}` : undefined}
              onClick={() => void act("always")}
            >
              {T.always}
              {approval.suggested_rule && <code>{approval.suggested_rule}</code>}
            </button>
          )}
          <span className="spacer" />
          <button type="button" className="gx-btn ghost sm" disabled={!!busy} onClick={() => setDenying(true)}>
            <Ban size={12} /> {T.deny}
          </button>
        </div>
      )}
    </div>
  );
}

export type ToolCardProps = {
  call: ToolCallMessage;
  project: string;
  sub?: boolean;
  /** Pending approval for a call id (also used for nested subagent calls). */
  approvalFor: (id: string) => ApprovalRequest | undefined;
  /** Calls made by a subagent, keyed by the `task` call that spawned it. */
  kidsOf?: (id: string) => ToolCallMessage[];
};

const hasWaiting = (calls: ToolCallMessage[], kidsOf?: (id: string) => ToolCallMessage[]): boolean =>
  calls.some((c) => c.status === "awaiting_approval" || hasWaiting(kidsOf?.(c.id) || [], kidsOf));

export function ToolCard({ call, project, sub, approvalFor, kidsOf }: ToolCardProps) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const meta = toolMeta(call.tool, zh);
  const approval = approvalFor(call.id);
  const isEdit = EDIT_TOOLS.has(call.tool);
  const isPlan = call.tool === "plan_submit" && typeof call.args?.plan === "string";
  const kids = kidsOf?.(call.id) || [];
  const isTask = call.tool === "task" || kids.length > 0 || !!call.subagent;
  const waiting = call.status === "awaiting_approval";
  const [toggled, setToggled] = useState<boolean | null>(null);
  const kidWaiting = hasWaiting(kids, kidsOf);
  const liveNow = call.status === "running" || call.status === "pending";
  // a nested approval must never hide behind a collapsed card
  const open = kidWaiting || (toggled ?? (waiting || (isEdit && call.status !== "denied") || (isPlan && call.status !== "denied") || (isTask && liveNow)));
  const spans = call.status === "done" ? callSpans(call) : [];
  const onePaper = spans.length > 1 && spans.every((s) => s.paper_id === spans[0].paper_id);
  const paperTitle = onePaper ? spans[0].title || useStore.getState().papers.find((p) => p.id === spans[0].paper_id)?.title || "" : "";
  const Glyph = meta.icon;
  const summary = argSummary(call);
  const duration = formatDuration(call.started_at, call.finished_at);
  const cp = call.checkpoints?.[0];
  const stat = cp && (cp.lines_added != null || cp.lines_removed != null)
    ? { added: cp.lines_added || 0, removed: cp.lines_removed || 0 }
    : isEdit && argsDiff(call) ? statsOf(argsDiff(call)!) : null;
  const live = call.status === "running" || call.status === "pending";
  const failure = call.status === "error" ? call.error || call.result_preview : null;
  const commitId = callCommit(call);
  const isVcsDiff = call.tool === "vcs_diff" && call.status === "done" && /^@@ /m.test(call.result_preview || "");
  const [full, setFull] = useState<string | null>(null);
  const loadFull = () => {
    const sid = call.session_id || useSessions.getState().activeId;
    if (!sid || !call.result_ref) return;
    void sessionsApi.result(sid, call.result_ref).then(setFull).catch((e) => useStore.getState().notify(String(e?.message || e)));
  };

  return (
    <div className={`gx-tool fam-${meta.family} st-${call.status}${open ? " open" : ""}${sub ? " sub" : ""}`}>
      <button type="button" className="gx-tool-row" aria-expanded={open} onClick={() => setToggled(!open)}>
        <span className="gx-tool-icon"><Glyph size={14} strokeWidth={1.75} /></span>
        <span className="gx-tool-name">{meta.label}</span>
        {summary && <span className="gx-tool-summary" title={summary}>{summary}</span>}
        <span className="gx-tool-end">
          {stat && (stat.added > 0 || stat.removed > 0) && (
            <span className="gx-diffstat"><b>+{stat.added}</b><i>−{stat.removed}</i></span>
          )}
          {spans.length > 0 && <span className="gx-tool-count"><Quote size={11} />{spans.length}</span>}
          {commitId && (
            <span
              className="gx-tool-count gx-tool-commit"
              role="link"
              title={zh ? "查看这次提交" : "Open this commit"}
              onClick={(e) => {
                e.stopPropagation();
                useStore.getState().openCommitTab(project, commitId);
              }}
            >
              <GitCommitHorizontal size={11} />{commitId}
            </span>
          )}
          {isTask && (kids.length > 0 || !!call.subagent?.tool_calls) && (
            <span className="gx-tool-count" title={zh ? "子智能体的工具调用" : "Tool calls made by the subagent"}>
              {zh ? `${Math.max(kids.length, call.subagent?.tool_calls || 0)} 次调用` : `${Math.max(kids.length, call.subagent?.tool_calls || 0)} tool${Math.max(kids.length, call.subagent?.tool_calls || 0) === 1 ? "" : "s"}`}
            </span>
          )}
          {call.exit_code != null && call.exit_code !== 0 && <span className="gx-tool-count bad">exit {call.exit_code}</span>}
          {duration && !live && <span className="gx-tool-time">{duration}</span>}
          {live && <Spinner size={13} />}
          {waiting && <ShieldQuestion size={14} className="gx-warn" />}
          {call.status === "error" && <CircleAlert size={14} className="gx-danger" />}
          {call.status === "denied" && <Ban size={13} className="gx-dim" />}
          {call.status === "done" && <Check size={14} className="gx-ok" />}
          <ChevronRight size={13} className="gx-tool-caret" />
        </span>
      </button>

      {!open && failure && <div className="gx-tool-error">{String(failure).split("\n")[0].slice(0, 200)}</div>}

      {spans.length > 0 && (
        <div className="gx-cites">
          {paperTitle && <span className="gx-cites-paper" title={paperTitle}>{paperTitle}</span>}
          {spans.slice(0, open ? 24 : 4).map((s) => (
            <button key={s.id} type="button" className="gx-cite" title={s.text ? s.text.slice(0, 400) : s.id} onClick={() => openSpan(s)}>
              <Quote size={10} />
              <span>{spanLabel(s, onePaper)}</span>
            </button>
          ))}
          {!open && spans.length > 4 && (
            <button type="button" className="gx-cite more" onClick={() => setToggled(true)}>+{spans.length - 4}</button>
          )}
        </div>
      )}

      {open && (
        <div className="gx-tool-body">
          {call.tool.startsWith("browser_") && <BrowserResult call={call} zh={zh} />}
          {isEdit ? (
            <EditBody call={call} project={project} zh={zh} />
          ) : isPlan ? (
            <>
              <div className="gx-plan gx-prose"><Markdown text={String(call.args.plan)} /></div>
              {call.status === "denied" && (call.error || call.result_preview) && <pre className="gx-result">{call.error || call.result_preview}</pre>}
            </>
          ) : (
            <>
              {isTask && typeof call.args?.prompt === "string" ? (
                <div className="gx-task-prompt" title={zh ? "交给子智能体的任务" : "The brief handed to the subagent"}>{call.args.prompt}</div>
              ) : (
                <ArgsTable args={call.args} />
              )}
              {kids.length > 0 && (
                <div className="gx-tools gx-subtools">
                  {kids.map((k) => (
                    <ToolCard key={k.id} call={k} project={project} sub approvalFor={approvalFor} kidsOf={kidsOf} />
                  ))}
                </div>
              )}
              {(call.result_preview || call.error) && (!call.tool.startsWith("browser_") || call.status === "error") && (
                isVcsDiff && full === null
                  ? <VcsDiffResult text={call.result_preview || ""} zh={zh} />
                  : isTask && call.status === "done" && full === null
                  ? <div className="gx-task-result gx-prose"><Markdown text={call.result_preview || ""} /></div>
                  : <pre className={`gx-result${call.status === "error" ? " bad" : ""}`}>{call.error && call.status === "error" ? call.error : full ?? call.result_preview}</pre>
              )}
              {call.result_ref && !call.tool.startsWith("browser_") && call.status === "done" && full === null && (call.result_chars == null || call.result_chars > (call.result_preview || "").length) && (
                <button type="button" className="gx-link" onClick={loadFull}>
                  {zh ? "显示完整结果" : "Show the full result"}{call.result_chars ? ` · ${formatTokens(call.result_chars)} ${zh ? "字符" : "chars"}` : ""}
                </button>
              )}
              {(call.checkpoints || []).map((c) => (
                <div key={c.id} className="gx-cp-extra">
                  <code>{c.path}</code>
                  <CheckpointActions project={project} cp={c} zh={zh} />
                </div>
              ))}
            </>
          )}
          {isEdit && call.status === "error" && (call.error || call.result_preview) && (
            <pre className="gx-result bad">{call.error || call.result_preview}</pre>
          )}
        </div>
      )}

      {waiting && approval && <ApprovalBar approval={approval} zh={zh} />}
    </div>
  );
}
