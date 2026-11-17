import { useEffect, useMemo, useState } from "react";
import { create } from "zustand";
import { ApiError, workspaceApi, type Checkpoint } from "../../api/harness";
import { diffLines, diffStats, parseUnifiedDiff } from "../../harness/diff.mjs";

export type DiffRow = { kind: "hunk" | "add" | "del" | "ctx"; text: string; a?: number; b?: number };

export function rowsForCheckpoint(cp: Checkpoint | null | undefined): DiffRow[] | null {
  if (!cp) return null;
  if (typeof cp.diff === "string" && cp.diff.trim()) return parseUnifiedDiff(cp.diff) as DiffRow[];
  if (cp.before !== undefined || cp.after !== undefined) return diffLines(cp.before ?? "", cp.after ?? "") as DiffRow[];
  return null;
}

export const statsOf = (rows: DiffRow[]) => diffStats(rows) as { added: number; removed: number };

/** Unified diff with a line-number gutter; long diffs collapse behind a toggle. */
export function DiffView({ rows, collapsedLines = 14, zh }: { rows: DiffRow[]; collapsedLines?: number; zh: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all || collapsedLines <= 0 || rows.length <= collapsedLines + 2 ? rows : rows.slice(0, collapsedLines);
  if (!rows.length) return <div className="gx-diff-empty">{zh ? "没有文本差异" : "No textual changes"}</div>;
  return (
    <div className="gx-diff">
      <div className="gx-diff-scroll">
        {shown.map((r, i) =>
          r.kind === "hunk" ? (
            <div key={i} className="gx-diff-row hunk"><span className="gx-diff-no" /><span className="gx-diff-no" /><code>{r.text}</code></div>
          ) : (
            <div key={i} className={`gx-diff-row ${r.kind}`}>
              <span className="gx-diff-no">{r.a ?? ""}</span>
              <span className="gx-diff-no">{r.b ?? ""}</span>
              <code><i>{r.kind === "add" ? "+" : r.kind === "del" ? "−" : " "}</i>{r.text || " "}</code>
            </div>
          ),
        )}
      </div>
      {shown.length < rows.length && (
        <button type="button" className="gx-diff-more" onClick={() => setAll(true)}>
          {zh ? `显示其余 ${rows.length - shown.length} 行` : `Show ${rows.length - shown.length} more lines`}
        </button>
      )}
    </div>
  );
}

// ---- checkpoint detail cache + local accept/revert marks ----

const detailCache = new Map<string, Promise<Checkpoint | null>>();

export function loadCheckpoint(project: string, id: string, list?: Checkpoint[]): Promise<Checkpoint | null> {
  const key = `${project}|${id}`;
  let hit = detailCache.get(key);
  if (!hit) {
    hit = workspaceApi
      .checkpoint(project, id)
      .catch(async () => {
        // no detail route: fall back to whatever the list carries
        const all = list || (await workspaceApi.checkpoints(project).catch(() => [] as Checkpoint[]));
        return all.find((c) => c.id === id) || null;
      });
    detailCache.set(key, hit);
  }
  return hit;
}

export function useCheckpoint(project: string, id: string | undefined) {
  const [cp, setCp] = useState<Checkpoint | null | undefined>(undefined);
  useEffect(() => {
    if (!id) return;
    let live = true;
    setCp(undefined);
    void loadCheckpoint(project, id).then((c) => live && setCp(c));
    return () => {
      live = false;
    };
  }, [project, id]);
  return cp;
}

const MARKS_KEY = "glossx.checkpointMarks";
type Mark = "accepted" | "reverted";

function readMarks(): Record<string, Mark> {
  try {
    return JSON.parse(localStorage.getItem(MARKS_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

export const useCheckpointMarks = create<{ marks: Record<string, Mark>; mark: (id: string, m: Mark) => void }>((set, get) => ({
  marks: readMarks(),
  mark: (id, m) => {
    const entries = Object.entries({ ...get().marks, [id]: m }).slice(-400);
    const marks = Object.fromEntries(entries);
    try {
      localStorage.setItem(MARKS_KEY, JSON.stringify(marks));
    } catch {
      /* ignore */
    }
    set({ marks });
  },
}));

/** Revert a checkpoint, remember it locally and tell workspace views to refresh. */
export async function revertCheckpoint(project: string, id: string) {
  let result: unknown;
  try {
    result = await workspaceApi.revert(project, id);
  } catch (e) {
    // 409: this checkpoint was already reverted (elsewhere) — just record that
    if (!(e instanceof ApiError && e.status === 409)) throw e;
  }
  detailCache.delete(`${project}|${id}`);
  useCheckpointMarks.getState().mark(id, "reverted");
  window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
  return result as { conflict?: boolean } | undefined;
}

export function useDiffRows(cp: Checkpoint | null | undefined, fallback: () => DiffRow[] | null, deps: unknown[]) {
  return useMemo(() => rowsForCheckpoint(cp) || fallback(), [cp, ...deps]);
}
