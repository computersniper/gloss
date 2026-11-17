import { useEffect, useMemo, useRef, useState } from "react";
import { useStore, type Activity } from "../../store";
import { useLibraryImport } from "../Library";
import { ImportQueue } from "../ImportQueue";
import { ScholarPanel } from "../ScholarPanel";
import { SettingsPanel } from "../SettingsPanel";
import { ExtensionsPanel } from "../ExtensionsPanel";
import { CapabilityGroups, ProjectDetail } from "../AgentWorkspace";
import { Archive, ArchiveRestore, BookOpen, BookOpenText, ChevronRight, FolderKanban, FolderTree, GitBranch, Layers, MessageSquarePlus, MessagesSquare, Pencil, Plus, Search, SquarePen, Tag, Trash2, FileText, X } from "lucide-react";
import { agentApi } from "../../api/agent";
import { HarnessSettings } from "../HarnessSettings";
import { EmptyState, MenuButton, MenuItem, MenuLabel, MenuSep, Segmented } from "../ui";
import { useSessions } from "../../sessionStore";
import { WorkspaceView } from "./WorkspaceView";
import { ScmHeadActions, ScmView } from "./ScmView";
import { ask } from "./dialog";
import { Icon } from "./icons";
import { MobileAccess } from "../MobileAccess";
import { WorkspaceLocation } from "./WorkspaceLocation";
import { openWorkspaceFolder } from "./openFolder";

export const ACTIVITIES: { id: Activity; icon: string; en: string; zh: string }[] = [
  { id: "library", icon: "library", en: "Library", zh: "论文库" },
  { id: "workspace", icon: "workspace", en: "Workspace", zh: "工作区" },
  { id: "scm", icon: "scm", en: "Source control", zh: "源代码管理" },
  { id: "projects", icon: "projects", en: "Projects", zh: "项目" },
  { id: "search", icon: "search", en: "Search (Scholar)", zh: "学术搜索" },
  { id: "mobile", icon: "phone", en: "Mobile access", zh: "移动端访问" },
  { id: "capabilities", icon: "plugins", en: "Capabilities & Plugins", zh: "能力与插件" },
  { id: "settings", icon: "settings", en: "Settings", zh: "设置" },
];

