// Tool families: icon, human label and the one-line argument summary on a tool card.
import {
  BadgeCheck, BookOpen, Bot, Brain, FilePen, FileSearch, FileText, FolderTree, GitBranch, GitBranchPlus, GitCommitHorizontal, GitCompareArrows,
  GitMerge, Globe, History, Library, ListTodo, Map as MapIcon,
  MessageCircleQuestion, Play, Plug, Search, Server, Sparkles, Terminal, Telescope, Workflow, Wrench, type LucideIcon,
} from "lucide-react";
import type { SpanRef, ToolCallMessage } from "../../api/harness";
import { useStore } from "../../store";

export type ToolFamily = "paper" | "research" | "read" | "edit" | "exec" | "web" | "agent" | "external";

const EXACT: Record<string, { icon: LucideIcon; family: ToolFamily; en: string; zh: string }> = {
  browser_open: { icon: Globe, family: "web", en: "Open browser", zh: "打开浏览器" },
  browser_snapshot: { icon: FileSearch, family: "web", en: "Inspect page", zh: "检查页面" },
  browser_click: { icon: Globe, family: "web", en: "Click page", zh: "点击页面" },
  browser_type: { icon: FilePen, family: "web", en: "Type into page", zh: "输入页面" },
  browser_press: { icon: Globe, family: "web", en: "Press key", zh: "按键" },
  browser_scroll: { icon: Globe, family: "web", en: "Scroll page", zh: "滚动页面" },
  browser_read: { icon: BookOpen, family: "web", en: "Read page", zh: "阅读页面" },
  browser_screenshot: { icon: FileSearch, family: "web", en: "Capture page", zh: "截取页面" },
  browser_tabs: { icon: Globe, family: "web", en: "Browser tabs", zh: "浏览器标签" },
  browser_import_paper: { icon: Library, family: "paper", en: "Import browser paper", zh: "导入当前论文" },
  library_search: { icon: Search, family: "paper", en: "Search library", zh: "检索论文库" },
  library_list: { icon: Library, family: "paper", en: "List library", zh: "列出论文库" },
  library_import: { icon: Library, family: "paper", en: "Import paper", zh: "导入论文" },
  paper_read: { icon: BookOpen, family: "paper", en: "Read paper", zh: "阅读论文" },
  paper_search_spans: { icon: FileSearch, family: "paper", en: "Search passages", zh: "检索段落" },
  paper_summary: { icon: BookOpen, family: "paper", en: "Summarize paper", zh: "总结论文" },
  paper_explain: { icon: BookOpen, family: "paper", en: "Explain passage", zh: "讲解段落" },
  paper_references: { icon: BookOpen, family: "paper", en: "References", zh: "参考文献" },
  scholar_search: { icon: Search, family: "paper", en: "Search scholar", zh: "学术搜索" },
  run_workflow: { icon: Workflow, family: "research", en: "Run workflow", zh: "运行工作流" },
  verify_claims: { icon: BadgeCheck, family: "research", en: "Verify claims", zh: "核验论断" },
  agent_reader: { icon: Telescope, family: "research", en: "Reader agent", zh: "阅读子智能体" },
  fs_list: { icon: FolderTree, family: "read", en: "List files", zh: "列出文件" },
  fs_read: { icon: FileText, family: "read", en: "Read file", zh: "读取文件" },
  fs_glob: { icon: FileSearch, family: "read", en: "Find files", zh: "查找文件" },
  fs_grep: { icon: FileSearch, family: "read", en: "Search files", zh: "搜索文件" },
  fs_write: { icon: FilePen, family: "edit", en: "Write file", zh: "写入文件" },
  fs_edit: { icon: FilePen, family: "edit", en: "Edit file", zh: "编辑文件" },
  shell_run: { icon: Terminal, family: "exec", en: "Run command", zh: "运行命令" },
  python_run: { icon: Play, family: "exec", en: "Run Python", zh: "运行 Python" },
  web_search: { icon: Globe, family: "web", en: "Search web", zh: "搜索网页" },
  web_fetch: { icon: Globe, family: "web", en: "Fetch page", zh: "抓取网页" },
  todo_write: { icon: ListTodo, family: "agent", en: "Update tasks", zh: "更新任务" },
  ask_user: { icon: MessageCircleQuestion, family: "agent", en: "Ask you", zh: "向你提问" },
  task: { icon: Bot, family: "agent", en: "Subagent", zh: "子智能体" },
  plan_submit: { icon: MapIcon, family: "agent", en: "Propose plan", zh: "提交计划" },
  memory_recall: { icon: Brain, family: "agent", en: "Recall memory", zh: "回忆记忆" },
  memory_note: { icon: Brain, family: "agent", en: "Save memory", zh: "写入记忆" },
  result_read: { icon: FileText, family: "agent", en: "Re-read result", zh: "重读结果" },
  vcs_status: { icon: GitCompareArrows, family: "read", en: "Check changes", zh: "查看修改状态" },
  vcs_log: { icon: History, family: "read", en: "Read history", zh: "查看提交历史" },
  vcs_diff: { icon: GitCompareArrows, family: "read", en: "Compare versions", zh: "比较版本" },
  vcs_commit: { icon: GitCommitHorizontal, family: "edit", en: "Commit", zh: "提交" },
  vcs_branch: { icon: GitBranchPlus, family: "edit", en: "Create branch", zh: "新建分支" },
  vcs_checkout: { icon: GitBranch, family: "edit", en: "Switch branch", zh: "切换分支" },
  vcs_merge: { icon: GitMerge, family: "edit", en: "Merge branch", zh: "合并分支" },
};

