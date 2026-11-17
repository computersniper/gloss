import { useEffect, useRef, useState } from "react";
import { api, streamPost, type Chat, type ChatAttachment } from "../api/client";
import { useStore } from "../store";
import { Markdown } from "./Markdown";

interface Msg {
  role: "user" | "assistant";
  content: string;
  attachments: ChatAttachment[];
}
// module-scoped so a given "ask" selection is sent exactly once, even across remounts
let lastAskId = 0;

function fmtTime(ts: number | undefined, uiLang: "en" | "zh") {
  if (!ts) return uiLang === "zh" ? "新会话" : "New";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// user message bubble — long content (e.g. a pasted equation) is collapsible
function UserMsg({ content, attachments }: { content: string; attachments: ChatAttachment[] }) {
  const uiLang = useStore((s) => s.uiLang);
  const [open, setOpen] = useState(false);
  const long = content.length > 260;
  const shown = open || !long ? content : content.slice(0, 240) + " …";
  return (
    <div className="msg-text">
      {attachments.length > 0 && (
        <div className="msg-images">
          {attachments.map((attachment) => (
            <figure key={attachment.id}>
              <img src={attachment.image_data_url} alt={`PDF page ${attachment.page + 1} region`} />
              <figcaption>PDF · p{attachment.page + 1}</figcaption>
            </figure>
          ))}
        </div>
      )}
      {shown}
      {long && (
        <button className="msg-more" onClick={() => setOpen((o) => !o)}>
          {open ? (uiLang === "zh" ? "收起" : "Show less") : uiLang === "zh" ? "展开全文" : "Show more"}
        </button>
      )}
    </div>
  );
}

export function ChatPanel() {
  const current = useStore((s) => s.current);
  const action = useStore((s) => s.selectionAction);
  const selection = useStore((s) => s.selection);
  const provider = useStore((s) => s.provider);
  const outputLanguage = useStore((s) => s.outputLanguage);
  const uiLang = useStore((s) => s.uiLang);
  const T = {
    en: {
      sessionsTitle: "Chat sessions for this paper",
      newTitle: "Start a new conversation",
      newChat: "＋ New chat",
      editTitle: "Edit conversation title",
      saveTitle: "Save title",
      cancelTitle: "Cancel editing",
      titlePlaceholder: "Conversation title",
      delTitle: "Delete this session",
      delConfirm: "Delete this session and all its messages?",
      empty:
        'Ask about this paper — methods, results, limitations, or select text in the PDF / any panel and hit "Add to chat".',
      thinking: "▍ thinking…",
      session: (n: number, t: string) => `Session ${n} · ${t}`,
      imagePrompt: "Please analyze this selected PDF region.",
      region: (page: number) => `PDF region · p${page}`,
      removeAttachment: "Remove attachment",
    },
    zh: {
      sessionsTitle: "本论文的会话",
      newTitle: "新建会话",
      newChat: "＋ 新会话",
      editTitle: "编辑会话标题",
      saveTitle: "保存标题",
      cancelTitle: "取消编辑",
      titlePlaceholder: "会话标题",
      delTitle: "删除当前会话",
      delConfirm: "删除当前会话及其全部消息？",
      empty: "就这篇论文提问 — 方法、结果、局限，或在 PDF / 各面板里选中内容点“加入会话”。",
      thinking: "▍ 思考中…",
      session: (n: number, t: string) => `会话 ${n} · ${t}`,
      imagePrompt: "请分析这个圈选的 PDF 区域。",
      region: (page: number) => `PDF 圈选区域 · 第 ${page} 页`,
      removeAttachment: "移除附件",
    },
  }[uiLang];
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streamed, setStreamed] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleInput, setTitleInput] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const setSelection = useStore((s) => s.setSelection);
  const chatAttachments = useStore((s) => s.chatAttachments);
  const removeChatAttachment = useStore((s) => s.removeChatAttachment);
  const clearChatAttachments = useStore((s) => s.clearChatAttachments);

  async function loadMessages(cid: string) {
    try {
      const { messages } = await api.getChatMessages(cid);
      setMsgs(messages.map((m) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
        attachments: m.attachments || [],
      })));
    } catch {
      setMsgs([]);
    }
  }
  async function refreshChats() {
    if (!current?.id) return;
    try {
      const { chats } = await api.listChats(current.id);
      setChats(chats);
    } catch {
      /* ignore */
    }
  }

  // Load this paper's chat sessions + the most recent one's messages.
  useEffect(() => {
    let cancelled = false;
    setMsgs([]);
    setChats([]);
    setChatId(null);
    setEditingTitle(false);
    if (!current?.id) return;
    (async () => {
      try {
        let list = (await api.listChats(current.id)).chats as Chat[];
        if (!list.length) {
          const c = await api.createChat(current.id);
          list = [c];
        }
        if (cancelled) return;
        setChats(list);
        setChatId(list[0].id);
        await loadMessages(list[0].id);
      } catch {
        /* history unavailable */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [current?.id]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [msgs, streamed]);

  async function newChat() {
    if (!current?.id || busy) return;
    try {
      const c = await api.createChat(current.id);
      setChatId(c.id);
      setMsgs([]);
      setEditingTitle(false);
      await refreshChats();
    } catch {
      /* ignore */
    }
  }
  async function switchChat(cid: string) {
    if (cid === chatId || busy) return;
    setChatId(cid);
    setEditingTitle(false);
    await loadMessages(cid);
  }
  function startEditingTitle() {
    const chat = chats.find((c) => c.id === chatId);
    if (!chat || busy) return;
    setTitleInput(chat.title === "Chat" ? "" : chat.title);
    setEditingTitle(true);
  }
  async function saveTitle() {
    const title = titleInput.trim();
    if (!chatId || !title || busy) return;
    try {
      const updated = await api.updateChatTitle(chatId, title);
      setChats((items) => items.map((c) => (c.id === chatId ? { ...c, title: updated.title } : c)));
      setEditingTitle(false);
    } catch {
      /* keep the editor open so the user can retry */
    }
  }
  async function deleteCurrentChat() {
    if (!current?.id || !chatId || busy) return;
    if (!confirm(T.delConfirm)) return;
    try {
      await api.deleteChat(chatId);
      const list = (await api.listChats(current.id)).chats as Chat[];
      if (list.length) {
        setChats(list);
        setChatId(list[0].id);
        await loadMessages(list[0].id);
      } else {
        const c = await api.createChat(current.id);
        setChats([c]);
        setChatId(c.id);
        setMsgs([]);
      }
    } catch {
      /* ignore */
    }
  }

  async function send(text: string, selText?: string, attachments = chatAttachments) {
    if ((!text.trim() && attachments.length === 0) || busy) return;
    const content = text.trim() || T.imagePrompt;
    const next = [...msgs, { role: "user" as const, content, attachments }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    setStreamed("");
    let cid = chatId;
    if (!cid && current?.id) {
      try {
        cid = (await api.createChat(current.id)).id;
        setChatId(cid);
      } catch {
        /* persistence unavailable */
      }
    }
    let acc = "";
    let failed = false;
    await streamPost(
      "/api/chat",
      {
        paper_id: current?.id,
        chat_id: cid,
        messages: next,
        selection: selText || null,
        language: outputLanguage,
      },
      {
        onDelta: (d) => {
          acc += d;
          setStreamed(acc);
        },
        onError: (e) => {
          failed = true;
          acc += `\n\n_error: ${e}_`;
          setStreamed(acc);
        },
        onDone: () => {},
      },
    );
    setMsgs((m) => [...m, { role: "assistant", content: acc, attachments: [] }]);
    setStreamed("");
    setBusy(false);
    if (!failed) clearChatAttachments();
    await refreshChats(); // picks up an auto-generated or externally edited title
  }

  // "Ask" from a selection popover (PDF or a side panel). Fire once per selection.
  // The selected content is embedded in the visible message so you can see exactly
  // what was asked (not just a generic instruction).
  useEffect(() => {
    if (action?.kind === "ask" && action.id !== lastAskId) {
      lastAskId = action.id;
      const sel = action.selection.text;
      const zh = outputLanguage.startsWith("中文");
      const prefix = zh ? "解释并讨论我选中的这段内容：" : "Explain and discuss this selected content:";
      send(`${prefix}\n\n${sel}`, undefined, []).finally(() => {
        setSelection(null); // clear the attached selection so you can keep typing freely
        inputRef.current?.focus();
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action?.id]);

  return (
    <div className="panel-body chat">
      <div className="chat-head">
        {editingTitle ? (
          <>
            <input
              className="chat-title-input"
              value={titleInput}
              maxLength={120}
              autoFocus
              placeholder={T.titlePlaceholder}
              onChange={(e) => setTitleInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveTitle();
                if (e.key === "Escape") setEditingTitle(false);
              }}
            />
            <button className="chat-head-icon save" onClick={saveTitle} disabled={!titleInput.trim()} title={T.saveTitle}>
              ✓
            </button>
            <button className="chat-head-icon" onClick={() => setEditingTitle(false)} title={T.cancelTitle}>
              ×
            </button>
          </>
        ) : (
          <>
            <select
              className="chat-session"
              value={chatId || ""}
              onChange={(e) => switchChat(e.target.value)}
              disabled={busy}
              title={T.sessionsTitle}
            >
              {chats.map((c, i) => (
                <option key={c.id} value={c.id}>
                  {c.title && c.title !== "Chat"
                    ? `${c.title} · ${fmtTime(c.created_at, uiLang)}`
                    : T.session(chats.length - i, fmtTime(c.created_at, uiLang))}
                </option>
              ))}
            </select>
            <button className="chat-head-icon" onClick={startEditingTitle} disabled={busy || !chatId} title={T.editTitle}>
              ✎
            </button>
            <button className="chat-new" onClick={newChat} disabled={busy} title={T.newTitle}>
              {T.newChat}
            </button>
            <button className="chat-del" onClick={deleteCurrentChat} disabled={busy || !chatId} title={T.delTitle}>
              🗑
            </button>
          </>
        )}
      </div>
      <div className="chat-body" ref={bodyRef}>
        {msgs.length === 0 && !streamed && <div className="muted">{T.empty}</div>}
        {msgs.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            <div className="msg-role">{m.role === "user" ? "you" : "GlossX"}</div>
            {m.role === "assistant" ? <Markdown text={m.content} /> : <UserMsg content={m.content} attachments={m.attachments} />}
          </div>
        ))}
        {streamed && (
          <div className="msg assistant">
            <div className="msg-role">GlossX</div>
            <Markdown text={streamed} />
          </div>
        )}
        {busy && !streamed && <div className="muted blink">{T.thinking}</div>}
      </div>
      <div className="chat-input">
        {chatAttachments.length > 0 && (
          <div className="chat-attachments">
            {chatAttachments.map((attachment) => (
              <div className="chat-attachment" key={attachment.id}>
                <img src={attachment.image_data_url} alt={T.region(attachment.page + 1)} />
                <span>{T.region(attachment.page + 1)}</span>
                <button title={T.removeAttachment} onClick={() => removeChatAttachment(attachment.id)}>×</button>
              </div>
            ))}
          </div>
        )}
        {selection?.text && (
          <div className="attached" title={selection.text}>
            ⧉ selection attached ({selection.text.length} chars)
          </div>
        )}
        <textarea
          ref={inputRef}
          value={input}
          placeholder={`Message (${provider})…  ⏎ to send`}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input, selection?.text, chatAttachments);
            }
          }}
        />
        <button disabled={busy} onClick={() => send(input, selection?.text, chatAttachments)}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
