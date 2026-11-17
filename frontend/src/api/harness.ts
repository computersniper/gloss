// GlossX harness API: sessions (+ SSE), harness registry/settings, workspace files.
// Contract: docs/glossx-harness.md §7. Response envelopes are read tolerantly
// (bare arrays or `{items: [...]}`) because the backend evolves in parallel.
import { decodeFrame, EVENT_NAMES } from "../harness/sessionReducer.mjs";

export type SessionMode = "ask" | "agent" | "auto" | "plan";
export type SessionStatus = "idle" | "running" | "awaiting_approval" | "awaiting_input";
export type ToolStatus = "pending" | "awaiting_approval" | "running" | "done" | "error" | "denied";
export type AttachmentKind = "paper" | "span" | "file" | "run" | "selection";
export type Attachment = { kind: AttachmentKind; ref: string; label?: string; text?: string; ok?: boolean; error?: string; /** already earlier in the live conversation, sent as a one-line reference */ repeat?: boolean };

export type SessionContext = { tokens_est: number; limit: number; compactions: number; percent?: number; system_tokens_est?: number; compacted_through?: string | null };
export type SessionUsage = { input_tokens?: number; input_tokens_est?: number; cached_input_tokens?: number; cache_write_tokens?: number; output_tokens?: number; output_tokens_est?: number; reasoning_tokens?: number; llm_calls: number; cache_hit_rate?: number; exact?: boolean; duration_seconds?: number; last_call?: { input?: number; cached?: number; output?: number; hit_rate?: number; exact?: boolean } };
export type SessionSummary = {
  archived?: boolean;
  id: string;
  title: string;
  project_id?: string | null;
  parent_id?: string | null;
  created_at?: number;
  updated_at?: number;
  mode: SessionMode;
  model?: { provider?: string | null; model?: string | null };
  status: SessionStatus;
  message_count?: number;
  last_message?: string;
  usage?: SessionUsage;
  context?: SessionContext;
  workspace?: { key: string; path: string; vcs?: { available?: boolean; branch?: string | null } | null } | null;
  /** Threads + inbox (docs/glossx-workspaces.md §1). All optional: older backends omit them. */
  last_activity_at?: number;
  last_viewed_at?: number | null;
  unread?: boolean;
  attention?: Attention;
  preview?: string;
  project?: { id: string; name: string } | null;
  /** The workspace branch this thread ran on (§3). */
  branch?: string | null;
};
export type Attention = "approval" | "input" | "error" | "done" | null;
export const isBlocking = (a: Attention | undefined) => a === "approval" || a === "input";
export type InboxItem = { session_id: string; title: string; project?: { id: string; name: string } | null; attention?: Attention; last_activity_at?: number; preview?: string };
export type InboxSummary = { unread: number; blocking: number; items: InboxItem[] };

export type CheckpointRef = { id: string; path: string; lines_added?: number; lines_removed?: number };
export type SpanRef = { id: string; paper_id: string; title?: string; page?: number | null; bbox?: [number, number, number, number] | null; text?: string };

export type UserMessage = { id: string; type: "user"; text: string; attachments?: Attachment[]; created_at?: number; command?: string | null; compacted?: boolean;
  /** Server flag: a slash command that ran locally, without a model turn. */
  local?: boolean;
  /** Client flag: echoed optimistically, not acknowledged by the server yet. */
  pending?: boolean;
};
export type AssistantMessage = { id: string; type: "assistant"; text: string; thinking?: string; steps?: string[]; thought_ms?: number; streaming?: boolean; interrupted?: boolean; error?: string | null; compacted?: boolean; created_at?: number };
export type ToolCallMessage = {
  id: string;
  type: "tool_call";
  tool: string;
  args: Record<string, any>;
  status: ToolStatus;
  result_preview?: string | null;
  result_ref?: string | null;
  error?: string | null;
  started_at?: number | null;
  finished_at?: number | null;
  checkpoints?: CheckpointRef[];
  spans?: SpanRef[];
  citations?: SpanRef[];
  session_id?: string | null;
  assistant_id?: string | null;
  parent_tool_call_id?: string | null;
  category?: string | null;
  source?: string | null;
  result_chars?: number | null;
  approval_reason?: string | null;
  /** What an fs edit would do, sent while the call waits for approval. */
  preview?: { path?: string; existed_before?: boolean; diff?: string } | null;
  subagent?: { session_id?: string; type?: string; description?: string; status?: string; tool_calls?: number; rounds?: number } | null;
  exit_code?: number | null;
  run_id?: string | null;
  path?: string | null;
  compacted?: boolean;
  [extra: string]: any;
};
export type SystemNote = { id: string; type: "system_note"; kind?: string; text: string; created_at?: number; compacted?: boolean; usage?: SessionUsage; turn_id?: string };
export type SessionMessage = UserMessage | AssistantMessage | ToolCallMessage | SystemNote;

