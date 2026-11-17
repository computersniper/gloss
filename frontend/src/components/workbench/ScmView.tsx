// Side bar: source control for the active project's workspace — changes with a
// commit box, and the commit history with a compact branch graph.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Check, GitBranch, GitCommitHorizontal, History, PackageX, RefreshCw, Sparkles, Undo2 } from "lucide-react";
import { vcsApi, type VcsChange, type VcsCommit } from "../../api/harness";
import { layoutGraph } from "../../harness/vcs.mjs";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { commitChanges, discardChanges, useVcs, useVcsEntry } from "../../vcsStore";
import { relativeTime } from "../session/toolMeta";
import { EmptyState, Segmented, Select, Spinner } from "../ui";
import { BranchChip } from "./ThreadsView";

const STATUS: Record<string, { letter: string; tone: string; en: string; zh: string }> = {
  M: { letter: "M", tone: "mod", en: "Modified", zh: "已修改" },
  A: { letter: "A", tone: "add", en: "Added", zh: "新增" },
  "?": { letter: "U", tone: "add", en: "Untracked (new file)", zh: "未跟踪（新文件）" },
  U: { letter: "U", tone: "add", en: "Untracked (new file)", zh: "未跟踪（新文件）" },
  D: { letter: "D", tone: "del", en: "Deleted", zh: "已删除" },
  R: { letter: "R", tone: "mod", en: "Renamed", zh: "已重命名" },
  C: { letter: "C", tone: "mod", en: "Copied", zh: "已复制" },
  "!": { letter: "!", tone: "del", en: "Conflict", zh: "冲突" },
};

/** `M`, `??`, ` M`, `modified`, `untracked`… → one status entry. */
export function statusMeta(raw: string) {
  const s = String(raw || "M").trim();
  const word: Record<string, string> = { modified: "M", added: "A", new: "A", untracked: "?", deleted: "D", removed: "D", renamed: "R", copied: "C", conflict: "!", unmerged: "!" };
  const key = word[s.toLowerCase()] || (s.includes("?") ? "?" : (s.replace(/\s/g, "")[0] || "M").toUpperCase());
  return STATUS[key] || { letter: key, tone: "mod", en: s, zh: s };
}

export function StatusLetter({ status, zh }: { status: string; zh: boolean }) {
  const m = statusMeta(status);
  return <span className={`gx-scm-letter ${m.tone}`} title={zh ? m.zh : m.en}>{m.letter}</span>;
}

/** Friendly explanation instead of raw 409s when the machine has no git. */
export function NoGit({ zh, reason, onRetry }: { zh: boolean; reason?: string; onRetry?: () => void }) {
  return (
    <EmptyState
      icon={<PackageX size={20} />}
      title={zh ? "未安装 Git" : "Git is not installed"}
      hint={
        <>
          {zh
            ? "版本历史和分支需要系统里有 git。安装 Git（git-scm.com）后重启 GlossX 即可启用。在此之前，智能体的每次修改仍会留下检查点，可以在“工作区 → 检查点”里查看和还原。"
            : "Version history and branches need the git program. Install Git (git-scm.com) and restart GlossX to turn them on. Until then every agent edit still leaves a checkpoint you can review and revert under Workspace → Checkpoints."}
          {reason && !/^409/.test(reason) && <span className="gx-nogit-reason">{reason}</span>}
        </>
      }
      action={onRetry && <button type="button" className="gx-btn sm" onClick={onRetry}><RefreshCw size={12} /> {zh ? "重新检测" : "Check again"}</button>}
    />
  );
}

