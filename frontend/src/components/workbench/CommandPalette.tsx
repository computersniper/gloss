import { useEffect, useMemo, useRef, useState } from "react";
import { useStore, type PaletteMode } from "../../store";
import { THEMES } from "../ThemePicker";
import {
  Activity, ArrowLeft, ArrowRightLeft, Bell, Blocks, BookOpen, Check, ChevronRight, CornerDownLeft, FileText, FolderKanban, FolderTree,
  GitBranch, GitBranchPlus, GitCommitHorizontal, GitCompareArrows, GitMerge, History, Home, Languages, Library, ListTree, MessageSquare, MessagesSquare,
  NotebookText, Palette, PanelBottom, PanelLeft, PanelRight, ScrollText, Search, Settings, SlidersHorizontal, Smartphone, Sparkles, SquarePen, Upload, Wrench, X, ZoomIn, ZoomOut,
  type LucideIcon,
} from "lucide-react";
import { activityOf, useSessions } from "../../sessionStore";
import { relativeTime } from "../session/toolMeta";
import { requestCloseTab } from "./EditorArea";
import { focusAiPane } from "./focus";
import { openWorkspaceFolder } from "./openFolder";
import { branchName, commitChanges, createBranch, mergeBranch, switchBranch, useVcs, useVcsEntry } from "../../vcsStore";
import { openInstructions } from "./WorkspaceView";
import { browserApi } from "../../api/browser";
import { openBrowser } from "../../browserStore";
import { contentZoomReset, zoomIn, zoomOut, zoomReset } from "../../zoom";

interface Item {
  id: string;
  label: string;
  detail?: string;
  keys?: string;
  icon?: LucideIcon;
  group?: string;
  checked?: boolean;
  run: () => void | Promise<unknown>;
  keepOpen?: boolean;
}

/** Subsequence fuzzy match: returns a score (higher is better) or -1. */
function fuzzy(query: string, text: string) {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  const direct = t.indexOf(q);
  if (direct >= 0) return 1000 - direct;
  let ti = 0;
  let score = 0;
  let streak = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found < 0) return -1;
    streak = found === ti ? streak + 1 : 0;
    score += 1 + streak * 2 - Math.min(found - ti, 5) * 0.2;
    ti = found + 1;
  }
  return score;
}

const INPUT_MODES = new Set<PaletteMode>(["import", "ask", "newProject", "newBranch", "tryBranch", "commit", "browserUrl"]);