export function toolMeta(tool: string, zh: boolean): { icon: LucideIcon; family: ToolFamily; label: string } {
  const hit = EXACT[tool];
  if (hit) return { icon: hit.icon, family: hit.family, label: zh ? hit.zh : hit.en };
  const pretty = tool.replace(/^(mcp|skill|delegate|remote)_/, "").replace(/_/g, " ");
  if (tool.startsWith("mcp_")) return { icon: Plug, family: "external", label: pretty };
  if (tool.startsWith("skill_")) return { icon: Sparkles, family: "external", label: pretty };
  if (tool.startsWith("delegate_")) return { icon: Bot, family: "external", label: pretty };
  if (tool.startsWith("remote_") || tool.includes("ssh")) return { icon: Server, family: "exec", label: pretty };
  if (tool.startsWith("paper_") || tool.startsWith("library_")) return { icon: BookOpen, family: "paper", label: pretty };
  if (tool.startsWith("browser_")) return { icon: Globe, family: "web", label: pretty };
  if (tool.startsWith("fs_")) return { icon: FileText, family: "read", label: pretty };
  return { icon: Wrench, family: "external", label: pretty };
}

const PRIMARY = ["path", "file_path", "command", "query", "pattern", "url", "question", "description", "workflow", "goal", "prompt", "task", "script", "text", "name"];

const PROSE = new Set(["question", "description", "goal", "prompt", "task", "text"]);