function LibraryView() {
  const papers = useStore((s) => s.papers);
  const openPaper = useStore((s) => s.openPaper);
  const askAgent = useStore((s) => s.askAgent);
  const activeTabKey = useStore((s) => s.activeTabKey);
  const openTabs = useStore((s) => s.openTabs);
  const notify = useStore((s) => s.notify);
  const zh = useStore((s) => s.uiLang) === "zh";
  const { importQ, setImportQ, uploadBusy, creatingImport, fileRef, onUpload, onImport, deletePaper } = useLibraryImport();
  const [filter, setFilter] = useState("");
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const projects = useStore((s) => s.projects);
  const [direction, setDirection] = useState<string | null>(null);
  useEffect(() => { if (!projects.length) void useStore.getState().refreshProjects(); }, []);
  // a paper's directions: those of the (non-hub) projects that hold it
  const directions = useMemo(() => {
    const byPaper = new Map<string, Set<string>>();
    for (const project of projects) {
      if (!project.category || project.hub_category) continue;
      for (const id of project.paper_ids || []) {
        if (!byPaper.has(id)) byPaper.set(id, new Set());
        byPaper.get(id)!.add(project.category);
      }
    }
    return byPaper;
  }, [projects]);
  const allDirections = useMemo(() => [...new Set([...directions.values()].flatMap((d) => [...d]))].sort((a, b) => a.localeCompare(b)), [directions]);
  const T = zh
    ? { filter: "筛选论文…", ph: "arXiv 编号 / DOI / 链接", imp: "导入", upload: "上传 PDF", empty: "还没有论文 — 导入 arXiv 编号或上传 PDF。", none: "没有匹配的论文", open: "打开", ask: "问智能体", ws: "打开工作区", thread: "就这篇论文新建对话", del: "删除", confirm: "删除这篇论文？", noWs: "当前后端还没有论文工作区。" }
    : { filter: "Filter papers…", ph: "arXiv id / DOI / URL", imp: "Import", upload: "Upload PDF", empty: "No papers yet — import an arXiv id or upload a PDF.", none: "No matching papers", open: "Open", ask: "Ask agent about it", ws: "Open workspace", thread: "New thread about this paper", del: "Delete", confirm: "Delete this paper?", noWs: "This backend has no paper workspaces yet." };

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("mousedown", close);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  const q = filter.trim().toLowerCase();
  const inDirection = direction === null ? papers
    : direction === "" ? papers.filter((p) => !directions.get(p.id)?.size)
    : papers.filter((p) => directions.get(p.id)?.has(direction));
  const shown = q
    ? inDirection.filter((p) =>
        [p.title, (p.authors || []).join(" "), p.year, p.arxiv_id, p.source].join(" ").toLowerCase().includes(q))
    : inDirection;
  const openIds = new Set(openTabs.filter((t) => t.kind === "paper").map((t) => t.id));

  async function act(kind: "open" | "ask" | "ws" | "thread" | "del", id: string) {
    setMenu(null);
    const paper = papers.find((p) => p.id === id);
    if (kind === "ws" || kind === "thread") {
      const st = useStore.getState();
      const project = await st.paperProject(id);
      if (kind === "ws") {
        if (!project) return notify(T.noWs);
        st.setAgentProjectId(project.id);
        st.setLayout({ activity: "workspace", sideOpen: true });
      } else {
        // the thread lives in the paper's project and starts with the paper attached
        useSessions.getState().newThread(project ? { projectId: project.id } : undefined);
        useSessions.getState().compose({ attachments: [{ kind: "paper", ref: id, label: paper?.title }] });
      }
      return;
    }
    if (kind === "open") void openPaper(id).catch(() => {});
    if (kind === "ask") useSessions.getState().compose({ attachments: [{ kind: "paper", ref: id, label: paper?.title }] });
    if (kind === "del" && confirm(`${T.confirm}\n${paper?.title || ""}`)) {
      await deletePaper(id).catch((e) => notify(String(e?.message || e)));
    }
  }

  return (
    <div className="wb-side-body">
      <div className="wb-import">
        <input
          className="wb-input"
          placeholder={T.ph}
          value={importQ}
          onChange={(e) => setImportQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void onImport()}
        />
        <button className="gx-btn sm" onClick={() => void onImport()} disabled={creatingImport || !importQ.trim()}>{T.imp}</button>
        <button className="gx-icon-btn bordered" title={T.upload} onClick={() => fileRef.current?.click()} disabled={uploadBusy}>
          <Icon name="upload" size={14} />
        </button>
        <input ref={fileRef} type="file" accept="application/pdf,.md,.markdown,.html,.htm,.pptx,.docx,.xlsx,.odt,.ods,.odp,.goodnotes" multiple hidden onChange={(e) => onUpload(e.target.files)} />
      </div>
      <ImportQueue />
      <label className="wb-filter">
        <Search size={13} />
        <input placeholder={T.filter} value={filter} onChange={(e) => setFilter(e.target.value)} />
        {papers.length > 0 && <span>{shown.length}</span>}
      </label>
      {allDirections.length > 0 && (
        <div className="gx-dir-chips" role="tablist" aria-label={zh ? "研究方向" : "Research directions"}>
          <button type="button" className={direction === null ? "on" : ""} onClick={() => setDirection(null)}>{zh ? "全部" : "All"}</button>
          {allDirections.map((d) => <button key={d} type="button" className={direction === d ? "on" : ""} title={d} onClick={() => setDirection(direction === d ? null : d)}>{d}</button>)}
          <button type="button" className={direction === "" ? "on" : ""} onClick={() => setDirection(direction === "" ? null : "")}>{zh ? "未分类" : "Unfiled"}</button>
        </div>
      )}
      <div className="wb-list">
        {papers.length === 0 && <EmptyState icon={<BookOpenText size={20} />} title={zh ? "论文库是空的" : "Your library is empty"} hint={T.empty} />}
        {papers.length > 0 && shown.length === 0 && <div className="muted wb-empty">{T.none}</div>}
        {shown.map((p) => (
          <div
            key={p.id}
            className={`wb-item${activeTabKey === `paper:${p.id}` ? " active" : ""}${openIds.has(p.id) ? " open" : ""}`}
            onClick={() => void act("open", p.id)}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu({ id: p.id, x: e.clientX, y: e.clientY });
            }}
            title={p.title}
          >
            <div className="wb-item-title">{p.title || "Untitled"}</div>
            <div className="wb-item-meta">
              {[(p.authors || []).slice(0, 2).join(", "), p.year, `${p.n_pages}p`].filter(Boolean).join(" · ")}
            </div>
            <button
              className="wb-item-more"
              onClick={(e) => {
                e.stopPropagation();
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setMenu({ id: p.id, x: r.left, y: r.bottom });
              }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              <Icon name="more" size={15} />
            </button>
          </div>
        ))}
      </div>
      {menu && (
        <div
          className="wb-menu"
          style={{ left: Math.min(menu.x, window.innerWidth - 250), top: Math.min(menu.y, window.innerHeight - 190) }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button onClick={() => void act("open", menu.id)}><FileText size={14} />{T.open}</button>
          <button onClick={() => void act("ws", menu.id)}><FolderTree size={14} />{T.ws}</button>
          <button onClick={() => void act("thread", menu.id)}><SquarePen size={14} />{T.thread}</button>
          <button onClick={() => void act("ask", menu.id)}><MessageSquarePlus size={14} />{T.ask}</button>
          <button className="danger" onClick={() => void act("del", menu.id)}><Trash2 size={14} />{T.del}</button>
        </div>
      )}
    </div>
  );
}

