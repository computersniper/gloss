import { FormEvent, KeyboardEvent, MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, Paper, ReadingLens } from "../api/client";
import { collectTags, filterPapersByTag, mergeTags, removeTag, tagsMatch } from "../libraryTags.mjs";
import { useStore } from "../store";
import { ImportQueue } from "./ImportQueue";
import { Logo } from "./Logo";

export function Library() {
  const papers = useStore((s) => s.papers);
  const openPaper = useStore((s) => s.openPaper);
  const loadPapers = useStore((s) => s.loadPapers);
  const updatePaper = useStore((s) => s.updatePaper);
  const enqueueImport = useStore((s) => s.enqueueImport);
  const notify = useStore((s) => s.notify);
  const uiLang = useStore((s) => s.uiLang);
  const T = {
    en: {
      tagline: "Annotate your papers with light · a local AI paper-reading companion, powered by Claude, Codex, or your preferred AI provider.",
      placeholder: "arXiv id / URL / DOI  (e.g. 1706.03762)",
      import: "Import",
      upload: "Upload PDF",
      working: "Downloading and parsing… this usually takes about a minute.",
      empty: "No papers yet — import an arXiv id or upload a PDF.",
      confirmDelete: "Delete this paper?",
      directions: "Research directions",
      all: "All",
      noMatches: "No papers match this direction.",
      editTitle: "Edit title",
      editPaper: "Edit paper details",
      saveTitle: "Save title",
      cancel: "Cancel",
      titleRequired: "A paper title cannot be empty.",
      addTag: "Add a direction tag",
      tagPlaceholder: "+ Add tag",
      removeTag: (tag: string) => `Remove ${tag}`,
      updateFailed: "Could not update paper: ",
      readingLens: "Reading lens",
      lens: {
        auto: "Auto",
        humanities: "Humanities",
        social_sciences: "Social sciences",
        stem: "STEM",
      } as Record<ReadingLens, string>,
      pages: (n: number) => `${n}p`,
    },
    zh: {
      tagline: "照亮论文的批注 · 本地 AI 论文阅读助手，由 Claude、Codex 或你选择的 AI 提供方驱动。",
      placeholder: "arXiv 编号 / 链接 / DOI  (例如 1706.03762)",
      import: "导入",
      upload: "上传 PDF",
      working: "正在下载并解析…通常需要约一分钟。",
      empty: "还没有论文 — 导入 arXiv 编号或上传 PDF。",
      confirmDelete: "删除这篇论文？",
      directions: "研究方向",
      all: "全部",
      noMatches: "这个方向下还没有论文。",
      editTitle: "编辑标题",
      editPaper: "编辑论文信息",
      saveTitle: "保存标题",
      cancel: "取消",
      titleRequired: "论文标题不能为空。",
      addTag: "添加研究方向标签",
      tagPlaceholder: "+ 添加标签",
      removeTag: (tag: string) => `删除标签 ${tag}`,
      updateFailed: "更新论文失败：",
      readingLens: "阅读视角",
      lens: {
        auto: "自动识别",
        humanities: "人文学科",
        social_sciences: "社会科学",
        stem: "理工科",
      } as Record<ReadingLens, string>,
      pages: (n: number) => `${n} 页`,
    },
  }[uiLang];
  const [importQ, setImportQ] = useState("");
  const [uploadBusy, setUploadBusy] = useState(false);
  const [creatingImport, setCreatingImport] = useState(false);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const allTags = useMemo(() => collectTags(papers) as string[], [papers]);
  const visiblePapers = useMemo(
    () => filterPapersByTag(papers, activeTag) as Paper[],
    [papers, activeTag],
  );

  useEffect(() => {
    if (activeTag && !allTags.some((tag) => tagsMatch(tag, activeTag))) setActiveTag(null);
  }, [activeTag, allTags]);

  function selectTag(tag: string) {
    setActiveTag(allTags.find((knownTag) => tagsMatch(knownTag, tag)) || tag);
  }

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploadBusy(true);
    try {
      for (const f of Array.from(files)) {
        const p = await api.uploadPaper(f);
        notify("Added: " + (p.title || f.name).slice(0, 40));
      }
      await loadPapers();
    } catch (e: any) {
      notify("Upload failed: " + (e.message || e));
    } finally {
      setUploadBusy(false);
    }
  }

  async function onImport() {
    if (!importQ.trim()) return;
    setCreatingImport(true);
    try {
      await enqueueImport(importQ.trim());
      setImportQ("");
    } catch (e: any) {
      notify("Import failed: " + (e.message || e));
    } finally {
      setCreatingImport(false);
    }
  }

  async function del(id: string, e: MouseEvent) {
    e.stopPropagation();
    if (!confirm(T.confirmDelete)) return;
    await api.deletePaper(id);
    await loadPapers();
  }

  return (
    <div className="library">
      <div className="lib-hero">
        <h1 className="hero-title"><Logo size={40} /> Gloss <span className="hero-zh">旁注</span></h1>
        <p className="tagline">{T.tagline}</p>
        <div className="import-row">
          <input
            className="import-input"
            placeholder={T.placeholder}
            value={importQ}
            onChange={(e) => setImportQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onImport()}
          />
          <button onClick={onImport} disabled={creatingImport || !importQ.trim()}>{T.import}</button>
          <button onClick={() => fileRef.current?.click()} disabled={uploadBusy}>{T.upload}</button>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            multiple
            hidden
            onChange={(e) => onUpload(e.target.files)}
          />
        </div>
        <ImportQueue />
      </div>

      {allTags.length > 0 && (
        <div className="library-filters" aria-label={T.directions}>
          <span className="filter-label">{T.directions}</span>
          <button
            className={`filter-chip ${activeTag === null ? "active" : ""}`}
            aria-pressed={activeTag === null}
            onClick={() => setActiveTag(null)}
          >
            {T.all}<span>{papers.length}</span>
          </button>
          {allTags.map((tag) => {
            const count = filterPapersByTag(papers, tag).length;
            return (
              <button
                className={`filter-chip ${tagsMatch(activeTag, tag) ? "active" : ""}`}
                aria-pressed={tagsMatch(activeTag, tag)}
                key={tag}
                onClick={() => setActiveTag(tag)}
              >
                {tag}<span>{count}</span>
              </button>
            );
          })}
        </div>
      )}

      <div className="lib-grid">
        {papers.length === 0 && <div className="muted empty">{T.empty}</div>}
        {papers.length > 0 && visiblePapers.length === 0 && (
          <div className="muted empty">{T.noMatches}</div>
        )}
        {visiblePapers.map((paper) => (
          <PaperCard
            key={paper.id}
            paper={paper}
            labels={T}
            onOpen={() => openPaper(paper.id)}
            onDelete={(event) => del(paper.id, event)}
            onFilter={selectTag}
            onUpdate={(fields) => updatePaper(paper.id, fields)}
            notify={notify}
          />
        ))}
      </div>
    </div>
  );
}

