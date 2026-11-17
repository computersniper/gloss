// Editor tabs for workspace files (markdown edit/preview, monospaced text) and checkpoint diffs.
import { useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import { Check, Eye, FileText, FileX, Lock, Pencil, RefreshCw, Save, Undo2 } from "lucide-react";
import { workspaceApi, type Checkpoint } from "../../api/harness";
import { splitWorkspaceId, useStore } from "../../store";
import { Markdown } from "../Markdown";
import { EmptyState, Segmented, Spinner } from "../ui";
import { DiffView, loadCheckpoint, revertCheckpoint, rowsForCheckpoint, statsOf, useCheckpointMarks } from "../session/DiffView";
import { relativeTime } from "../session/toolMeta";
import { DocumentView, PdfPages, formatLabel, rawUrl, type DocPayload } from "./DocumentView";

// Office documents, PDFs and images come back read-only with a viewer kind.
type Viewer = { readonly?: boolean; mtime?: number; viewer?: "document" | "pdf" | "image"; document?: DocPayload };
type Buffer = { content: string; saved: string; loading: boolean; saving: boolean; error: string | null; loaded: boolean; view?: Viewer };
const BLANK: Buffer = { content: "", saved: "", loading: false, saving: false, error: null, loaded: false };

export const useFiles = create<{
  buffers: Record<string, Buffer>;
  load: (id: string, force?: boolean) => Promise<void>;
  edit: (id: string, content: string) => void;
  save: (id: string) => Promise<boolean>;
  drop: (id: string) => void;
}>((set, get) => {
  const patch = (id: string, p: Partial<Buffer>) => set((s) => ({ buffers: { ...s.buffers, [id]: { ...(s.buffers[id] || BLANK), ...p } } }));
  return {
    buffers: {},
    load: async (id, force) => {
      const cur = get().buffers[id];
      if (cur?.loading || (cur?.loaded && !force)) return;
      if (cur?.loaded && cur.content !== cur.saved) return; // never clobber unsaved edits
      patch(id, { loading: true, error: null });
      const { project, ref } = splitWorkspaceId(id);
      try {
        const file = await workspaceApi.read(project, ref);
        const view = file.readonly ? { readonly: true, viewer: file.viewer, document: file.document, mtime: file.mtime } : undefined;
        patch(id, { content: file.content, saved: file.content, loading: false, loaded: true, view });
      } catch (e: any) {
        patch(id, { loading: false, loaded: true, error: String(e?.message || e) });
      }
    },
    edit: (id, content) => patch(id, { content }),
    save: async (id) => {
      const cur = get().buffers[id];
      if (!cur || cur.saving || cur.view?.readonly) return false;
      const { project, ref } = splitWorkspaceId(id);
      const content = cur.content;
      patch(id, { saving: true });
      try {
        await workspaceApi.write(project, ref, content);
        patch(id, { saving: false, saved: content, error: null });
        window.dispatchEvent(new CustomEvent("glossx:workspace-changed", { detail: { self: id } }));
        return true;
      } catch (e: any) {
        patch(id, { saving: false });
        useStore.getState().notify(String(e?.message || e));
        return false;
      }
    },
    drop: (id) => set((s) => {
      const buffers = { ...s.buffers };
      delete buffers[id];
      return { buffers };
    }),
  };
});

export const isDirty = (id: string) => {
  const b = useFiles.getState().buffers[id];
  return !!b && b.loaded && b.content !== b.saved;
};

const isMarkdown = (path: string) => /\.(md|markdown|mdx)$/i.test(path);

function CodeArea({ value, onChange, wrap }: { value: string; onChange: (v: string) => void; wrap: boolean }) {
  const gutter = useRef<HTMLPreElement>(null);
  const lines = useMemo(() => value.split("\n").length, [value]);
  const numbers = useMemo(() => Array.from({ length: lines }, (_, i) => i + 1).join("\n"), [lines]);
  return (
    <div className={`gx-code${wrap ? " wrap" : ""}`}>
      {!wrap && <pre className="gx-code-gutter" ref={gutter} aria-hidden="true">{numbers}</pre>}
      <textarea
        value={value}
        spellCheck={false}
        wrap={wrap ? "soft" : "off"}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop;
        }}
        onKeyDown={(e) => {
          if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            const ta = e.currentTarget;
            const { selectionStart: s, selectionEnd: en } = ta;
            onChange(`${value.slice(0, s)}  ${value.slice(en)}`);
            requestAnimationFrame(() => ta.setSelectionRange(s + 2, s + 2));
          }
        }}
      />
    </div>
  );
}

