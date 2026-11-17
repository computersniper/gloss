// Branch switcher (status bar, chat header): current branch; create from it,
// switch, merge another branch into it, delete. Project switcher for the title bar.
import { useEffect, useState } from "react";
import { BookOpen, Check, FolderKanban, GitBranch, GitBranchPlus, GitCommitHorizontal, GitMerge, MessagesSquare, PackageX, Plus, Trash2 } from "lucide-react";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { branchName, createBranch, deleteBranch, mergeBranch, switchBranch, useVcs, useVcsEntry } from "../../vcsStore";
import { relativeTime } from "../session/toolMeta";
import { MenuItem, MenuLabel, MenuSep, Popover, Spinner } from "../ui";

export function BranchSwitcher({ className = "wb-status-item", placement = "top-start" }: { className?: string; placement?: "top-start" | "bottom-start" | "bottom-end" | "top-end" }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const projectId = useStore((s) => s.agentProjectId);
  const projectName = useStore((s) => s.projects.find((p) => p.id === s.agentProjectId)?.name);
  const setLayout = useStore((s) => s.setLayout);
  const project = projectId || "default";
  const entry = useVcsEntry(project);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // keep the label fresh: on project change, when files change, when the window regains focus
  useEffect(() => {
    void useVcs.getState().refresh(project);
    let timer = 0;
    const on = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void useVcs.getState().refresh(project), 400);
    };
    window.addEventListener("glossx:workspace-changed", on);
    window.addEventListener("focus", on);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("glossx:workspace-changed", on);
      window.removeEventListener("focus", on);
    };
  }, [project]);
  useEffect(() => {
    if (!open) setCreating(null);
    else void useVcs.getState().refresh(project);
  }, [open]);
  useEffect(() => {
    const on = () => setOpen(true);
    window.addEventListener("glossx:open-branches", on);
    return () => window.removeEventListener("glossx:open-branches", on);
  }, []);

  if (entry.phase === "loading" || entry.phase === "missing" || (entry.phase === "error" && !entry.status)) return null;
  const status = entry.status;
  const nogit = entry.phase === "nogit";
  const current = status?.branch || "";
  const changes = status?.changes.length || 0;
  const close = () => setOpen(false);
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    close();
    await fn();
    setBusy(null);
  };
  const create = () => {
    const name = branchName(creating || "");
    if (!name) return;
    void run("create", () => createBranch(project, name, { from: current || undefined }));
  };

  return (
    <>
      <button
        ref={setAnchor}
        type="button"
        className={`${className} gx-branch-btn${open ? " open" : ""}${nogit ? " off" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={nogit ? (zh ? "未安装 Git：没有版本历史" : "Git is not installed: no version history") : zh ? `分支：${current}${changes ? ` · ${changes} 个未提交的修改` : ""}` : `Branch: ${current}${changes ? ` · ${changes} uncommitted change${changes === 1 ? "" : "s"}` : ""}`}
        onClick={() => setOpen((o) => !o)}
      >
        {busy ? <Spinner size={12} /> : nogit ? <PackageX size={13} /> : <GitBranch size={13} />}
        <span>{nogit ? (zh ? "无 Git" : "No git") : current || (zh ? "无分支" : "no branch")}</span>
        {!nogit && changes > 0 && <i className="gx-branch-dirty" aria-label={zh ? "有未提交的修改" : "uncommitted changes"}>{changes}</i>}
      </button>
      <Popover anchor={anchor} open={open} onClose={close} placement={placement} width={300} className="gx-branches">
        {open && (nogit ? (
          <div className="gx-branches-off">
            <strong>{zh ? "未安装 Git" : "Git is not installed"}</strong>
            <p>{zh ? "安装 Git 并重启 GlossX 后即可使用分支和版本历史。检查点仍然可用。" : "Install Git and restart GlossX to get branches and version history. Checkpoints keep working."}</p>
          </div>
        ) : (
          <>
            <MenuLabel>{zh ? "分支" : "Branches"}{projectName ? ` · ${projectName}` : ""}</MenuLabel>
            <div className="gx-branch-list">
              {(status?.branches || []).map((b) => {
                const isCurrent = b.name === current;
                return (
                  <div key={b.name} className={`gx-branch-row${isCurrent ? " current" : ""}`}>
                    <button
                      type="button"
                      role="menuitem"
                      className="gx-branch-main"
                      title={isCurrent ? (zh ? "当前分支" : "Current branch") : zh ? `切换到 ${b.name}` : `Switch to ${b.name}`}
                      onClick={() => (isCurrent ? close() : void run(b.name, () => switchBranch(project, b.name)))}
                    >
                      <span className="gx-menu-icon">{isCurrent ? <Check size={14} /> : <GitBranch size={13} />}</span>
                      <span className="gx-menu-text">
                        <span className="gx-menu-label">{b.name}</span>
                        {(b.updated_at || b.head) && <span className="gx-menu-detail">{[b.head ? String(b.head).slice(0, 7) : "", relativeTime(b.updated_at, zh)].filter(Boolean).join(" · ")}</span>}
                      </span>
                    </button>
                    {!isCurrent && (
                      <>
                        <button type="button" className="gx-icon-btn" title={zh ? `把 ${b.name} 合并到 ${current}` : `Merge ${b.name} into ${current}`} onClick={() => void run(b.name, () => mergeBranch(project, b.name))}><GitMerge size={13} /></button>
                        <button type="button" className="gx-icon-btn danger" title={zh ? `删除分支 ${b.name}` : `Delete branch ${b.name}`} onClick={() => void run(b.name, () => deleteBranch(project, b.name))}><Trash2 size={13} /></button>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
            <MenuSep />
            {creating !== null ? (
              <div className="gx-branch-new">
                <GitBranchPlus size={14} />
                <input
                  autoFocus
                  className="gx-input mono"
                  value={creating}
                  placeholder={zh ? "新分支名称" : "new-branch-name"}
                  onChange={(e) => setCreating(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.nativeEvent.isComposing) create();
                    if (e.key === "Escape") {
                      e.stopPropagation();
                      setCreating(null);
                    }
                  }}
                />
                <button type="button" className="gx-btn primary sm" disabled={!branchName(creating)} onClick={create}>{zh ? "创建" : "Create"}</button>
              </div>
            ) : (
              <MenuItem icon={<GitBranchPlus size={14} />} label={zh ? "新建分支…" : "New branch…"} detail={zh ? `从 ${current || "当前提交"} 创建并切换过去` : `From ${current || "the current commit"}, and switch to it`} onSelect={() => setCreating("")} />
            )}
            <MenuItem
              icon={<GitCommitHorizontal size={14} />}
              label={changes ? (zh ? `提交 ${changes} 个修改…` : `Commit ${changes} change${changes === 1 ? "" : "s"}…`) : zh ? "打开源代码管理" : "Open source control"}
              onSelect={() => {
                close();
                setLayout({ activity: "scm", sideOpen: true });
                if (changes) setTimeout(() => window.dispatchEvent(new CustomEvent("glossx:focus-commit")), 80);
              }}
            />
          </>
        ))}
      </Popover>
    </>
  );
}

/** Active project, obvious in the title bar: paper workspaces vs research projects, plus "General". */
export function ProjectSwitcher({ className = "wb-project-switch", placement = "bottom-start" }: { className?: string; placement?: "top-start" | "bottom-start" }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const projects = useStore((s) => s.projects);
  const projectId = useStore((s) => s.agentProjectId);
  const setProjectId = useStore((s) => s.setAgentProjectId);
  const openPalette = useStore((s) => s.openPalette);
  const setLayout = useStore((s) => s.setLayout);
  const current = useStore((s) => s.current);
  const sessions = useSessions((s) => s.list);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const active = projects.find((p) => p.id === projectId);
  const Glyph = !active ? MessagesSquare : active.kind === "paper" ? BookOpen : FolderKanban;
  const close = () => setOpen(false);
  const count = (id: string | null) => sessions.filter((s) => (s.project_id || null) === id).length;
  const recent = (id: string) => Math.max(0, ...sessions.filter((s) => s.project_id === id).map((s) => s.last_activity_at || s.updated_at || 0));
  const research = projects.filter((p) => p.kind !== "paper");
  // one project per paper adds up: show the ones in use, the rest are a search away
  const papers = projects.filter((p) => p.kind === "paper").sort((a, b) => Number(b.id === projectId) - Number(a.id === projectId) || recent(b.id) - recent(a.id) || (b.created_at || 0) - (a.created_at || 0));
  const shownPapers = papers.slice(0, 7);
  const pick = (id: string | null) => {
    close();
    setProjectId(id);
  };
  const detail = (id: string) => {
    const n = count(id);
    return n ? (zh ? `${n} 个对话` : `${n} thread${n === 1 ? "" : "s"}`) : undefined;
  };

  return (
    <>
      <button
        ref={setAnchor}
        type="button"
        className={`${className}${open ? " open" : ""}${active ? "" : " none"}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={zh ? `当前项目：${active?.name || "通用（无项目）"} — 工作区、新对话和版本历史都属于它` : `Active project: ${active?.name || "General (no project)"} — the workspace, new threads and history belong to it`}
        onClick={() => setOpen((o) => !o)}
      >
        <Glyph size={13} strokeWidth={1.8} />
        <span>{active?.name || (zh ? "通用" : "General")}</span>
      </button>
      <Popover anchor={anchor} open={open} onClose={close} placement={placement} width={320}>
        {open && (
          <>
            {current && !shownPapers.some((p) => (p.paper_ids || []).includes(current.id)) && papers.some((p) => (p.paper_ids || []).includes(current.id)) && (
              <>
                <MenuLabel>{zh ? "当前论文" : "Open paper"}</MenuLabel>
                {papers.filter((p) => (p.paper_ids || []).includes(current.id)).slice(0, 1).map((p) => (
                  <MenuItem key={p.id} icon={<BookOpen size={14} />} label={p.name} detail={detail(p.id)} selected={p.id === projectId} onSelect={() => pick(p.id)} />
                ))}
              </>
            )}
            {research.length > 0 && <MenuLabel>{zh ? "研究项目" : "Research projects"}</MenuLabel>}
            {research.map((p) => (
              <MenuItem key={p.id} icon={<FolderKanban size={14} />} label={p.name} detail={[(p.paper_ids || []).length ? (zh ? `${p.paper_ids!.length} 篇论文` : `${p.paper_ids!.length} paper${p.paper_ids!.length === 1 ? "" : "s"}`) : "", detail(p.id)].filter(Boolean).join(" · ") || undefined} selected={p.id === projectId} onSelect={() => pick(p.id)} />
            ))}
            {shownPapers.length > 0 && <MenuLabel>{zh ? "论文工作区" : "Paper workspaces"}</MenuLabel>}
            {shownPapers.map((p) => (
              <MenuItem key={p.id} icon={<BookOpen size={14} />} label={p.name} detail={detail(p.id)} selected={p.id === projectId} onSelect={() => pick(p.id)} />
            ))}
            {papers.length > shownPapers.length && (
              <MenuItem icon={<span />} label={zh ? `全部 ${papers.length} 个论文工作区…` : `All ${papers.length} paper workspaces…`} onSelect={() => { close(); openPalette("projects"); }} />
            )}
            <MenuSep />
            <MenuItem icon={<MessagesSquare size={14} />} label={zh ? "通用" : "General"} detail={zh ? "不属于任何项目 · 默认工作区" : "No project · the default workspace"} selected={!projectId} onSelect={() => pick(null)} />
            <MenuSep />
            <MenuItem icon={<Plus size={14} />} label={zh ? "新建研究项目…" : "New research project…"} onSelect={() => { close(); openPalette("newProject"); }} />
            <MenuItem icon={<FolderKanban size={14} />} label={zh ? "管理项目" : "Manage projects"} onSelect={() => { close(); setLayout({ activity: "projects", sideOpen: true }); }} />
          </>
        )}
      </Popover>
    </>
  );
}