export type Todo = { id: string; text: string; active?: string; status: "pending" | "in_progress" | "completed" | (string & {}) };
export type ApprovalRequest = {
  tool_call_id: string;
  tool: string;
  args: Record<string, any>;
  reason?: string;
  suggested_rule?: string | null;
  category?: string | null;
  session_id?: string | null;
};
export type InputRequest = { tool_call_id?: string; question: string; choices: string[] };

export type SessionState = {
  session: SessionSummary | null;
  messages: SessionMessage[];
  todos: Todo[];
  context: SessionContext;
  status: SessionStatus;
  approvals: ApprovalRequest[];
  input: InputRequest | null;
  error: string | null;
  loaded: boolean;
  /** How the last turn ended (turn.completed). */
  stop?: { reason: "completed" | "budget" | "interrupted" | "error" | "compacted" | "noop" | (string & {}); rounds: number | null };
};
export type SendResult = { started: boolean; command?: string | null; message?: UserMessage; note?: SystemNote; session?: SessionSummary; cleared?: boolean; mode?: SessionMode; [extra: string]: any };
export type CompactResult = { compacted: boolean; reason?: string; tokens_before?: number; tokens_after?: number; session?: SessionSummary };

export type HarnessTool = {
  name: string;
  description?: string;
  category?: string;
  source?: string;
  trust?: string;
  permission?: "allow" | "ask" | "deny" | "unavailable" | (string & {});
  permission_reason?: string;
  modes?: string[];
};
export type HarnessCommand = { name: string; description?: string; argument_hint?: string; source?: string; kind?: string };
export type HarnessAgentType = { name: string; description?: string; tools?: string[] | null; model?: string | null; source?: string };
export type HookRule = { matcher: string; command: string; timeout?: number };
export type HookEvent = "pre_tool_use" | "post_tool_use" | "session_start" | "stop";
export type HarnessSettings = {
  permissions: { allow: string[]; ask: string[]; deny: string[] };
  hooks: Record<HookEvent, HookRule[]>;
  context_limit: number;
  default_mode: SessionMode;
  vcs?: { auto_commit?: boolean; [extra: string]: any };
  browser?: { path?: string | null; headed?: boolean; allow_private?: boolean; idle_timeout?: number; navigation_budget?: number };
  /** Read-only: where the harness keeps its files (`~/.glossx`, `<workspace>/.glossx`). */
  paths?: { home?: string; global_instructions?: string; workspace_config?: string; [extra: string]: any };
  [extra: string]: any;
};
export type Mention = { kind: AttachmentKind; ref: string; label: string; detail?: string; insert?: string };

export type TreeEntry = { path: string; type: "dir" | "file"; size?: number; mtime?: number };
export type WorkspaceFile = {
  path: string; content: string; mtime?: number; truncated?: boolean;
  /** office documents, PDFs and images: a read-only preview */
  readonly?: boolean; viewer?: "document" | "pdf" | "image"; document?: { format?: string; title?: string; blocks?: any[]; notice?: string };
};
export type Checkpoint = {
  id: string;
  path: string;
  source?: string;
  session_id?: string | null;
  tool_call_id?: string | null;
  created_at?: number;
  status?: "applied" | "reverted" | (string & {});
  lines_added?: number;
  lines_removed?: number;
  existed_before?: boolean;
  exists_after?: boolean;
  before?: string | null;
  after?: string | null;
  diff?: string;
};

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** True when the endpoint itself does not exist yet (older backend). */
export const isMissing = (e: unknown) =>
  e instanceof ApiError &&
  (e.status === 405 || e.status === 501 || (e.status === 404 && /^(not found|endpoint not available)$/i.test(e.message.trim())));