export function FileEditor({ id, visible }: { id: string; visible: boolean }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const buf = useFiles((s) => s.buffers[id]) || BLANK;
  const load = useFiles((s) => s.load);
  const edit = useFiles((s) => s.edit);
  const save = useFiles((s) => s.save);
  const { ref: path } = splitWorkspaceId(id);
  const md = isMarkdown(path);
  const [view, setView] = useState<"edit" | "preview">(md ? "preview" : "edit");
  const dirty = buf.loaded && buf.content !== buf.saved;
  const viewer = buf.view?.viewer;
  const readonly = !!buf.view?.readonly;
  const [pdfView, setPdfView] = useState<"pages" | "text">("pages");
  const { project } = splitWorkspaceId(id);

  useEffect(() => {
    void load(id);
  }, [id]);

  // pick up agent edits when this buffer has no unsaved changes
  useEffect(() => {
    const on = (e: Event) => {
      if ((e as CustomEvent).detail?.self === id) return;
      if (!isDirty(id)) void load(id, true);
    };
    window.addEventListener("glossx:workspace-changed", on);
    return () => window.removeEventListener("glossx:workspace-changed", on);
  }, [id]);

  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save(id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, id]);

  const parts = path.split("/");
  return (
    <div className={`wb-doc gx-file${visible ? "" : " hidden"}`}>
      <div className="gx-file-bar">
        <div className="gx-crumbs" title={path}>
          {parts.map((p, i) => (
            <span key={i} className={i === parts.length - 1 ? "leaf" : ""}>{p}</span>
          ))}
          {dirty && <i className="gx-dirty" title={zh ? "未保存" : "Unsaved"} />}
        </div>
        <span className="spacer" />
        {readonly && (
          <span className="gx-file-meta" title={zh ? "只读预览" : "Read-only preview"}>
            <Lock size={11} /> {formatLabel(buf.view?.document?.format) || (viewer === "image" ? (zh ? "图片" : "Image") : "")} · {zh ? "只读" : "Read-only"}
          </span>
        )}
        {viewer === "pdf" && (
          <Segmented<"pages" | "text">
            size="sm"
            value={pdfView}
            onChange={setPdfView}
            options={[
              { value: "pages", label: zh ? "页面" : "Pages", icon: <Eye size={12} /> },
              { value: "text", label: zh ? "文字" : "Text", icon: <FileText size={12} /> },
            ]}
          />
        )}
        {md && !readonly && (
          <Segmented<"edit" | "preview">
            size="sm"
            value={view}
            onChange={setView}
            options={[
              { value: "edit", label: zh ? "编辑" : "Edit", icon: <Pencil size={12} /> },
              { value: "preview", label: zh ? "预览" : "Preview", icon: <Eye size={12} /> },
            ]}
          />
        )}
        {!readonly && (
          <button type="button" className="gx-btn sm" disabled={!dirty || buf.saving} title="Ctrl+S" onClick={() => void save(id)}>
            {buf.saving ? <Spinner size={12} /> : dirty ? <Save size={12} /> : <Check size={12} />}
            {dirty ? (zh ? "保存" : "Save") : zh ? "已保存" : "Saved"}
          </button>
        )}
      </div>
      <div className="gx-file-body">
        {buf.loading && !buf.loaded ? (
          <div className="gx-chat-loading"><div className="gx-skel w40" /><div className="gx-skel w90" /><div className="gx-skel w75" /><div className="gx-skel w60" /></div>
        ) : buf.error ? (
          <EmptyState
            icon={<FileX size={22} />}
            title={zh ? "无法打开文件" : "Could not open this file"}
            hint={buf.error}
            action={<button type="button" className="gx-btn sm" onClick={() => void load(id, true)}><RefreshCw size={12} /> {zh ? "重试" : "Retry"}</button>}
          />
        ) : viewer === "image" ? (
          <div className="gx-file-image"><img src={`${rawUrl(project, path)}&v=${buf.view?.mtime || 0}`} alt={path} /></div>
        ) : viewer === "pdf" && pdfView === "pages" ? (
          <div className="gx-docv"><div className="gx-docv-scroll"><PdfPages url={rawUrl(project, path)} zh={zh} /></div></div>
        ) : readonly && buf.view?.document ? (
          <DocumentView doc={buf.view.document} project={project} path={path} zh={zh} />
        ) : md && view === "preview" ? (
          <div className="gx-file-preview">
            {buf.content.trim() ? <article className="gx-prose reading"><Markdown text={buf.content} /></article> : (
              <EmptyState title={zh ? "空文档" : "Empty document"} hint={zh ? "切换到“编辑”开始书写。" : "Switch to Edit to start writing."} />
            )}
          </div>
        ) : (
          <CodeArea value={buf.content} onChange={(v) => edit(id, v)} wrap={md} />
        )}
      </div>
    </div>
  );
}

