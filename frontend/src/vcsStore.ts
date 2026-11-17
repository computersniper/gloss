// Version control state per workspace (docs/glossx-workspaces.md §3) and the
// user-facing branch / commit actions shared by the status bar, the Source
// control view, the command palette and the chat header.
import { create } from "zustand";
import { ApiError, isMissing, vcsApi, type VcsStatus } from "./api/harness";
import { ask } from "./components/workbench/dialog";
import { useStore } from "./store";

/** loading → ok | nogit (git not installed) | missing (older backend) | error */
export type VcsPhase = "loading" | "ok" | "nogit" | "missing" | "error";
export type VcsEntry = { phase: VcsPhase; status: VcsStatus | null; error?: string; at: number };

const inflight = new Map<string, Promise<void>>();
const keyOf = (project?: string | null) => project || "default";
const zh = () => useStore.getState().uiLang === "zh";
const notify = (m: string) => useStore.getState().notify(m);
const message = (e: unknown) => String((e as any)?.message || e);

/** Tell every workspace view (tree, open files, source control) that files or refs changed. */
export const workspaceChanged = () => window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));

interface VcsStore {
  byProject: Record<string, VcsEntry>;
  /** Bumped after commits / branch changes so history lists reload. */
  rev: number;
  refresh: (project?: string | null) => Promise<void>;
  /** Refresh unless a fresh answer is already there. */
  ensure: (project?: string | null, maxAgeMs?: number) => void;
}

export const useVcs = create<VcsStore>((set, get) => ({
  byProject: {},
  rev: 0,
  refresh: (project) => {
    const key = keyOf(project);
    const running = inflight.get(key);
    if (running) return running;
    const put = (entry: Omit<VcsEntry, "at">) => set((s) => ({ byProject: { ...s.byProject, [key]: { ...entry, at: Date.now() } } }));
    const job = vcsApi
      .status(key)
      .then((status) => put({ phase: status.available ? "ok" : "nogit", status, error: status.reason }))
      .catch((e) => {
        // 409 = "git is not available" on every VCS endpoint; 404/405 = a backend without version control
        const phase: VcsPhase = e instanceof ApiError && e.status === 409 ? "nogit" : isMissing(e) || (e instanceof ApiError && (e.status === 0 || e.status === 404)) ? "missing" : "error";
        put({ phase, status: get().byProject[key]?.status ?? null, error: message(e) });
      })
      .finally(() => inflight.delete(key));
    inflight.set(key, job);
    return job;
  },
  ensure: (project, maxAgeMs = 4000) => {
    const entry = get().byProject[keyOf(project)];
    if (!entry || Date.now() - entry.at > maxAgeMs) void get().refresh(project);
  },
}));

const LOADING: VcsEntry = { phase: "loading", status: null, at: 0 };
export const useVcsEntry = (project?: string | null): VcsEntry => useVcs((s) => s.byProject[keyOf(project)]) || LOADING;
export const vcsEntry = (project?: string | null): VcsEntry => useVcs.getState().byProject[keyOf(project)] || LOADING;
/** The workspace's current branch, when git is available there. */
export const useBranch = (project?: string | null): string | null => useVcs((s) => {
  const e = s.byProject[keyOf(project)];
  return e?.phase === "ok" ? e.status?.branch ?? null : null;
});

async function done(project: string | null | undefined) {
  useVcs.setState((s) => ({ rev: s.rev + 1 }));
  await useVcs.getState().refresh(project);
  workspaceChanged();
}

/** Valid-enough git branch name from free text ("try a new idea" → "try-a-new-idea"). */
export function branchName(text: string): string {
  return text.trim().replace(/\s+/g, "-").replace(/[~^:?*[\]\\\x00-\x1f]+/g, "").replace(/\.{2,}/g, ".").replace(/^[-./]+|[./]+$/g, "").slice(0, 80);
}

export async function commitChanges(project: string | null | undefined, text: string, paths?: string[]): Promise<boolean> {
  const msg = text.trim();
  if (!msg) return false;
  try {
    const c = await vcsApi.commit(project, msg, paths);
    notify(zh() ? `已提交${c.short ? ` ${c.short}` : ""}` : `Committed${c.short ? ` ${c.short}` : ""}`);
    await done(project);
    return true;
  } catch (e) {
    notify(message(e));
    void useVcs.getState().refresh(project);
    return false;
  }
}

