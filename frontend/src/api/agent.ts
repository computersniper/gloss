// GlossX agent API: capabilities, runs and the per-run event stream.

export type AgentCapability = {
  name: string;
  kind: string;
  card: string;
  input_schema: Record<string, string>;
  source: "builtin" | "plugin" | "skill" | "mcp" | "cli" | "ssh" | (string & {});
  permissions?: string[];
  requires_approval?: boolean;
  trust?: "first-party" | "third-party" | "external" | string;
};
export type AgentRoute = { workflow: string | null; loop: string; reason: string; confidence?: number };
export type AgentApproval = {
  id: string;
  step_id: string;
  capability: string;
  args: Record<string, unknown>;
  permissions: string[];
  reason: string;
  requested_at: number;
};
export type AgentBudget = {
  steps: number;
  max_steps: number;
  llm_calls: number;
  max_llm_calls: number;
  tokens_est: number;
  max_tokens_est: number;
};
export type AgentWorkflow = { id: string; name: string; card: string; source: string };
export type AgentLoop = { id: string; card: string };
export type AgentMemoryNote = { id: string; text: string; source_run?: string | null; ts: number; span_ids?: string[] };
export type AgentProject = {
  id: string;
  name: string;
  description?: string;
  created_at?: number;
  memory?: AgentMemoryNote[];
  run_ids?: string[];
  runs?: Pick<AgentRun, "id" | "goal" | "status" | "created_at">[];
  /** "paper" = created automatically for one paper; "research" = made by the user (may hold several papers). */
  kind?: "paper" | "research" | (string & {});
  paper_ids?: string[];
  /** research direction the project is filed under ("" = unfiled) */
  category?: string;
  /** set on the project that gathers every paper of that direction */
  hub_category?: string;
  archived?: boolean;
  removed?: boolean;
  workspace?: { key: string; path: string; vcs?: { available?: boolean; branch?: string | null } | null } | null;
};
export type McpServerCfg = { name: string; command: string; args: string[]; env: Record<string, string>; enabled: boolean };
export type SshHostCfg = { name: string; host: string; user: string; port: number; workdir: string; enabled?: boolean };
export type AgentConnections = {
  mcp_servers: McpServerCfg[];
  ssh_hosts: SshHostCfg[];
  cli_agents: { claude: { enabled: boolean }; codex: { enabled: boolean } };
  auto_approve: string[];
};
export type ConnectionsStatus = {
  mcp: { name: string; ok: boolean; tools: number | string[]; error?: string }[];
  ssh: { name: string; ok: boolean; error?: string }[];
  cli: { claude: { available: boolean }; codex: { available: boolean } };
};
export const APPROVAL_PERMISSIONS = ["code:execute", "remote:ssh", "mcp:call", "library:write"] as const;
export type AgentEvent = {
  ts: number;
  phase:
    | "route" | "plan" | "gate" | "approval" | "execute" | "adapter"
    | "synthesize" | "verify" | "reflect" | "budget" | "final" | "error" | "end"
    | (string & {});
  approval_id?: string;
  status: string;
  step_id?: string;
  capability?: string;
  detail?: string;
};
export type AgentSpan = {
  id: string;
  paper_id: string;
  title: string;
  page: number;
  bbox: [number, number, number, number] | null;
  text: string;
};
export type AgentClaim = {
  text: string;
  span_ids: string[];
  verdict?: "supported" | "partial" | "unsupported";
  confidence?: number;
  note?: string;
};
export type AgentRun = {
  id: string;
  goal: string;
  paper_ids: string[];
  status: "running" | "awaiting_approval" | "completed" | "failed" | "cancelled";
  route?: AgentRoute | null;
  project_id?: string | null;
  pending_approval?: AgentApproval | null;
  budget?: AgentBudget | null;
  phase: string;
  created_at: number;
  finished_at: number | null;
  trace?: AgentEvent[];
  plan: {
    sub_questions?: string[];
    steps?: { id: string; capability: string; args: Record<string, unknown>; purpose?: string; depends_on?: string[] }[];
  } | null;
  result: {
    answer: string;
    claims: AgentClaim[];
    citations: AgentSpan[];
    sub_questions?: string[];
    stop_reason: string;
    rounds: number;
  } | null;
  error: string | null;
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

export const agentApi = {
  capabilities: () => fetch("/api/agent/capabilities").then((r) => j<AgentCapability[]>(r)),
  startRun: (body: {
    goal: string;
    paper_ids: string[];
    max_rounds?: number;
    provider?: string | null;
    project_id?: string | null;
    workflow?: string;
    loop?: string;
    budget?: { max_steps?: number; max_llm_calls?: number; max_tokens_est?: number };
  }) =>
    fetch("/api/agent/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<AgentRun>(r)),
  listRuns: () => fetch("/api/agent/runs").then((r) => j<AgentRun[]>(r)),
  getRun: (id: string) => fetch(`/api/agent/runs/${encodeURIComponent(id)}`).then((r) => j<AgentRun>(r)),
  approve: (runId: string, approvalId: string, approve: boolean) =>
    fetch(`/api/agent/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approve }),
    }).then((r) => j<AgentRun>(r)),
  workflows: () => fetch("/api/agent/workflows").then((r) => j<AgentWorkflow[]>(r)),
  loops: () => fetch("/api/agent/loops").then((r) => j<AgentLoop[]>(r)),
  projects: () => fetch("/api/agent/projects").then((r) => j<AgentProject[]>(r)),
  archivedProjects: () => fetch("/api/agent/projects?archived=true").then((r) => j<AgentProject[]>(r)),
  projectLifecycle: (id: string, action: "archive" | "restore" | "remove") =>
    fetch(`/api/agent/projects/${encodeURIComponent(id)}/lifecycle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }).then((r) => j<unknown>(r)),
  directionLifecycle: (name: string, action: "archive" | "restore" | "remove") =>
    fetch("/api/agent/categories/lifecycle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, action }) }).then((r) => j<unknown>(r)),
  /** Projects that hold a paper. */
  projectsForPaper: (paperId: string) => fetch(`/api/agent/projects?paper_id=${encodeURIComponent(paperId)}`).then((r) => j<AgentProject[]>(r)),
  /** The paper's own project (created lazily by the backend for older papers). */
  paperProject: (paperId: string) => fetch(`/api/papers/${encodeURIComponent(paperId)}/project`).then((r) => j<AgentProject>(r)),
  addProjectPaper: (id: string, paperId: string) =>
    fetch(`/api/agent/projects/${encodeURIComponent(id)}/papers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: paperId }),
    }).then((r) => j<unknown>(r)),
  removeProjectPaper: (id: string, paperId: string) =>
    fetch(`/api/agent/projects/${encodeURIComponent(id)}/papers/${encodeURIComponent(paperId)}`, { method: "DELETE" }).then((r) => j<unknown>(r)),
  createProject: (name: string, description?: string) =>
    fetch("/api/agent/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }).then((r) => j<AgentProject>(r)),
  openFolder: (path: string) => fetch("/api/agent/projects/open-folder", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
  }).then(r => j<AgentProject>(r)),
  patchProject: (id: string, patch: { name?: string; category?: string }) =>
    fetch(`/api/agent/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then((r) => j<AgentProject>(r)),
  categoryHub: (name: string) =>
    fetch("/api/agent/categories/hub", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then((r) => j<AgentProject>(r)),
  renameCategory: (name: string, newName: string) =>
    fetch("/api/agent/categories/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, new_name: newName }),
    }).then((r) => j<{ changed: number }>(r)),
  getProject: (id: string) => fetch(`/api/agent/projects/${encodeURIComponent(id)}`).then((r) => j<AgentProject>(r)),
  addMemory: (id: string, text: string) =>
    fetch(`/api/agent/projects/${encodeURIComponent(id)}/memory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => j<unknown>(r)),
  deleteMemory: (id: string, memoryId: string) =>
    fetch(`/api/agent/projects/${encodeURIComponent(id)}/memory/${encodeURIComponent(memoryId)}`, {
      method: "DELETE",
    }).then((r) => j<unknown>(r)),
  getConnections: () => fetch("/api/agent/connections").then((r) => j<AgentConnections>(r)),
  putConnections: (body: AgentConnections) =>
    fetch("/api/agent/connections", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<AgentConnections>(r)),
  connectionsStatus: () => fetch("/api/agent/connections/status").then((r) => j<ConnectionsStatus>(r)),
  cancelRun: (id: string) =>
    fetch(`/api/agent/runs/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => j<AgentRun>(r)),
};

/** Subscribe to a run's SSE stream. Returns a close function. */
export function streamAgentEvents(
  id: string,
  onEvent: (e: AgentEvent) => void,
  onClose?: () => void,
): () => void {
  const source = new EventSource(`/api/agent/runs/${encodeURIComponent(id)}/events`);
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    source.close();
    onClose?.();
  };
  source.onmessage = (m) => {
    try {
      const e = JSON.parse(m.data) as AgentEvent;
      onEvent(e);
      if (e.phase === "end") finish();
    } catch {
      // ignore heartbeat frames
    }
  };
  source.onerror = finish;
  return () => {
    done = true;
    source.close();
  };
}
