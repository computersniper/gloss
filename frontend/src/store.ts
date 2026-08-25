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
  view: "library" | "reader";
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
  closePaper: () => void;
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
  view: "library",
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
  uiLang: (localStorage.getItem("gloss.uiLang") as "en" | "zh") || "en",
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
    const [paper, pages] = await Promise.all([api.getPaper(id), api.getPages(id)]);
    set({
      current: paper, pages, view: "reader", activeTab: "summary", selection: null,
      highlights: [], drawings: [], chatAttachments: [],
      scholarView: { key: null, query: "" },
    });
    await Promise.all([get().refreshHighlights(), get().refreshDrawings()]);
  },
  closePaper: () => set({
    view: "library", current: null, pages: null, highlights: [], drawings: [], chatAttachments: [], selection: null,
  }),
  setTab: (t) => set({ activeTab: t }),
  openFeatureSettings: (featureId) => set({ activeTab: "settings", settingsFocus: featureId }),
  setUiLang: (l) => {
    localStorage.setItem("gloss.uiLang", l);
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
    set({ highlights });
  },
  refreshDrawings: async () => {
    const cur = get().current;
    if (!cur) return;
    const { drawings } = await api.listDrawings(cur.id);
    set({ drawings });
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
    const tab = kind === "ask" ? "chat" : kind;
    set({ activeTab: tab, selectionAction: { kind, selection: sel, id: Date.now() } });
  },
  askAboutText: (text) => {
    const t = (text || "").trim();
    if (!t) return;
    const sel = { text: t, page: 0, rects: [] as [number, number, number, number][] };
    // jump to chat and let ChatPanel's "ask" effect answer about the selection
    set({ selection: sel, activeTab: "chat", selectionAction: { kind: "ask", selection: sel, id: Date.now() } });
  },
  addChatAttachment: (attachment) => set((state) => ({
    chatAttachments: [...state.chatAttachments.filter((item) => item.id !== attachment.id), attachment].slice(-4),
    activeTab: "chat",
  })),
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
