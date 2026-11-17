// Side bar: threads grouped by project, and the inbox (docs/glossx-workspaces.md §1).
import { useEffect, useMemo, useState } from "react";
import {
  Archive, ArchiveRestore, Bell, BookOpen, CheckCheck, ChevronRight, CircleAlert, CircleCheck, FolderKanban, GitBranch, GitFork, Inbox, MessageCircleQuestion,
  MessagesSquare, Pencil, Search, ShieldQuestion, SquarePen, Trash2, type LucideIcon,
} from "lucide-react";
import { isBlocking, sessionsApi, type Attention, type SessionSummary } from "../../api/harness";
import { useStore } from "../../store";
import { activityOf, useSessions } from "../../sessionStore";
import { useBranch, useVcs } from "../../vcsStore";
import { relativeTime } from "../session/toolMeta";
import { EmptyState, MenuButton, MenuItem, MenuSep, Spinner } from "../ui";
import { ask } from "./dialog";

const ATTENTION: Record<Exclude<Attention, null>, { icon: LucideIcon; tone: string; en: string; zh: string }> = {
  approval: { icon: ShieldQuestion, tone: "warn", en: "Needs approval", zh: "待批准" },
  input: { icon: MessageCircleQuestion, tone: "warn", en: "Needs answer", zh: "待回答" },
  done: { icon: CircleCheck, tone: "ok", en: "Finished", zh: "已完成" },
  error: { icon: CircleAlert, tone: "bad", en: "Failed", zh: "失败" },
};

/** What a thread wants from the user. Falls back to its live status on backends without `attention`. */
export function attentionOf(s: Pick<SessionSummary, "attention" | "status">): Exclude<Attention, null> | null {
  if (s.attention && ATTENTION[s.attention]) return s.attention;
  if (s.status === "awaiting_approval") return "approval";
  if (s.status === "awaiting_input") return "input";
  return null;
}

export function AttentionChip({ kind, zh, compact }: { kind: Exclude<Attention, null>; zh: boolean; compact?: boolean }) {
  const a = ATTENTION[kind];
  const I = a.icon;
  return (
    <span className={`gx-tag gx-att ${a.tone}${compact ? " compact" : ""}`} title={zh ? a.zh : a.en}>
      <I size={11} />
      {!compact && <span>{zh ? a.zh : a.en}</span>}
    </span>
  );
}

export function BranchChip({ name, title }: { name: string; title?: string }) {
  return <span className="gx-branch" title={title || name}><GitBranch size={10} /><span>{name}</span></span>;
}

/** Bell with the unread count; red when a thread is blocked on the user. */
export function InboxBell({ size = 15 }: { size?: number }) {
  const unread = useSessions((s) => s.inbox.unread);
  const blocking = useSessions((s) => s.inbox.blocking);
  const n = Math.max(unread, blocking);
  return (
    <span className="gx-bell">
      <Bell size={size} />
      {n > 0 && <i className={blocking > 0 ? "blocking" : ""}>{n > 99 ? "99+" : n}</i>}
    </span>
  );
}

export const inboxTitle = (zh: boolean, unread: number, blocking: number) =>
  zh
    ? `收件箱${unread ? ` · ${unread} 个未读` : ""}${blocking ? ` · ${blocking} 个在等你` : ""}`
    : `Inbox${unread ? ` · ${unread} unread` : ""}${blocking ? ` · ${blocking} waiting for you` : ""}`;

async function removeThread(s: SessionSummary, zh: boolean) {
  const ok = await ask({
    title: zh ? "删除这个对话？" : "Delete this thread?",
    body: zh ? `「${s.title || "新对话"}」及其全部消息会被删除。工作区里的文件和提交不受影响。` : `“${s.title || "New thread"}” and all of its messages will be deleted. Files and commits in the workspace are kept.`,
    actions: [{ id: "delete", label: zh ? "删除" : "Delete", danger: true }],
    cancel: zh ? "取消" : "Cancel",
  });
  if (ok) await useSessions.getState().remove(s.id);
}

