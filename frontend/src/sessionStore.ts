// Agent sessions: list, active session, live per-session state fed by SSE.
import { create } from "zustand";
import {
  ApiError, harnessApi, isBlocking, isMissing, openInboxStream, openSessionStream, sessionsApi,
  type Attachment, type InboxSummary, type SessionMode, type SessionState, type SessionSummary,
} from "./api/harness";
import { applySessionEvent, emptySessionState } from "./harness/sessionReducer.mjs";
import { useStore } from "./store";
import { useBrowser } from "./browserStore";

export type ModelChoice = { provider: string | null; model: string | null; effort?: string | null };
type Conn = "open" | "reconnecting" | "closed";

const ACTIVE_KEY = "glossx.session";
const DRAFT_KEY = "glossx.sessionDraft";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

const streams = new Map<string, () => void>();
let inboxStream: (() => void) | null = null;
let inboxTimer = 0;
/** false once the backend answered that POST /read does not exist. */
let readSupported = true;
const readAt = new Map<string, number>();
const NO_INBOX: InboxSummary = { unread: 0, blocking: 0, items: [] };
export const activityOf = (s: SessionSummary) => s.last_activity_at || s.updated_at || s.created_at || 0;
const byRecency = (a: SessionSummary, b: SessionSummary) => activityOf(b) - activityOf(a);
/** Inbox order (spec §1): blocking attention, then unread, then the rest — each by last activity. */
export function inboxOrder(list: SessionSummary[]): SessionSummary[] {
  const rank = (s: SessionSummary) => (isBlocking(s.attention) ? 0 : s.unread ? 1 : 2);
  return [...list].sort((a, b) => rank(a) - rank(b) || byRecency(a, b));
}
const EMPTY: SessionState = emptySessionState() as SessionState;

interface SessionsStore {
  /** null = not probed yet, false = the backend has no session API. */
  available: boolean | null;
  loadingList: boolean;
  list: SessionSummary[];
  activeId: string | null;
  states: Record<string, SessionState>;
  conn: Record<string, Conn>;
  sending: boolean;
  /** Mode/model used for the next new chat (and mirrored from the active session). */
  draft: { mode: SessionMode; model: ModelChoice };
  /** Text queued for the composer by other parts of the UI (palette, welcome, library). */
  prefill: { text?: string; attachments?: Attachment[]; send?: boolean; id: number } | null;
  /** Live unread / blocking counts (GET /api/sessions/inbox/events). */
  inbox: InboxSummary;
  /** Threads in inbox order (GET /api/sessions?view=inbox); null = not loaded yet. */
  inboxList: SessionSummary[] | null;
  /** Which list the Threads activity shows. */
  sideView: "threads" | "inbox" | "archive";
  /** Branch the next new thread starts on ("Try on a branch"); null = the workspace's current branch. */
  pendingBranch: string | null;

  refresh: () => Promise<void>;
  select: (id: string | null) => void;
  newChat: () => void;
  /** Start a new thread, optionally in a given project and on a (new or existing) branch. */
  newThread: (opts?: { projectId?: string | null; branch?: string | null }) => void;
  /** Open a thread in the AI pane, make its project active and mark it read. */
  openThread: (id: string) => void;
  setSideView: (v: "threads" | "inbox" | "archive") => void;
  /** Show the Threads activity on its inbox list. */
  openInbox: () => void;
  refreshInbox: () => Promise<void>;
  /** Subscribe to the live inbox summary (idempotent). */
  startInbox: () => void;
  markRead: (id: string, force?: boolean) => Promise<void>;
  markAllRead: () => Promise<void>;
  setPendingBranch: (branch: string | null) => void;
  send: (text: string, attachments?: Attachment[]) => Promise<boolean>;
  interrupt: () => Promise<void>;
  approve: (toolCallId: string, decision: "once" | "always" | "deny", feedback?: string) => Promise<void>;
  answer: (text: string, toolCallId?: string | null) => Promise<void>;
  setMode: (mode: SessionMode) => Promise<void>;
  setModel: (model: ModelChoice) => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  archive: (id: string, archived: boolean) => Promise<void>;
  moveProject: (id: string, projectId: string | null) => Promise<void>;
  /** Fork the active chat up to and including `messageId` (whole chat when omitted); `prefill` lands in the composer. */
  fork: (messageId?: string | null, prefill?: { text: string; attachments?: Attachment[] }, sessionId?: string) => Promise<void>;
  /** Go back to `messageId` in this chat (dropping it too unless `include`); `prefill` lands in the composer. */
  rewind: (messageId: string, opts: { include?: boolean; restoreFiles?: boolean; prefill?: { text: string; attachments?: Attachment[] } }) => Promise<void>;
  compact: (focus?: string) => Promise<void>;
  compose: (p: { text?: string; attachments?: Attachment[]; send?: boolean }) => void;
  clearError: () => void;
}

