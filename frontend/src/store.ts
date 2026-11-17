import { create } from "zustand";
import {
  api,
  ChatAttachment,
  Drawing,
  Highlight,
  ImportJob,
  PagesResponse,
  Paper,
  PluginSnapshot,
  ProviderConnectionStatus,
  ProviderTestResult,
} from "./api/client";
import { mergeImportJobSnapshots } from "./importQueue.mjs";
import {
  agentApi, streamAgentEvents,
  type AgentEvent, type AgentProject, type AgentRun, type AgentSpan,
} from "./api/agent";
import { evictPdfDoc } from "./pdf/docCache";

// ---- workbench layout + editor tabs ----
export type UiMode = "gloss" | "glossx"; // classic reader | agent workbench
export type Activity = "library" | "workspace" | "scm" | "projects" | "search" | "capabilities" | "mobile" | "settings";
export type AiMode = "agent" | "threads" | "tools" | "browser";
export type BottomTab = "trace" | "runs" | "tasks" | "terminal";
export type PaletteMode = "commands" | "papers" | "import" | "ask" | "runs" | "chats" | "projects" | "newProject" | "theme" | "branches" | "newBranch" | "commit" | "tryBranch" | "browserUrl";
// file / checkpoint ids are `<workspace key>|<path or checkpoint id>`;
// vcsdiff = `<key>|<path>` (working tree vs HEAD); commit = `<key>|<commit id>`
export type EditorTab = { kind: "paper" | "run" | "welcome" | "file" | "checkpoint" | "vcsdiff" | "commit" | "browser"; id: string };
export const splitWorkspaceId = (id: string) => {
  const i = id.indexOf("|");
  return { project: i < 0 ? "default" : id.slice(0, i), ref: i < 0 ? id : id.slice(i + 1) };
};
export const tabKey = (t: EditorTab) => `${t.kind}:${t.id}`;

export interface Layout {
  activity: Activity;
  sideOpen: boolean;
  aiOpen: boolean;
  aiMode: AiMode;
  bottomOpen: boolean;
  bottomTab: BottomTab;
  sideWidth: number;
  aiWidth: number;
  bottomHeight: number;
  outlineWidth: number;
  outlineCollapsed: boolean;
}