function ThreadRow({ s, zh, inbox, groupBranch, onChange }: { s: SessionSummary; zh: boolean; inbox?: boolean; groupBranch?: string | null; onChange?: () => void }) {
  const active = useSessions((st) => st.activeId === s.id);
  const openThread = useSessions((st) => st.openThread);
  const rename = useSessions((st) => st.rename);
  const fork = useSessions((st) => st.fork);
  const markRead = useSessions((st) => st.markRead);
  const projects = useStore((st) => st.projects);
  const [moving, setMoving] = useState(false);
  const [destination, setDestination] = useState("");
  const [movingBusy, setMovingBusy] = useState(false);
  const [moveError, setMoveError] = useState("");
  const projectName = useStore((st) => s.project?.name || st.projects.find((p) => p.id === s.project_id)?.name || "");
  const [editing, setEditing] = useState<string | null>(null);
  const att = attentionOf(s);
  const running = s.status === "running";
  const title = s.title || (zh ? "新对话" : "New thread");
  // a branch chip is noise when the thread simply ran on the branch its workspace is on
  const branch = s.branch && (inbox || s.branch !== groupBranch) ? s.branch : null;

  return (
    <div className={`gx-th-row${active ? " active" : ""}${s.unread ? " unread" : ""}${inbox ? " inbox" : ""}`}>
      {editing !== null ? (
        <input
          autoFocus
          className="gx-input gx-th-rename"
          value={editing}
          onChange={(e) => setEditing(e.target.value)}
          onFocus={(e) => e.target.select()}
          onBlur={() => {
            if (editing.trim() && editing.trim() !== s.title) void rename(s.id, editing);
            setEditing(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") setEditing(null);
          }}
        />
      ) : (
        <button type="button" className="gx-th-main" title={s.preview ? `${title}\n\n${s.preview}` : title} onClick={() => { if (s.archived) useStore.getState().notify(zh ? "恢复对话后可打开" : "Restore the thread to open it"); else openThread(s.id); }}>
          <span className="gx-th-lead">
            {running ? <Spinner size={12} /> : s.unread ? <i className="gx-unread" aria-label={zh ? "未读" : "Unread"} /> : null}
          </span>
          <span className="gx-th-body">
            <span className="gx-th-line">
              <span className="gx-th-title">{title}</span>
              {!inbox && branch && <BranchChip name={branch} />}
              {!inbox && att && <AttentionChip kind={att} zh={zh} compact={att === "done"} />}
              <span className="gx-th-time">{relativeTime(activityOf(s), zh)}</span>
            </span>
            {inbox && (s.preview || s.last_message) && <span className="gx-th-preview">{s.preview || s.last_message}</span>}
            {inbox && (att || projectName || branch) && (
              <span className="gx-th-meta">
                {att && <AttentionChip kind={att} zh={zh} />}
                {projectName && <span className="gx-th-project">{projectName}</span>}
                {branch && <BranchChip name={branch} />}
              </span>
            )}
          </span>
        </button>
      )}
      {editing === null && (
        <MenuButton className="gx-icon-btn gx-th-more" chevron={false} placement="bottom-end" width={200} label={<span className="gx-ellipsis">⋯</span>} title={zh ? "更多" : "More"}>
          {(close) => (
            <>
              <MenuItem icon={<Pencil size={14} />} label={zh ? "重命名" : "Rename"} onSelect={() => { close(); setEditing(title); }} />
              <MenuItem icon={<FolderKanban size={14} />} label={zh ? "移动到项目…" : "Move to project…"} disabled={s.status !== "idle"} onSelect={() => { close(); setDestination(projects.some((p) => p.id === s.project_id) ? s.project_id! : ""); setMoveError(""); setMoving(true); }} />
              <MenuItem icon={<GitFork size={14} />} label={zh ? "分叉为新对话" : "Fork into a new thread"} disabled={s.archived || s.status !== "idle"} onSelect={() => { close(); void fork(null, undefined, s.id); }} />
              {(s.unread || s.attention === "done" || s.attention === "error") && (
                <MenuItem icon={<CheckCheck size={14} />} label={zh ? "标为已读" : "Mark as read"} onSelect={() => { close(); void markRead(s.id, true); }} />
              )}
              <MenuSep />
              <MenuItem icon={s.archived ? <ArchiveRestore size={14} /> : <Archive size={14} />} disabled={s.status !== "idle"} label={s.archived ? (zh ? "恢复对话" : "Restore thread") : (zh ? "归档对话" : "Archive thread")} onSelect={() => { close(); void useSessions.getState().archive(s.id, !s.archived).then(onChange).catch(() => {}); }} />
              {s.archived && <MenuItem icon={<Trash2 size={14} />} danger label={zh ? "永久删除" : "Delete permanently"} onSelect={() => { close(); void removeThread(s, zh).then(onChange); }} />}
            </>
          )}
        </MenuButton>
      )}
      {moving && <div className="gx-th-move">
        <select aria-label={zh ? "目标项目" : "Destination project"} value={destination} disabled={movingBusy} onChange={(e) => setDestination(e.target.value)}>
          <option value="">{zh ? "通用（不属于项目）" : "General (no project)"}</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.hub_category ? `${p.hub_category} · ${zh ? "整个方向" : "Whole direction"}` : p.name}</option>)}
        </select>
        <small>{zh ? "消息保留；后续使用目标工作区，原文件不会搬动。" : "Messages stay; future tools use the destination workspace. Existing files stay where they are."}</small>
        {moveError && <small role="alert" className="error">{moveError}</small>}
        <div><button className="gx-btn sm" disabled={movingBusy || destination === (s.project_id || "")} onClick={() => {
          setMovingBusy(true); setMoveError("");
          void useSessions.getState().moveProject(s.id, destination || null).then(() => { setMoving(false); onChange?.(); }).catch((e) => setMoveError(String(e?.message || e))).finally(() => setMovingBusy(false));
        }}>{zh ? "移动" : "Move"}</button><button className="gx-btn ghost sm" disabled={movingBusy} onClick={() => setMoving(false)}>{zh ? "取消" : "Cancel"}</button></div>
      </div>}
    </div>
  );
}