async function request<T>(url: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const opts: RequestInit = { ...init };
  if (init && "json" in init) {
    opts.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    opts.body = JSON.stringify(init.json ?? {});
  }
  let r: Response;
  try {
    r = await fetch(url, opts);
  } catch (e: any) {
    throw new ApiError(0, String(e?.message || e));
  }
  const text = await r.text();
  if (!r.ok) {
    let detail = text;
    let code: string | undefined;
    try {
      const body = JSON.parse(text);
      code = body?.detail?.code || body?.code;
      detail = typeof body?.detail === "string" ? body.detail : body?.detail ? JSON.stringify(body.detail) : text;
    } catch {
      /* plain text error */
    }
    throw new ApiError(r.status, (detail || r.statusText).slice(0, 400), code);
  }
  if (!text) return undefined as T;
  // an SPA fallback answers unknown routes with index.html: treat as "endpoint missing"
  if (text.trimStart().startsWith("<")) throw new ApiError(404, "endpoint not available");
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

function listOf<T>(raw: any, ...keys: string[]): T[] {
  if (Array.isArray(raw)) return raw as T[];
  for (const k of keys) if (Array.isArray(raw?.[k])) return raw[k] as T[];
  return [];
}

const enc = encodeURIComponent;
const S = (id: string) => `/api/sessions/${enc(id)}`;
const W = (projectId?: string | null) => `/api/workspace/${enc(projectId || "default")}`;

export const sessionsApi = {
  archived: () => request<SessionSummary[]>("/api/sessions?archived=true"),
  list: (projectId?: string | null, includeSubagents = false, view?: "inbox") =>
    request<any>(`/api/sessions${query({ project_id: projectId, include_subagents: includeSubagents ? "true" : undefined, view })}`).then((r) => listOf<SessionSummary>(r, "sessions", "items")),
  /** Mark a thread as seen: clears `unread` and a non-blocking `attention`. */
  read: (id: string) => request<SessionSummary | undefined>(`${S(id)}/read`, { method: "POST", json: {} }),
  inboxSummary: () => request<any>("/api/sessions/inbox/summary").then(normalizeInbox),
  create: (body: { title?: string; project_id?: string | null; mode?: SessionMode; provider?: string | null; model?: string | null; effort?: string | null; branch?: string | null }) =>
    request<SessionSummary>("/api/sessions", { method: "POST", json: clean(body) }),
  get: (id: string) => request<SessionSummary & { messages?: SessionMessage[]; todos?: unknown[] }>(S(id)),
  patch: (id: string, body: { archived?: boolean; title?: string; mode?: SessionMode; model?: string | { provider?: string | null; model?: string | null; effort?: string | null } }) =>
    request<SessionSummary>(S(id), { method: "PATCH", json: body }),
  remove: (id: string) => request<unknown>(S(id), { method: "DELETE" }),
  moveProject: (id: string, projectId: string | null) => request<SessionSummary>(`${S(id)}/project`, { method: "POST", json: { project_id: projectId } }),
  /** Copy the session up to and including `messageId` (the whole session when omitted). */
  fork: (id: string, messageId?: string | null) => request<SessionSummary>(`${S(id)}/fork`, { method: "POST", json: messageId ? { message_id: messageId } : {} }),
  rewind: (id: string, messageId: string, opts: { include?: boolean; restoreFiles?: boolean } = {}) =>
    request<{ removed: number; reverted: string[]; conflicts: string[]; session: any }>(`${S(id)}/rewind`, { method: "POST", json: { message_id: messageId, include: !!opts.include, restore_files: !!opts.restoreFiles } }),
  send: (id: string, text: string, attachments?: Attachment[]) =>
    request<SendResult>(`${S(id)}/messages`, {
      method: "POST",
      json: attachments?.length
        ? { text, attachments: attachments.map(({ kind, ref, label, text: quote }) => clean({ kind, ref, label: label?.slice(0, 300), text: kind === "span" ? quote?.slice(0, 8000) : undefined })) }
        : { text },
    }),
  interrupt: (id: string) => request<unknown>(`${S(id)}/interrupt`, { method: "POST", json: {} }),
  approve: (id: string, toolCallId: string, decision: "once" | "always" | "deny", feedback?: string) =>
    request<unknown>(`${S(id)}/approvals/${enc(toolCallId)}`, { method: "POST", json: feedback ? { decision, feedback } : { decision } }),
  input: (id: string, answer: string, toolCallId?: string | null) =>
    request<unknown>(`${S(id)}/input`, { method: "POST", json: toolCallId ? { answer, tool_call_id: toolCallId } : { answer } }),
  result: (id: string, ref: string) => request<{ text: string }>(`${S(id)}/results/${enc(ref)}`).then((r) => String(r?.text ?? "")),
  compact: (id: string, focus?: string) => request<CompactResult>(`${S(id)}/compact`, { method: "POST", json: focus ? { focus } : {} }),
};

export function normalizeInbox(raw: any): InboxSummary {
  const items = listOf<InboxItem>(raw, "items").filter((i) => i && i.session_id);
  return { unread: Number(raw?.unread) || 0, blocking: Number(raw?.blocking) || 0, items };
}

/**
 * Live inbox badge: `inbox.updated` carries the summary whenever any thread's
 * unread/attention changes (replayed on connect). Returns a close fn; gives up
 * quietly when the endpoint does not exist.
 */
export function openInboxStream(onSummary: (s: InboxSummary) => void, onState?: (ok: boolean) => void): () => void {
  let source: EventSource | null = null;
  let closed = false;
  let retry = 0;
  let timer = 0;
  const connect = () => {
    if (closed) return;
    source = new EventSource("/api/sessions/inbox/events");
    const handle = (m: MessageEvent) => {
      if (m.data === undefined) return;
      try {
        const parsed = JSON.parse(m.data);
        const body = parsed?.type === "inbox.updated" || parsed?.event === "inbox.updated" ? parsed.data ?? parsed.payload ?? parsed : parsed;
        if (body && typeof body === "object" && ("unread" in body || "items" in body)) onSummary(normalizeInbox(body));
      } catch {
        /* heartbeat */
      }
    };
    source.addEventListener("inbox.updated", handle as EventListener);
    source.onmessage = handle;
    source.onopen = () => {
      retry = 0;
      onState?.(true);
    };
    source.onerror = () => {
      source?.close();
      if (closed) return;
      retry += 1;
      onState?.(false);
      if (retry > 8) return; // endpoint missing or backend gone: the periodic list refresh takes over
      timer = window.setTimeout(connect, Math.min(15000, 500 * 2 ** retry));
    };
  };
  connect();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    source?.close();
  };
}