function Changes({ project, changes, zh }: { project: string; changes: VcsChange[]; zh: boolean }) {
  const openVcsDiffTab = useStore((s) => s.openVcsDiffTab);
  const activeTabKey = useStore((s) => s.activeTabKey);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [skip, setSkip] = useState<Set<string>>(new Set());
  const box = useRef<HTMLTextAreaElement>(null);
  const picked = changes.filter((c) => !skip.has(c.path));
  const partial = picked.length !== changes.length;

  useEffect(() => {
    const on = () => box.current?.focus();
    window.addEventListener("glossx:focus-commit", on);
    return () => window.removeEventListener("glossx:focus-commit", on);
  }, []);
  // forget exclusions for files that are no longer changed
  useEffect(() => {
    setSkip((prev) => {
      const next = new Set([...prev].filter((p) => changes.some((c) => c.path === p)));
      return next.size === prev.size ? prev : next;
    });
  }, [changes]);

  async function commit() {
    if (!msg.trim() || !picked.length || busy) return;
    setBusy(true);
    const ok = await commitChanges(project, msg, partial ? picked.map((c) => c.path) : undefined);
    setBusy(false);
    if (ok) setMsg("");
  }

  if (!changes.length) {
    return (
      <EmptyState
        icon={<Check size={20} />}
        title={zh ? "没有未提交的修改" : "No uncommitted changes"}
        hint={zh ? "工作区与最近一次提交一致。智能体每轮结束时会自动提交它的修改。" : "The workspace matches the last commit. The agent commits its own changes at the end of each turn."}
      />
    );
  }
  return (
    <>
      <div className="gx-scm-commit">
        <textarea
          ref={box}
          value={msg}
          rows={Math.min(5, Math.max(2, msg.split("\n").length))}
          placeholder={zh ? "提交说明（Ctrl+Enter 提交）" : "Commit message (Ctrl+Enter to commit)"}
          onChange={(e) => setMsg(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void commit();
            }
          }}
        />
        <button type="button" className="gx-btn primary sm" disabled={!msg.trim() || !picked.length || busy} onClick={() => void commit()}>
          {busy ? <Spinner size={12} /> : <ArrowUp size={13} strokeWidth={2.2} />}
          {partial ? (zh ? `提交 ${picked.length} 个文件` : `Commit ${picked.length} file${picked.length === 1 ? "" : "s"}`) : zh ? "提交全部" : "Commit all"}
        </button>
      </div>
      <div className="gx-scm-head">
        <span>{zh ? "修改" : "Changes"}</span>
        <em className="gx-count">{changes.length}</em>
        <span className="spacer" />
        <button type="button" className="gx-icon-btn" title={zh ? "丢弃全部修改" : "Discard all changes"} onClick={() => void discardChanges(project, changes.map((c) => c.path))}><Undo2 size={13} /></button>
      </div>
      <div className="gx-scm-list">
        {changes.map((c) => {
          const name = c.path.split("/").pop();
          const dir = c.path.includes("/") ? c.path.slice(0, c.path.lastIndexOf("/")) : "";
          const included = !skip.has(c.path);
          return (
            <div key={c.path} className={`gx-scm-row${activeTabKey === `vcsdiff:${project}|${c.path}` ? " active" : ""}${included ? "" : " skipped"}`}>
              <input
                type="checkbox"
                className="gx-check"
                checked={included}
                title={included ? (zh ? "包含在这次提交中" : "Included in this commit") : zh ? "不包含在这次提交中" : "Left out of this commit"}
                onChange={() => setSkip((prev) => {
                  const next = new Set(prev);
                  if (!next.delete(c.path)) next.add(c.path);
                  return next;
                })}
              />
              <button type="button" className="gx-scm-main" title={`${c.path} — ${zh ? "查看差异" : "show the diff"}`} onClick={() => openVcsDiffTab(project, c.path)}>
                <span className="gx-scm-name">{name}{dir && <em>{dir}</em>}</span>
                {c.staged && <span className="gx-scm-staged" title={zh ? "已暂存（git index）" : "Staged in the git index"}>{zh ? "已暂存" : "staged"}</span>}
              </button>
              <button type="button" className="gx-icon-btn gx-scm-discard" title={zh ? "丢弃这个文件的修改" : "Discard changes to this file"} onClick={() => void discardChanges(project, [c.path])}><Undo2 size={13} /></button>
              <StatusLetter status={c.status} zh={zh} />
            </div>
          );
        })}
      </div>
    </>
  );
}

const ROW_H = 44;
const LANE_W = 12;
const laneX = (k: number) => 8 + k * LANE_W;