export async function createBranch(project: string | null | undefined, text: string, opts?: { from?: string | null; checkout?: boolean }): Promise<string | null> {
  const name = branchName(text);
  if (!name) return null;
  try {
    await vcsApi.createBranch(project, name, opts?.from);
    if (opts?.checkout !== false) {
      try {
        await vcsApi.checkout(project, name);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw e;
        await useVcs.getState().refresh(project);
        if (e.code !== "dirty" && (e.code === "busy" || !vcsEntry(project).status?.changes.length)) throw e;
        // the server will not move uncommitted work for us: let the user decide
        useVcs.setState((s) => ({ rev: s.rev + 1 }));
        return (await switchBranch(project, name)) ? name : null;
      }
    }
    notify(zh() ? `已创建分支 ${name}` : `Created branch ${name}`);
    await done(project);
    return name;
  } catch (e) {
    notify(message(e));
    void useVcs.getState().refresh(project);
    return null;
  }
}

/** Switch branch; asks what to do with uncommitted changes first. */
export async function switchBranch(project: string | null | undefined, branch: string): Promise<boolean> {
  const Z = zh();
  await useVcs.getState().refresh(project);
  const status = vcsEntry(project).status;
  if (status?.branch === branch) return true;
  const askDirty = () => {
    const n = vcsEntry(project).status?.changes.length || 0;
    return ask({
      title: Z ? `切换到 ${branch}？` : `Switch to ${branch}?`,
      body: Z
        ? `工作区有${n ? ` ${n} 个` : ""}未提交的修改。切换分支前要怎么处理它们？`
        : `The workspace has ${n ? `${n} ` : ""}uncommitted change${n === 1 ? "" : "s"}. What should happen to ${n === 1 ? "it" : "them"} before switching?`,
      detail: (vcsEntry(project).status?.changes || []).slice(0, 6).map((c) => `${c.status}  ${c.path}`).concat(n > 6 ? [`… +${n - 6}`] : []).join("\n") || undefined,
      actions: [
        { id: "stash", label: Z ? "暂存后切换" : "Stash and switch", hint: Z ? "修改会保存在 git stash 中" : "Changes are kept in the git stash", primary: true },
        { id: "discard", label: Z ? "丢弃修改并切换" : "Discard and switch", hint: Z ? "未提交的修改将永久丢失" : "Uncommitted changes are lost for good", danger: true },
      ],
      cancel: Z ? "取消" : "Cancel",
    }) as Promise<"stash" | "discard" | null>;
  };
  try {
    let how: "stash" | "discard" | undefined;
    if (status && !status.clean) {
      const choice = await askDirty();
      if (!choice) return false;
      how = choice;
    }
    try {
      await vcsApi.checkout(project, branch, how);
    } catch (e) {
      // the tree became dirty in between (the server refuses with 409)
      if (!(e instanceof ApiError && e.status === 409) || how) throw e;
      await useVcs.getState().refresh(project);
      if (e.code !== "dirty" && (e.code === "busy" || !vcsEntry(project).status?.changes.length)) throw e;
      const choice = await askDirty();
      if (!choice) return false;
      await vcsApi.checkout(project, branch, choice);
      how = choice;
    }
    notify(Z ? `已切换到 ${branch}${how === "stash" ? "（修改已暂存）" : ""}` : `Switched to ${branch}${how === "stash" ? " (changes stashed)" : ""}`);
    await done(project);
    return true;
  } catch (e) {
    notify(message(e));
    void useVcs.getState().refresh(project);
    return false;
  }
}