const fail = (e: unknown) => useStore.getState().notify(String((e as any)?.message || e));
const isZh = () => useStore.getState().uiLang === "zh";
const hadDraft = (() => {
  try {
    return localStorage.getItem(DRAFT_KEY) !== null;
  } catch {
    return true;
  }
})();
let defaultModeAsked = hadDraft;

export const useSessions = create<SessionsStore>((set, get) => {
  const patchState = (id: string, fn: (s: SessionState) => SessionState) =>
    set((st) => {
      const prev = st.states[id] || EMPTY;
      const next = fn(prev);
      return next === prev ? st : { states: { ...st.states, [id]: next } };
    });

  const syncSummary = (id: string) => {
    const s = get().states[id];
    if (!s?.session) return;
    set((st) => {
      const merged = { ...(st.list.find((x) => x.id === id) || {}), ...s.session, status: s.status } as SessionSummary;
      const rest = st.list.filter((x) => x.id !== id);
      return {
        list: [merged, ...rest].sort(byRecency),
        inboxList: st.inboxList ? st.inboxList.map((x) => (x.id === id ? { ...x, ...merged } : x)) : st.inboxList,
      };
    });
  };

  /** Local echo of a read: the server's summary follows on the inbox stream. */
  const applyRead = (id: string) => {
    const clear = (s: SessionSummary): SessionSummary => (s.id !== id || (!s.unread && !s.attention) ? s : { ...s, unread: false, attention: isBlocking(s.attention) ? s.attention : null });
    set((st) => {
      const wasUnread = st.list.find((s) => s.id === id)?.unread;
      const state = st.states[id];
      return {
        list: st.list.map(clear),
        inboxList: st.inboxList ? st.inboxList.map(clear) : st.inboxList,
        states: state?.session ? { ...st.states, [id]: { ...state, session: clear(state.session) } } : st.states,
        inbox: wasUnread ? { ...st.inbox, unread: Math.max(0, st.inbox.unread - 1) } : st.inbox,
      };
    });
  };

  function watch(id: string) {
    if (streams.has(id)) return;
    // History must not depend on a streaming response reaching a mobile proxy.
    // Ignore an HTTP snapshot if newer SSE events arrived while it was in flight.
    let revision = 0;
    let stopped = false;
    let fetching = false;
    let historyError = false;
    let lastEvent = Date.now();
    const snapshot = async () => {
      if (stopped || fetching) return;
      fetching = true;
      const before = revision;
      try {
        const session = await sessionsApi.get(id);
        if (!stopped && revision === before) {
          patchState(id, (s) => applySessionEvent(historyError ? { ...s, error: null } : s, "session.updated", session) as SessionState);
          historyError = false;
          syncSummary(id);
        }
      } catch (error) {
        if (!stopped && revision === before && !get().states[id]?.loaded) {
          historyError = true;
          patchState(id, (s) => ({ ...s, loaded: true, error: error instanceof Error ? error.message : String(error) }));
        }
      } finally { fetching = false; }
    };
    void snapshot();
    const poll = window.setInterval(() => {
      if (Date.now() - lastEvent > 20_000) void snapshot();
    }, 10_000);
    const close = openSessionStream(
      id,
      (name, payload) => {
        revision += 1;
        lastEvent = Date.now();
        const recovered = historyError && name === "session.updated";
        patchState(id, (s) => applySessionEvent(recovered ? { ...s, error: null } : s, name, payload) as SessionState);
        if (recovered) historyError = false;
        if (name === "browser.updated") useBrowser.getState().put(id, payload);
        if (name === "tool.updated" && String(payload?.tool || "").startsWith("browser_") && !["denied", "error", "awaiting_approval"].includes(payload?.status)) {
          if (useStore.getState().mode === "glossx" && useStore.getState().browserSessionId !== id) useStore.getState().openBrowserTab(id);
        }
          if (name === "session.updated" || name === "turn.completed" || name === "usage.updated") {
          syncSummary(id);
          const st = get();
          if (id === st.activeId) {
            const sess = st.states[id]?.session;
            if (sess?.mode && sess.mode !== st.draft.mode) set({ draft: { ...st.draft, mode: sess.mode } });
          }
        }
        if (name === "turn.completed") {
          // a background chat that finished no longer needs its stream (browsers cap connections per host)
          if (id !== get().activeId) unwatch(id);
          void get().refresh();
          // edits may have touched workspace files / checkpoints
          window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
        }
        if (name === "tool.updated" && payload?.checkpoints?.length) {
          window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
        }
      },
      (state) => {
        set((st) => ({ conn: { ...st.conn, [id]: state } }));
        if (state !== "open") void snapshot();
      },
    );
    streams.set(id, () => { stopped = true; window.clearInterval(poll); close(); });
    // keep at most a few live streams: drop the oldest background ones
    for (const other of [...streams.keys()]) {
      if (streams.size <= 4) break;
      if (other !== id && other !== get().activeId) unwatch(other);
    }
  }

  function unwatch(id: string) {
    streams.get(id)?.();
    streams.delete(id);
  }

  return {
    available: null,
    loadingList: false,
    list: [],
    activeId: read<string | null>(ACTIVE_KEY, null),
    states: {},
    conn: {},
    sending: false,
    draft: read(DRAFT_KEY, { mode: "agent" as SessionMode, model: { provider: null, model: null } }),
    prefill: null,
    inbox: NO_INBOX,
    inboxList: null,
    sideView: "threads",
    pendingBranch: null,

    newThread: (opts) => {
      if (opts && "projectId" in opts && opts.projectId !== useStore.getState().agentProjectId) useStore.getState().setAgentProjectId(opts.projectId || null);
      get().select(null);
      set({ pendingBranch: opts?.branch || null });
      useStore.getState().setLayout({ aiOpen: true, aiMode: "agent" });
      set({ prefill: { id: Date.now(), text: "" } });
    },

    openThread: (id) => {
      const sess = get().list.find((s) => s.id === id) || get().inboxList?.find((s) => s.id === id);
      get().select(id);
      const st = useStore.getState();
      st.setLayout({ aiOpen: true, aiMode: "agent" });
      // the explorer / source control follow the thread's project
      if (sess?.project_id && sess.project_id !== st.agentProjectId) st.setAgentProjectId(sess.project_id);
      void get().markRead(id, true);
    },

    setSideView: (v) => {
      set({ sideView: v });
      if (v === "inbox") void get().refreshInbox();
    },

    openInbox: () => {
      useStore.getState().setLayout({ aiOpen: true, aiMode: "threads" });
      get().setSideView("inbox");
    },

    refreshInbox: async () => {
      try {
        const all = (await sessionsApi.list(undefined, false, "inbox")).filter((s) => !s.parent_id);
        // an older backend ignores ?view=inbox and has none of the inbox fields: order it here
        const ordered = all.some((s) => s.last_activity_at != null || s.unread != null) ? all : inboxOrder(all);
        set({ inboxList: ordered });
      } catch {
        set((st) => ({ inboxList: st.inboxList ?? inboxOrder(st.list) }));
      }
    },

    startInbox: () => {
      if (inboxStream) return;
      inboxStream = openInboxStream((summary) => {
        set({ inbox: summary });
        // unread / attention changed somewhere: refresh the lists (coalesced)
        window.clearTimeout(inboxTimer);
        inboxTimer = window.setTimeout(() => {
          void get().refresh();
          if (get().sideView === "inbox") void get().refreshInbox();
        }, 250);
      });
    },

    markRead: async (id, force) => {
      if (!readSupported || !id) return;
      const sess = get().list.find((s) => s.id === id) || get().states[id]?.session;
      const pending = !!sess && (sess.unread === true || (!!sess.attention && !isBlocking(sess.attention)));
      if (!pending && !force) return;
      const now = Date.now();
      if (now - (readAt.get(id) || 0) < 800) return; // activity arrives in bursts
      readAt.set(id, now);
      applyRead(id);
      try {
        await sessionsApi.read(id);
      } catch (e) {
        if (isMissing(e)) readSupported = false;
      }
    },

    markAllRead: async () => {
      const ids = new Set([...get().list, ...(get().inboxList || [])].filter((s) => s.unread || (s.attention && !isBlocking(s.attention))).map((s) => s.id));
      for (const item of get().inbox.items) if (!isBlocking(item.attention)) ids.add(item.session_id);
      await Promise.all([...ids].map((id) => {
        readAt.delete(id);
        return get().markRead(id, true);
      }));
      void get().refreshInbox();
    },

    setPendingBranch: (branch) => set({ pendingBranch: branch }),

    refresh: async () => {
      set({ loadingList: true });
      try {
        if (!defaultModeAsked) {
          // first run: a new chat starts in the harness' configured default mode
          defaultModeAsked = true;
          void harnessApi.getSettings().then((s) => !get().activeId && set((st) => ({ draft: { ...st.draft, mode: s.default_mode } }))).catch(() => {});
        }
        const all = await sessionsApi.list();
        const list = all.filter((s) => !s.parent_id).sort(byRecency);
        const { activeId } = get();
        const stillThere = !activeId || list.some((s) => s.id === activeId);
        set({ list, available: true, loadingList: false, activeId: stillThere ? activeId : null });
        if (activeId && stillThere) watch(activeId);
      } catch (e) {
        set({ loadingList: false, available: isMissing(e) || (e instanceof ApiError && e.status === 0) ? false : get().available ?? false });
      }
    },

    select: (id) => {
      const prev = get().activeId;
      if (prev && prev !== id && (get().states[prev]?.status || "idle") === "idle") unwatch(prev);
      write(ACTIVE_KEY, id);
      set({ activeId: id, pendingBranch: null });
      if (id) {
        const sess = get().list.find((s) => s.id === id);
        if (sess) set((st) => ({ draft: { mode: sess.mode || st.draft.mode, model: { provider: sess.model?.provider ?? null, model: sess.model?.model ?? null, effort: (sess.model as any)?.effort ?? null } } }));
        watch(id);
      }
    },

    newChat: () => get().select(null),

    send: async (text, attachments) => {
      const body = text.trim();
      if (!body || get().sending) return false;
      set({ sending: true });
      let id = get().activeId;
      try {
        if (!id) {
          const { draft } = get();
          const created = await sessionsApi.create({
            project_id: useStore.getState().agentProjectId,
            mode: draft.mode,
            provider: draft.model.provider,
            model: draft.model.model,
            effort: draft.model.effort || null,
            branch: get().pendingBranch,
          });
          id = created.id;
          // a thread on another branch checks that branch out in the workspace
          if (get().pendingBranch) window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
          set((st) => ({
            available: true,
            list: [created, ...st.list.filter((s) => s.id !== created.id)],
            states: { ...st.states, [created.id]: { ...EMPTY, session: created, loaded: true } },
          }));
          get().select(id);
        }
        const sid = id;
        const localId = `local-${Date.now()}`;
        patchState(sid, (s) => ({
          ...s,
          error: null,
          status: "running",
          messages: [...s.messages, { id: localId, type: "user", text: body, attachments, pending: true }],
        }));
        watch(sid);
        try {
          const res = await sessionsApi.send(sid, body, attachments);
          patchState(sid, (s) => {
            // the response is authoritative: swap the echo for the stored message (the stream may also deliver it)
            let next = { ...s, messages: s.messages.filter((m) => m.id !== localId) };
            if (res?.message) next = applySessionEvent(next, "message.created", res.message) as SessionState;
            if (res?.note) next = applySessionEvent(next, "message.created", res.note) as SessionState;
            if (res?.session) {
              // status and pending prompts belong to the event stream: a short turn may already be over by now
              const { status: _status, pending_approvals: _pa, pending_input: _pi, messages: _m, ...summary } = res.session as Record<string, any>;
              next = applySessionEvent({ ...next, status: "running" }, "session.updated", summary) as SessionState;
              next = { ...next, status: s.status };
            }
            // a local slash command (/help, /mode, /clear…) starts no turn
            if (res && res.started === false) next = { ...next, status: res.session?.status || "idle" };
            return next;
          });
          if (res?.session) {
            syncSummary(sid);
            const mode = res.session.mode;
            if (mode && mode !== get().draft.mode) set((st) => ({ draft: { ...st.draft, mode } }));
            const model = res.session.model;
            if (res.command === "model" && model) set((st) => ({ draft: { ...st.draft, model: { provider: model.provider ?? null, model: model.model ?? null, effort: st.draft.model.effort ?? null } } }));
          }
          const missing = (res?.message?.attachments || []).filter((a) => a.ok === false);
          if (missing.length) {
            const names = missing.map((a) => a.label || a.ref).join(", ");
            useStore.getState().notify(isZh() ? `无法附加：${names}` : `Could not attach: ${names}`);
          }
          if (res?.command === "compact" && res.started === false) reportCompaction(res);
        } catch (e) {
          patchState(sid, (s) => ({
            ...s,
            status: e instanceof ApiError && e.status === 409 ? s.status : "idle",
            messages: s.messages.filter((m) => m.id !== localId),
            error: e instanceof ApiError && e.status === 409 ? "busy" : String((e as any)?.message || e),
          }));
          return false;
        }
        return true;
      } catch (e) {
        if (isMissing(e)) set({ available: false });
        else fail(e);
        return false;
      } finally {
        set({ sending: false });
      }
    },

    interrupt: async () => {
      const id = get().activeId;
      if (!id) return;
      try {
        await sessionsApi.interrupt(id);
      } catch (e) {
        fail(e);
      }
    },

    approve: async (toolCallId, decision, feedback) => {
      const id = get().activeId;
      if (!id) return;
      try {
        await sessionsApi.approve(id, toolCallId, decision, feedback);
        patchState(id, (s) => ({ ...s, approvals: s.approvals.filter((a) => a.tool_call_id !== toolCallId) }));
      } catch (e) {
        fail(e);
      }
    },

    answer: async (text, toolCallId) => {
      const id = get().activeId;
      if (!id || !text.trim()) return;
      try {
        await sessionsApi.input(id, text.trim(), toolCallId);
        patchState(id, (s) => ({ ...s, input: null }));
      } catch (e) {
        fail(e);
      }
    },

    setMode: async (mode) => {
      const draft = { ...get().draft, mode };
      write(DRAFT_KEY, draft);
      set({ draft });
      const id = get().activeId;
      if (!id) return;
      try {
        await sessionsApi.patch(id, { mode });
        patchState(id, (s) => (s.session ? { ...s, session: { ...s.session, mode } } : s));
        syncSummary(id);
      } catch (e) {
        fail(e);
      }
    },

    setModel: async (model) => {
      const draft = { ...get().draft, model };
      write(DRAFT_KEY, draft);
      set({ draft });
      const id = get().activeId;
      if (!id) return;
      try {
        await sessionsApi.patch(id, { model });
        patchState(id, (s) => (s.session ? { ...s, session: { ...s.session, model } } : s));
        syncSummary(id);
      } catch (e) {
        fail(e);
      }
    },

    rename: async (id, title) => {
      const t = title.trim();
      if (!t) return;
      try {
        await sessionsApi.patch(id, { title: t });
        set((st) => ({
          list: st.list.map((s) => (s.id === id ? { ...s, title: t } : s)),
          inboxList: st.inboxList ? st.inboxList.map((s) => (s.id === id ? { ...s, title: t } : s)) : st.inboxList,
        }));
        patchState(id, (s) => (s.session ? { ...s, session: { ...s.session, title: t } } : s));
      } catch (e) {
        fail(e);
      }
    },

    archive: async (id, archived) => {
      try {
        await sessionsApi.patch(id, { archived });
        if (archived && get().activeId === id) { unwatch(id); get().select(null); }
        await get().refresh();
        await get().refreshInbox();
      } catch (e) { fail(e); throw e; }
    },

    moveProject: async (id, projectId) => {
      try {
        const session = await sessionsApi.moveProject(id, projectId);
        patchState(id, (s) => ({ ...s, session }));
        if (get().activeId === id) useStore.getState().setAgentProjectId(projectId);
        await get().refresh();
        await get().refreshInbox();
      } catch (e) { fail(e); throw e; }
    },

    remove: async (id) => {
      try {
        await sessionsApi.remove(id);
        unwatch(id);
        if (get().activeId === id) get().select(null);
        set((st) => {
          const states = { ...st.states };
          delete states[id];
          return { list: st.list.filter((s) => s.id !== id), inboxList: st.inboxList ? st.inboxList.filter((s) => s.id !== id) : st.inboxList, states };
        });
      } catch (e) {
        fail(e);
      }
    },

    fork: async (messageId, prefill, sessionId) => {
      const id = sessionId || get().activeId;
      if (!id) return;
      try {
        const forked = await sessionsApi.fork(id, messageId);
        set((st) => ({ list: [forked, ...st.list.filter((s) => s.id !== forked.id)] }));
        get().select(forked.id);
        if (prefill) set({ prefill: { ...prefill, id: Date.now() } });
        useStore.getState().notify(isZh() ? "已分叉出新对话" : "Forked into a new chat");
      } catch (e) {
        fail(e);
      }
    },

    rewind: async (messageId, { include, restoreFiles, prefill }) => {
      const id = get().activeId;
      if (!id) return;
      try {
        const res = await sessionsApi.rewind(id, messageId, { include, restoreFiles });
        if (res?.session) patchState(id, (s) => applySessionEvent(s, "session.updated", res.session) as SessionState);
        syncSummary(id);
        if (prefill) set({ prefill: { ...prefill, id: Date.now() } });
        if (res?.reverted?.length) window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
        const zh = isZh();
        const files = res?.reverted?.length ? (zh ? `，已撤销 ${res.reverted.length} 个文件的修改` : `; reverted ${res.reverted.length} file(s)`) : "";
        const conflicts = res?.conflicts?.length ? (zh ? `（${res.conflicts.length} 个文件之后又被改过，已覆盖）` : ` (${res.conflicts.length} had later edits and were overwritten)`) : "";
        useStore.getState().notify((zh ? "已回退到这条消息" : "Rewound to this message") + files + conflicts);
      } catch (e) {
        fail(e);
      }
    },

    compact: async (focus) => {
      const id = get().activeId;
      if (!id) return;
      patchState(id, (s) => ({ ...s, status: "running" }));
      try {
        const res = await sessionsApi.compact(id, focus);
        if (res?.session) patchState(id, (s) => applySessionEvent(s, "session.updated", res.session) as SessionState);
        reportCompaction(res);
      } catch (e) {
        // 409 = a turn is running: leave its status alone
        if (!(e instanceof ApiError && e.status === 409)) patchState(id, (s) => ({ ...s, status: "idle" }));
        fail(e);
      }
    },

    compose: (p) => {
      useStore.getState().setLayout({ aiOpen: true, aiMode: "agent" });
      set({ prefill: { ...p, id: Date.now() } });
    },

    clearError: () => {
      const id = get().activeId;
      if (id) patchState(id, (s) => (s.error ? { ...s, error: null } : s));
    },
  };
});

function reportCompaction(res: { compacted?: boolean; reason?: string; tokens_before?: number; tokens_after?: number; [k: string]: any } | undefined) {
  if (!res) return;
  const k = (n?: number) => (n == null ? "?" : n >= 1000 ? `${Math.round(n / 100) / 10}k` : String(n));
  const zh = isZh();
  useStore.getState().notify(
    res.compacted
      ? zh ? `上下文已压缩：${k(res.tokens_before)} → ${k(res.tokens_after)} tokens` : `Context compacted: ${k(res.tokens_before)} → ${k(res.tokens_after)} tokens`
      : zh ? `没有可压缩的内容${res.reason ? `（${res.reason}）` : ""}` : `Nothing to compact${res.reason ? ` (${res.reason})` : ""}`,
  );
}

export const useActiveSession = (): SessionState => useSessions((s) => (s.activeId && s.states[s.activeId]) || EMPTY);