/** Papers linked to a project: open, remove, add from the library. */
function ProjectPapers({ projectId, paperIds, kind, zh }: { projectId: string; paperIds: string[]; kind?: string; zh: boolean }) {
  const papers = useStore((s) => s.papers);
  const openPaper = useStore((s) => s.openPaper);
  const refreshProjects = useStore((s) => s.refreshProjects);
  const notify = useStore((s) => s.notify);
  const linked = paperIds.map((id) => papers.find((p) => p.id === id) || { id, title: "" });
  const candidates = papers.filter((p) => !paperIds.includes(p.id));

  async function change(fn: () => Promise<unknown>) {
    try {
      await fn();
      await refreshProjects();
    } catch (e: any) {
      notify(String(e?.message || e));
    }
  }
  async function remove(id: string, title: string) {
    const ok = await ask({
      title: zh ? "从项目中移除这篇论文？" : "Remove this paper from the project?",
      body: zh ? `「${title || id}」会与项目解除关联。论文本身、项目里的笔记和文件都会保留。` : `“${title || id}” is unlinked from the project. The paper itself and the project’s notes and files are kept.`,
      actions: [{ id: "remove", label: zh ? "移除" : "Remove", primary: true }],
      cancel: zh ? "取消" : "Cancel",
    });
    if (ok) await change(() => agentApi.removeProjectPaper(projectId, id));
  }

  return (
    <div className="gx-proj-papers">
      <div className="gx-proj-h">
        <span>{zh ? "论文" : "Papers"}</span>
        <em className="gx-count">{linked.length}</em>
        <span className="spacer" />
        <MenuButton
          className="gx-icon-btn"
          chevron={false}
          placement="bottom-end"
          width={300}
          disabled={!candidates.length}
          title={candidates.length ? (zh ? "添加论文库中的论文" : "Add a paper from the library") : zh ? "论文库里的论文都已加入" : "Every paper in the library is already here"}
          label={<Plus size={14} />}
        >
          {(close) => candidates.slice(0, 60).map((p) => (
            <MenuItem
              key={p.id}
              icon={<FileText size={14} />}
              label={p.title || "Untitled"}
              detail={[(p.authors || []).slice(0, 2).join(", "), p.year].filter(Boolean).join(" · ") || undefined}
              onSelect={() => {
                close();
                void change(() => agentApi.addProjectPaper(projectId, p.id));
              }}
            />
          ))}
        </MenuButton>
      </div>
      {linked.length === 0 && (
        <div className="gx-proj-none">{kind === "paper" ? (zh ? "这篇论文已从论文库删除；笔记和文件仍保留在这个工作区。" : "Its paper was deleted from the library; the notes and files stay in this workspace.") : zh ? "还没有关联论文。添加后，这个项目里的对话默认以它们为范围。" : "No papers linked yet. Threads in this project are scoped to the papers you add."}</div>
      )}
      {linked.map((p) => (
        <div key={p.id} className={`gx-proj-paper${p.title ? "" : " missing"}`}>
          <button type="button" title={p.title || p.id} disabled={!p.title} onClick={() => void openPaper(p.id).catch(() => {})}>
            <FileText size={13} />
            <span>{p.title || (zh ? "（已从论文库删除）" : "(deleted from the library)")}</span>
          </button>
          <button type="button" className="gx-icon-btn" title={zh ? "从项目中移除" : "Remove from the project"} onClick={() => void remove(p.id, p.title || "")}><X size={12} /></button>
        </div>
      ))}
    </div>
  );
}

