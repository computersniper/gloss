import { Activity, Bot, CheckCircle2, Clock3, MessageSquare, ShieldAlert, XCircle } from "lucide-react";
import type { SessionMessage, SessionState, ToolCallMessage } from "../../api/harness";
import { useActiveSession } from "../../sessionStore";
import { useStore } from "../../store";
import { argSummary, formatTokens, toolMeta } from "../session/toolMeta";
import "./SessionTrace.css";

const statusText = (status: string, zh: boolean) => ({
  idle: zh ? "就绪" : "Ready", running: zh ? "正在执行" : "Running",
  awaiting_approval: zh ? "等待你批准" : "Awaiting approval",
  awaiting_input: zh ? "等待你的回答" : "Awaiting your answer",
  pending: zh ? "待执行" : "Pending", done: zh ? "已完成" : "Completed",
  error: zh ? "失败" : "Failed", denied: zh ? "未获批准" : "Denied",
}[status] || status);

function timeLabel(value?: number | null) {
  if (!value || !Number.isFinite(value)) return "";
  const date = new Date(value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Tool results are summarized rather than dumping protocol objects into the UI. */
function resultSummary(call: ToolCallMessage, zh: boolean): string {
  if (call.error) return call.error;
  const facts: string[] = [];
  if (call.exit_code != null) facts.push(zh ? `退出码 ${call.exit_code}` : `Exit code ${call.exit_code}`);
  if (call.checkpoints?.length) facts.push(zh ? `已修改 ${call.checkpoints.length} 个文件` : `${call.checkpoints.length} files changed`);
  if (call.citations?.length || call.spans?.length) facts.push(zh ? `${(call.citations || call.spans)!.length} 处引用` : `${(call.citations || call.spans)!.length} references`);
  if (call.subagent?.description) facts.push(call.subagent.description);
  const preview = call.result_preview?.trim();
  if (preview) {
    if (/^[\[{]/.test(preview)) {
      try {
        const data = JSON.parse(preview);
        if (typeof data?.title === "string") facts.push(data.title);
        if (typeof data?.url === "string") facts.push(data.url);
        if (Array.isArray(data)) facts.push(zh ? `${data.length} 条结果` : `${data.length} results`);
        else if (Array.isArray(data?.items)) facts.push(zh ? `${data.items.length} 条结果` : `${data.items.length} results`);
      } catch { /* Truncated JSON is intentionally omitted. */ }
    } else if (!/^<tool_/i.test(preview)) facts.push(preview.slice(0, 320));
  }
  if (!facts.length && call.result_chars != null) facts.push(zh ? `返回 ${call.result_chars} 个字符` : `${call.result_chars} characters returned`);
  return facts.join(" · ");
}

function ReadableText({ text, zh }: { text: string; zh: boolean }) {
  if (!text) return null;
  if (text.length <= 360) return <p className="session-trace-text">{text}</p>;
  return <details className="session-trace-detail"><summary>{text.slice(0, 180).replace(/\s+/g, " ")}… <span>{zh ? "展开" : "Expand"}</span></summary><p className="session-trace-text">{text}</p></details>;
}

function TraceEntry({ message, state, zh }: { message: SessionMessage; state: SessionState; zh: boolean }) {
  let label: string;
  let text = "";
  let badge = "";
  let outcome = "";
  let tone = "";
  let Icon = MessageSquare;
  let timestamp: number | null | undefined;
  if (message.type === "tool_call") {
    const meta = toolMeta(message.tool, zh);
    Icon = meta.icon;
    label = meta.label;
    text = argSummary(message);
    badge = statusText(message.status, zh);
    tone = ["error", "denied"].includes(message.status) ? "error" : message.status === "awaiting_approval" ? "attention" : message.status === "done" ? "done" : "active";
    const approval = state.approvals.find((a) => a.tool_call_id === message.id);
    outcome = message.status === "awaiting_approval"
      ? [approval?.reason || message.approval_reason, zh ? "请在对话中批准或拒绝此操作。" : "Approve or deny this action in the chat."].filter(Boolean).join(" · ")
      : resultSummary(message, zh);
    timestamp = message.started_at;
  } else if (message.type === "user") {
    label = zh ? "你的请求" : "Your request";
    text = message.text;
    badge = message.pending ? (zh ? "发送中" : "Sending") : message.local ? (zh ? "本地命令" : "Local command") : "";
    timestamp = message.created_at;
  } else if (message.type === "assistant") {
    Icon = Bot;
    label = zh ? "助手回复" : "Assistant response";
    text = message.text;
    badge = message.error ? statusText("error", zh) : message.interrupted ? (zh ? "已中断" : "Interrupted") : message.streaming ? (zh ? "正在回答" : "Responding") : "";
    outcome = message.error || "";
    tone = message.error ? "error" : message.streaming ? "active" : "";
    timestamp = message.created_at;
  } else {
    Icon = message.kind === "turn_usage" ? CheckCircle2 : Activity;
    label = message.kind === "turn_usage" ? (zh ? "本轮用量" : "Turn usage")
      : message.kind === "cache_reset" ? (zh ? "缓存上下文更新" : "Cache context updated")
      : message.kind === "compaction" ? (zh ? "上下文压缩" : "Context compaction") : (zh ? "会话记录" : "Session event");
    text = message.text;
    timestamp = message.created_at;
    if (message.usage) {
      const u = message.usage;
      const input = u.input_tokens ?? u.input_tokens_est;
      const output = u.output_tokens ?? u.output_tokens_est;
      text = [zh ? `${u.llm_calls} 次模型调用` : `${u.llm_calls} model calls`,
        input == null ? "" : `${zh ? "输入" : "Input"} ${formatTokens(input)}`,
        output == null ? "" : `${zh ? "输出" : "Output"} ${formatTokens(output)}`,
        u.cache_hit_rate == null ? "" : `${zh ? "缓存命中" : "Cache hit"} ${(u.cache_hit_rate * 100).toFixed(1)}%`,
        u.duration_seconds == null ? "" : `${u.duration_seconds.toFixed(1)}s`,
      ].filter(Boolean).join(" · ");
      badge = u.exact === false ? (zh ? "估算" : "Estimated") : u.exact === true ? (zh ? "实测" : "Measured") : "";
    }
  }
  return <li className={`session-trace-entry ${tone}`}>
    <span className="session-trace-marker"><Icon size={15} aria-hidden="true" /></span>
    <div className="session-trace-content"><div className="session-trace-entry-heading"><strong>{label}</strong>{badge && <span className="session-trace-badge">{badge}</span>}<time>{timeLabel(timestamp)}</time></div>
      <ReadableText text={text} zh={zh} />{outcome && <p className="session-trace-outcome">{outcome}</p>}
      {message.compacted && <span className="session-trace-archived">{zh ? "已纳入压缩摘要" : "Included in compacted summary"}</span>}
    </div>
  </li>;
}

export function SessionTraceView({ state, zh }: { state: SessionState; zh: boolean }) {
  if (!state.session) return <div className="session-trace-empty"><Activity size={24} /><strong>{zh ? "对话运行轨迹" : "Chat activity"}</strong><p>{zh ? "选择一个对话，查看它如何读取文件、浏览网页、调用工具和完成回答。需要批准的操作和每轮用量也会显示在这里。" : "Select a chat to see file reads, browser actions, tools and responses, including approval requests and turn usage."}</p></div>;
  return <section className="session-trace" aria-label={zh ? "当前对话运行轨迹" : "Current chat activity"}>
    <header className="session-trace-header"><span><Activity size={15} /><strong>{state.session.title || (zh ? "当前对话" : "Current chat")}</strong></span><span className={`session-trace-state ${state.status}`}>{statusText(state.status, zh)}</span></header>
    {!state.loaded && !state.messages.length ? <p className="session-trace-hint">{zh ? "正在读取会话记录…" : "Loading chat history…"}</p>
      : !state.messages.length ? <p className="session-trace-hint">{zh ? "发送一条消息后，实际执行的步骤会按顺序出现在这里。" : "Send a message to see the actual steps in execution order."}</p>
      : <ol className="session-trace-list">{state.messages.map((message) => <TraceEntry key={message.id} message={message} state={state} zh={zh} />)}</ol>}
    {state.approvals.filter((a) => !state.messages.some((m) => m.id === a.tool_call_id)).map((a) => <div className="session-trace-notice attention" key={a.tool_call_id}><ShieldAlert size={16} /><span>{zh ? "等待批准" : "Awaiting approval"}: {toolMeta(a.tool, zh).label}{a.reason ? ` · ${a.reason}` : ""}</span></div>)}
    {state.input && <div className="session-trace-notice attention"><Clock3 size={16} /><span><strong>{zh ? "等待你的回答" : "Awaiting your answer"}</strong><p>{state.input.question}</p><small>{zh ? "请在对话中回答，以继续执行。" : "Answer in the chat to continue."}</small></span></div>}
    {state.error && <div className="session-trace-notice error"><XCircle size={16} /><span>{state.error}</span></div>}
    {state.stop?.reason === "interrupted" && <p className="session-trace-hint">{zh ? "本轮已中断，以上记录保留已完成的步骤。" : "This turn was interrupted. Completed steps remain above."}</p>}
    {state.stop?.reason === "budget" && <p className="session-trace-hint">{zh ? "本轮达到执行预算，发送新消息可继续。" : "This turn reached its execution budget. Send a new message to continue."}</p>}
  </section>;
}

export function SessionTrace() {
  const state = useActiveSession();
  const zh = useStore((s) => s.uiLang === "zh");
  return <SessionTraceView state={state} zh={zh} />;
}
