// The side chat next to an agent thread, like Codex's side chat: quick
// questions about the open paper or the thread that never enter the thread's
// context. It reads a recap of the thread (sent as session_id) and the open
// paper (retrieved excerpts per question); an answer goes into the thread only
// when the user brings it back as a chip in the composer.
import { useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { CornerDownLeft, Eraser, MessageSquareQuote, Send, X } from "lucide-react";
import { streamPost, type ChatAttachment } from "../../api/client";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { Markdown } from "../Markdown";

type SideMsg = { role: "user" | "assistant"; content: string; attachments?: ChatAttachment[]; quote?: string };

const KEY = "glossx.sideChats";
const MAX_MESSAGES = 40;   // kept per thread
const SENT_HISTORY = 12;   // recent turns sent with a question
const read = (): Record<string, SideMsg[]> => {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { return {}; }
};

export const useSideChats = create<{
  chats: Record<string, SideMsg[]>;
  set: (key: string, msgs: SideMsg[]) => void;
}>((set, get) => ({
  chats: read(),
  set: (key, msgs) => {
    const chats = { ...get().chats, [key]: msgs.slice(-MAX_MESSAGES) };
    const keys = Object.keys(chats);
    for (const k of keys.slice(0, Math.max(0, keys.length - 30))) delete chats[k];
    // screenshots are large; keep only their text in storage
    const slim = Object.fromEntries(Object.entries(chats).map(([k, v]) => [k, v.map((m) => ({ ...m, attachments: undefined }))]));
    try { localStorage.setItem(KEY, JSON.stringify(slim)); } catch { /* quota or private mode */ }
    set({ chats });
  },
}));

export function SideChat() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const current = useStore((s) => s.current);
  const sideAsk = useStore((s) => s.sideAsk);
  const close = useStore((s) => s.closeSideChat);
  const outputLanguage = useStore((s) => s.outputLanguage);
  const images = useStore((s) => s.chatAttachments);
  const removeImage = useStore((s) => s.removeChatAttachment);
  const clearImages = useStore((s) => s.clearChatAttachments);
  const activeId = useSessions((s) => s.activeId);
  const key = activeId || "draft";
  const msgs = useSideChats((s) => s.chats[key]) || [];
  const save = useSideChats((s) => s.set);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streamed, setStreamed] = useState("");
  const body = useRef<HTMLDivElement>(null);
  const lastAsk = useRef(0);

  useEffect(() => { body.current?.scrollTo({ top: body.current.scrollHeight }); }, [msgs.length, streamed]);

  async function send(text: string, quote?: string) {
    const question = text.trim() || (quote ? (zh ? "解释一下这段内容。" : "Explain this passage.") : images.length ? (zh ? "请分析这个圈选的 PDF 区域。" : "Please analyze this selected PDF region.") : "");
    if (!question || busy) return;
    const sentImages = images;
    const mine: SideMsg = { role: "user", content: question, attachments: sentImages.length ? sentImages : undefined, quote };
    const history = [...msgs, mine];
    save(key, history);
    setInput(""); setBusy(true); setStreamed("");
    let acc = "";
    await streamPost("/api/chat", {
      paper_id: current?.id || null,
      session_id: activeId || null,
      // the selection rides on this question only; earlier turns carry their own text
      messages: history.slice(-SENT_HISTORY).map((m) => ({
        role: m.role,
        content: m.quote ? `[Selected text]:\n${m.quote}\n\n${m.content}` : m.content,
        attachments: m.attachments || [],
      })),
      language: outputLanguage,
    }, {
      onDelta: (d) => { acc += d; setStreamed(acc); },
      onError: (e) => { acc += `\n\n_error: ${e}_`; setStreamed(acc); },
    });
    save(key, [...history, { role: "assistant", content: acc }]);
    setStreamed(""); setBusy(false);
    if (sentImages.length) clearImages();
  }

  useEffect(() => {
    if (sideAsk && sideAsk.id !== lastAsk.current) {
      lastAsk.current = sideAsk.id;
      void send("", sideAsk.text);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sideAsk?.id]);

  const bringBack = (m: SideMsg, i: number) => {
    const question = msgs[i - 1]?.content || "";
    useSessions.getState().compose({
      attachments: [{
        kind: "selection", ref: `side-${Date.now()}`,
        label: zh ? "辅助对话的回答" : "Side chat answer",
        text: (question ? `Side-chat question: ${question}\n\nAnswer:\n` : "") + m.content,
      }],
    });
  };

  return (
    <section className="gx-side-chat" aria-label={zh ? "辅助对话" : "Side chat"}>
      <header>
        <MessageSquareQuote size={13} />
        <b>{zh ? "辅助对话" : "Side chat"}</b>
        <span className="gx-side-scope" title={current?.title}>
          {current?.title ? `· ${current.title}` : zh ? "· 当前对话" : "· this thread"}
        </span>
        <span className="spacer" />
        <button type="button" className="gx-icon-btn" title={zh ? "清空辅助对话" : "Clear side chat"} disabled={busy || !msgs.length} onClick={() => save(key, [])}><Eraser size={13} /></button>
        <button type="button" className="gx-icon-btn" title={zh ? "关闭" : "Close"} onClick={close}><X size={13} /></button>
      </header>
      <div className="gx-side-body" ref={body}>
        {!msgs.length && !streamed && (
          <p className="gx-side-empty">
            {zh ? "随手问一句，不会进入主对话的上下文。它能看到主对话的概要和当前论文；有用的回答点「带回主对话」。"
              : "Quick questions that stay out of the main thread's context. It sees a recap of the thread and the open paper; bring a useful answer back with one click."}
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`gx-side-msg ${m.role}`}>
            {m.quote && <blockquote title={m.quote}>{m.quote.length > 280 ? `${m.quote.slice(0, 280)}…` : m.quote}</blockquote>}
            {m.attachments?.map((a) => <img key={a.id} src={a.image_data_url} alt={`PDF p.${a.page + 1}`} />)}
            {m.role === "assistant" ? <Markdown text={m.content} /> : <p>{m.content}</p>}
            {m.role === "assistant" && m.content && (
              <button type="button" className="gx-side-back" onClick={() => bringBack(m, i)} title={zh ? "作为附件放进主对话的输入框" : "Add to the main composer as an attachment"}>
                <CornerDownLeft size={12} /> {zh ? "带回主对话" : "Bring back"}
              </button>
            )}
          </div>
        ))}
        {streamed && <div className="gx-side-msg assistant"><Markdown text={streamed} /></div>}
        {busy && !streamed && <p className="gx-side-empty blink">{zh ? "思考中…" : "Thinking…"}</p>}
      </div>
      {images.length > 0 && (
        <div className="gx-side-images">
          {images.map((a) => (
            <span key={a.id}><img src={a.image_data_url} alt="" /> p.{a.page + 1}<button type="button" onClick={() => removeImage(a.id)}>×</button></span>
          ))}
        </div>
      )}
      <form onSubmit={(e) => { e.preventDefault(); void send(input); }}>
        <textarea
          rows={1}
          value={input}
          placeholder={zh ? "问一句（Enter 发送）" : "Ask a side question (Enter to send)"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input); } }}
        />
        <button type="submit" className="gx-icon-btn" disabled={busy || (!input.trim() && !images.length)} title={zh ? "发送" : "Send"}><Send size={14} /></button>
      </form>
    </section>
  );
}