function ProjectDivider({ ratio, onChange, zh }: { ratio: number; onChange: (ratio: number) => void; zh: boolean }) {
  const drag = useRef<{ y: number; ratio: number; height: number } | null>(null);
  const clamp = (v: number) => Math.max(0.15, Math.min(0.85, v));
  return <div className="gx-project-sash" role="separator" aria-orientation="horizontal" tabIndex={0}
    aria-label={zh ? "调整项目列表与工作区设置高度" : "Resize projects and workspace settings"}
    aria-valuemin={15} aria-valuemax={85} aria-valuenow={Math.round(ratio * 100)}
    onPointerDown={(e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const list = e.currentTarget.previousElementSibling;
      const detail = e.currentTarget.nextElementSibling;
      drag.current = { y: e.clientY, ratio, height: (list?.getBoundingClientRect().height || 0) + (detail?.getBoundingClientRect().height || 0) };
      e.currentTarget.setPointerCapture(e.pointerId);
    }}
    onPointerMove={(e) => { const d = drag.current; if (d && d.height > 0) onChange(clamp(d.ratio + (e.clientY - d.y) / d.height)); }}
    onPointerUp={(e) => { drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }}
    onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={(e) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      onChange(e.key === "Home" ? 0.15 : e.key === "End" ? 0.85 : clamp(ratio + (e.key === "ArrowDown" ? 0.03 : -0.03)));
    }} />;
}

