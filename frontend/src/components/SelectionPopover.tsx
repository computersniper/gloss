import { useState } from "react";
import { useStore } from "../store";
import { useSessions } from "../sessionStore";

const COLORS = ["#ffd54f", "#a5d6a7", "#90caf9", "#ef9a9a", "#ce93d8"];

export function SelectionPopover({
  pos,
  onClose,
}: {
  pos: { x: number; y: number };
  onClose: () => void;
}) {
  const run = useStore((s) => s.runSelectionAction);
  const addHl = useStore((s) => s.addUserHighlight);
  const selection = useStore((s) => s.selection);
  const uiLang = useStore((s) => s.uiLang);
  const askAgent = useStore((s) => s.askAgent);
  const current = useStore((s) => s.current);
  const glossx = useStore((s) => s.mode) === "glossx";
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [color, setColor] = useState(COLORS[0]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const T = {
    en: { explain: "💡 Explain", translate: "🌐 Translate", ask: "💬 Add to chat", agent: "🤖 Ask agent", addThread: "💬 Add to thread", side: "❓ Side question", highlight: "Highlight", copy: "⧉ Copy", note: "📝 Add note", save: "Save note", saving: "Saving…", saveError: "Could not save the highlight.", placeholder: "Optional note…" },
    zh: { explain: "💡 解释", translate: "🌐 翻译", ask: "💬 加入会话", agent: "🤖 问智能体", addThread: "💬 加入对话", side: "❓ 辅助提问", highlight: "高亮", copy: "⧉ 复制", note: "📝 添加笔记", save: "保存笔记", saving: "保存中…", saveError: "高亮保存失败，请重试。", placeholder: "补充你的笔记…" },
  }[uiLang];

  async function saveHighlight(nextColor: string, nextNote = "") {
    if (saving) return;
    setSaving(true);
    setSaveError("");
    try {
      await addHl(nextColor, nextNote);
      onClose();
    } catch {
      setSaveError(T.saveError);
      setSaving(false);
    }
  }

  return (
    <div
      className={`sel-popover ${noteOpen ? "with-note" : ""}`}
      style={{ position: "fixed", left: pos.x, top: pos.y }}
      onMouseDown={(event) => {
        if (!(event.target instanceof HTMLTextAreaElement)) event.preventDefault();
      }}
    >
      <div className="sel-popover-main">
        <button onClick={() => { run("explain"); onClose(); }}>{T.explain}</button>
        <button onClick={() => { run("translate"); onClose(); }}>{T.translate}</button>
        {glossx ? (
          <>
            {/* the main thread gets the passage as a chip; the side chat answers without touching it */}
            <button
              title={uiLang === "zh" ? "作为附件放进智能体对话的输入框" : "Attach to the agent thread's composer"}
              onClick={() => {
                if (selection) {
                  const page = selection.page != null ? selection.page + 1 : "?";
                  useSessions.getState().compose({ attachments: [{ kind: "selection", ref: `sel-${Date.now()}`, label: `${current?.title ? `${current.title.slice(0, 40)}, ` : ""}p.${page}`, text: selection.text }] });
                }
                onClose();
              }}
            >{T.addThread}</button>
            <button title={uiLang === "zh" ? "在辅助对话里问，不进入主对话" : "Ask in the side chat; the thread stays clean"} onClick={() => { run("ask"); onClose(); }}>{T.side}</button>
          </>
        ) : <button onClick={() => { run("ask"); onClose(); }}>{T.ask}</button>}
        {!glossx && <button
          onClick={() => {
            if (selection) {
              const quote = selection.text.length > 600 ? selection.text.slice(0, 600) + "…" : selection.text;
              askAgent({
                goal: (uiLang === "zh" ? "关于这段内容：\n" : "About this passage:\n") + `"${quote}"\n`,
                paperIds: current ? [current.id] : undefined,
              });
            }
            onClose();
          }}
        >
          {T.agent}
        </button>}
        <span className="hl-colors">
          {COLORS.map((c) => (
            <button
              key={c}
              className={`hl-dot ${color === c ? "selected" : ""}`}
              style={{ background: c }}
              title={T.highlight}
              onClick={() => {
                setColor(c);
                if (!noteOpen) void saveHighlight(c);
              }}
              disabled={saving}
            />
          ))}
        </span>
        <button onClick={() => setNoteOpen((open) => !open)}>{T.note}</button>
        <button
          onClick={() => {
            if (selection) navigator.clipboard?.writeText(selection.text);
            onClose();
          }}
        >
          {T.copy}
        </button>
      </div>
      {noteOpen && (
        <div className="sel-note-editor">
          <textarea autoFocus value={note} placeholder={T.placeholder} onChange={(event) => setNote(event.target.value)} />
          <button className="primary" disabled={saving} onClick={() => void saveHighlight(color, note.trim())}>
            {saving ? T.saving : T.save}
          </button>
          {saveError && <span className="sel-note-error">{saveError}</span>}
        </div>
      )}
    </div>
  );
}