const clip = (s: string, n = 120) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** One-line summary of what the call is about. */
export function argSummary(call: Pick<ToolCallMessage, "tool" | "args">): string {
  const args = call.args || {};
  const papers = useStore.getState().papers;
  const zh = useStore.getState().uiLang === "zh";
  const title = (id: unknown) => papers.find((p) => p.id === id)?.title || String(id);
  const parts: string[] = [];
  if (typeof args.paper_id === "string") parts.push(clip(title(args.paper_id), 60));
  if (Array.isArray(args.paper_ids) && args.paper_ids.length) {
    parts.push(args.paper_ids.length === 1 ? clip(title(args.paper_ids[0]), 60) : zh ? `${args.paper_ids.length} 篇论文` : `${args.paper_ids.length} papers`);
  }
  if (call.tool === "todo_write" && Array.isArray(args.todos)) return zh ? `${args.todos.length} 项` : `${args.todos.length} item${args.todos.length === 1 ? "" : "s"}`;
  if (call.tool === "task" && args.subagent_type) parts.push(String(args.subagent_type));
  if (call.tool.startsWith("vcs_")) return vcsSummary(call.tool, args, zh);
  if (call.tool === "plan_submit" && typeof args.plan === "string") {
    return clip((args.plan.split("\n").find((l: string) => l.trim()) || "").replace(/^[#>\s]+/, "").replace(/[*_`]+/g, ""), 80);
  }
  const key = PRIMARY.find((k) => typeof args[k] === "string" && args[k].trim());
  if (key) {
    let value = String(args[key]);
    // prose arguments may carry markdown emphasis; paths, globs and commands must stay verbatim
    if (PROSE.has(key)) value = value.replace(/\*\*|__|`/g, "");
    parts.push(clip(value.replace(/\s+/g, " ").trim()));
  }
  else if (!parts.length) {
    const first = Object.entries(args).find(([, v]) => typeof v === "string" || typeof v === "number");
    if (first) parts.push(clip(`${first[0]}: ${String(first[1]).replace(/\s+/g, " ")}`));
  }
  if (typeof args.section === "string" && args.section) parts.push(`§ ${clip(args.section, 40)}`);
  if (args.pages != null && args.pages !== "") parts.push(`p. ${args.pages}`);
  return parts.join(" · ");
}

const short = (rev: unknown) => (/^[0-9a-f]{12,40}$/i.test(String(rev)) ? String(rev).slice(0, 7) : String(rev));

/** What a version-control call does, in the words a reader would use. */
function vcsSummary(tool: string, args: Record<string, any>, zh: boolean): string {
  const str = (k: string) => (typeof args[k] === "string" && args[k].trim() ? String(args[k]).trim() : "");
  const branch = str("branch") || str("name");
  switch (tool) {
    case "vcs_commit": {
      const n = Array.isArray(args.paths) ? args.paths.length : 0;
      return [clip(str("message").split("\n")[0], 90), n ? (zh ? `${n} 个文件` : `${n} file${n === 1 ? "" : "s"}`) : ""].filter(Boolean).join(" · ");
    }
    case "vcs_branch":
      return [branch, str("from") ? (zh ? `基于 ${short(str("from"))}` : `from ${short(str("from"))}`) : ""].filter(Boolean).join(" · ");
    case "vcs_checkout":
      return [branch, args.stash ? (zh ? "暂存修改" : "stash changes") : args.discard ? (zh ? "丢弃修改" : "discard changes") : ""].filter(Boolean).join(" · ");
    case "vcs_merge":
      return branch ? (zh ? `${branch} → 当前分支` : `${branch} → current branch`) : "";
    case "vcs_diff": {
      const from = str("from");
      const to = str("to");
      const range = from || to ? `${from ? short(from) : "HEAD"} → ${to ? short(to) : zh ? "工作区" : "working tree"}` : zh ? "未提交的修改" : "uncommitted changes";
      return [str("path"), range].filter(Boolean).join(" · ");
    }
    case "vcs_log":
      return [str("path"), branch, args.limit ? (zh ? `最近 ${args.limit} 次` : `last ${args.limit}`) : ""].filter(Boolean).join(" · ");
    default:
      return str("path");
  }
}

/** The commit a `vcs_commit` call produced: structured when the server sends it, else read off the result text. */
export function callCommit(call: ToolCallMessage): string | null {
  if (call.tool !== "vcs_commit" || call.status !== "done") return null;
  const c = (call as any).commit;
  if (c) return String(typeof c === "string" ? c : c.short || c.id || "").slice(0, 10) || null;
  const m = /\b([0-9a-f]{7,40})\b/.exec(call.result_preview || "");
  return m ? m[1].slice(0, 7) : null;
}

const SPAN_MARK = /\[S:([^\]\s]+#\d+)\]/g;

/** Citable spans of a tool call: structured ones first, then `[S:<paper>#<n>]` markers in the preview. */
export function callSpans(call: ToolCallMessage): SpanRef[] {
  const out = new Map<string, SpanRef>();
  for (const s of [...(call.spans || []), ...(call.citations || [])]) {
    if (s && s.id && s.paper_id) out.set(String(s.id), s);
  }
  const text = call.result_preview || "";
  for (const m of text.matchAll(SPAN_MARK)) {
    const id = m[1];
    if (out.has(id)) continue;
    // "… [S:id] (p. 4)" / "p.4 [S:id]" style hints near the marker give us a page to jump to
    const near = text.slice(Math.max(0, m.index! - 40), m.index! + m[0].length + 40);
    const page = /\bp(?:age|\.)?\s*(\d{1,4})\b/i.exec(near);
    // span pages are 0-based; the hint printed for readers is 1-based
    out.set(id, { id, paper_id: id.slice(0, id.lastIndexOf("#")), page: page ? Math.max(0, Number(page[1]) - 1) : null });
  }
  return [...out.values()];
}

/** Chip label. When every chip of a card is from one paper (`samePaper`) the title is noise: show the passage instead. */
export function spanLabel(span: SpanRef, samePaper = false): string {
  const where = span.page != null ? `p.${span.page + 1}` : `#${span.id.slice(span.id.lastIndexOf("#") + 1)}`;
  if (samePaper && span.text) {
    const words = span.text.replace(/\s+/g, " ").trim();
    return `${where} · ${words.length > 20 ? `${words.slice(0, 19)}…` : words}`;
  }
  const title = span.title || useStore.getState().papers.find((p) => p.id === span.paper_id)?.title || span.paper_id;
  const short = title.length > 20 ? `${title.slice(0, 19)}…` : title;
  return `${short} · ${where}`;
}

/** Open the paper tab for a span and flash it when we know where it is. */
export function openSpan(span: SpanRef) {
  const st = useStore.getState();
  if (!st.papers.some((p) => p.id === span.paper_id)) {
    st.notify(st.uiLang === "zh" ? "这篇论文不在论文库中" : "That paper is not in the library");
    return;
  }
  if (span.page != null) {
    void st.openCitation({ id: span.id, paper_id: span.paper_id, title: span.title || "", page: span.page, bbox: span.bbox ?? null, text: span.text || "" });
  } else {
    void st.openPaper(span.paper_id).catch(() => {});
  }
}

export function formatDuration(start?: number | null, end?: number | null): string {
  if (!start || !end || end < start) return "";
  const s = end - start;
  if (s < 1) return `${Math.max(1, Math.round(s * 1000))}ms`;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function relativeTime(ts: number | undefined, zh: boolean): string {
  if (!ts) return "";
  const d = Date.now() / 1000 - ts;
  if (d < 60) return zh ? "刚刚" : "now";
  if (d < 3600) return zh ? `${Math.floor(d / 60)} 分钟前` : `${Math.floor(d / 60)}m`;
  if (d < 86400) return zh ? `${Math.floor(d / 3600)} 小时前` : `${Math.floor(d / 3600)}h`;
  if (d < 86400 * 7) return zh ? `${Math.floor(d / 86400)} 天前` : `${Math.floor(d / 86400)}d`;
  return new Date(ts * 1000).toLocaleDateString(zh ? "zh-CN" : undefined, { month: "short", day: "numeric" });
}

export const formatTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(Math.round(n)));
