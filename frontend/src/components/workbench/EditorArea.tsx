import { useEffect } from "react";
import { Command, FolderTree, MessageSquare, Upload, Globe, type LucideIcon } from "lucide-react";
import { splitWorkspaceId, tabKey, useStore, type EditorTab } from "../../store";
import { useSessions } from "../../sessionStore";
import { openWorkspaceFolder } from "./openFolder";
import { Outline } from "../Outline";
import { Logo } from "../Logo";
import { PdfViewer } from "../../pdf/PdfViewer";
import { RunResult, RunStatus, RunTrace, agentText } from "../AgentRunViews";
import { relativeTime } from "../session/toolMeta";
import { Spinner } from "../ui";
import { CheckpointView, FileEditor, isDirty, useFiles } from "./FileEditor";
import { Icon } from "./icons";
import { CommitView, VcsDiffView } from "./VcsViews";
import { SHORTCUTS } from "./shortcuts";
import { openBrowser } from "../../browserStore";

const baseName = (path: string) => path.split("/").pop() || path;

function useTabTitle(tab: EditorTab) {
  const papers = useStore((s) => s.papers);
  const run = useStore((s) => (tab.kind === "run" ? s.runs[tab.id] : undefined));
  const zh = useStore((s) => s.uiLang) === "zh";
  if (tab.kind === "welcome") return zh ? "欢迎" : "Welcome";
  if (tab.kind === "browser") return zh ? "浏览器" : "Browser";
  if (tab.kind === "run") return run?.goal || (zh ? "运行报告" : "Run report");
  if (tab.kind === "file") return baseName(splitWorkspaceId(tab.id).ref);
  if (tab.kind === "checkpoint") return zh ? "修改差异" : "Change diff";
  if (tab.kind === "vcsdiff") return `${baseName(splitWorkspaceId(tab.id).ref)} ${zh ? "（修改）" : "(changes)"}`;
  if (tab.kind === "commit") return `${zh ? "提交" : "Commit"} ${splitWorkspaceId(tab.id).ref.slice(0, 7)}`;
  return papers.find((p) => p.id === tab.id)?.title || (zh ? "论文" : "Paper");
}

const TAB_ICON: Record<EditorTab["kind"], string> = { paper: "paper", run: "report", welcome: "home", file: "file", checkpoint: "diff", vcsdiff: "diff", commit: "commit", browser: "browser" };

/** Close a tab, asking first when a workspace file has unsaved edits. */
export function requestCloseTab(key: string) {
  const st = useStore.getState();
  const tab = st.openTabs.find((t) => tabKey(t) === key);
  if (tab?.kind === "file") {
    if (isDirty(tab.id) && !confirm(st.uiLang === "zh" ? "这个文件有未保存的修改，仍然关闭？" : "This file has unsaved changes. Close anyway?")) return;
    useFiles.getState().drop(tab.id);
  }
  st.closeTab(key);
}

function TabButton({ tab }: { tab: EditorTab }) {
  const key = tabKey(tab);
  const active = useStore((s) => s.activeTabKey === key);
  const activateTab = useStore((s) => s.activateTab);
  const live = useStore((s) => tab.kind === "run" && ["running", "awaiting_approval"].includes(s.runs[tab.id]?.status || ""));
  const dirty = useFiles((s) => {
    const b = tab.kind === "file" ? s.buffers[tab.id] : undefined;
    return !!b && b.loaded && b.content !== b.saved;
  });
  const zh = useStore((s) => s.uiLang) === "zh";
  const title = useTabTitle(tab);
  return (
    <div
      role="tab"
      aria-selected={active}
      className={`wb-tab kind-${tab.kind}${active ? " active" : ""}${dirty ? " dirty" : ""}`}
      title={tab.kind === "file" || tab.kind === "checkpoint" || tab.kind === "vcsdiff" || tab.kind === "commit" ? splitWorkspaceId(tab.id).ref : title}
      onClick={() => void activateTab(key)}
      onMouseDown={(e) => e.button === 1 && e.preventDefault()}
      onAuxClick={(e) => {
        if (e.button === 1) {
          e.preventDefault();
          requestCloseTab(key);
        }
      }}
    >
      <Icon name={TAB_ICON[tab.kind]} size={14} />
      <span className="wb-tab-title">{title}</span>
      {live && <span className="gx-dot st-running" />}
      <button
        className="wb-tab-close"
        title={zh ? "关闭 (Ctrl+W)" : "Close (Ctrl+W)"}
        onClick={(e) => {
          e.stopPropagation();
          requestCloseTab(key);
        }}
      >
        <i className="wb-tab-dirty" />
        <Icon name="close" size={13} />
      </button>
    </div>
  );
}