export function CheckpointView({ id, visible }: { id: string; visible: boolean }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const openFileTab = useStore((s) => s.openFileTab);
  const notify = useStore((s) => s.notify);
  const { project, ref } = splitWorkspaceId(id);
  const mark = useCheckpointMarks((s) => s.marks[ref]);
  const setMark = useCheckpointMarks((s) => s.mark);
  const [cp, setCp] = useState<Checkpoint | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    void loadCheckpoint(project, ref).then((c) => live && setCp(c));
    return () => {
      live = false;
    };
  }, [project, ref, mark]);

  const rows = useMemo(() => rowsForCheckpoint(cp), [cp]);
  const stats = rows ? statsOf(rows) : { added: cp?.lines_added || 0, removed: cp?.lines_removed || 0 };
  const reverted = mark === "reverted" || cp?.status === "reverted";

  async function revert() {
    setBusy(true);
    try {
      const r = await revertCheckpoint(project, ref);
      notify(r?.conflict ? (zh ? "已还原（文件在此之后被改动过）" : "Reverted (the file had changed since)") : zh ? "已还原到修改前" : "Reverted to the previous content");
    } catch (e: any) {
      notify(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`wb-doc gx-file${visible ? "" : " hidden"}`}>
      {cp === undefined ? (
        <div className="gx-file-body"><div className="gx-chat-loading"><div className="gx-skel w40" /><div className="gx-skel w90" /><div className="gx-skel w75" /></div></div>
      ) : cp === null ? (
        <div className="gx-file-body"><EmptyState icon={<FileX size={22} />} title={zh ? "找不到这个检查点" : "Checkpoint not found"} /></div>
      ) : (
        <>
          <div className="gx-file-bar">
            <div className="gx-crumbs" title={cp.path}>
              {cp.path.split("/").map((p, i, all) => <span key={i} className={i === all.length - 1 ? "leaf" : ""}>{p}</span>)}
            </div>
            <span className="gx-diffstat"><b>+{stats.added}</b><i>−{stats.removed}</i></span>
            <span className="gx-file-meta">
              {[cp.source, relativeTime(cp.created_at, zh)].filter(Boolean).join(" · ")}
            </span>
            <span className="spacer" />
            <button type="button" className="gx-btn ghost sm" onClick={() => openFileTab(project, cp.path)}>{zh ? "打开文件" : "Open file"}</button>
            {reverted ? (
              <span className="gx-tag muted"><Undo2 size={12} /> {zh ? "已还原" : "Reverted"}</span>
            ) : (
              <>
                <button type="button" className="gx-btn sm" disabled={busy} onClick={() => void revert()}>
                  {busy ? <Spinner size={12} /> : <Undo2 size={12} />} {zh ? "还原" : "Revert"}
                </button>
                {mark === "accepted" ? (
                  <span className="gx-tag ok"><Check size={12} /> {zh ? "已接受" : "Accepted"}</span>
                ) : (
                  <button type="button" className="gx-btn primary sm" onClick={() => setMark(ref, "accepted")}><Check size={12} /> {zh ? "接受" : "Accept"}</button>
                )}
              </>
            )}
          </div>
          <div className="gx-file-body gx-file-diff">
            {rows ? <DiffView rows={rows} collapsedLines={0} zh={zh} /> : (
              <EmptyState title={zh ? "这个检查点没有可显示的差异" : "No diff is available for this checkpoint"} hint={zh ? "后端没有返回修改前后的内容。" : "The backend did not return the before/after content."} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