function ProjectsView() {
  const [split, setSplit] = useState(() => {
    try { const value = Number(localStorage.getItem("glossx.projects.split") || "0.5"); return Number.isFinite(value) ? Math.max(0.15, Math.min(0.85, value)) : 0.5; } catch { return 0.5; }
  });
  useEffect(() => { try { localStorage.setItem("glossx.projects.split", String(split)); } catch { /* storage unavailable */ } }, [split]);
  const liveProjects = useStore((s) => s.projects);
  const [archivedProjects, setArchivedProjects] = useState<typeof liveProjects>([]);
  const [archiveView, setArchiveView] = useState(false);
  const projects = archiveView ? archivedProjects : liveProjects;
  const projectId = useStore((s) => s.agentProjectId);
  const setProjectId = useStore((s) => s.setAgentProjectId);
  const createProject = useStore((s) => s.createProject);
  const refreshProjects = useStore((s) => s.refreshProjects);
  const openRunTab = useStore((s) => s.openRunTab);
  const setLayout = useStore((s) => s.setLayout);
  const notify = useStore((s) => s.notify);
  const sessions = useSessions((s) => s.list);
  const zh = useStore((s) => s.uiLang) === "zh";
  const [name, setName] = useState("");
  const [filter, setFilter] = useState("");
  const T = zh
    ? { ph: "新建研究项目…", add: "新建", empty: "每篇论文都会自动得到一个工作区；研究项目可以把多篇论文放在一起。", research: "研究项目", paper: "论文工作区", general: "通用", generalHint: "不属于任何项目", filter: "筛选项目…" }
    : { ph: "New research project…", add: "New", empty: "Every paper gets its own workspace automatically; a research project brings several papers together.", research: "Research projects", paper: "Paper workspaces", general: "General", generalHint: "Not tied to a project", filter: "Filter projects…" };

  useEffect(() => {
    void refreshProjects();
  }, []);
  useEffect(() => {
    if (archiveView) void agentApi.archivedProjects().then(setArchivedProjects).catch((e) => notify(String(e)));
  }, [archiveView, liveProjects]);

  async function manage(id: string, action: "archive" | "restore" | "remove", direction = false) {
    if (action !== "restore") {
      const ok = await ask({
        title: action === "archive" ? (zh ? `归档${direction ? "整个方向" : "项目"}？` : `Archive ${direction ? "direction" : "project"}?`) : (zh ? `移除${direction ? "整个方向" : "项目"}？` : `Remove ${direction ? "direction" : "project"}?`),
        body: action === "archive"
          ? (zh ? "项目及其对话将移入归档，可随时恢复。归档方向会包含该方向下的全部项目。" : "Move the project and its threads into the archive. A direction includes every project under it. You can restore them later.")
          : (zh ? "移除后不再显示项目，无法恢复。论文库、工作区文件及 Git 历史保留，对话仍可在对话归档中管理。" : "Permanently remove the project from the list. Library papers, workspace files and Git history are kept; threads remain in the thread archive."),
        actions: [{ id: "ok", label: action === "archive" ? (zh ? "归档" : "Archive") : (zh ? "移除" : "Remove"), danger: action === "remove", primary: action === "archive" }],
        cancel: zh ? "取消" : "Cancel",
      });
      if (ok !== "ok") return;
    }
    try {
      if (direction) await agentApi.directionLifecycle(id, action);
      else await agentApi.projectLifecycle(id, action);
      await refreshProjects();
      const st = useStore.getState();
      if (st.agentProjectId && !st.projects.some((p) => p.id === st.agentProjectId)) st.setAgentProjectId(null);
      await useSessions.getState().refresh();
      setArchivedProjects(await agentApi.archivedProjects());
    } catch (e: any) { notify(String(e?.message || e)); }
  }
  const actions = (id: string, direction = false) => (
    <span onClick={(e) => e.stopPropagation()}>
      <MenuButton className="gx-icon-btn" chevron={false} placement="bottom-end" width={180} title={zh ? "项目操作" : "Project actions"} label={<span>⋯</span>}>
        {(close) => <>
          <MenuItem icon={archiveView ? <ArchiveRestore size={14} /> : <Archive size={14} />} label={archiveView ? (zh ? "恢复" : "Restore") : (zh ? "归档" : "Archive")} onSelect={() => { close(); void manage(id, archiveView ? "restore" : "archive", direction); }} />
          {archiveView && <MenuItem icon={<Trash2 size={14} />} danger label={zh ? "移除项目" : "Remove project"} onSelect={() => { close(); void manage(id, "remove", direction); }} />}
        </>}
      </MenuButton>
    </span>
  );

  async function add() {
    if (!name.trim()) return;
    try {
      await createProject(name.trim());
      setName("");
    } catch (e: any) {
      notify(String(e?.message || e));
    }
  }

  const q = filter.trim().toLowerCase();
  const threadCount = useMemo(() => {
    const n = new Map<string, number>();
    for (const s of sessions) if (s.project_id) n.set(s.project_id, (n.get(s.project_id) || 0) + 1);
    return n;
  }, [sessions]);
  const shown = q ? projects.filter((p) => p.name.toLowerCase().includes(q)) : projects;
  const categories = useMemo(() => [...new Set(projects.flatMap((p) => [p.category || "", p.hub_category || ""]).filter(Boolean))].sort((a, b) => a.localeCompare(b)), [projects]);
  const unfiled = shown.filter((p) => !p.category && !p.hub_category);
  const research = unfiled.filter((p) => p.kind !== "paper");
  const paper = unfiled.filter((p) => p.kind === "paper");
  const active = liveProjects.find((p) => p.id === projectId);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("glossx.categories.collapsed") || "[]")); } catch { return new Set(); }
  });
  const toggle = (c: string) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(c)) next.delete(c); else next.add(c);
    try { localStorage.setItem("glossx.categories.collapsed", JSON.stringify([...next])); } catch { /* private mode */ }
    return next;
  });
  const [renaming, setRenaming] = useState<string | null>(null);
  const file = async (id: string, category: string) => {
    try { await agentApi.patchProject(id, { category }); await refreshProjects(); } catch (e: any) { notify(String(e?.message || e)); }
  };
  const openHub = async (category: string) => {
    try {
      const hub = await agentApi.categoryHub(category);
      await refreshProjects();
      setProjectId(hub.id);
    } catch (e: any) { notify(String(e?.message || e)); }
  };
  const rename = async (from: string, to: string) => {
    setRenaming(null);
    if (!to.trim() || to.trim() === from) return;
    try { await agentApi.renameCategory(from, to.trim()); await refreshProjects(); } catch (e: any) { notify(String(e?.message || e)); }
  };

  const row = (p: (typeof projects)[number]) => {
    const n = (p.paper_ids || []).length;
    const t = threadCount.get(p.id) || 0;
    const branch = p.workspace?.vcs?.branch;
    const meta = [
      p.kind === "paper" ? "" : zh ? `${n} 篇论文` : `${n} paper${n === 1 ? "" : "s"}`,
      t ? (zh ? `${t} 个对话` : `${t} thread${t === 1 ? "" : "s"}`) : "",
    ].filter(Boolean).join(" · ");
    const I = p.hub_category ? Layers : p.kind === "paper" ? BookOpen : FolderKanban;
    return (
      <div key={p.id} className={`wb-item gx-proj-row${projectId === p.id ? " active" : ""}${p.hub_category ? " hub" : ""}`} onClick={() => { if (!archiveView) setProjectId(p.id); }} title={p.description || p.name}>
        <I size={14} strokeWidth={1.7} className="gx-proj-icon" />
        <div className="gx-proj-text">
          <div className="gx-proj-name">{p.hub_category ? (zh ? "整个方向" : "Whole direction") : p.name}</div>
          {(meta || branch) && <div className="wb-item-meta">{meta}{branch && <span className="gx-branch"><GitBranch size={10} /><span>{branch}</span></span>}</div>}
        </div>
        {!archiveView && !p.hub_category && (
          <span className="gx-proj-tag" onClick={(e) => e.stopPropagation()}>
            <CategoryMenu zh={zh} categories={categories} value={p.category || ""} onPick={(c) => void file(p.id, c)} />
          </span>
        )}
        {actions(p.id)}
      </div>
    );
  };
  const group = (c: string) => {
    const members = shown.filter((p) => p.category === c || p.hub_category === c);
    if (q && !members.length) return null;
    const hub = members.find((p) => p.hub_category === c);
    const rest = members.filter((p) => p !== hub).sort((a, b) => Number(a.kind === "paper") - Number(b.kind === "paper"));
    const papers = new Set(members.flatMap((p) => p.paper_ids || [])).size;
    const open = !collapsed.has(c) || !!q;
    return (
      <div key={`cat:${c}`} className="gx-cat">
        <div className={`gx-cat-head${open ? " open" : ""}`} onClick={() => toggle(c)}>
          <ChevronRight size={12} className="gx-cat-chev" />
          {renaming === c ? (
            <input className="wb-input gx-cat-rename" autoFocus defaultValue={c} onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => { if (e.key === "Enter") void rename(c, e.currentTarget.value); if (e.key === "Escape") setRenaming(null); }}
              onBlur={(e) => void rename(c, e.currentTarget.value)} />
          ) : <span className="gx-cat-name" title={c}>{c}</span>}
          <i>{zh ? `${papers} 篇` : `${papers}`}</i>
          <span className="spacer" />
          {actions(c, true)}
          {!archiveView && <button type="button" className="gx-icon-btn" title={zh ? "重命名方向" : "Rename direction"} onClick={(e) => { e.stopPropagation(); setRenaming(c); }}><Pencil size={11} /></button>}
          {!archiveView && <button type="button" className="gx-btn ghost sm gx-cat-hub" title={zh ? "把这个方向的全部论文放进一个研究项目，一起读、一起问" : "Gather every paper of this direction in one research project"} onClick={(e) => { e.stopPropagation(); void openHub(c); }}>
            <Layers size={11} /> {zh ? "合在一起看" : "View together"}
          </button>}
        </div>
        {open && <>{hub && row(hub)}{rest.map(row)}</>}
      </div>
    );
  };

  return (
    <div className="wb-side-body gx-projects">
      <div className="gx-project-switch"><Segmented size="sm" value={archiveView ? "archive" : "active"} onChange={(v) => setArchiveView(v === "archive")} options={[{ value: "active", label: zh ? "项目" : "Projects" }, { value: "archive", label: zh ? "归档" : "Archived" }]} /><button className="gx-btn sm" onClick={() => void openWorkspaceFolder()}>{zh ? "打开文件夹…" : "Open folder…"}</button></div>
      {!archiveView &&
      <div className="wb-import">
        <input className="wb-input" placeholder={T.ph} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void add()} />
        <button className="gx-btn sm" onClick={() => void add()} disabled={!name.trim()}>{T.add}</button>
      </div>}
      {projects.length > 8 && (
        <label className="wb-filter">
          <Search size={13} />
          <input placeholder={T.filter} value={filter} onChange={(e) => setFilter(e.target.value)} />
          <span>{shown.length}</span>
        </label>
      )}
      <div className="wb-list wb-list-compact gx-proj-list" style={!archiveView ? { flex: `${split} 1 0` } : undefined}>
        {projects.length === 0 && <EmptyState icon={<FolderKanban size={20} />} title={archiveView ? (zh ? "暂无归档项目" : "No archived projects") : (zh ? "还没有项目" : "No projects yet")} hint={archiveView ? undefined : T.empty} />}
        {categories.map(group)}
        {categories.length > 0 && unfiled.length > 0 && <div className="gx-menu-heading gx-cat-unfiled">{zh ? "未分类" : "Unfiled"}</div>}
        {research.length > 0 && <div className="gx-menu-heading">{T.research}<i>{research.length}</i></div>}
        {research.map(row)}
        {paper.length > 0 && <div className="gx-menu-heading">{T.paper}<i>{paper.length}</i></div>}
        {paper.map(row)}
        {!archiveView && projects.length > 0 && !q && (
          <div className={`wb-item gx-proj-row general${!projectId ? " active" : ""}`} onClick={() => setProjectId(null)}>
            <MessagesSquare size={14} strokeWidth={1.7} className="gx-proj-icon" />
            <div className="gx-proj-text">
              <div className="gx-proj-name">{T.general}</div>
              <div className="wb-item-meta">{T.generalHint}</div>
            </div>
          </div>
        )}
      </div>
      {!archiveView && <ProjectDivider ratio={split} onChange={setSplit} zh={zh} />}
      {!archiveView && active && (
        <div key={active.id} className="wb-project agent-panel" style={{ flex: `${1 - split} 1 0` }}>
          <h4 className="agent-h">{zh ? "工作区设置" : "Workspace settings"}</h4>
          <strong className="gx-workspace-name" title={active.name}>{active.hub_category ? `${active.hub_category} · ${zh ? "整个方向" : "Whole direction"}` : active.name}</strong>
          <div className="gx-proj-detail-head">
            <span className={`gx-tag${active.kind === "paper" ? " warn" : ""}`}>{active.hub_category ? (zh ? `方向：${active.hub_category}` : `Direction: ${active.hub_category}`) : active.kind === "paper" ? (zh ? "论文工作区" : "Paper workspace") : zh ? "研究项目" : "Research project"}</span>
            {!active.hub_category && <CategoryMenu zh={zh} categories={categories} value={active.category || ""} onPick={(c) => void file(active.id, c)} labelled />}
            <span className="spacer" />
            <button type="button" className="gx-btn ghost sm" onClick={() => setLayout({ activity: "workspace", sideOpen: true })}><FolderTree size={12} /> {zh ? "文件" : "Files"}</button>
            <button type="button" className="gx-btn sm" onClick={() => useSessions.getState().newThread({ projectId: active.id })}><SquarePen size={12} /> {zh ? "新对话" : "New thread"}</button>
          </div>
          {active.workspace?.path && <code className="gx-proj-path" title={active.workspace.path}>{active.workspace.path}</code>}
          <WorkspaceLocation key={active.id} projectId={active.id} />
          <ProjectPapers projectId={active.id} paperIds={active.paper_ids || []} kind={active.kind} zh={zh} />
          <details className="gx-workspace-memory"><summary>{zh ? "项目记忆与运行" : "Memory and runs"}</summary><ProjectDetail key={active.id} projectId={active.id} onOpenRun={openRunTab} showTitle={false} /></details>
        </div>
      )}
      {!archiveView && !active && <div className="wb-project agent-panel" style={{ flex: `${1 - split} 1 0` }}><h4 className="agent-h">{zh ? "工作区设置" : "Workspace settings"}</h4><div className="muted">{zh ? "在上方选择项目，设置会显示在这里。" : "Select a project above to configure its workspace here."}</div></div>}
    </div>
  );
}