function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${k}=${enc(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** Which registry to look at: a session's (its project and mode) or a project's. */
export type HarnessScope = { sessionId?: string | null; projectId?: string | null };
const scopeParams = (s?: HarnessScope) => (s?.sessionId ? { session_id: s.sessionId } : { project_id: s?.projectId });

function clean<T extends Record<string, any>>(body: T): T {
  return Object.fromEntries(Object.entries(body).filter(([, v]) => v !== undefined && v !== null && v !== "")) as T;
}

function normalizeMentions(raw: any): Mention[] {
  if (Array.isArray(raw) || Array.isArray(raw?.items) || Array.isArray(raw?.mentions) || Array.isArray(raw?.results)) {
    return listOf<any>(raw, "items", "mentions", "results")
      .map((m) => ({
        kind: (m.kind || m.type || "file") as AttachmentKind,
        ref: String(m.ref ?? m.id ?? m.path ?? ""),
        label: String(m.label ?? m.title ?? m.name ?? m.goal ?? m.path ?? m.ref ?? m.id ?? ""),
        detail: m.detail ?? m.description ?? undefined,
        insert: typeof m.insert === "string" ? m.insert : undefined,
      }))
      .filter((m) => m.ref);
  }
  const out: Mention[] = [];
  for (const p of listOf<any>(raw?.papers)) out.push({ kind: "paper", ref: String(p.id ?? p.ref), label: p.title || p.label || String(p.id), detail: [(p.authors || []).slice?.(0, 2).join(", "), p.year].filter(Boolean).join(" · ") || p.detail });
  for (const f of listOf<any>(raw?.files)) {
    const path = typeof f === "string" ? f : String(f.path ?? f.ref);
    out.push({ kind: "file", ref: path, label: path, detail: typeof f === "object" ? f.detail : undefined });
  }
  for (const r of listOf<any>(raw?.runs)) out.push({ kind: "run", ref: String(r.id ?? r.ref), label: r.goal || r.label || String(r.id), detail: r.status || r.detail });
  return out.filter((m) => m.ref && m.ref !== "undefined");
}

export const harnessApi = {
  tools: (mode?: SessionMode, scope?: HarnessScope) =>
    request<any>(`/api/harness/tools${query({ mode, ...scopeParams(scope) })}`).then((r) => listOf<HarnessTool>(r, "tools", "items")),
  commands: (scope?: HarnessScope) => request<any>(`/api/harness/commands${query(scopeParams(scope))}`).then((r) => listOf<HarnessCommand>(r, "commands", "items")),
  agents: (scope?: HarnessScope) => request<any>(`/api/harness/agents${query(scopeParams(scope))}`).then((r) => listOf<HarnessAgentType>(r, "agents", "items")),
  getSettings: (projectId?: string | null) => request<any>("/api/harness/settings" + (projectId ? `?project_id=${encodeURIComponent(projectId)}` : "")).then((r) => normalizeSettings(r?.settings ?? r)),
  /** Partial update: only the top-level keys that are present are replaced (unknown keys are a 400). */
  putSettings: (body: Partial<HarnessSettings>) => request<any>("/api/harness/settings", { method: "PUT", json: body }).then((r) => normalizeSettings(r?.settings ?? r ?? body)),
  mentions: (q: string, scope?: HarnessScope, limit = 8) =>
    request<any>(`/api/harness/mentions${query({ q: q || undefined, ...scopeParams(scope), limit })}`).then(normalizeMentions),
};

export const HOOK_EVENTS: HookEvent[] = ["pre_tool_use", "post_tool_use", "session_start", "stop"];

export function normalizeSettings(raw: any): HarnessSettings {
  const perms = raw?.permissions || {};
  const hooks = raw?.hooks || {};
  return {
    ...(raw || {}),
    permissions: { allow: listOf<string>(perms.allow), ask: listOf<string>(perms.ask), deny: listOf<string>(perms.deny) },
    hooks: Object.fromEntries(HOOK_EVENTS.map((e) => [e, listOf<HookRule>(hooks[e])])) as Record<HookEvent, HookRule[]>,
    context_limit: Number(raw?.context_limit) || 120000,
    default_mode: (["ask", "agent", "auto", "plan"].includes(raw?.default_mode) ? raw.default_mode : "agent") as SessionMode,
  };
}

function flattenTree(raw: any): TreeEntry[] {
  const out: TreeEntry[] = [];
  const visit = (nodes: any[], prefix: string) => {
    for (const n of nodes || []) {
      if (!n) continue;
      const path = String(n.path ?? (prefix ? `${prefix}/${n.name}` : n.name) ?? "").replace(/\\/g, "/");
      if (!path) continue;
      const isDir = n.type === "dir" || n.type === "directory" || n.is_dir === true || Array.isArray(n.children);
      out.push({ path, type: isDir ? "dir" : "file", size: n.size, mtime: n.mtime });
      if (Array.isArray(n.children)) visit(n.children, path);
    }
  };
  visit(listOf<any>(raw, "entries", "tree", "files", "items", "children"), "");
  return out;
}

export const workspaceApi = {
  location: (projectId: string, path: string) => request<{ project_id: string; path: string; previous_path: string; source_preserved: boolean; verified_files: number }>(`${W(projectId)}/location`, { method: "POST", json: { path, confirm: true } }),
  tree: (projectId?: string | null) =>
    request<any>(`${W(projectId)}/tree`).then((r) => ({ entries: flattenTree(r), truncated: !!r?.truncated, root: (r?.root || r?.path || "") as string })),
  read: (projectId: string | null | undefined, path: string) =>
    request<any>(`${W(projectId)}/file?path=${enc(path)}`).then((r): WorkspaceFile =>
      typeof r === "string" ? { path, content: r } : { ...r, path: r?.path || path, content: String(r?.content ?? r?.text ?? "") }),
  write: (projectId: string | null | undefined, path: string, content: string) =>
    request<any>(`${W(projectId)}/file?path=${enc(path)}`, { method: "PUT", json: { content } }),
  checkpoints: (projectId?: string | null, filter?: { sessionId?: string | null; path?: string | null; limit?: number }) =>
    request<any>(`${W(projectId)}/checkpoints${query({ session_id: filter?.sessionId, path: filter?.path, limit: filter?.limit })}`).then((r) => listOf<Checkpoint>(r, "checkpoints", "items")),
  /** One checkpoint with `before` / `after` and a unified `diff`. */
  checkpoint: (projectId: string | null | undefined, id: string) =>
    request<any>(`${W(projectId)}/checkpoints/${enc(id)}`).then((r) => (r?.checkpoint ?? r) as Checkpoint),
  revert: (projectId: string | null | undefined, id: string) =>
    request<any>(`${W(projectId)}/checkpoints/${enc(id)}/revert`, { method: "POST", json: {} }),
};

// ---- version control (docs/glossx-workspaces.md §3) ----
export type VcsBranch = { name: string; current?: boolean; head?: string; updated_at?: number };
export type VcsChange = { path: string; status: string; staged?: boolean };
export type VcsStatus = {
  available: boolean;
  branch: string | null;
  branches: VcsBranch[];
  changes: VcsChange[];
  ahead_of?: Record<string, any> | null;
  clean: boolean;
  /** Why git is unavailable, when the backend says. */
  reason?: string;
};
export type VcsCommit = {
  id: string;
  short: string;
  message: string;
  author?: string;
  time?: number;
  parents: string[];
  refs: string[];
  files_changed?: number | string[] | { path: string; status?: string }[];
  session_id?: string | null;
};
export type MergeResult = { merged: boolean; fast_forward?: boolean; conflicts: string[] };

const V = (projectId?: string | null) => `${W(projectId)}/vcs`;
const asText = (r: any): string => (typeof r === "string" ? r : String(r?.diff ?? r?.text ?? r?.content ?? ""));

export function normalizeVcsStatus(raw: any): VcsStatus {
  const branches = listOf<any>(raw?.branches).map((b) => (typeof b === "string" ? { name: b } : b)).filter((b) => b && b.name) as VcsBranch[];
  const branch = (raw?.branch ?? branches.find((b) => b.current)?.name ?? null) as string | null;
  const changes = listOf<any>(raw?.changes).filter((c) => c && c.path).map((c) => ({ path: String(c.path).replace(/\\/g, "/"), status: String(c.status || "M"), staged: !!c.staged }));
  return {
    available: raw?.available !== false,
    branch,
    branches: branches.map((b) => ({ ...b, current: b.current ?? b.name === branch })),
    changes,
    ahead_of: raw?.ahead_of ?? null,
    clean: typeof raw?.clean === "boolean" ? raw.clean : changes.length === 0,
    reason: typeof raw?.reason === "string" ? raw.reason : typeof raw?.message === "string" ? raw.message : undefined,
  };
}

export function normalizeCommit(c: any): VcsCommit {
  const id = String(c?.id ?? c?.sha ?? c?.hash ?? "");
  const time = typeof c?.time === "number" ? c.time : c?.time ? Date.parse(c.time) / 1000 || undefined : undefined;
  return {
    ...c,
    id,
    short: String(c?.short ?? id.slice(0, 7)),
    message: String(c?.message ?? c?.subject ?? ""),
    author: typeof c?.author === "string" ? c.author : c?.author?.name,
    time,
    parents: listOf<string>(c?.parents).map(String),
    refs: listOf<string>(c?.refs).map(String),
  };
}

export const vcsApi = {
  status: (projectId?: string | null) => request<any>(`${V(projectId)}/status`).then(normalizeVcsStatus),
  log: (projectId: string | null | undefined, opts?: { branch?: string | null; limit?: number; path?: string | null }) =>
    request<any>(`${V(projectId)}/log${query({ branch: opts?.branch, limit: opts?.limit, path: opts?.path })}`).then((r) => listOf<any>(r, "commits", "items", "log").map(normalizeCommit).filter((c) => c.id)),
  /** Unified diff; the working tree is compared when `to` is empty. */
  diff: (projectId: string | null | undefined, opts?: { from?: string | null; to?: string | null; path?: string | null }) =>
    request<any>(`${V(projectId)}/diff${query({ from: opts?.from, to: opts?.to, path: opts?.path })}`).then(asText),
  file: (projectId: string | null | undefined, rev: string, path: string) =>
    request<any>(`${V(projectId)}/file${query({ rev, path })}`).then(asText),
  commit: (projectId: string | null | undefined, message: string, paths?: string[]) =>
    request<any>(`${V(projectId)}/commit`, { method: "POST", json: paths?.length ? { message, paths } : { message } }).then((r) => normalizeCommit(r?.commit ?? r ?? {})),
  createBranch: (projectId: string | null | undefined, name: string, from?: string | null) =>
    request<any>(`${V(projectId)}/branches`, { method: "POST", json: from ? { name, from } : { name } }),
  checkout: (projectId: string | null | undefined, branch: string, dirty?: "stash" | "discard") =>
    request<any>(`${V(projectId)}/checkout`, { method: "POST", json: dirty ? { branch, [dirty]: true } : { branch } }),
  merge: (projectId: string | null | undefined, branch: string) =>
    request<any>(`${V(projectId)}/merge`, { method: "POST", json: { branch } }).then((r): MergeResult => ({ merged: !!r?.merged, fast_forward: !!r?.fast_forward, conflicts: listOf<string>(r?.conflicts).map(String) })),
  deleteBranch: (projectId: string | null | undefined, name: string) =>
    request<any>(`${V(projectId)}/branches/${name.split("/").map(enc).join("/")}`, { method: "DELETE" }),
  /** Restore files from a revision as a new commit. */
  restore: (projectId: string | null | undefined, rev: string, paths?: string[]) =>
    request<any>(`${V(projectId)}/restore`, { method: "POST", json: paths?.length ? { rev, paths } : { rev } }),
  discard: (projectId: string | null | undefined, paths: string[]) =>
    request<any>(`${V(projectId)}/discard`, { method: "POST", json: { paths } }),
};

/**
 * Subscribe to a session's event stream. The server replays the current state
 * first on every (re)connect, so reconnecting is always safe. Returns a close fn.
 */
export function openSessionStream(
  id: string,
  onEvent: (name: string, payload: any) => void,
  onState?: (state: "open" | "reconnecting" | "closed") => void,
): () => void {
  let source: EventSource | null = null;
  let closed = false;
  let retry = 0;
  let timer = 0;

  const connect = () => {
    if (closed) return;
    source = new EventSource(`${S(id)}/events`);
    const handle = (eventName: string) => (m: MessageEvent) => {
      const frame = decodeFrame(eventName, m.data);
      if (frame) onEvent(frame.name, frame.payload);
    };
    for (const name of EVENT_NAMES) {
      // the browser's own connection `error` event carries no data; skip it here
      source.addEventListener(name, (m) => (m as MessageEvent).data !== undefined && handle(name)(m as MessageEvent));
    }
    source.onmessage = handle("message");
    source.onopen = () => {
      retry = 0;
      onState?.("open");
    };
    source.onerror = (e) => {
      if ((e as MessageEvent).data !== undefined) return; // a server-sent `error` event, not a dropped connection
      source?.close();
      if (closed) return;
      retry += 1;
      if (retry > 6) {
        closed = true;
        onState?.("closed");
        return;
      }
      onState?.("reconnecting");
      timer = window.setTimeout(connect, Math.min(8000, 400 * 2 ** retry));
    };
  };
  connect();

  return () => {
    closed = true;
    window.clearTimeout(timer);
    source?.close();
  };
}