export function CommandPalette() {
  const mode = useStore((s) => s.palette);
  const openPalette = useStore((s) => s.openPalette);
  const papers = useStore((s) => s.papers);
  const runs = useStore((s) => s.recentRuns);
  const projects = useStore((s) => s.projects);
  const sessions = useSessions((s) => s.list);
  const projectId = useStore((s) => s.agentProjectId);
  const vcs = useVcsEntry(projectId);
  const inboxCount = useSessions((s) => Math.max(s.inbox.unread, s.inbox.blocking));
  const layout = useStore((s) => s.layout);
  const uiLang = useStore((s) => s.uiLang);
  const zh = uiLang === "zh";
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setQuery("");
    setIndex(0);
    if (mode) setTimeout(() => inputRef.current?.focus(), 0);
    if (mode) useVcs.getState().ensure(useStore.getState().agentProjectId, 2000);
  }, [mode]);

  const close = () => openPalette(null);

  const items: Item[] = useMemo(() => {
    const st = useStore.getState();
    const L = (en: string, zhText: string) => (zh ? zhText : en);
    if (mode === "branches") {
      const status = vcs.status;
      const current = status?.branch || "";
      return [
        ...(status?.branches || []).map((b) => ({
          id: `b:${b.name}`,
          label: b.name,
          detail: b.name === current ? L("current branch", "当前分支") : relativeTime(b.updated_at, zh),
          icon: GitBranch,
          checked: b.name === current,
          run: () => (b.name === current ? undefined : switchBranch(projectId, b.name)),
        })),
        ...(status?.branches || []).filter((b) => b.name !== current).map((b) => ({
          id: `m:${b.name}`,
          label: L(`Merge ${b.name} into ${current}`, `把 ${b.name} 合并到 ${current}`),
          icon: GitMerge,
          run: () => mergeBranch(projectId, b.name),
        })),
        { id: "__new", label: L("Create a new branch…", "新建分支…"), icon: GitBranchPlus, keepOpen: true, run: () => openPalette("newBranch") },
      ];
    }
    if (mode === "papers") {
      return papers.map((p) => ({
        id: p.id,
        label: p.title || "Untitled",
        detail: [(p.authors || []).slice(0, 3).join(", "), p.year].filter(Boolean).join(" · "),
        icon: FileText,
        run: () => st.openPaper(p.id).catch(() => {}),
      }));
    }
    if (mode === "runs") {
      return runs.map((r) => ({ id: r.id, label: r.goal, detail: r.status, icon: ScrollText, run: () => st.openRunTab(r.id) }));
    }
    if (mode === "chats") {
      return sessions.map((s) => ({
        id: s.id,
        label: s.title || L("New thread", "新对话"),
        detail: [s.project?.name || projects.find((p) => p.id === s.project_id)?.name, relativeTime(activityOf(s), zh)].filter(Boolean).join(" · "),
        icon: MessageSquare,
        run: () => useSessions.getState().openThread(s.id),
      }));
    }
    if (mode === "projects") {
      return [
        { id: "__none", label: L("General (no project)", "通用（不使用项目）"), icon: MessagesSquare, checked: !st.agentProjectId, run: () => st.setAgentProjectId(null) },
        ...[...projects].sort((a, b) => Number(a.kind === "paper") - Number(b.kind === "paper")).map((p) => ({
          id: p.id,
          label: p.name,
          detail: p.kind === "paper" ? L("paper workspace", "论文工作区") : p.description || L("research project", "研究项目"),
          icon: p.kind === "paper" ? BookOpen : FolderKanban,
          checked: st.agentProjectId === p.id,
          run: () => st.setAgentProjectId(p.id),
        })),
      ];
    }
    if (mode === "theme") {
      return THEMES.map((t) => ({ id: t.id, label: t.name, icon: Palette, checked: st.theme === t.id, run: () => st.setTheme(t.id) }));
    }
    if (mode !== "commands") return [];
    const toMode = (m: PaletteMode) => () => openPalette(m);
    const vcsOk = vcs.phase === "ok";
    const nChanges = vcs.status?.changes.length || 0;
    const G = { vcs: L("Source control", "源代码管理"), agent: L("Agent", "智能体"), papers: L("Papers", "论文"), go: L("Go to", "前往"), view: L("View", "视图"), prefs: L("Preferences", "偏好") };
    return [
      { id: "ask", group: G.agent, icon: Sparkles, label: L("Ask the agent…", "问智能体…"), run: toMode("ask"), keepOpen: true },
      { id: "newChat", group: G.agent, icon: SquarePen, label: L("New thread", "新对话"), run: () => { useSessions.getState().newThread(); focusAiPane(); } },
      { id: "openChat", group: G.agent, icon: History, label: L("Open thread…", "打开对话…"), run: toMode("chats"), keepOpen: true },
      { id: "inbox", group: G.agent, icon: Bell, label: L("Open inbox", "打开收件箱"), detail: inboxCount ? L(`${inboxCount} waiting`, `${inboxCount} 个待处理`) : undefined, run: () => useSessions.getState().openInbox() },
      { id: "threads", group: G.agent, icon: MessagesSquare, label: L("Show threads", "显示全部对话"), run: () => { st.setLayout({ aiOpen: true, aiMode: "threads" }); useSessions.getState().setSideView("threads"); } },
      { id: "tryBranch", group: G.agent, icon: GitBranchPlus, label: L("New thread on a new branch…", "在新分支上开始对话…"), detail: L("try another approach", "换一种做法试试"), run: toMode("tryBranch"), keepOpen: true },
      { id: "scm", group: G.vcs, icon: GitCompareArrows, label: L("Open source control", "打开源代码管理"), detail: nChanges ? L(`${nChanges} uncommitted`, `${nChanges} 个未提交`) : undefined, run: () => st.setLayout({ activity: "scm", sideOpen: true }) },
      ...(vcsOk ? [
        { id: "commit", group: G.vcs, icon: GitCommitHorizontal, label: L("Commit changes…", "提交修改…"), detail: nChanges ? undefined : L("nothing to commit", "没有可提交的修改"), run: toMode("commit"), keepOpen: true },
        { id: "branches", group: G.vcs, icon: GitBranch, label: L("Switch or merge branch…", "切换或合并分支…"), detail: vcs.status?.branch || undefined, run: toMode("branches"), keepOpen: true },
        { id: "newBranch", group: G.vcs, icon: GitBranchPlus, label: L("Create branch…", "新建分支…"), run: toMode("newBranch"), keepOpen: true },
        { id: "vcsHistory", group: G.vcs, icon: History, label: L("Show commit history", "显示提交历史"), run: () => { st.setLayout({ activity: "scm", sideOpen: true }); setTimeout(() => window.dispatchEvent(new CustomEvent("glossx:scm-tab", { detail: "history" })), 60); } },
      ] as Item[] : []),
      { id: "open", group: G.papers, icon: BookOpen, label: L("Open paper…", "打开论文…"), run: toMode("papers"), keepOpen: true },
      { id: "import", group: G.papers, icon: Upload, label: L("Import paper (arXiv / DOI / URL)…", "导入论文（arXiv / DOI / 链接）…"), run: toMode("import"), keepOpen: true },
      { id: "search", group: G.papers, icon: Search, label: L("Search scholar", "学术搜索"), run: () => st.setLayout({ activity: "search", sideOpen: true }) },
      { id: "library", group: G.go, icon: Library, label: L("Show library", "显示论文库"), run: () => st.setLayout({ activity: "library", sideOpen: true }) },
      { id: "workspace", group: G.go, icon: FolderTree, label: L("Show workspace files", "显示工作区文件"), run: () => st.setLayout({ activity: "workspace", sideOpen: true }) },
      { id: "openFolder", group: G.go, icon: FolderTree, label: L("Open folder…", "打开文件夹…"), run: () => { void openWorkspaceFolder(); } },
      { id: "browser", group: G.go, icon: Search, label: L("Open browser", "打开浏览器"), run: () => openBrowser() },
      { id: "browserUrl", group: G.go, icon: Search, label: L("Open URL in browser…", "在浏览器中打开网址…"), run: toMode("browserUrl"), keepOpen: true },
      { id: "browserImport", group: G.papers, icon: Upload, label: L("Import current browser page as paper", "把当前浏览器页面导入为论文"), run: async () => { const id = st.browserSessionId || useSessions.getState().activeId; if (!id) return st.notify(L("Open a browser page first", "请先打开浏览器页面")); try { await browserApi.input(id, { type: "import_paper" }); await st.loadPapers(); st.notify(L("Paper import started", "论文导入已开始")); } catch (e: any) { st.notify(String(e?.message || e)); } } },
      { id: "instructions", group: G.go, icon: NotebookText, label: L("Open GLOSSX.md (project instructions)", "打开 GLOSSX.md（项目说明）"), run: () => openInstructions(st.agentProjectId) },
      { id: "switchProject", group: G.go, icon: FolderKanban, label: L("Switch project…", "切换项目…"), run: toMode("projects"), keepOpen: true },
      { id: "newProject", group: G.go, icon: FolderKanban, label: L("New project…", "新建项目…"), run: toMode("newProject"), keepOpen: true },
      { id: "openRun", group: G.go, icon: ScrollText, label: L("Open run report…", "打开运行报告…"), run: toMode("runs"), keepOpen: true },
      { id: "caps", group: G.go, icon: Blocks, label: L("Show capabilities & plugins", "显示能力与插件"), run: () => st.setLayout({ activity: "capabilities", sideOpen: true }) },
      { id: "welcome", group: G.go, icon: Home, label: L("Open Welcome tab", "打开欢迎页"), run: () => st.openWelcome() },
      { id: "side", group: G.view, icon: PanelLeft, label: L("Toggle side bar", "切换侧边栏"), keys: "Ctrl+B", run: () => st.setLayout({ sideOpen: !layout.sideOpen }) },
      { id: "ai", group: G.view, icon: PanelRight, label: L("Toggle AI pane", "切换 AI 面板"), keys: "Ctrl+L", run: () => st.setLayout({ aiOpen: !layout.aiOpen }) },
      { id: "aiFocus", group: G.view, icon: PanelRight, label: L("Focus AI pane", "聚焦 AI 面板"), keys: "Ctrl+Alt+B", run: () => focusAiPane() },
      { id: "bottom", group: G.view, icon: PanelBottom, label: L("Toggle bottom panel", "切换底部面板"), keys: "Ctrl+J", run: () => st.setLayout({ bottomOpen: !layout.bottomOpen }) },
      { id: "zoomIn", group: G.view, icon: ZoomIn, label: L("View: Zoom in", "视图：放大"), keys: "Ctrl+=", run: zoomIn, keepOpen: true },
      { id: "zoomOut", group: G.view, icon: ZoomOut, label: L("View: Zoom out", "视图：缩小"), keys: "Ctrl+-", run: zoomOut, keepOpen: true },
      { id: "zoomReset", group: G.view, icon: Search, label: L("View: Reset zoom", "视图：重置缩放"), keys: "Ctrl+0", run: zoomReset },
      { id: "contentZoomReset", group: G.view, icon: Search, label: L("Editor: Reset font zoom", "编辑器：重置字体缩放"), run: contentZoomReset },
      { id: "agentMode", group: G.view, icon: Sparkles, label: L("AI pane: Agent", "AI 面板：智能体"), run: () => st.setLayout({ aiOpen: true, aiMode: "agent" }) },
      { id: "chatMode", group: G.view, icon: MessageSquare, label: L("Side chat (stays out of the thread)", "辅助对话（不进入主对话）"), run: () => st.openSideChat() },
      { id: "toolsMode", group: G.view, icon: Wrench, label: L("AI pane: Paper tools", "AI 面板：论文工具"), run: () => st.setLayout({ aiOpen: true, aiMode: "tools" }) },
      { id: "trace", group: G.view, icon: ListTree, label: L("Show run trace", "显示运行轨迹"), run: () => st.setLayout({ bottomOpen: true, bottomTab: "trace" }) },
      { id: "tasks", group: G.view, icon: Activity, label: L("Show tasks", "显示任务"), run: () => st.setLayout({ bottomOpen: true, bottomTab: "tasks" }) },
      { id: "closeTab", group: G.view, icon: X, label: L("Close tab", "关闭标签页"), keys: "Ctrl+W", run: () => st.activeTabKey && requestCloseTab(st.activeTabKey) },
      { id: "nextTab", group: G.view, icon: ChevronRight, label: L("Next tab", "下一个标签页"), keys: "Ctrl+Tab", run: () => st.cycleTab(1) },
      { id: "prevTab", group: G.view, icon: ArrowLeft, label: L("Previous tab", "上一个标签页"), keys: "Ctrl+Shift+Tab", run: () => st.cycleTab(-1) },
      { id: "settings", group: G.prefs, icon: Settings, label: L("Open settings", "打开设置"), run: () => { st.setLayout({ activity: "settings", sideOpen: true }); useStore.setState({ settingsFocus: null }); } },
      { id: "mobile", group: G.prefs, icon: Smartphone, label: L("Mobile access: use from iPhone, iPad or Android (QR code)", "手机 / 平板访问：扫码连接这台电脑"), run: () => {
        st.setLayout({ activity: "mobile", sideOpen: true });
      } },
      { id: "theme", group: G.prefs, icon: Palette, label: L("Switch theme…", "切换主题…"), run: toMode("theme"), keepOpen: true },
      { id: "lang", group: G.prefs, icon: Languages, label: zh ? "Switch interface language to English" : "切换界面语言为中文", run: () => st.setUiLang(zh ? "en" : "zh") },
      { id: "mode", group: G.prefs, icon: ArrowRightLeft, label: L("Switch to Gloss mode (classic reader)", "切换到 Gloss 模式（经典阅读器）"), keys: "Ctrl+Shift+M", run: () => st.setMode("gloss") },
    ];
  }, [mode, papers, runs, projects, sessions, layout, zh, vcs, projectId, inboxCount]);

  const isInput = !!mode && INPUT_MODES.has(mode);
  const filtered = useMemo(() => {
    if (isInput) return [];
    if (!query.trim()) return items; // keep the authored order (and its groups)
    return items
      .map((it) => ({ it, score: Math.max(fuzzy(query, it.label), fuzzy(query, it.detail || "") - 50) }))
      .filter((x) => x.score >= 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.it);
  }, [items, query, isInput]);

  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    listRef.current?.querySelector(".wb-pal-item.on")?.scrollIntoView({ block: "nearest" });
  }, [index]);

  if (!mode) return null;

  function choose(it?: Item) {
    if (!it) return;
    if (!it.keepOpen) close();
    void it.run();
  }

  async function submitInput() {
    const text = query.trim();
    if (!text) return;
    const st = useStore.getState();
    close();
    try {
      if (mode === "import") {
        await st.enqueueImport(text);
        st.setLayout({ activity: "library", sideOpen: true });
      } else if (mode === "browserUrl") {
        await openBrowser(/^https?:\/\//i.test(text) || text === "about:blank" ? text : `https://${text}`);
      } else if (mode === "ask") {
        useSessions.getState().newChat();
        useSessions.getState().compose({ text, send: true });
      } else if (mode === "newProject") {
        await st.createProject(text);
        st.setLayout({ activity: "projects", sideOpen: true });
      } else if (mode === "commit") {
        await commitChanges(st.agentProjectId, text);
      } else if (mode === "newBranch") {
        await createBranch(st.agentProjectId, text, { from: vcs.status?.branch });
      } else if (mode === "tryBranch") {
        // the branch is created by the server when the thread starts, so nothing changes until the first message
        const name = branchName(text);
        if (name) {
          useSessions.getState().newThread({ branch: name });
          focusAiPane();
        }
      }
    } catch (e: any) {
      st.notify(String(e?.message || e));
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      if (mode !== "commands") openPalette("commands");
      else close();
    } else if (e.key === "Backspace" && !query && mode !== "commands") {
      openPalette("commands");
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setIndex((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (isInput) void submitInput();
      else choose(filtered[index]);
    }
  }

  const placeholder = {
    commands: zh ? "输入命令…" : "Type a command…",
    browserUrl: zh ? "输入网址，回车打开" : "Enter a URL, Enter to open",
    papers: zh ? "搜索要打开的论文…" : "Search a paper to open…",
    runs: zh ? "搜索运行…" : "Search runs…",
    chats: zh ? "搜索对话…" : "Search threads…",
    projects: zh ? "切换到项目…" : "Switch to project…",
    theme: zh ? "选择主题…" : "Pick a theme…",
    import: zh ? "arXiv 编号 / DOI / 链接，回车导入" : "arXiv id / DOI / URL, Enter to import",
    ask: zh ? "研究问题，回车交给智能体" : "Research question, Enter to hand to the agent",
    newProject: zh ? "新项目名称，回车创建" : "New project name, Enter to create",
    branches: zh ? "切换到分支，或合并…" : "Switch to a branch, or merge one…",
    newBranch: zh ? `新分支名称，回车从 ${vcs.status?.branch || "当前分支"} 创建并切换` : `New branch name — Enter creates it from ${vcs.status?.branch || "the current branch"} and switches`,
    tryBranch: zh ? "分支名称，回车在该分支上开始一个新对话" : "Branch name — Enter starts a new thread on that branch",
    commit: zh ? "提交说明，回车提交全部修改" : "Commit message — Enter commits every change",
  }[mode];

  return (
    <div className="wb-pal-backdrop" onMouseDown={close}>
      <div className="wb-pal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <div className="wb-pal-field">
        {mode === "commands" ? <Search size={15} /> : (
          <button type="button" className="wb-pal-back" title="Esc" onClick={() => openPalette("commands")}><ArrowLeft size={14} /></button>
        )}
        <input
          ref={inputRef}
          className="wb-pal-input"
          value={query}
          placeholder={placeholder}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
          role="combobox"
          aria-expanded
          aria-controls="wb-pal-list"
          aria-activedescendant={filtered[index] ? `pal-${filtered[index].id}` : undefined}
        />
        </div>
        {!isInput && (
          <div className="wb-pal-list" id="wb-pal-list" role="listbox" ref={listRef}>
            {filtered.length === 0 && <div className="muted wb-pal-empty">{zh ? "没有匹配项" : "No matches"}</div>}
            {filtered.map((it, i) => {
              const I = it.icon || SlidersHorizontal;
              const heading = !query.trim() && it.group && it.group !== filtered[i - 1]?.group ? it.group : null;
              return (
                <div key={it.id} role="none">
                  {heading && <div className="wb-pal-group">{heading}</div>}
                  <div
                    id={`pal-${it.id}`}
                    role="option"
                    aria-selected={i === index}
                    className={`wb-pal-item${i === index ? " on" : ""}`}
                    onMouseMove={() => i !== index && setIndex(i)}
                    onClick={() => choose(it)}
                  >
                    <I size={15} strokeWidth={1.6} className="wb-pal-icon" />
                    <span className="wb-pal-label">{it.label}</span>
                    {it.detail && <span className="wb-pal-detail">{it.detail}</span>}
                    {it.checked && <Check size={14} className="wb-pal-check" />}
                    {it.keys && <kbd>{it.keys}</kbd>}
                    {it.keepOpen && !it.keys && <ChevronRight size={13} className="wb-pal-more" />}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <div className="wb-pal-foot">
          {isInput ? (
            <span><kbd><CornerDownLeft size={10} /></kbd>{zh ? "确认" : "confirm"}</span>
          ) : (
            <>
              <span><kbd>↑</kbd><kbd>↓</kbd>{zh ? "选择" : "navigate"}</span>
              <span><kbd><CornerDownLeft size={10} /></kbd>{zh ? "执行" : "run"}</span>
            </>
          )}
          <span><kbd>Esc</kbd>{mode === "commands" ? (zh ? "关闭" : "close") : zh ? "返回" : "back"}</span>
        </div>
      </div>
    </div>
  );
}