/** File a project under a research direction: pick one, type a new one, or unfile. */
function CategoryMenu({ zh, categories, value, onPick, labelled }: { zh: boolean; categories: string[]; value: string; onPick: (c: string) => void; labelled?: boolean }) {
  return (
    <MenuButton
      className={labelled ? "gx-btn ghost sm" : "gx-icon-btn"}
      chevron={false}
      placement="bottom-end"
      width={220}
      title={zh ? "归入研究方向" : "File under a research direction"}
      label={<><Tag size={labelled ? 12 : 11} />{labelled && <span>{value || (zh ? "分类" : "Category")}</span>}</>}
    >
      {(close) => (
        <>
          <MenuLabel>{zh ? "研究方向" : "Research direction"}</MenuLabel>
          {categories.map((c) => <MenuItem key={c} label={c} selected={c === value} onSelect={() => { close(); if (c !== value) onPick(c); }} />)}
          <div className="gx-cat-new">
            <input
              className="wb-input"
              placeholder={zh ? "新方向，回车确定" : "New direction, press Enter"}
              onKeyDown={(e) => {
                e.stopPropagation();
                const v = e.currentTarget.value.trim();
                if (e.key === "Enter" && v) { close(); onPick(v); }
              }}
            />
          </div>
          {value && <><MenuSep /><MenuItem icon={<X size={13} />} label={zh ? "移出分类" : "Remove from direction"} onSelect={() => { close(); onPick(""); }} /></>}
        </>
      )}
    </MenuButton>
  );
}