const DEFAULT_LAYOUT: Layout = {
  activity: "library", sideOpen: true, aiOpen: true, aiMode: "agent", bottomOpen: false, bottomTab: "trace",
  sideWidth: 290, aiWidth: 420, bottomHeight: 220, outlineWidth: 190, outlineCollapsed: false,
};

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}
function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}
function readStr(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

const TABS_KEY = "gloss.tabs";
const LAYOUT_KEY = "gloss.layout";

// Cheap per-paper cache so switching back to an open tab is instant.
interface PaperEntry { paper: Paper; pages: PagesResponse; highlights: Highlight[]; drawings: Drawing[] }
const paperCache = new Map<string, PaperEntry>();
let paperSeq = 0;

// Agent run event streams + refetch coalescing.
const runStreams = new Map<string, () => void>();
const runRefetch = new Map<string, "busy" | "again">();

const isLive = (r?: AgentRun | null) => r?.status === "running" || r?.status === "awaiting_approval";

let paperProjectMissing = false;

let importMutation = 0;
let importRefreshInFlight: Promise<void> | null = null;
let papersRequestSequence = 0;
let papersAppliedSequence = 0;

export interface Selection {
  text: string;
  page: number;
  rects: [number, number, number, number][]; // in PDF points
}

interface State {
  papers: Paper[];
  importJobs: ImportJob[];
  // Gloss (classic) ⇄ GlossX (workbench) mode; `view` is the classic view
  mode: UiMode;
  setMode: (m: UiMode) => void;
  view: "library" | "reader" | "agent";
  closePaper: () => void;
  // layout
  layout: Layout;
  setLayout: (patch: Partial<Layout>) => void;
  showActivity: (a: Activity) => void;
  palette: PaletteMode | null; // open command palette + its current mode
  openPalette: (mode?: PaletteMode | null) => void;
  theme: string;
  setTheme: (t: string) => void;
  // editor tabs (ordered) + the active one; `current` is the active paper
  openTabs: EditorTab[];
  activeTabKey: string | null;
  activateTab: (key: string) => Promise<void>;
  closeTab: (key: string) => void;
  cycleTab: (dir: 1 | -1) => void;
  openRunTab: (runId: string) => void;
  openWelcome: () => void;
  openFileTab: (project: string | null | undefined, path: string) => void;
  openCheckpointTab: (project: string | null | undefined, checkpointId: string) => void;
  openVcsDiffTab: (project: string | null | undefined, path: string) => void;
  openCommitTab: (project: string | null | undefined, commitId: string) => void;
  openBrowserTab: (sessionId: string) => void;
  browserSessionId: string | null;
  restoreTabs: () => Promise<void>;
  openCitation: (span: AgentSpan) => Promise<void>;
  // agent
  agentRunId: string | null;
  agentProjectId: string | null;
  agentTick: number;
  runs: Record<string, AgentRun>;
  runEvents: Record<string, AgentEvent[]>;
  recentRuns: AgentRun[];
  projects: AgentProject[];
  agentPrefill: { goal?: string; paperIds?: string[]; id: number } | null;
  /** GlossX side chat: quick questions next to the agent thread that never enter its context. */
  sideChatOpen: boolean;
  sideAsk: { text: string; id: number } | null;
  openSideChat: (ask?: string) => void;
  closeSideChat: () => void;
  setAgentRunId: (id: string | null) => void;
  setAgentProjectId: (id: string | null) => void;
  /** paper id → its own project id (GET /api/papers/{id}/project), false = the backend has no such route. */
  paperProjects: Record<string, string>;
  /** Resolve (and cache) a paper's project; null when the backend cannot say. */
  paperProject: (paperId: string) => Promise<AgentProject | null>;
  /** Make a paper's project the active one (workspace explorer, new threads, chat scope). */
  activatePaperProject: (paperId: string) => Promise<AgentProject | null>;
  bumpAgentTick: () => void;
  refreshRuns: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  createProject: (name: string) => Promise<AgentProject>;
  loadRun: (id: string) => Promise<AgentRun>;
  startAgentRun: (body: Parameters<typeof agentApi.startRun>[0]) => Promise<AgentRun>;
  cancelAgentRun: (id: string) => Promise<void>;
  decideApproval: (id: string, approve: boolean) => Promise<void>;
  askAgent: (opts: { goal?: string; paperIds?: string[] }) => void;
  providerModels: Record<string, string>;
  current: Paper | null;
  pages: PagesResponse | null;
  highlights: Highlight[];
  drawings: Drawing[];
  chatAttachments: ChatAttachment[];
  pluginSnapshot: PluginSnapshot;
  selection: Selection | null;
  activeTab: string;
  targetLanguage: string;
  outputLanguage: string;
  uiLang: "en" | "zh"; // interface-chrome language (NOT content/translation language)
  scholarView: { key: string | null; query: string }; // persists across tab switches
  providers: string[];
  provider: string;
  providerStatuses: Record<string, ProviderConnectionStatus>;
  featureSettings: Record<string, any>;
  gotoPage: number | null;
  flash: { page: number; rects: [number, number, number, number][]; id: number } | null;
  toast: string | null;
  selectionAction: { kind: string; selection: Selection; id: number } | null;
  settingsFocus: string | null;

  loadPapers: () => Promise<void>;
  updatePaper: (
    id: string,
    fields: Partial<Pick<Paper, "title" | "tags" | "year" | "reading_lens">>,
  ) => Promise<Paper>;
  loadImportJobs: () => Promise<void>;
  enqueueImport: (query: string) => Promise<ImportJob>;
  cancelImport: (id: string) => Promise<void>;
  openPaper: (id: string) => Promise<void>;
  setTab: (t: string) => void;
  openFeatureSettings: (featureId: string | null) => void;
  setUiLang: (l: "en" | "zh") => void;
  setScholarView: (v: { key: string | null; query: string }) => void;
  flashLocate: (page: number, rects: [number, number, number, number][]) => void;
  clearFlash: () => void;
  setSelection: (s: Selection | null) => void;
  refreshHighlights: () => Promise<void>;
  refreshDrawings: () => Promise<void>;
  loadPlugins: () => Promise<void>;
  loadSettings: () => Promise<void>;
  refreshProviderStatuses: () => Promise<void>;
  checkProvider: (provider: string) => Promise<ProviderTestResult>;
  switchProvider: (provider: string) => Promise<void>;
  setGoto: (p: number | null) => void;
  notify: (m: string | null) => void;
  runSelectionAction: (kind: "explain" | "translate" | "ask") => void;
  askAboutText: (text: string) => void;
  addChatAttachment: (attachment: ChatAttachment) => void;
  removeChatAttachment: (id: string) => void;
  clearChatAttachments: () => void;
  addUserHighlight: (color?: string, note?: string) => Promise<void>;
}

export const useStore = create<State>((set, get) => ({
  papers: [],
  importJobs: [],
  mode: readStr("gloss.mode", "gloss") === "glossx" ? "glossx" : "gloss",
  setMode: (m) => {
    const st = get();
    if (m === st.mode) return;
    try {
      localStorage.setItem("gloss.mode", m);
    } catch {
      /* ignore */
    }
    if (m === "glossx") {
      // the paper open in the classic reader becomes the active paper tab
      set({ mode: m });
      if (st.view === "reader" && st.current) void get().openPaper(st.current.id).catch(() => {});
    } else {
      const active = st.openTabs.find((t) => tabKey(t) === st.activeTabKey);
      // classic Gloss has no agent workspace: anything but a paper maps to the library
      const view = active?.kind === "paper" && st.current ? "reader" : "library";
      set({ mode: m, view });
    }
  },
  view: "library",
  closePaper: () => set({ view: "library", selection: null }),
  layout: ((saved) => ({
    ...saved,
    // The browser lives in the right pane; older layouts may still point the side bar at it.
    // The browser and the thread list live in the right pane now; older layouts may still point the side bar at them.
    activity: ["browser", "threads"].includes(saved.activity as string) ? DEFAULT_LAYOUT.activity : saved.activity,
    // paper chat became the agent's side chat
    aiMode: (saved.aiMode as string) === "chat" ? "agent" : saved.aiMode,
  }))(readJson<Layout>(LAYOUT_KEY, DEFAULT_LAYOUT)),
  setLayout: (patch) => {
    const layout = { ...get().layout, ...patch };
    writeJson(LAYOUT_KEY, layout);
    set({ layout });
  },
  showActivity: (a) => {
    const { layout } = get();
    // clicking the active activity collapses the side bar
    if (layout.activity === a && layout.sideOpen) get().setLayout({ sideOpen: false });
    else get().setLayout({ activity: a, sideOpen: true });
  },
  palette: null,
  openPalette: (mode = "commands") => set({ palette: mode }),
  theme: readStr("gloss.theme", "midnight"),
  setTheme: (t) => {
    try {
      localStorage.setItem("gloss.theme", t);
    } catch {
      /* ignore */
    }
    document.documentElement.dataset.theme = t;
    set({ theme: t });
  },

  openTabs: [],
  activeTabKey: null,
  activateTab: async (key) => {
    const tab = get().openTabs.find((t) => tabKey(t) === key);
    if (!tab) return;
    set({ activeTabKey: key });
    persistTabs();
    if (tab.kind === "paper") {
      // GlossX: the workspace follows the paper in the active tab
      if (get().mode === "glossx") void get().activatePaperProject(tab.id);
      await showPaper(tab.id);
    } else if (tab.kind === "run") {
      set({ agentRunId: tab.id });
      if (!get().runs[tab.id]) await get().loadRun(tab.id).catch(() => {});
    }
  },
  closeTab: (key) => {
    const { openTabs, activeTabKey, current } = get();
    const idx = openTabs.findIndex((t) => tabKey(t) === key);
    if (idx < 0) return;
    const closing = openTabs[idx];
    const rest = openTabs.filter((_, i) => i !== idx);
    if (closing.kind === "paper") {
      paperCache.delete(closing.id);
      evictPdfDoc(closing.id);
    }
    set({ openTabs: rest });
    if (activeTabKey === key) {
      const next = rest[Math.min(idx, rest.length - 1)];
      if (next) void get().activateTab(tabKey(next));
      else set({ activeTabKey: null });
    }
    if (closing.kind === "paper" && current?.id === closing.id) {
      const nextActive = rest.find((t) => tabKey(t) === get().activeTabKey);
      if (!nextActive || nextActive.kind !== "paper") {
        const fallback = [...rest].reverse().find((t) => t.kind === "paper");
        if (fallback) void showPaper(fallback.id);
        else set({ current: null, pages: null, highlights: [], drawings: [], chatAttachments: [], selection: null });
      }
    }
    persistTabs();
  },
  cycleTab: (dir) => {
    const { openTabs, activeTabKey } = get();
    if (!openTabs.length) return;
    const idx = openTabs.findIndex((t) => tabKey(t) === activeTabKey);
    const next = openTabs[(idx + dir + openTabs.length) % openTabs.length];
    void get().activateTab(tabKey(next));
  },
  openRunTab: (runId) => {
    addTab({ kind: "run", id: runId });
    void get().activateTab(`run:${runId}`);
  },
  openWelcome: () => {
    addTab({ kind: "welcome", id: "welcome" });
    void get().activateTab("welcome:welcome");
  },
  openFileTab: (project, path) => {
    const id = `${project || "default"}|${path}`;
    addTab({ kind: "file", id });
    void get().activateTab(`file:${id}`);
  },
  openCheckpointTab: (project, checkpointId) => {
    const id = `${project || "default"}|${checkpointId}`;
    addTab({ kind: "checkpoint", id });
    void get().activateTab(`checkpoint:${id}`);
  },
  openVcsDiffTab: (project, path) => {
    const id = `${project || "default"}|${path}`;
    addTab({ kind: "vcsdiff", id });
    void get().activateTab(`vcsdiff:${id}`);
  },
  openCommitTab: (project, commitId) => {
    const id = `${project || "default"}|${commitId}`;
    addTab({ kind: "commit", id });
    void get().activateTab(`commit:${id}`);
  },
  browserSessionId: readStr("gloss.browserSession", "") || null,
  openBrowserTab: (id) => {
    localStorage.setItem("gloss.browserSession", id);
    set({ browserSessionId: id });
    get().setLayout({ aiOpen: true, aiMode: "browser", aiWidth: Math.max(520, get().layout.aiWidth) });
  },
  restoreTabs: async () => {
    const saved = readJson<{ tabs: EditorTab[]; active: string | null }>(TABS_KEY, { tabs: [], active: null });
    const known = new Set(get().papers.map((p) => p.id));
    const legacyBrowser = (Array.isArray(saved.tabs) ? saved.tabs : []).find((t) => t.kind === "browser" && `browser:${t.id}` === saved.active);
    if (legacyBrowser) get().openBrowserTab(legacyBrowser.id);
    const tabs = (Array.isArray(saved.tabs) ? saved.tabs : []).filter(
      (t) => t && typeof t.id === "string" && (t.kind === "paper" ? known.has(t.id) : ["run", "welcome", "file"].includes(t.kind)),
    );
    if (!tabs.length) return;
    set({ openTabs: tabs });
    // drop report tabs whose run no longer exists on the backend
    tabs.filter((t) => t.kind === "run").forEach((t) => {
      get().loadRun(t.id).catch(() => get().closeTab(tabKey(t)));
    });
    const active = tabs.find((t) => tabKey(t) === saved.active) || tabs[0];
    const lastPaper = active.kind === "paper" ? null : [...tabs].reverse().find((t) => t.kind === "paper");
    await get().activateTab(tabKey(active));
    if (lastPaper) void showPaper(lastPaper.id);
  },
  openCitation: async (span) => {
    const key = `paper:${span.paper_id}`;
    const ready = get().activeTabKey === key && get().current?.id === span.paper_id;
    const cached = paperCache.has(span.paper_id);
    try {
      await get().openPaper(span.paper_id);
    } catch (e: any) {
      get().notify(String(e?.message || e));
      return;
    }
    const go = () => {
      if (span.bbox) get().flashLocate(span.page, [span.bbox]);
      else get().setGoto(span.page);
    };
    if (get().mode === "gloss") set({ activeTab: "agent" }); // classic: keep the Agent tab in view
    if (ready) go();
    else setTimeout(go, cached ? 150 : 700);
  },

  agentRunId: null,
  agentProjectId: readStr("gloss.agentProject", "") || null,
  agentTick: 0,
  runs: {},
  runEvents: {},
  recentRuns: [],
  projects: [],
  agentPrefill: null,
  sideChatOpen: readStr("glossx.sideChat", "") === "1",
  sideAsk: null,
  openSideChat: (ask) => {
    try { localStorage.setItem("glossx.sideChat", "1"); } catch { /* private mode */ }
    get().setLayout({ aiOpen: true, aiMode: "agent" });
    const text = (ask || "").trim();
    set(text ? { sideChatOpen: true, sideAsk: { text, id: Date.now() } } : { sideChatOpen: true });
  },
  closeSideChat: () => {
    try { localStorage.setItem("glossx.sideChat", ""); } catch { /* private mode */ }
    set({ sideChatOpen: false });
  },
  setAgentRunId: (id) => set({ agentRunId: id }),
  setAgentProjectId: (id) => {
    try {
      localStorage.setItem("gloss.agentProject", id || "");
    } catch {
      /* ignore */
    }
    set({ agentProjectId: id });
  },
  paperProjects: {},
  paperProject: async (paperId) => {
    if (paperProjectMissing) return null;
    const known = get().paperProjects[paperId];
    const hit = known ? get().projects.find((p) => p.id === known) : undefined;
    if (hit) return hit;
    try {
      const p = await agentApi.paperProject(paperId);
      if (!p || !p.id || p.archived || p.removed) return null;
      set((s) => ({
        paperProjects: { ...s.paperProjects, [paperId]: p.id },
        projects: s.projects.some((x) => x.id === p.id) ? s.projects.map((x) => (x.id === p.id ? { ...x, ...p } : x)) : [...s.projects, p],
      }));
      return p;
    } catch (e: any) {
      // older backend (no per-paper projects): keep whatever project is active and stop asking
      const msg = String(e?.message || e);
      if (/^(405|501)\b/.test(msg) || (/^404\b/.test(msg) && /"Not Found"/i.test(msg)) || e instanceof SyntaxError) paperProjectMissing = true;
      return null;
    }
  },
  activatePaperProject: async (paperId) => {
    const p = await get().paperProject(paperId);
    if (!p || get().activeTabKey !== `paper:${paperId}`) return p; // the user moved on while this resolved
    const active = get().projects.find((x) => x.id === get().agentProjectId);
    // a research project that already holds this paper stays active
    if (active?.id !== p.id && !(active?.paper_ids || []).includes(paperId)) get().setAgentProjectId(p.id);
    return p;
  },
  bumpAgentTick: () => set((s) => ({ agentTick: s.agentTick + 1 })),
  refreshRuns: async () => {
    try {
      const recentRuns = await agentApi.listRuns();
      set({ recentRuns });
      // keep streams attached to any live run so the trace/budget stay current
      recentRuns.filter(isLive).forEach((r) => watchRun(r.id));
    } catch {
      /* backend may be starting */
    }
  },
  refreshProjects: async () => {
    try {
      const projects = await agentApi.projects();
      set({ projects });
      if (get().agentProjectId && !projects.some((p) => p.id === get().agentProjectId)) get().setAgentProjectId(null);
    } catch {
      /* ignore */
    }
  },
  createProject: async (name) => {
    const p = await agentApi.createProject(name);
    set((s) => ({ projects: [...s.projects.filter((x) => x.id !== p.id), p] }));
    get().setAgentProjectId(p.id);
    return p;
  },
  loadRun: async (id) => {
    const r = await agentApi.getRun(id);
    set((s) => ({
      runs: { ...s.runs, [id]: r },
      runEvents: runStreams.has(id) ? s.runEvents : { ...s.runEvents, [id]: r.trace || [] },
    }));
    if (isLive(r)) watchRun(id);
    return r;
  },
  startAgentRun: async (body) => {
    const r = await agentApi.startRun(body);
    set((s) => ({ runs: { ...s.runs, [r.id]: r }, runEvents: { ...s.runEvents, [r.id]: [] }, agentRunId: r.id }));
    watchRun(r.id);
    void get().refreshRuns();
    get().bumpAgentTick();
    return r;
  },
  cancelAgentRun: async (id) => {
    const r = await agentApi.cancelRun(id);
    set((s) => ({ runs: { ...s.runs, [id]: r } }));
    void get().refreshRuns();
  },
  decideApproval: async (id, approve) => {
    const a = get().runs[id]?.pending_approval;
    if (!a) return;
    const r = await agentApi.approve(id, a.id, approve);
    set((s) => ({ runs: { ...s.runs, [id]: r } }));
  },
  askAgent: ({ goal, paperIds }) => {
    routeTab("agent");
    set({ agentPrefill: { goal, paperIds, id: Date.now() } });
  },
  providerModels: {},
  current: null,
  pages: null,
  highlights: [],
  drawings: [],
  chatAttachments: [],
  pluginSnapshot: {
    api_version: 1,
    permissions: [],
    contribution_points: [],
    core: [],
    marketplace: [],
    installed: [],
    coming_soon: [],
  },
  selection: null,
  activeTab: "summary",
  targetLanguage: "中文 (Simplified Chinese)",
  outputLanguage: "中文 (Simplified Chinese)",
  uiLang: readStr("gloss.uiLang", "en") === "zh" ? "zh" : "en",
  providers: [],
  provider: "local_claude",
  providerStatuses: {},
  featureSettings: {},
  gotoPage: null,
  flash: null,
  toast: null,
  selectionAction: null,
  settingsFocus: null,
  scholarView: { key: null, query: "" },

  loadPapers: async () => {
    const requestSequence = ++papersRequestSequence;
    const { papers } = await api.listPapers();
    // Apply the newest successful response. A slower, older request can no
    // longer overwrite a library refresh triggered by a completed import.
    if (requestSequence > papersAppliedSequence) {
      papersAppliedSequence = requestSequence;
      set({ papers });
    }
  },
  updatePaper: async (id, fields) => {
    const paper = await api.updatePaper(id, fields);
    set((state) => ({
      papers: state.papers.map((item) => item.id === id ? paper : item),
      current: state.current?.id === id ? paper : state.current,
    }));
    return paper;
  },
  loadImportJobs: () => {
    if (importRefreshInFlight) return importRefreshInFlight;
    const mutationAtStart = importMutation;
    importRefreshInFlight = (async () => {
      const { jobs } = await api.listImportJobs();
      if (mutationAtStart !== importMutation) return;

      const previous = get().importJobs;
      const merged = mergeImportJobSnapshots(previous, jobs) as ImportJob[];
      const previousById = new Map(previous.map((job) => [job.id, job]));
      const newlyCompleted = merged.filter(
        (job) => job.status === "completed" && previousById.get(job.id)?.status !== "completed",
      );
      const newlyFailed = merged.filter(
        (job) => job.status === "failed" && previousById.get(job.id)?.status !== "failed",
      );
      const completedNeedingRefresh = merged.filter(
        (job) =>
          job.status === "completed" &&
          job.paper_id &&
          !get().papers.some((paper) => paper.id === job.paper_id),
      );

      if (newlyCompleted.length || completedNeedingRefresh.length) {
        // Publish "completed" only after the library refresh succeeds. If the
        // backend is briefly unavailable, the next poll retries this refresh.
        await get().loadPapers();
      }
      set({ importJobs: merged });

      if (newlyCompleted.length) {
        const latest = newlyCompleted[newlyCompleted.length - 1];
        get().notify(`Imported: ${(latest.title || latest.query).slice(0, 50)}`);
      } else if (newlyFailed.length) {
        const latest = newlyFailed[newlyFailed.length - 1];
        get().notify(`Import failed: ${latest.error || latest.query}`);
      }
    })().finally(() => {
      importRefreshInFlight = null;
    });
    return importRefreshInFlight;
  },
  enqueueImport: async (query) => {
    const job = await api.enqueueImport(query);
    importMutation += 1;
    set((state) => ({
      importJobs: [...state.importJobs.filter((item) => item.id !== job.id), job],
    }));
    return job;
  },
  cancelImport: async (id) => {
    const previousJob = get().importJobs.find((job) => job.id === id);
    importMutation += 1;
    set((state) => ({
      importJobs: state.importJobs.map((job) =>
        job.id === id && !["completed", "failed", "cancelled"].includes(job.status)
          ? {
              ...job,
              status: "cancelling",
              stage_detail: "Cancelling import",
              revision: job.revision + 1,
            }
          : job,
      ),
    }));
    try {
      await api.cancelImportJob(id);
      importMutation += 1;
      set((state) => ({ importJobs: state.importJobs.filter((job) => job.id !== id) }));
      // If completion won the server-side race, DELETE clears only the job and
      // keeps the finished paper. Refreshing is harmless for a true cancel.
      await get().loadPapers();
    } catch (error) {
      importMutation += 1;
      if (previousJob) {
        set((state) => ({
          importJobs: state.importJobs.map((job) => job.id === id ? previousJob : job),
        }));
      }
      try {
        await get().loadPapers();
      } catch {
        // The next successful queue poll retries completed-paper refreshes.
      }
      throw error;
    }
  },
  openPaper: async (id) => {
    const classic = get().mode === "gloss";
    const fresh = get().current?.id !== id;
    addTab({ kind: "paper", id });
    await get().activateTab(`paper:${id}`);
    if (classic) {
      // classic reader: switch view once loaded, fresh side panel per paper (as before)
      if (fresh) set({ activeTab: "summary", scholarView: { key: null, query: "" } });
      set({ view: "reader" });
    }
  },
  setTab: (t) => routeTab(t),
  openFeatureSettings: (featureId) => {
    if (get().mode === "gloss") set({ activeTab: "settings" });
    else get().setLayout({ activity: "settings", sideOpen: true });
    set({ settingsFocus: featureId });
  },
  setUiLang: (l) => {
    try {
      localStorage.setItem("gloss.uiLang", l);
    } catch {
      /* ignore */
    }
    set({ uiLang: l });
  },
  setScholarView: (v) => set({ scholarView: v }),
  flashLocate: (page, rects) => set({ flash: { page, rects, id: Date.now() }, gotoPage: page }),
  clearFlash: () => set({ flash: null }),
  setSelection: (s) => set({ selection: s }),
  refreshHighlights: async () => {
    const cur = get().current;
    if (!cur) return;
    const { highlights } = await api.listHighlights(cur.id);
    const entry = paperCache.get(cur.id);
    if (entry) entry.highlights = highlights;
    if (get().current?.id === cur.id) set({ highlights });
  },
  refreshDrawings: async () => {
    const cur = get().current;
    if (!cur) return;
    const { drawings } = await api.listDrawings(cur.id);
    const entry = paperCache.get(cur.id);
    if (entry) entry.drawings = drawings;
    if (get().current?.id === cur.id) set({ drawings });
  },
  loadPlugins: async () => {
    try {
      const pluginSnapshot = await api.listPlugins();
      set({ pluginSnapshot });
    } catch {
      /* backend may still be starting */
    }
  },
  loadSettings: async () => {
    try {
      const s = await api.getSettings();
      set({
        providers: s.available_providers,
        provider: s.config.provider,
        providerStatuses: s.provider_statuses || {},
        providerModels: Object.fromEntries(
          Object.entries((s.config.providers || {}) as Record<string, any>).map(([k, v]) => [k, String(v?.model || "")]),
        ),
        targetLanguage: s.config.target_language,
        outputLanguage: s.config.output_language || s.config.target_language,
        featureSettings: s.config.feature_settings || {},
      });
    } catch {
      /* backend not ready */
    }
  },
  refreshProviderStatuses: async () => {
    try {
      const result = await api.getProviderStatuses();
      set({ providerStatuses: result.provider_statuses || {} });
    } catch {
      /* backend not ready */
    }
  },
  checkProvider: async (provider) => {
    set((state) => ({
      providerStatuses: {
        ...state.providerStatuses,
        [provider]: {
          status: "checking",
          connected: false,
          error: "",
          checked_at: state.providerStatuses[provider]?.checked_at || null,
        },
      },
    }));
    try {
      const result = await api.testProvider(provider);
      set((state) => ({
        providerStatuses: {
          ...state.providerStatuses,
          [provider]: {
            status: result.ok ? "connected" : "error",
            connected: result.ok,
            error: result.error || "",
            checked_at: Date.now() / 1000,
          },
        },
      }));
      return result;
    } catch (error: any) {
      const message = String(error?.message || error);
      const result = { ok: false, provider, error: message };
      set((state) => ({
        providerStatuses: {
          ...state.providerStatuses,
          [provider]: {
            status: "error",
            connected: false,
            error: message,
            checked_at: Date.now() / 1000,
          },
        },
      }));
      return result;
    }
  },
  switchProvider: async (provider) => {
    const previous = get().provider;
    if (provider === previous) {
      await get().checkProvider(provider);
      return;
    }
    set({ provider });
    try {
      await api.updateSettings({ provider });
      get().notify(`AI provider: ${provider}`);
      await get().checkProvider(provider);
    } catch (error: any) {
      set({ provider: previous });
      get().notify(`Provider switch failed: ${String(error?.message || error)}`);
    }
  },
  setGoto: (p) => set({ gotoPage: p }),
  notify: (m) => set({ toast: m }),
  runSelectionAction: (kind) => {
    const sel = get().selection;
    if (!sel) return;
    if (kind === "ask" && get().mode === "glossx") return get().openSideChat(sel.text);
    routeTab(kind === "ask" ? "chat" : kind);
    set({ selectionAction: { kind, selection: sel, id: Date.now() } });
  },
  askAboutText: (text) => {
    const t = (text || "").trim();
    if (!t) return;
    if (get().mode === "glossx") return get().openSideChat(t);
    const sel = { text: t, page: 0, rects: [] as [number, number, number, number][] };
    // jump to chat and let ChatPanel's "ask" effect answer about the selection
    routeTab("chat");
    set({ selection: sel, selectionAction: { kind: "ask", selection: sel, id: Date.now() } });
  },
  addChatAttachment: (attachment) => {
    set((state) => ({
      chatAttachments: [...state.chatAttachments.filter((item) => item.id !== attachment.id), attachment].slice(-4),
    }));
    routeTab("chat");
  },
  removeChatAttachment: (id) => set((state) => ({
    chatAttachments: state.chatAttachments.filter((item) => item.id !== id),
  })),
  clearChatAttachments: () => set({ chatAttachments: [] }),
  addUserHighlight: async (color = "#ffd54f", note = "") => {
    const cur = get().current;
    const sel = get().selection;
    if (!cur || !sel) return;
    await api.addHighlight(cur.id, {
      page: sel.page,
      rects: sel.rects,
      text: sel.text,
      color,
      note,
      kind: "user",
    });
    await get().refreshHighlights();
    get().notify("Highlight saved");
  },
}));

// ---- helpers that need the store instance ----

function persistTabs() {
  const { openTabs, activeTabKey } = useStore.getState();
  writeJson(TABS_KEY, { tabs: openTabs, active: activeTabKey });
}

/** Insert a tab right after the active one (or activate it if already open). */
function addTab(tab: EditorTab) {
  const { openTabs, activeTabKey } = useStore.getState();
  const key = tabKey(tab);
  if (openTabs.some((t) => tabKey(t) === key)) return;
  const idx = openTabs.findIndex((t) => tabKey(t) === activeTabKey);
  const next = [...openTabs];
  next.splice(idx < 0 ? next.length : idx + 1, 0, tab);
  useStore.setState({ openTabs: next });
  persistTabs();
}

/** Route a side-panel tab id to the pane that hosts it (classic: the side panel itself). */
function routeTab(t: string) {
  const st = useStore.getState();
  if (st.mode === "gloss") useStore.setState({ activeTab: t });
  else if (t === "chat") st.openSideChat();
  else if (t === "agent") st.setLayout({ aiOpen: true, aiMode: "agent" });
  else if (t === "settings") st.setLayout({ activity: "settings", sideOpen: true });
  else if (t === "extensions") st.setLayout({ activity: "capabilities", sideOpen: true });
  else if (t === "scholar") st.setLayout({ activity: "search", sideOpen: true });
  else {
    st.setLayout({ aiOpen: true, aiMode: "tools" });
    useStore.setState({ activeTab: t });
  }
}

/** Make `id` the active paper, from cache when possible. */
async function showPaper(id: string) {
  const store = useStore;
  const seq = ++paperSeq;
  const cached = paperCache.get(id);
  if (cached) {
    if (store.getState().current?.id !== id) {
      store.setState({
        current: cached.paper, pages: cached.pages, highlights: cached.highlights, drawings: cached.drawings,
        selection: null, chatAttachments: [],
      });
    }
    void store.getState().refreshHighlights().catch(() => {});
    void store.getState().refreshDrawings().catch(() => {});
    return;
  }
  let paper: Paper;
  let pages: PagesResponse;
  try {
    [paper, pages] = await Promise.all([api.getPaper(id), api.getPages(id)]);
  } catch (e) {
    store.getState().closeTab(`paper:${id}`);
    store.getState().notify(`Could not open paper: ${String((e as any)?.message || e)}`);
    throw e;
  }
  const entry: PaperEntry = { paper, pages, highlights: [], drawings: [] };
  paperCache.set(id, entry);
  if (seq !== paperSeq) return; // user moved on; keep the cache for later
  store.setState({
    current: paper, pages, highlights: [], drawings: [], chatAttachments: [], selection: null,
  });
  await Promise.all([store.getState().refreshHighlights(), store.getState().refreshDrawings()]).catch(() => {});
}

function refetchRun(id: string) {
  if (runRefetch.has(id)) {
    runRefetch.set(id, "again");
    return;
  }
  runRefetch.set(id, "busy");
  void agentApi.getRun(id).then((r) => {
    useStore.setState((s) => ({ runs: { ...s.runs, [id]: r } }));
  }).catch(() => {}).finally(() => {
    const again = runRefetch.get(id) === "again";
    runRefetch.delete(id);
    if (again) refetchRun(id);
  });
}

function watchRun(id: string) {
  if (runStreams.has(id)) return;
  const close = streamAgentEvents(
    id,
    (e) => {
      useStore.setState((s) => ({ runEvents: { ...s.runEvents, [id]: [...(s.runEvents[id] || []), e] } }));
      refetchRun(id);
    },
    () => {
      runStreams.delete(id);
      refetchRun(id);
      // small delay: lets the backend settle the final status and avoids a
      // tight reconnect loop if the stream keeps failing
      window.setTimeout(() => void useStore.getState().refreshRuns(), 1500);
      useStore.getState().bumpAgentTick();
    },
  );
  runStreams.set(id, close);
}