/** Merge `branch` into the current one; conflicts abort the merge and are reported. */
export async function mergeBranch(project: string | null | undefined, branch: string): Promise<boolean> {
  const Z = zh();
  const current = vcsEntry(project).status?.branch || "";
  const ok = await ask({
    title: Z ? `把 ${branch} 合并到 ${current}？` : `Merge ${branch} into ${current}?`,
    body: Z ? "合并会在当前分支上产生新的提交。若有冲突，合并会被中止，工作区保持不变。" : "The merge lands on the current branch. If files conflict, the merge is aborted and the workspace is left untouched.",
    actions: [{ id: "merge", label: Z ? "合并" : "Merge", primary: true }],
    cancel: Z ? "取消" : "Cancel",
  });
  if (!ok) return false;
  try {
    const r = await vcsApi.merge(project, branch);
    if (r.conflicts.length || !r.merged) {
      await ask({
        title: Z ? "合并已中止：存在冲突" : "Merge aborted: conflicting changes",
        body: r.conflicts.length
          ? Z ? `${branch} 与 ${current} 都修改了下列文件。没有任何内容被改动——请先在其中一个分支上协调这些文件，再重新合并。` : `${branch} and ${current} both changed the files below. Nothing was modified — reconcile them on one of the branches, then merge again.`
          : Z ? "合并没有完成，工作区保持不变。" : "The merge did not complete; the workspace is unchanged.",
        detail: r.conflicts.join("\n") || undefined,
        tone: "warn",
        actions: [{ id: "ok", label: Z ? "知道了" : "OK", primary: true }],
        cancel: null,
      });
      void useVcs.getState().refresh(project);
      return false;
    }
    notify(Z ? `已把 ${branch} 合并到 ${current}${r.fast_forward ? "（快进）" : ""}` : `Merged ${branch} into ${current}${r.fast_forward ? " (fast-forward)" : ""}`);
    await done(project);
    return true;
  } catch (e) {
    notify(message(e));
    void useVcs.getState().refresh(project);
    return false;
  }
}

export async function deleteBranch(project: string | null | undefined, branch: string): Promise<boolean> {
  const Z = zh();
  const ok = await ask({
    title: Z ? `删除分支 ${branch}？` : `Delete branch ${branch}?`,
    body: Z ? "只存在于这个分支上、尚未合并的提交将无法再从分支列表访问。" : "Commits that only exist on this branch and were never merged will no longer be reachable from any branch.",
    actions: [{ id: "delete", label: Z ? "删除分支" : "Delete branch", danger: true }],
    cancel: Z ? "取消" : "Cancel",
  });
  if (!ok) return false;
  try {
    await vcsApi.deleteBranch(project, branch);
    notify(Z ? `已删除分支 ${branch}` : `Deleted branch ${branch}`);
    await done(project);
    return true;
  } catch (e) {
    notify(message(e));
    return false;
  }
}

export async function discardChanges(project: string | null | undefined, paths: string[]): Promise<boolean> {
  if (!paths.length) return false;
  const Z = zh();
  const one = paths.length === 1;
  const ok = await ask({
    title: one ? (Z ? `丢弃对 ${paths[0].split("/").pop()} 的修改？` : `Discard changes to ${paths[0].split("/").pop()}?`) : Z ? `丢弃 ${paths.length} 个文件的修改？` : `Discard changes to ${paths.length} files?`,
    body: Z ? "文件会恢复到上一次提交时的内容，新建的文件会被删除。此操作无法撤销。" : "The files go back to their last committed content, and new files are deleted. This cannot be undone.",
    detail: one ? paths[0] : paths.slice(0, 8).concat(paths.length > 8 ? [`… +${paths.length - 8}`] : []).join("\n"),
    actions: [{ id: "discard", label: Z ? "丢弃修改" : "Discard changes", danger: true }],
    cancel: Z ? "取消" : "Cancel",
  });
  if (!ok) return false;
  try {
    await vcsApi.discard(project, paths);
    await done(project);
    return true;
  } catch (e) {
    notify(message(e));
    return false;
  }
}

export async function restoreVersion(project: string | null | undefined, rev: string, short: string, paths?: string[]): Promise<boolean> {
  const Z = zh();
  const dirty = vcsEntry(project).status?.clean === false;
  const ok = await ask({
    title: Z ? `恢复到版本 ${short}？` : `Restore version ${short}?`,
    body:
      (Z ? "文件会恢复成该版本时的内容，并作为一次新的提交记录下来——之后的历史不会丢失。" : "Files return to how they were at this version, recorded as a new commit — later history is kept.") +
      (dirty ? (Z ? " 当前未提交的修改可能被覆盖。" : " Uncommitted changes may be overwritten.") : ""),
    tone: dirty ? "warn" : undefined,
    actions: [{ id: "restore", label: Z ? "恢复这个版本" : "Restore this version", primary: !dirty, danger: dirty }],
    cancel: Z ? "取消" : "Cancel",
  });
  if (!ok) return false;
  try {
    await vcsApi.restore(project, rev, paths);
    notify(Z ? `已恢复到 ${short}` : `Restored ${short}`);
    await done(project);
    return true;
  } catch (e) {
    notify(message(e));
    return false;
  }
}
