import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store";
import { Markdown } from "./Markdown";

/** User-authored paper notes, stored independently from Deep Paper Note output. */
export function PersonalNotesPanel() {
  const current = useStore((state) => state.current);
  const uiLang = useStore((state) => state.uiLang);
  const T = uiLang === "zh"
    ? {
        placeholder: "记录你的想法、问题、公式推导和阅读结论…",
        loading: "载入个人笔记…",
        saving: "保存中…",
        saved: "已自动保存",
        error: "保存失败",
        edit: "编辑",
        preview: "预览",
        previewEmpty: "还没有内容。切换到“编辑”，用 Markdown 记录笔记。",
        markdownHelp: "支持 Markdown 与 LaTeX 公式",
      }
    : {
        placeholder: "Write your ideas, questions, derivations, and reading conclusions…",
        loading: "Loading personal notes…",
        saving: "Saving…",
        saved: "Autosaved",
        error: "Save failed",
        edit: "Edit",
        preview: "Preview",
        previewEmpty: "Nothing to preview yet. Switch to Edit and write in Markdown.",
        markdownHelp: "Markdown and LaTeX supported",
      };
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const loadSequence = useRef(0);
  const editRevision = useRef(0);
  const saveTimer = useRef<number | null>(null);
  const saveChain = useRef<Promise<unknown>>(Promise.resolve());

  function persist(paperId: string, value: string, revision: number) {
    if (saveTimer.current !== null) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    setSaveState("saving");
    const request = saveChain.current
      .catch(() => undefined)
      .then(() => api.savePersonalNote(paperId, value));
    saveChain.current = request;
    void request
      .then(() => {
        if (useStore.getState().current?.id !== paperId || editRevision.current !== revision) return;
        setDirty(false);
        setSaveState("saved");
      })
      .catch(() => {
        if (useStore.getState().current?.id === paperId && editRevision.current === revision) {
          setSaveState("error");
        }
      });
  }

  useEffect(() => {
    const paperId = current?.id;
    const sequence = ++loadSequence.current;
    setContent("");
    setLoaded(false);
    setDirty(false);
    setMode("edit");
    editRevision.current = 0;
    setSaveState("idle");
    if (!paperId) return;
    api.getPersonalNote(paperId)
      .then((note) => {
        if (loadSequence.current !== sequence) return;
        setContent(note.content || "");
        setLoaded(true);
        setSaveState("saved");
      })
      .catch(() => {
        if (loadSequence.current !== sequence) return;
        setLoaded(true);
        setSaveState("error");
      });
  }, [current?.id]);

  useEffect(() => {
    const paperId = current?.id;
    if (!paperId || !loaded || !dirty) return;
    const revision = editRevision.current;
    setSaveState("saving");
    const timer = window.setTimeout(() => {
      saveTimer.current = null;
      persist(paperId, content, revision);
    }, 700);
    saveTimer.current = timer;
    return () => window.clearTimeout(timer);
  }, [content, loaded, dirty, current?.id]);

  return (
    <div className="panel-body">
      <div className="personal-notes">
        {!loaded ? <div className="muted">{T.loading}</div> : (
          <>
            <div className="personal-note-toolbar">
              <div className="seg" role="group" aria-label={T.markdownHelp}>
                <button
                  type="button"
                  aria-pressed={mode === "edit"}
                  className={`seg-btn${mode === "edit" ? " active" : ""}`}
                  onClick={() => setMode("edit")}
                >
                  {T.edit}
                </button>
                <button
                  type="button"
                  aria-pressed={mode === "preview"}
                  className={`seg-btn${mode === "preview" ? " active" : ""}`}
                  onClick={() => setMode("preview")}
                >
                  {T.preview}
                </button>
              </div>
              <span className="personal-note-markdown-hint">{T.markdownHelp}</span>
            </div>
            {mode === "edit" ? (
              <textarea
                value={content}
                placeholder={T.placeholder}
                aria-label={T.placeholder}
                onChange={(event) => {
                  setContent(event.target.value);
                  editRevision.current += 1;
                  setDirty(true);
                }}
                onBlur={() => {
                  if (current && loaded && dirty) {
                    persist(current.id, content, editRevision.current);
                  }
                }}
              />
            ) : (
              <div className="personal-note-preview">
                {content.trim() ? <Markdown text={content} /> : <div className="muted">{T.previewEmpty}</div>}
              </div>
            )}
            <div className={`personal-note-status ${saveState}`}>
              {saveState === "saving" ? T.saving : saveState === "error" ? T.error : T.saved}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