function GraphCell({ row, head }: { row: { lane: number; top: { from: number; to: number }[]; bottom: { from: number; to: number }[]; width: number }; head: boolean }) {
  const w = laneX(row.width - 1) + 8;
  const mid = ROW_H / 2;
  const seg = (s: { from: number; to: number }, y0: number, y1: number, key: string) => {
    const x0 = laneX(s.from);
    const x1 = laneX(s.to);
    const d = x0 === x1 ? `M${x0} ${y0}V${y1}` : `M${x0} ${y0}C${x0} ${(y0 + y1) / 2} ${x1} ${(y0 + y1) / 2} ${x1} ${y1}`;
    // a segment is drawn in the colour of the lane it runs along (the outer one when it bends)
    return <path key={key} d={d} className={`gx-lane l${Math.max(s.from, s.to) % 5}`} />;
  };
  return (
    <svg className="gx-graph" width={w} height={ROW_H} viewBox={`0 0 ${w} ${ROW_H}`} aria-hidden="true">
      {row.top.map((s, i) => seg(s, 0, mid, `t${i}`))}
      {row.bottom.map((s, i) => seg(s, mid, ROW_H, `b${i}`))}
      <circle cx={laneX(row.lane)} cy={mid} r={head ? 4.5 : 3.5} className={`gx-node l${row.lane % 5}${head ? " head" : ""}`} />
    </svg>
  );
}

/** Commits seen in any history list, so a commit tab can show its header without another request. */
export const commitCache = new Map<string, VcsCommit>();
export const filesCount = (c: VcsCommit) => (Array.isArray(c.files_changed) ? c.files_changed.length : typeof c.files_changed === "number" ? c.files_changed : null);