const GROUP_CAP = 6;
const COLLAPSED_KEY = "glossx.threadGroups";

function readCollapsed(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]");
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

type Group = { id: string | null; name: string; kind?: string; branch: string | null; items: SessionSummary[]; latest: number };

function ProjectGroup({ g, zh, open, onToggle, filtering }: { g: Group; zh: boolean; open: boolean; onToggle: () => void; filtering: boolean }) {
  const isActive = useStore((s) => (s.agentProjectId || null) === g.id);
  const newThread = useSessions((s) => s.newThread);
  const liveBranch = useBranch(isActive ? g.id : "__none__");
  const branch = (isActive ? liveBranch : null) || g.branch;
  const [all, setAll] = useState(false);
  const shown = all || filtering || g.items.length <= GROUP_CAP + 1 ? g.items : g.items.slice(0, GROUP_CAP);
  const waiting = g.items.filter((s) => isBlocking(attentionOf(s))).length;
  const unread = g.items.filter((s) => s.unread).length;
  const I = g.id === null ? MessagesSquare : g.kind === "paper" ? BookOpen : FolderKanban;
  return (
    <div className={`gx-th-group${open ? " open" : ""}${isActive ? " current" : ""}`}>
      <div className="gx-th-group-head">
        <button type="button" className="gx-th-group-toggle" aria-expanded={open} title={g.name} onClick={onToggle}>
          <ChevronRight size={12} className="gx-tree-caret" />
          <I size={13} strokeWidth={1.7} className="gx-th-group-icon" />
          <span className="gx-th-group-name">{g.name}</span>
          {branch && <BranchChip name={branch} title={zh ? `工作区分支：${branch}` : `Workspace branch: ${branch}`} />}
          {!open && (waiting > 0 || unread > 0) && <i className={`gx-th-group-dot${waiting ? " blocking" : ""}`} />}
        </button>
        <button
          type="button"
          className="gx-icon-btn gx-th-new"
          title={zh ? `在「${g.name}」中新建对话` : `New thread in ${g.name}`}
          aria-label={zh ? "新建对话" : "New thread"}
          onClick={() => newThread({ projectId: g.id })}
        >
          <SquarePen size={13} />
        </button>
      </div>
      {open && (
        <div className="gx-th-list">
          {g.items.length === 0 && <div className="gx-th-none">{zh ? "还没有对话" : "No threads yet"}</div>}
          {shown.map((s) => <ThreadRow key={s.id} s={s} zh={zh} groupBranch={branch} />)}
          {shown.length < g.items.length && (
            <button type="button" className="gx-link gx-th-all" onClick={() => setAll(true)}>
              {zh ? `显示其余 ${g.items.length - shown.length} 个` : `Show ${g.items.length - shown.length} more`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Actions in the side bar's title row: the inbox bell and "new thread". */
export function ThreadsHeadActions() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const view = useSessions((s) => s.sideView);
  const setView = useSessions((s) => s.setSideView);
  const unread = useSessions((s) => s.inbox.unread);
  const blocking = useSessions((s) => s.inbox.blocking);
  const newThread = useSessions((s) => s.newThread);
  return (
    <>
      <button type="button" className={`gx-icon-btn${view === "archive" ? " open" : ""}`} aria-pressed={view === "archive"} title={zh ? "归档对话" : "Archived threads"} onClick={() => setView(view === "archive" ? "threads" : "archive")}><Archive size={14} /></button>
      <button
        type="button"
        className={`gx-icon-btn gx-bell-btn${view === "inbox" ? " open" : ""}`}
        aria-pressed={view === "inbox"}
        title={view === "inbox" ? (zh ? "返回按项目分组的对话" : "Back to threads by project") : inboxTitle(zh, unread, blocking)}
        onClick={() => setView(view === "inbox" ? "threads" : "inbox")}
      >
        <InboxBell />
      </button>
      <button type="button" className="gx-icon-btn" title={zh ? "新对话" : "New thread"} onClick={() => newThread()}>
        <SquarePen size={14} />
      </button>
    </>
  );
}

export function ThreadsView() {
  const [archived, setArchived] = useState<SessionSummary[]>([]);
  const [archiveError, setArchiveError] = useState("");
  const loadArchive = () => { setArchiveError(""); void sessionsApi.archived().then(setArchived).catch((e) => setArchiveError(String(e?.message || e))); };
  const zh = useStore((s) => s.uiLang) === "zh";
  const projects = useStore((s) => s.projects);
  const activeProject = useStore((s) => s.agentProjectId || null);
  const refreshProjects = useStore((s) => s.refreshProjects);
  const list = useSessions((s) => s.list);
  const inboxList = useSessions((s) => s.inboxList);
  const view = useSessions((s) => s.sideView);
  const setView = useSessions((s) => s.setSideView);
  const available = useSessions((s) => s.available);
  const loading = useSessions((s) => s.loadingList);
  const unread = useSessions((s) => s.inbox.unread);
  const blocking = useSessions((s) => s.inbox.blocking);
  const markAllRead = useSessions((s) => s.markAllRead);
  const newThread = useSessions((s) => s.newThread);
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(readCollapsed()));

  useEffect(() => {
    void useSessions.getState().refresh();
    void refreshProjects();
  }, []);
  useEffect(() => {
    if (view === "inbox") void useSessions.getState().refreshInbox();
    if (view === "archive") loadArchive();
  }, [view, list]);
  // the active project's branch is shown live on its group
  useEffect(() => {
    useVcs.getState().ensure(activeProject, 15000);
  }, [activeProject]);

  const needle = q.trim().toLowerCase();
  const match = (s: SessionSummary) => !needle || `${s.title} ${s.preview || s.last_message || ""} ${s.project?.name || ""} ${s.branch || ""}`.toLowerCase().includes(needle);

  const groups = useMemo(() => {
    const byId = new Map<string | null, Group>();
    const ensure = (id: string | null, name?: string): Group => {
      let g = byId.get(id);
      if (!g) {
        const p = id ? projects.find((x) => x.id === id) : undefined;
        g = { id, name: id === null ? (zh ? "通用" : "General") : p?.name || name || id, kind: p?.kind, branch: p?.workspace?.vcs?.branch || null, items: [], latest: 0 };
        byId.set(id, g);
      }
      return g;
    };
    for (const s of list) {
      if (!match(s)) continue;
      const g = ensure(s.project_id || null, s.project?.name);
      g.items.push(s);
      g.latest = Math.max(g.latest, activityOf(s));
    }
    if (!needle) ensure(activeProject); // the active project is always there, so a thread can be started in it
    for (const g of byId.values()) g.items.sort((a, b) => activityOf(b) - activityOf(a));
    return [...byId.values()].sort((a, b) => Number(b.id === activeProject) - Number(a.id === activeProject) || Number(a.id === null) - Number(b.id === null) || b.latest - a.latest);
  }, [list, projects, activeProject, needle, zh]);

  const toggle = (key: string) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (!next.delete(key)) next.add(key);
    try {
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]));
    } catch {
      /* ignore */
    }
    return next;
  });

  const inboxItems = (inboxList || []).filter(match);
  const canMarkAll = unread > 0 || (inboxList || list).some((s) => s.unread || s.attention === "done" || s.attention === "error");
  const waiting = inboxItems.filter((s) => isBlocking(attentionOf(s)));
  const fresh = inboxItems.filter((s) => !isBlocking(attentionOf(s)) && s.unread);
  const rest = inboxItems.filter((s) => !isBlocking(attentionOf(s)) && !s.unread);
  const sections: [string, SessionSummary[]][] = [
    [zh ? "在等你" : "Waiting for you", waiting],
    [zh ? "未读" : "Unread", fresh],
    [zh ? "更早" : "Earlier", rest],
  ];

  return (
    <div className="wb-side-body gx-threads">
      <label className="wb-filter">
        <Search size={13} />
        <input placeholder={zh ? "筛选对话…" : "Filter threads…"} value={q} onChange={(e) => setQ(e.target.value)} />
        {loading && <Spinner size={11} />}
      </label>
      {view === "inbox" && (
        <div className="gx-inbox-bar">
          <Inbox size={13} />
          <strong>{zh ? "收件箱" : "Inbox"}</strong>
          <span>{unread > 0 ? (zh ? `${unread} 个未读` : `${unread} unread`) : zh ? "全部已读" : "All caught up"}{blocking > 0 ? (zh ? ` · ${blocking} 个在等你` : ` · ${blocking} waiting`) : ""}</span>
          <span className="spacer" />
          <button type="button" className="gx-link" disabled={!canMarkAll} onClick={() => void markAllRead()}><CheckCheck size={12} />{zh ? "全部标为已读" : "Mark all read"}</button>
        </div>
      )}
      <div className="gx-th-scroll">
        {view === "archive" ? <>
          <div className="gx-menu-heading">{zh ? "归档对话" : "Archived threads"}<i>{archived.length}</i></div>
          {archiveError && <div className="error" role="alert">{archiveError}</div>}
          {archived.filter(match).map((s) => <ThreadRow key={s.id} s={s} zh={zh} inbox onChange={loadArchive} />)}
          {!archived.length && !archiveError && <div className="muted wb-empty">{zh ? "暂无归档对话" : "No archived threads"}</div>}
        </> : available === false ? (
          <EmptyState icon={<MessagesSquare size={20} />} title={zh ? "会话服务不可用" : "Threads are unavailable"} hint={zh ? "当前后端尚未提供 /api/sessions。" : "This backend does not expose /api/sessions yet."} />
        ) : view === "inbox" ? (
          inboxList === null ? (
            <div className="gx-chat-loading"><div className="gx-skel w75" /><div className="gx-skel w60" /><div className="gx-skel w90" /></div>
          ) : inboxItems.length === 0 ? (
            <EmptyState
              icon={<Inbox size={20} />}
              title={needle ? (zh ? "没有匹配的对话" : "No matching threads") : zh ? "收件箱是空的" : "Nothing in the inbox"}
              hint={needle ? undefined : zh ? "智能体需要你批准、提问或完成任务时，对话会出现在这里。" : "Threads land here when the agent needs an approval or an answer, or finishes its work."}
              action={needle ? undefined : <button type="button" className="gx-btn sm" onClick={() => setView("threads")}>{zh ? "查看全部对话" : "Show all threads"}</button>}
            />
          ) : (
            sections.filter(([, items]) => items.length).map(([label, items]) => (
              <div key={label} className="gx-inbox-section">
                <div className="gx-menu-heading">{label}<i>{items.length}</i></div>
                {items.map((s) => <ThreadRow key={s.id} s={s} zh={zh} inbox />)}
              </div>
            ))
          )
        ) : list.length === 0 && !loading ? (
          <EmptyState
            icon={<MessagesSquare size={20} />}
            title={zh ? "还没有对话" : "No threads yet"}
            hint={zh ? "每个对话都属于一个项目，并在该项目的工作区里工作。" : "Every thread belongs to a project and works in that project’s workspace."}
            action={<button type="button" className="gx-btn sm" onClick={() => newThread()}><SquarePen size={12} /> {zh ? "新对话" : "New thread"}</button>}
          />
        ) : groups.length === 0 || (needle && groups.every((g) => g.items.length === 0)) ? (
          <div className="muted wb-empty">{zh ? "没有匹配的对话" : "No matching threads"}</div>
        ) : (
          groups.map((g) => {
            const key = g.id ?? "__general";
            return <ProjectGroup key={key} g={g} zh={zh} filtering={!!needle} open={!!needle || !collapsed.has(key)} onToggle={() => toggle(key)} />;
          })
        )}
      </div>
    </div>
  );
}