interface PaperCardProps {
  paper: Paper;
  labels: any;
  onOpen: () => void;
  onDelete: (event: MouseEvent) => Promise<void>;
  onFilter: (tag: string) => void;
  onUpdate: (fields: Partial<Pick<Paper, "title" | "tags" | "reading_lens">>) => Promise<Paper>;
  notify: (message: string | null) => void;
}

function PaperCard({ paper, labels: T, onOpen, onDelete, onFilter, onUpdate, notify }: PaperCardProps) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(paper.title || "");
  const [lensDraft, setLensDraft] = useState<ReadingLens>(paper.reading_lens || "auto");
  const [tagDraft, setTagDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => setTitleDraft(paper.title || ""), [paper.title]);
  useEffect(() => setLensDraft(paper.reading_lens || "auto"), [paper.reading_lens]);

  function stop(event: { stopPropagation: () => void }) {
    event.stopPropagation();
  }

  async function saveTitle(event?: FormEvent) {
    event?.preventDefault();
    event?.stopPropagation();
    const title = titleDraft.trim().replace(/\s+/g, " ");
    if (!title) {
      notify(T.titleRequired);
      return;
    }
    if (title === paper.title && lensDraft === (paper.reading_lens || "auto")) {
      setEditingTitle(false);
      return;
    }
    setSaving(true);
    try {
      await onUpdate({ title, reading_lens: lensDraft });
      setEditingTitle(false);
    } catch (error: any) {
      notify(T.updateFailed + (error?.message || error));
    } finally {
      setSaving(false);
    }
  }

  function cancelTitle() {
    setTitleDraft(paper.title || "");
    setLensDraft(paper.reading_lens || "auto");
    setEditingTitle(false);
  }

  async function addTags(event: FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    const tags = mergeTags(paper.tags, tagDraft) as string[];
    if (!tagDraft.trim() || tags.length === (paper.tags || []).length) {
      setTagDraft("");
      return;
    }
    setSaving(true);
    try {
      await onUpdate({ tags });
      setTagDraft("");
    } catch (error: any) {
      notify(T.updateFailed + (error?.message || error));
    } finally {
      setSaving(false);
    }
  }

  async function deleteTag(tag: string, event: MouseEvent) {
    event.stopPropagation();
    setSaving(true);
    try {
      await onUpdate({ tags: removeTag(paper.tags, tag) as string[] });
    } catch (error: any) {
      notify(T.updateFailed + (error?.message || error));
    } finally {
      setSaving(false);
    }
  }

  function onTitleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelTitle();
    }
  }

  return (
    <article className="card" onClick={onOpen}>
      {editingTitle ? (
        <form className="card-details-editor" onSubmit={saveTitle} onClick={stop}>
          <div className="card-title-editor">
            <input
              autoFocus
              maxLength={300}
              value={titleDraft}
              onChange={(event) => setTitleDraft(event.target.value)}
              onKeyDown={onTitleKeyDown}
              aria-label={T.editTitle}
            />
            <button type="submit" disabled={saving} aria-label={T.saveTitle}>✓</button>
            <button type="button" onClick={cancelTitle} aria-label={T.cancel}>×</button>
          </div>
          <label className="reading-lens-control">
            <span>{T.readingLens}</span>
            <select
              aria-label={T.readingLens}
              value={lensDraft}
              disabled={saving}
              onChange={(event) => setLensDraft(event.target.value as ReadingLens)}
            >
              {(Object.keys(T.lens) as ReadingLens[]).map((lens) => (
                <option key={lens} value={lens}>{T.lens[lens]}</option>
              ))}
            </select>
          </label>
        </form>
      ) : (
        <div className="card-title">{paper.title || "Untitled"}</div>
      )}
      <div className="card-auth">{(paper.authors || []).slice(0, 4).join(", ")}</div>
      <div className="card-meta">
        <span>{T.pages(paper.n_pages)}</span>
        {paper.year && <span>· {paper.year}</span>}
        {paper.source && <span>· {paper.source}</span>}
      </div>
      <div className="card-tags" onClick={stop}>
        {(paper.tags || []).map((tag) => (
          <span className="paper-tag" key={tag}>
            <button type="button" className="paper-tag-filter" onClick={() => onFilter(tag)}>#{tag}</button>
            <button
              type="button"
              className="paper-tag-remove"
              disabled={saving}
              aria-label={T.removeTag(tag)}
              onClick={(event) => deleteTag(tag, event)}
            >×</button>
          </span>
        ))}
        <form className="tag-adder" onSubmit={addTags}>
          <input
            value={tagDraft}
            disabled={saving}
            aria-label={T.addTag}
            placeholder={T.tagPlaceholder}
            onChange={(event) => setTagDraft(event.target.value)}
          />
        </form>
      </div>
      {!editingTitle && (
        <button
          className="card-edit"
          aria-label={T.editPaper}
          title={T.editPaper}
          onClick={(event) => {
            event.stopPropagation();
            setEditingTitle(true);
          }}
        >✎</button>
      )}
      <button className="card-del" aria-label={T.confirmDelete} onClick={onDelete}>×</button>
    </article>
  );
}