function CapabilitiesView() {
  const zh = useStore((s) => s.uiLang) === "zh";
  return (
    <div className="wb-side-body wb-scroll">
      <details className="wb-section" open>
        <summary>{zh ? "能力" : "Capabilities"}</summary>
        <div className="agent-panel wb-caps"><CapabilityGroups /></div>
      </details>
      <details className="wb-section" open>
        <summary>{zh ? "插件市场" : "Plugins"}</summary>
        <div className="wb-ext"><ExtensionsPanel /></div>
      </details>
    </div>
  );
}

function SearchView() {
  return <div className="wb-side-body"><ScholarPanel /></div>;
}

function SettingsView() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const focus = useStore((s) => s.settingsFocus);
  const [tab, setTab] = useState<"general" | "harness">("general");
  // a feature-settings deep link always lands on the general page
  const shown = focus ? "general" : tab;
  return (
    <div className="wb-side-body">
      <div className="gx-ws-tabs">
        <Segmented
          size="sm"
          value={shown}
          onChange={(v) => {
            if (focus) useStore.setState({ settingsFocus: null });
            setTab(v);
          }}
          options={[
            { value: "general", label: zh ? "通用" : "General" },
            { value: "harness", label: zh ? "智能体" : "Agent harness" },
          ]}
        />
      </div>
      <div className="wb-settings-page" style={{ display: shown === "general" ? "flex" : "none" }}><SettingsPanel /></div>
      {shown === "harness" && <div className="wb-settings-page"><HarnessSettings /></div>}
    </div>
  );
}