function HistoryList({ project, branch, branches, zh }: { project: string; branch: string | null; branches: string[]; zh: boolean }) {
  const openCommitTab = useStore((s) => s.openCommitTab);
  const activeTabKey = useStore((s) => s.activeTabKey);
  const rev = useVcs((s) => s.rev);
  const [of, setOf] = useState<string>("");
  const [commits, setCommits] = useState<VcsCommit[] | null>(null);
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(60);
  const shownBranch = of && branches.includes(of) ? of : "";

  useEffect(() => {
    let live = true;
    setError("");
    vcsApi
      .log(project, { branch: shownBranch || undefined, limit })
      .then((list) => {
        if (!live) return;
        for (const c of list) commitCache.set(`${project}|${c.id}`, c);
        setCommits(list);
      })
      .catch((e) => live && (setError(String(e?.message || e)), setCommits([])));
    return () => {
      live = false;
    };
  }, [project, shownBranch, limit, rev, branch]);

  const rows = useMemo(() => layoutGraph(commits || []), [commits]);
  const width = Math.max(1, ...rows.map((r: any) => r.width));

  return (
    <>
      {branches.length > 1 && (
        <div className="gx-scm-head">
          <span>{zh ? "历史" : "History of"}</span>
          <Select
            className="sm"
            value={shownBranch}
            onChange={setOf}
            width={220}
            options={[{ value: "", label: zh ? `当前分支（${branch || "?"}）` : `Current branch (${branch || "?"})`, icon: <GitBranch size={13} /> }, ...branches.filter((b) => b !== branch).map((b) => ({ value: b, label: b, icon: <GitBranch size={13} /> }))]}
          />
        </div>
      )}
      {commits === null ? (
        <div className="gx-chat-loading"><div className="gx-skel w75" /><div className="gx-skel w60" /><div className="gx-skel w90" /></div>
      ) : error ? (
        <EmptyState icon={<History size={20} />} title={zh ? "无法读取历史" : "Could not load the history"} hint={error} />
      ) : commits.length === 0 ? (
        <EmptyState icon={<History size={20} />} title={zh ? "还没有提交" : "No commits yet"} hint={zh ? "第一次提交后，历史会出现在这里。" : "The history appears here after the first commit."} />
      ) : (
        <div className="gx-scm-list gx-log" style={{ ["--gx-graph-w" as any]: `${laneX(width - 1) + 8}px` }}>
          {commits.map((c, i) => {
            const n = filesCount(c);
            const refs = c.refs.filter((r) => r && r !== "HEAD").map((r) => r.replace(/^HEAD -> /, "").replace(/^refs\/heads\//, ""));
            const isHead = i === 0 && !shownBranch;
            return (
              <button
                key={c.id}
                type="button"
                className={`gx-log-row${activeTabKey === `commit:${project}|${c.id}` ? " active" : ""}`}
                title={`${c.short} · ${c.message}`}
                onClick={() => openCommitTab(project, c.id)}
              >
                <span className="gx-log-graph"><GraphCell row={rows[i]} head={isHead} /></span>
                <span className="gx-log-body">
                  <span className="gx-log-msg">
                    <span>{c.message.split("\n")[0] || (zh ? "（无说明）" : "(no message)")}</span>
                    {refs.slice(0, 2).map((r) => <BranchChip key={r} name={r} />)}
                  </span>
                  <span className="gx-log-meta">
                    <code>{c.short}</code>
                    {c.session_id && <span className="gx-log-agent" title={zh ? "由智能体在一轮对话结束时提交" : "Committed by the agent at the end of a turn"}><Sparkles size={10} />{zh ? "智能体" : "agent"}</span>}
                    {!c.session_id && c.author && <span>{c.author}</span>}
                    {n != null && <span>{zh ? `${n} 个文件` : `${n} file${n === 1 ? "" : "s"}`}</span>}
                    <span>{relativeTime(c.time, zh)}</span>
                  </span>
                </span>
              </button>
            );
          })}
          {commits.length >= limit && (
            <button type="button" className="gx-link gx-th-all" onClick={() => setLimit(limit * 3)}>{zh ? "加载更早的提交" : "Load older commits"}</button>
          )}
        </div>
      )}
    </>
  );
}

export function ScmHeadActions() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const project = useStore((s) => s.agentProjectId) || "default";
  return (
    <button type="button" className="gx-icon-btn" title={zh ? "刷新" : "Refresh"} onClick={() => { void useVcs.getState().refresh(project); useVcs.setState((s) => ({ rev: s.rev + 1 })); }}>
      <RefreshCw size={13} />
    </button>
  );
}

export function ScmView() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const projectId = useStore((s) => s.agentProjectId);
  const projectName = useStore((s) => s.projects.find((p) => p.id === s.agentProjectId)?.name);
  const setLayout = useStore((s) => s.setLayout);
  const project = projectId || "default";
  const entry = useVcsEntry(project);
  const [tab, setTab] = useState<"changes" | "history">("changes");
  const refresh = useCallback(() => useVcs.getState().refresh(project), [project]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const on = (e: Event) => {
      const want = (e as CustomEvent).detail;
      if (want === "changes" || want === "history") setTab(want);
    };
    window.addEventListener("glossx:scm-tab", on);
    return () => window.removeEventListener("glossx:scm-tab", on);
  }, []);

  const status = entry.status;
  const changes = status?.changes || [];
  const openThread = useSessions((s) => s.openThread);
  void openThread;

  return (
    <div className="wb-side-body gx-ws gx-scm">
      <div className="gx-ws-head">
        <button type="button" className="gx-ws-scope" title={zh ? "切换项目" : "Switch project"} onClick={() => setLayout({ activity: "projects", sideOpen: true })}>
          <GitCommitHorizontal size={14} />
          <span>{projectName || (zh ? "默认工作区" : "Default workspace")}</span>
        </button>
        {entry.phase === "ok" && status?.branch && <BranchChip name={status.branch} title={zh ? `当前分支：${status.branch}（在状态栏切换）` : `Current branch: ${status.branch} (switch it in the status bar)`} />}
      </div>
      {entry.phase === "loading" ? (
        <div className="gx-chat-loading"><div className="gx-skel w60" /><div className="gx-skel w75" /><div className="gx-skel w40" /></div>
      ) : entry.phase === "nogit" ? (
        <NoGit zh={zh} reason={entry.error} onRetry={() => void refresh()} />
      ) : entry.phase !== "ok" || !status ? (
        <EmptyState
          icon={<GitCommitHorizontal size={20} />}
          title={entry.phase === "missing" ? (zh ? "版本控制不可用" : "Source control unavailable") : zh ? "无法读取版本状态" : "Could not read the repository"}
          hint={entry.phase === "missing" ? (zh ? "当前后端尚未提供工作区的版本控制接口。" : "This backend does not expose version control for workspaces yet.") : entry.error}
          action={<button type="button" className="gx-btn sm" onClick={() => void refresh()}><RefreshCw size={12} /> {zh ? "重试" : "Retry"}</button>}
        />
      ) : (
        <>
          <div className="gx-ws-tabs">
            <Segmented<"changes" | "history">
              size="sm"
              value={tab}
              onChange={setTab}
              options={[
                { value: "changes", label: <>{zh ? "修改" : "Changes"}{changes.length > 0 && <em className="gx-count">{changes.length}</em>}</> },
                { value: "history", label: zh ? "历史" : "History" },
              ]}
            />
          </div>
          <div className="gx-ws-body">
            {tab === "changes"
              ? <Changes project={project} changes={changes} zh={zh} />
              : <HistoryList project={project} branch={status.branch} branches={status.branches.map((b) => b.name)} zh={zh} />}
          </div>
        </>
      )}
    </div>
  );
}