function PaperEditor({ visible }: { visible: boolean }) {
  const layout = useStore((s) => s.layout);
  const setLayout = useStore((s) => s.setLayout);
  const zh = useStore((s) => s.uiLang) === "zh";
  const clamp = (w: number) => Math.min(420, Math.max(120, w));

  const startDrag = (e: React.MouseEvent) => {
    const startX = e.clientX;
    const startW = layout.outlineWidth;
    document.body.style.userSelect = "none";
    const onMove = (ev: MouseEvent) => setLayout({ outlineWidth: clamp(startW + ev.clientX - startX) });
    const onUp = () => {
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className={`wb-doc wb-paper${visible ? "" : " hidden"}`}>
      {layout.outlineCollapsed ? (
        <button className="outline-expand" title={zh ? "显示大纲" : "Show outline"} onClick={() => setLayout({ outlineCollapsed: false })}>
          »
        </button>
      ) : (
        <>
          <div className="outline-host" style={{ width: layout.outlineWidth }}>
            <Outline onCollapse={() => setLayout({ outlineCollapsed: true })} />
          </div>
          <div className="divider" onMouseDown={startDrag} />
        </>
      )}
      <PdfViewer />
    </div>
  );
}

function ReportView({ runId, visible }: { runId: string; visible: boolean }) {
  const run = useStore((s) => s.runs[runId]);
  const events = useStore((s) => s.runEvents[runId]) || [];
  const loadRun = useStore((s) => s.loadRun);
  const papers = useStore((s) => s.papers);
  const zh = useStore((s) => s.uiLang) === "zh";
  const T = agentText(zh);
  useEffect(() => {
    if (!run) void loadRun(runId).catch(() => {});
  }, [runId]);

  return (
    <div className={`wb-doc wb-report${visible ? "" : " hidden"}`}>
      <div className="wb-report-inner agent-panel">
        {!run ? (
          <div className="gx-chat-loading"><div className="gx-skel w40" /><div className="gx-skel w90" /><div className="gx-skel w75" /></div>
        ) : (
          <>
            <div className="wb-report-kicker">
              <span className={`agent-dot ${run.status}`} /> {T.status_[run.status] || run.status}
              {run.created_at ? ` · ${new Date(run.created_at * 1000).toLocaleString()}` : ""}
            </div>
            <h1 className="wb-report-goal">{run.goal}</h1>
            {run.paper_ids.length > 0 && (
              <div className="muted wb-report-papers">
                {run.paper_ids
                  .map((id) => papers.find((p) => p.id === id)?.title || id)
                  .slice(0, 6)
                  .join(" · ")}
                {run.paper_ids.length > 6 ? ` +${run.paper_ids.length - 6}` : ""}
              </div>
            )}
            <RunStatus run={run} />
            {!run.result && events.length > 0 && (
              <details className="agent-section" open>
                <summary>{T.trace}</summary>
                <RunTrace events={events} />
              </details>
            )}
            <RunResult run={run} />
          </>
        )}
      </div>
    </div>
  );
}

function Welcome({ visible }: { visible: boolean }) {
  const papers = useStore((s) => s.papers);
  const runs = useStore((s) => s.recentRuns);
  const openPaper = useStore((s) => s.openPaper);
  const openRunTab = useStore((s) => s.openRunTab);
  const openPalette = useStore((s) => s.openPalette);
  const setLayout = useStore((s) => s.setLayout);
  const sessions = useSessions((s) => s.list);
  const compose = useSessions((s) => s.compose);
  const newChat = useSessions((s) => s.newChat);
  const zh = useStore((s) => s.uiLang) === "zh";
  const T = zh
    ? { kicker: "研究工作台", title: "把论文读进去，把问题想清楚。", tag: "导入论文，向智能体提出问题；它会逐段阅读、给出带出处的回答，并把笔记和代码留在工作区里。", ph: "提出一个研究问题…", imp: "导入论文", impH: "arXiv、DOI、链接或 PDF", chat: "新对话", chatH: "向智能体提问或布置任务", ws: "打开工作区", wsH: "笔记、代码与修改记录", pal: "命令面板", papers: "最近论文", chats: "最近对话", runs: "最近运行", keys: "快捷键", noneP: "导入第一篇论文后，它会出现在这里。", noneC: "还没有对话。" }
    : { kicker: "Research workbench", title: "Read closely. Ask better questions.", tag: "Import papers and put questions to the agent — it reads passage by passage, answers with sources, and leaves notes and code in your workspace.", ph: "Ask a research question…", imp: "Import a paper", impH: "arXiv, DOI, URL or PDF", chat: "New chat", chatH: "Ask or hand over a task", ws: "Open workspace", wsH: "Notes, code and change history", pal: "Command palette", papers: "Recent papers", chats: "Recent chats", runs: "Recent runs", keys: "Keyboard", noneP: "Import your first paper and it will show up here.", noneC: "No chats yet." };

  const actions: { icon: LucideIcon; title: string; hint: string; keys?: string; run: () => void }[] = [
    { icon: Upload, title: T.imp, hint: T.impH, run: () => openPalette("import") },
    { icon: MessageSquare, title: T.chat, hint: T.chatH, keys: "Ctrl+L", run: () => { newChat(); compose({}); } },
    { icon: FolderTree, title: T.ws, hint: zh ? "选择文件夹作为项目工作区" : "Choose a folder as a project workspace", run: () => { void openWorkspaceFolder(); } },
    { icon: Globe, title: zh ? "打开浏览器" : "Open browser", hint: zh ? "实时网页与接管控制" : "Live pages and shared control", run: () => { void openBrowser(); } },
    { icon: Command, title: T.pal, hint: zh ? "所有操作，一处可达" : "Every action in one place", keys: "Ctrl+K", run: () => openPalette("commands") },
  ];

  return (
    <div className={`wb-doc wb-welcome${visible ? "" : " hidden"}`}>
      <div className="wb-welcome-inner">
        <header className="wb-hero">
          <div className="wb-hero-kicker"><Logo size={20} x /><span>GlossX</span><i>旁注</i><em>{T.kicker}</em></div>
          <h1>{T.title}</h1>
          <p>{T.tag}</p>
        </header>

        <div className="wb-actions">
          {actions.map((a) => {
            const I = a.icon;
            return (
              <button key={a.title} type="button" onClick={a.run}>
                <I size={16} strokeWidth={1.6} />
                <strong>{a.title}</strong>
                <small>{a.hint}</small>
                {a.keys && <kbd>{a.keys}</kbd>}
              </button>
            );
          })}
        </div>

        <div className="wb-welcome-grid">
          <section>
            <h3>{T.papers}</h3>
            {papers.length === 0 && <p className="wb-welcome-none">{T.noneP}</p>}
            {papers.slice(0, 6).map((p) => (
              <button key={p.id} className="wb-row" onClick={() => void openPaper(p.id).catch(() => {})} title={p.title}>
                <Icon name="paper" size={14} />
                <span>{p.title || "Untitled"}</span>
                <em>{[(p.authors || [])[0], p.year].filter(Boolean).join(" · ")}</em>
              </button>
            ))}
          </section>
          <section>
            <h3>{T.chats}</h3>
            {sessions.length === 0 && runs.length === 0 && <p className="wb-welcome-none">{T.noneC}</p>}
            {sessions.slice(0, 5).map((s) => (
              <button
                key={s.id}
                className="wb-row"
                title={s.title}
                onClick={() => useSessions.getState().openThread(s.id)}
              >
                {s.status !== "idle" ? <Spinner size={13} /> : <Icon name="chat" size={14} />}
                <span>{s.title}</span>
                <em>{relativeTime(s.last_activity_at || s.updated_at, zh)}</em>
              </button>
            ))}
            {runs.length > 0 && <h3>{T.runs}</h3>}
            {runs.slice(0, 4).map((r) => (
              <button key={r.id} className="wb-row" onClick={() => openRunTab(r.id)} title={r.goal}>
                <Icon name="report" size={14} />
                <span>{r.goal}</span>
                <em>{relativeTime(r.created_at, zh)}</em>
              </button>
            ))}
          </section>
        </div>

        <footer className="wb-keys">
          <h3>{T.keys}</h3>
          <div>
            {SHORTCUTS.map((s) => (
              <span key={s.keys}>
                <kbd>{s.keys}</kbd>
                {zh ? s.zh : s.en}
              </span>
            ))}
          </div>
        </footer>
      </div>
    </div>
  );
}

export function EditorArea() {
  const openTabs = useStore((s) => s.openTabs);
  const activeTabKey = useStore((s) => s.activeTabKey);
  const current = useStore((s) => s.current);
  const zh = useStore((s) => s.uiLang) === "zh";
  const active = openTabs.find((t) => tabKey(t) === activeTabKey);
  const showWelcome = !active || active.kind === "welcome";
  const paperActive = active?.kind === "paper";
  const loading = paperActive && current?.id !== active.id;

  // keep the active tab scrolled into view in the strip
  useEffect(() => {
    document.querySelector(".wb-tab.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeTabKey]);

  return (
    <div className="wb-editor">
      {openTabs.length > 0 && (
        <div
          className="wb-tabs"
          role="tablist"
          onWheel={(e) => {
            if (e.deltaY) e.currentTarget.scrollLeft += e.deltaY;
          }}
        >
          {openTabs.map((t) => <TabButton key={tabKey(t)} tab={t} />)}
        </div>
      )}
      <div className="wb-editor-body">
        {current && <PaperEditor visible={!!paperActive && !loading} />}
        {loading && <div className="wb-doc wb-loading"><Spinner size={16} /> {zh ? "正在打开论文…" : "Opening paper…"}</div>}
        {openTabs.filter((t) => t.kind === "run").map((t) => (
          <ReportView key={t.id} runId={t.id} visible={activeTabKey === tabKey(t)} />
        ))}
        {openTabs.filter((t) => t.kind === "file").map((t) => (
          <FileEditor key={t.id} id={t.id} visible={activeTabKey === tabKey(t)} />
        ))}
        {openTabs.filter((t) => t.kind === "checkpoint").map((t) => (
          <CheckpointView key={t.id} id={t.id} visible={activeTabKey === tabKey(t)} />
        ))}
        {openTabs.filter((t) => t.kind === "vcsdiff").map((t) => (
          <VcsDiffView key={t.id} id={t.id} visible={activeTabKey === tabKey(t)} />
        ))}
        {openTabs.filter((t) => t.kind === "commit").map((t) => (
          <CommitView key={t.id} id={t.id} visible={activeTabKey === tabKey(t)} />
        ))}
        <Welcome visible={showWelcome} />
      </div>
    </div>
  );
}