// iPhone / iPad / Android pairing (QR code, internet access)
function MobileView() {
  const zh = useStore((s) => s.uiLang) === "zh";
  return <div className="wb-side-body"><MobileAccess zh={zh} /></div>;
}

const VIEWS: Record<Activity, () => JSX.Element> = {
  library: LibraryView,
  workspace: WorkspaceView,
  scm: ScmView,
  projects: ProjectsView,
  search: SearchView,
  capabilities: CapabilitiesView,
  mobile: MobileView,
  settings: SettingsView,
};

export function SideBar() {
  const activity = useStore((s) => s.layout.activity);
  const zh = useStore((s) => s.uiLang) === "zh";
  const meta = ACTIVITIES.find((a) => a.id === activity)!;
  // Views stay mounted after first visit so their state survives switching.
  const [visited, setVisited] = useState<Set<Activity>>(() => new Set([activity]));
  useEffect(() => {
    setVisited((v) => (v.has(activity) ? v : new Set(v).add(activity)));
  }, [activity]);
  return (
    <div className="wb-sidebar">
      <div className="wb-pane-head">
        <span>{zh ? meta.zh : meta.en}</span>
        <span className="spacer" />
        {activity === "scm" && <ScmHeadActions />}
      </div>
      {ACTIVITIES.filter((a) => visited.has(a.id)).map((a) => {
        const V = VIEWS[a.id];
        return (
          <div key={a.id} className="wb-side-view" style={{ display: a.id === activity ? "flex" : "none" }}>
            <V />
          </div>
        );
      })}
    </div>
  );
}
