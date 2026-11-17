import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Globe, Hand, Import, MessageSquarePlus, Plus, RefreshCw, Send, Sparkles, X } from "lucide-react";
import { browserApi, type BrowserFrame, type BrowserInput, type BrowserStatus } from "../../api/browser";
import { useBrowser, openBrowser } from "../../browserStore";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { EmptyState, Spinner } from "../ui";
import { desktop } from "../../desktop";

export function BrowserView({ sessionId, visible }: { sessionId: string; visible: boolean }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const state = useBrowser((s) => s.states[sessionId]);
  const sharedError = useBrowser((s) => s.errors[sessionId]);
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [frame, setFrame] = useState<BrowserFrame | null>(null);
  const [address, setAddress] = useState("");
  const addressEdited = useRef(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [text, setText] = useState("");
  const [actions, setActions] = useState<{ time: number; text: string }[]>([]);
  const native = desktop?.browser;
  const image = useRef<HTMLImageElement>(null);
  const pageArea = useRef<HTMLDivElement>(null);
  const addressField = useRef<HTMLInputElement>(null);
  const inputQueue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef(0);
  const scroll = useRef<{ x: number; y: number; timer: ReturnType<typeof setTimeout> | null }>({ x: 0, y: 0, timer: null });
  const current = state?.tabs?.find((t) => t.id === state.active || t.active) || state?.tabs?.[0];
  const url = state?.url || current?.url || "";
  const controlling = state?.controlling === "user";
  const history = [...actions, ...(state?.log || []).map((a) => ({ time: (a.time || a.at || Date.now() / 1000) * 1000, text: a.text || a.note || [a.action, a.url].filter(Boolean).join(" ") }))].filter((a) => a.text).sort((a, b) => b.time - a.time).slice(0, 30);
  const put = useBrowser.getState().put;
  const note = (value: string) => setActions((a) => [{ time: Date.now(), text: value }, ...a].slice(0, 12));

  const load = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([browserApi.status(), browserApi.state(sessionId)]);
      setStatus(s); put(sessionId, b); setError("");
    } catch (e: any) { setError(String(e?.message || e)); }
  }, [sessionId]);
  useEffect(() => { if (visible) void load(); }, [visible, load]);
  useEffect(() => { if (!addressEdited.current) setAddress(url); }, [url]);
  useEffect(() => { if (native || !visible || !status?.available || !state?.tabs?.length) return;
    setConnected(false);
    const stop = browserApi.frames(sessionId, (f) => { setFrame(f); setConnected(true); }, (s) => put(sessionId, s), (message) => { setConnected(false); if (message) setError(message); });
    return stop;
  }, [visible, sessionId, status?.available, current?.id]);
  useEffect(() => () => { if (scroll.current.timer) clearTimeout(scroll.current.timer); }, []);
  // Size the remote viewport to this panel so pages use their own responsive
  // layout rather than a fixed desktop page scaled down to fit.
  useEffect(() => { const el = pageArea.current; if (native || !visible || !el || !current) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let sent = "";
    const send = () => { const width = Math.floor(el.clientWidth), height = Math.floor(el.clientHeight); const size = `${width}x${height}`;
      if (width < 50 || height < 50 || size === sent) return;
      sent = size; void browserApi.input(sessionId, { type: "viewport", width, height }).catch(() => { sent = ""; }); };
    const observer = new ResizeObserver(() => { if (timer) clearTimeout(timer); timer = setTimeout(send, 200); });
    observer.observe(el); send();
    return () => { observer.disconnect(); if (timer) clearTimeout(timer); };
  }, [visible, sessionId, current?.id]);
  // In the desktop app the real page sits over this pane. It is drawn above the
  // app, so it follows the pane every frame and steps aside for modal dialogs.
  const nativeShown = !!native && visible && !!current && url !== "about:blank" && status?.available !== false;
  useEffect(() => { const el = pageArea.current; if (!native || !nativeShown || !el || !current) return;
    let last = "", frameId = 0;
    const place = () => {
      const r = el.getBoundingClientRect();
      const blocked = !!document.querySelector('[aria-modal="true"], .wb-mb-list');
      const key = blocked ? "hidden" : `${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)},${Math.round(r.height)}`;
      if (key !== last) { last = key; void (blocked ? native.hide() : native.show(current.id, { x: r.x, y: r.y, width: r.width, height: r.height })); }
      frameId = requestAnimationFrame(place);
    };
    // With several windows open the page sits in one of them; focusing this one brings it here.
    const claim = () => { last = ""; };
    window.addEventListener("focus", claim);
    place();
    return () => { cancelAnimationFrame(frameId); window.removeEventListener("focus", claim); void native.hide(); };
  }, [nativeShown, current?.id]);
  // Leaving this tab always gives control back, including when the editor tab closes.
  useEffect(() => { if (!visible && controlling) void browserApi.input(sessionId, { type: "takeover", controlling: "agent" }).catch(() => {}); }, [visible, controlling, sessionId]);
  useEffect(() => () => { if (useBrowser.getState().states[sessionId]?.controlling === "user") void browserApi.input(sessionId, { type: "takeover", controlling: "agent" }).catch(() => {}); }, [sessionId]);

  const act = (body: BrowserInput, label?: string) => {
    pending.current += 1; setBusy(true);
    const run = async () => {
    setError("");
    try {
      const result = await browserApi.input(sessionId, body);
      useBrowser.getState().error(sessionId, "");
      if (result?.tabs) put(sessionId, result);
      else if (result?.state?.tabs) put(sessionId, result.state);
      if (label) note(label);
      if (body.type === "import_paper") {
        await useStore.getState().loadPapers();
        useStore.getState().notify(zh ? "论文导入已开始" : "Paper import started");
      }
      if (!["click", "scroll", "key", "text"].includes(body.type)) await load();
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { pending.current -= 1; if (!pending.current) setBusy(false); }
    };
    const next = inputQueue.current.then(run, run);
    inputQueue.current = next;
    return next;
  };
  const navigate = () => {
    const input = address.trim();
    if (!input) return;
    const target = /^https?:\/\//i.test(input) || input === "about:blank" ? input : `https://${input}`;
    addressEdited.current = false;
    setFrame(null);
    void act({ type: "navigate", url: target }, `${zh ? "打开" : "Navigate"} ${target}`);
  };
  const share = async () => {
    setBusy(true); setError("");
    try {
      const read = await browserApi.read(sessionId);
      const body = read.markdown || read.text || "";
      if (!body) throw new Error(zh ? "页面没有可读取的正文" : "This page has no readable content");
      useSessions.getState().openThread(sessionId);
      useSessions.getState().compose({ attachments: [{ kind: "selection", ref: `page-${Date.now()}`, label: read.title || read.url || url, text: `Source: ${read.url || url}\n\n${body}` }] });
      note(zh ? "已附加页面正文到对话" : "Page text attached to chat");
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  const key = (e: React.KeyboardEvent) => {
    if (!controlling || e.nativeEvent.isComposing) return;
    if ((e.ctrlKey || e.metaKey) && ["k", "l", "w"].includes(e.key.toLowerCase())) return;
    e.preventDefault(); e.stopPropagation();
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) void act({ type: "text", text: e.key });
    else void act({ type: "key", key: [e.ctrlKey ? "Control" : "", e.metaKey ? "Meta" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", e.key].filter(Boolean).join("+") });
  };

  return <div className={`wb-doc gx-browser${visible ? "" : " hidden"}`}>
    <div className="gx-browser-tabs" aria-label={zh ? "浏览器标签" : "Browser tabs"}>
      {(state?.tabs || []).map((t) => <div key={t.id} className={`gx-browser-tab${current?.id === t.id ? " active" : ""}`}><button type="button" onClick={() => { setFrame(null); void act({ type: "switch_tab", id: t.id }); }} title={t.url}><Globe size={11} /><span>{t.title || t.url || (zh ? "新标签" : "New tab")}</span></button><button type="button" title={zh ? "关闭标签" : "Close tab"} onClick={() => void act({ type: "close_tab", id: t.id })}><X size={11} /></button></div>)}
      <button type="button" className="gx-icon-btn" title={zh ? "新标签" : "New tab"} disabled={busy || status?.available === false} onClick={() => void act({ type: "new_tab", url: "about:blank" })}><Plus size={14} /></button>
    </div>
    <form className="gx-browser-address" onSubmit={(e) => { e.preventDefault(); navigate(); }}>
      <button type="button" className="gx-icon-btn" title={zh ? "后退" : "Back"} disabled={!current || busy} onClick={() => void act({ type: "back" })}><ArrowLeft size={14} /></button>
      <button type="button" className="gx-icon-btn" title={zh ? "前进" : "Forward"} disabled={!current || busy} onClick={() => void act({ type: "forward" })}><ArrowRight size={14} /></button>
      <button type="button" className="gx-icon-btn" title={zh ? "刷新" : "Reload"} disabled={!current || busy} onClick={() => void act({ type: "reload" })}>{busy || state?.loading ? <Spinner size={14} /> : <RefreshCw size={14} />}</button>
      <label><Globe size={13} /><input ref={addressField} value={address} onChange={(e) => { addressEdited.current = true; setAddress(e.target.value); }} aria-label={zh ? "网址" : "Address"} placeholder="https://arxiv.org/abs/1706.03762" spellCheck={false} /><button type="submit" className="gx-icon-btn" title={zh ? "打开网址" : "Go"} disabled={!address.trim() || busy}><Send size={12} /></button></label>
    </form>
    <div className="gx-browser-controls">
      <button type="button" className={`gx-browser-takeover${controlling ? " on" : ""}`} aria-pressed={controlling} disabled={!current || busy} onClick={() => void act({ type: "takeover", controlling: controlling ? "agent" : "user" })}>{controlling ? <Hand size={13} /> : <Sparkles size={13} />}{controlling ? (zh ? "你正在控制 · 交回智能体" : "You are controlling · give back") : (zh ? "智能体控制 · 接管" : "Agent controls · take over")}</button>
      <span className="spacer" />
      <button type="button" className="gx-btn ghost sm" disabled={!current || busy} onClick={() => void act({ type: "import_paper" }, zh ? "导入论文" : "Import paper")}><Import size={13} />{zh ? "导入这篇论文" : "Import this paper"}</button>
      <button type="button" className="gx-btn ghost sm" disabled={!current || busy} onClick={() => void share()}><MessageSquarePlus size={13} />{zh ? "发到对话" : "Send page to chat"}</button>
    </div>
    {status?.restart_pending && <div className="gx-browser-notice" role="status">{zh ? "浏览器设置已更改，将在浏览器重启后生效。可重启 GlossX，或等浏览器空闲关闭。" : "Browser settings changed and will apply after it restarts. Restart GlossX or wait for the idle browser to close."}</div>}
    {(error || sharedError) && <div className="gx-browser-error" role="alert"><span>{error || sharedError}</span><button type="button" className="gx-link" onClick={() => void openBrowser(undefined, sessionId)}>{zh ? "重试" : "Retry"}</button></div>}
    <div ref={pageArea} className="gx-browser-page">
      {status?.available === false ? <EmptyState icon={<Globe size={24} />} title={zh ? "没有找到浏览器" : "No browser found"} hint={zh ? "安装 Edge、Chrome 或 Chromium，或在智能体设置中指定浏览器路径。" : "Install Edge, Chrome or Chromium, or set its executable path in agent settings."} action={<button type="button" className="gx-btn" onClick={() => useStore.getState().setLayout({ activity: "settings", sideOpen: true })}>{zh ? "打开设置" : "Open settings"}</button>} />
      : !current || url === "about:blank" ? <EmptyState icon={<Globe size={30} />} title={zh ? "从一个网址开始" : "Start with a URL"} hint={zh ? "输入网址，或让智能体打开一篇论文。这个浏览器使用 GlossX 独立配置，不读取你的个人浏览器资料。" : "Enter a URL or ask the agent to open a paper. This browser uses a separate GlossX profile."} action={<button type="button" className="gx-btn" onClick={() => { setAddress(""); addressEdited.current = true; addressField.current?.focus(); }}>{zh ? "输入网址" : "Enter a URL"}</button>} />
      : native ? null
      : frame ? <img ref={image} className={`gx-browser-frame${controlling ? " controlling" : ""}`} src={frame.data.startsWith("data:") ? frame.data : `data:image/jpeg;base64,${frame.data}`} alt={state?.title || current.title || (zh ? "实时浏览页面" : "Live browser page")} tabIndex={controlling ? 0 : -1} draggable={false}
        onClick={(e) => { if (!controlling) return; e.currentTarget.focus(); const b = e.currentTarget.getBoundingClientRect(); void act({ type: "click", x: Math.round((e.clientX - b.x) * frame.width / b.width), y: Math.round((e.clientY - b.y) * frame.height / b.height) }); }}
        onWheel={(e) => { if (!controlling) return; e.stopPropagation(); scroll.current.x += e.deltaX; scroll.current.y += e.deltaY; if (!scroll.current.timer) scroll.current.timer = setTimeout(() => { const { x, y } = scroll.current; scroll.current = { x: 0, y: 0, timer: null }; void act({ type: "scroll", delta_x: x, delta_y: y }); }, 80); }}
        onKeyDown={key} onPaste={(e) => { if (controlling) { e.preventDefault(); void act({ type: "text", text: e.clipboardData.getData("text") }); } }} />
      : <div className="gx-browser-wait"><Spinner size={22} /><span>{zh ? "正在连接页面画面…" : "Connecting to the live page…"}</span></div>}
    </div>
    {controlling && !native && <form className="gx-browser-type" onSubmit={(e) => { e.preventDefault(); if (text) { void act({ type: "text", text }); setText(""); } }}><Hand size={13} /><input value={text} onChange={(e) => setText(e.target.value)} placeholder={zh ? "向页面当前焦点输入文字（支持中文）…" : "Type into the page’s focused field…"} /><button type="submit" className="gx-btn sm" disabled={!text}>{zh ? "输入" : "Type"}</button><button type="button" className="gx-btn ghost sm" onClick={() => void act({ type: "key", key: "Enter" })}>Enter</button></form>}
    <footer className="gx-browser-foot"><span className={`gx-dot${connected || native ? " st-idle" : ""}`} /><span>{native ? (zh ? "内置浏览器" : "Built-in browser") : connected ? (zh ? "画面已连接" : "Live view connected") : (zh ? "等待画面" : "Waiting for live view")}</span><span className="spacer" /><details><summary>{zh ? "操作记录" : "Actions"} · {history.length}</summary><ol>{history.length ? history.map((a, i) => <li key={a.time + a.text + i}><time>{new Date(a.time).toLocaleTimeString()}</time>{a.text}</li>) : <li>{zh ? "你和智能体的操作会显示在这里。" : "Your actions and the agent’s actions appear here."}</li>}</ol></details></footer>
  </div>;
}
