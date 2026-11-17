// Editor tabs for version control: a file's uncommitted diff, and one commit
// (message, files, diff, "restore this version").
import { useEffect, useMemo, useRef, useState } from "react";
import { FileX, GitCommitHorizontal, History, RefreshCw, RotateCcw, Sparkles, Undo2 } from "lucide-react";
import { vcsApi, workspaceApi, type VcsCommit } from "../../api/harness";
import { diffLines, parseUnifiedDiff } from "../../harness/diff.mjs";
import { splitDiff } from "../../harness/vcs.mjs";
import { splitWorkspaceId, useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { discardChanges, restoreVersion, useVcs, useVcsEntry } from "../../vcsStore";
import { DiffView, statsOf, type DiffRow } from "../session/DiffView";
import { EmptyState, Spinner } from "../ui";
import { BranchChip } from "./ThreadsView";
import { NoGit, StatusLetter, commitCache } from "./ScmView";

type Section = { path: string; oldPath: string; status: string; binary: boolean; text: string; rows: DiffRow[]; added: number; removed: number };

function sectionsOf(diff: string): Section[] {
  return (splitDiff(diff) as Omit<Section, "rows" | "added" | "removed">[]).map((s) => {
    const rows = parseUnifiedDiff(s.text) as DiffRow[];
    const st = statsOf(rows);
    return { ...s, rows, added: st.added, removed: st.removed };
  });
}

const Skeleton = () => <div className="gx-file-body"><div className="gx-chat-loading"><div className="gx-skel w40" /><div className="gx-skel w90" /><div className="gx-skel w75" /></div></div>;

/** Uncommitted changes of one file (working tree vs the last commit). */
export function VcsDiffView({ id, visible }: { id: string; visible: boolean }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const openFileTab = useStore((s) => s.openFileTab);
  const closeTab = useStore((s) => s.closeTab);
  const { project, ref: path } = splitWorkspaceId(id);
  const entry = useVcsEntry(project);
  const change = entry.status?.changes.find((c) => c.path === path);
  const [rows, setRows] = useState<DiffRow[] | null | undefined>(undefined);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setError("");
    (async () => {
      try {
        const text = await vcsApi.diff(project, { path });
        const own = (splitDiff(text) as { path: string; text: string }[]);
        const section = own.find((s) => s.path === path) || own[0];
        let next = section ? (parseUnifiedDiff(section.text) as DiffRow[]) : [];
        if (!next.length) {
          // untracked files have no diff against HEAD: show the whole file as added
          const [after, before] = await Promise.all([
            workspaceApi.read(project, path).then((f) => f.content).catch(() => ""),
            vcsApi.file(project, "HEAD", path).catch(() => ""),
          ]);
          if (after !== before) next = diffLines(before, after, 3) as DiffRow[];
        }
        if (live) setRows(next);
      } catch (e: any) {
        if (live) {
          setError(String(e?.message || e));
          setRows(null);
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [project, path, tick]);

  useEffect(() => {
    let timer = 0;
    const on = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setTick((t) => t + 1), 300);
    };
    window.addEventListener("glossx:workspace-changed", on);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("glossx:workspace-changed", on);
    };
  }, []);

  const stats = rows ? statsOf(rows) : { added: 0, removed: 0 };
  const parts = path.split("/");
  return (
    <div className={`wb-doc gx-file${visible ? "" : " hidden"}`}>
      <div className="gx-file-bar">
        {change && <StatusLetter status={change.status} zh={zh} />}
        <div className="gx-crumbs" title={path}>
          {parts.map((p, i) => <span key={i} className={i === parts.length - 1 ? "leaf" : ""}>{p}</span>)}
        </div>
        <span className="gx-diffstat"><b>+{stats.added}</b><i>−{stats.removed}</i></span>
        <span className="gx-file-meta">{zh ? "未提交的修改 · 对比上一次提交" : "Uncommitted · compared with the last commit"}</span>
        <span className="spacer" />
        <button type="button" className="gx-btn ghost sm" onClick={() => openFileTab(project, path)}>{zh ? "打开文件" : "Open file"}</button>
        {change && (
          <button
            type="button"
            className="gx-btn sm"
            onClick={async () => {
              if (await discardChanges(project, [path])) closeTab(`vcsdiff:${id}`);
            }}
          >
            <Undo2 size={12} /> {zh ? "丢弃修改" : "Discard"}
          </button>
        )}
      </div>
      {rows === undefined ? <Skeleton /> : (
        <div className="gx-file-body gx-file-diff">
          {entry.phase === "nogit" ? <NoGit zh={zh} /> : rows === null ? (
            <EmptyState
              icon={<FileX size={22} />}
              title={zh ? "无法读取差异" : "Could not load the diff"}
              hint={error}
              action={<button type="button" className="gx-btn sm" onClick={() => setTick((t) => t + 1)}><RefreshCw size={12} /> {zh ? "重试" : "Retry"}</button>}
            />
          ) : rows.length === 0 ? (
            <EmptyState title={zh ? "这个文件没有未提交的修改" : "This file has no uncommitted changes"} hint={zh ? "它与上一次提交的内容一致。" : "It matches the last commit."} />
          ) : (
            <DiffView rows={rows} collapsedLines={0} zh={zh} />
          )}
        </div>
      )}
    </div>
  );
}

/** One commit: header, changed files, their diffs, and "restore this version". */
export function CommitView({ id, visible }: { id: string; visible: boolean }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const openFileTab = useStore((s) => s.openFileTab);
  const { project, ref: rev } = splitWorkspaceId(id);
  const vcsRev = useVcs((s) => s.rev);
  const entry = useVcsEntry(project);
  const [commit, setCommit] = useState<VcsCommit | null | undefined>(() => commitCache.get(id));
  const [sections, setSections] = useState<Section[] | null | undefined>(undefined);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      let c = commitCache.get(id);
      if (!c) {
        // opened from a chat marker: look the commit up in the recent history (ids may be abbreviated)
        const log = await vcsApi.log(project, { limit: 300 }).catch(() => [] as VcsCommit[]);
        for (const x of log) commitCache.set(`${project}|${x.id}`, x);
        c = log.find((x) => x.id === rev || x.id.startsWith(rev) || x.short === rev);
      }
      if (!live) return;
      setCommit(c || null);
      const to = c?.id || rev;
      const attempts = c ? [c.parents[0] || undefined] : [`${rev}^`, undefined];
      let lastError = "";
      for (const from of attempts) {
        try {
          const text = await vcsApi.diff(project, { from, to });
          if (live) setSections(sectionsOf(text));
          return;
        } catch (e: any) {
          lastError = String(e?.message || e);
        }
      }
      if (live) {
        setError(lastError);
        setSections(null);
      }
    })();
    return () => {
      live = false;
    };
  }, [id, vcsRev]);

  const totals = useMemo(() => (sections || []).reduce((t, s) => ({ added: t.added + s.added, removed: t.removed + s.removed }), { added: 0, removed: 0 }), [sections]);
  const short = commit?.short || rev.slice(0, 7);
  const [subject, ...body] = (commit?.message || "").split("\n");
  const isHead = !!commit && entry.status?.branches.some((b) => b.current && b.head && (commit.id.startsWith(b.head) || b.head.startsWith(commit.id)));
  const refs = (commit?.refs || []).filter((r) => r && r !== "HEAD").map((r) => r.replace(/^HEAD -> /, "").replace(/^refs\/heads\//, ""));
  const jump = (i: number) => scroller.current?.querySelector(`[data-file="${i}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });

  return (
    <div className={`wb-doc gx-file gx-commit${visible ? "" : " hidden"}`}>
      {entry.phase === "nogit" ? <div className="gx-file-body"><NoGit zh={zh} /></div> : sections === undefined ? <Skeleton /> : (
        <div className="gx-commit-scroll" ref={scroller}>
          <header className="gx-commit-head">
            <div className="gx-commit-kicker">
              <GitCommitHorizontal size={14} />
              <code>{short}</code>
              {refs.map((r) => <BranchChip key={r} name={r} />)}
              {commit?.session_id ? (
                <button type="button" className="gx-link" title={zh ? "打开产生这次提交的对话" : "Open the thread that made this commit"} onClick={() => useSessions.getState().openThread(commit.session_id!)}>
                  <Sparkles size={12} /> {zh ? "智能体自动提交" : "Committed by the agent"}
                </button>
              ) : commit?.author ? <span>{commit.author}</span> : null}
              {commit?.time ? <span>{new Date(commit.time * 1000).toLocaleString(zh ? "zh-CN" : undefined, { dateStyle: "medium", timeStyle: "short" })}</span> : null}
            </div>
            <h1>{subject || (commit === null ? (zh ? `提交 ${short}` : `Commit ${short}`) : zh ? "（无说明）" : "(no message)")}</h1>
            {body.join("\n").trim() && <pre className="gx-commit-body">{body.join("\n").trim()}</pre>}
            <div className="gx-commit-actions">
              <span className="gx-commit-stat">
                {zh ? `${sections?.length ?? 0} 个文件` : `${sections?.length ?? 0} file${sections?.length === 1 ? "" : "s"} changed`}
                <span className="gx-diffstat"><b>+{totals.added}</b><i>−{totals.removed}</i></span>
              </span>
              <span className="spacer" />
              {isHead ? (
                <span className="gx-tag muted"><History size={12} /> {zh ? "这是当前版本" : "This is the current version"}</span>
              ) : (
                <button
                  type="button"
                  className="gx-btn sm"
                  disabled={busy}
                  title={zh ? "把工作区恢复成这个版本的内容，并记为一次新提交" : "Bring the workspace back to this version, recorded as a new commit"}
                  onClick={async () => {
                    setBusy(true);
                    await restoreVersion(project, commit?.id || rev, short);
                    setBusy(false);
                  }}
                >
                  {busy ? <Spinner size={12} /> : <RotateCcw size={12} />} {zh ? "恢复这个版本" : "Restore this version"}
                </button>
              )}
            </div>
          </header>
          {sections === null ? (
            <EmptyState icon={<FileX size={22} />} title={zh ? "无法读取这次提交的差异" : "Could not load this commit’s diff"} hint={error} />
          ) : sections.length === 0 ? (
            <EmptyState title={zh ? "这次提交没有文件改动" : "This commit changes no files"} hint={zh ? "例如一次没有冲突的合并提交。" : "A merge commit without conflicts, for example."} />
          ) : (
            <>
              {sections.length > 1 && (
                <nav className="gx-commit-files" aria-label={zh ? "改动的文件" : "Changed files"}>
                  {sections.map((s, i) => (
                    <button key={s.path + i} type="button" title={s.path} onClick={() => jump(i)}>
                      <StatusLetter status={s.status} zh={zh} />
                      <span>{s.path}</span>
                      <span className="gx-diffstat"><b>+{s.added}</b><i>−{s.removed}</i></span>
                    </button>
                  ))}
                </nav>
              )}
              {sections.map((s, i) => (
                <section key={s.path + i} className="gx-commit-file" data-file={i}>
                  <div className="gx-commit-file-head">
                    <StatusLetter status={s.status} zh={zh} />
                    <code title={s.path}>{s.status === "R" && s.oldPath !== s.path ? `${s.oldPath} → ${s.path}` : s.path}</code>
                    <span className="gx-diffstat"><b>+{s.added}</b><i>−{s.removed}</i></span>
                    <span className="spacer" />
                    {s.status !== "D" && <button type="button" className="gx-link" onClick={() => openFileTab(project, s.path)}>{zh ? "打开文件" : "Open file"}</button>}
                    {!isHead && (
                      <button type="button" className="gx-link" title={zh ? "只把这个文件恢复成该版本" : "Restore only this file to this version"} onClick={() => void restoreVersion(project, commit?.id || rev, short, [s.path])}>
                        <RotateCcw size={11} /> {zh ? "恢复此文件" : "Restore file"}
                      </button>
                    )}
                  </div>
                  {s.binary ? <div className="gx-diff-empty">{zh ? "二进制文件" : "Binary file"}</div> : <DiffView rows={s.rows} collapsedLines={80} zh={zh} />}
                </section>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
