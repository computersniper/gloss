import React, { useEffect, useRef, useState } from "react";
import { pdfjsLib } from "../pdf/pdfSetup";
import { complete } from "./client";
import { loadState, saveState, papers, type Conversation, type LocalPaper } from "./storage";
import "./local.css";

function Reader({ paper }: { paper: LocalPaper }) {
  const [doc, setDoc] = useState<any>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const canvas = useRef<HTMLCanvasElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(400);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    if (holder.current) observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let alive = true;
    const task = pdfjsLib.getDocument({ data: paper.bytes.slice(0) });
    setDoc(null); setPage(1); setError("");
    void task.promise.then(d => { if (alive) setDoc(d); }).catch(() => { if (alive) setError("PDF 无法打开，可能损坏或需要密码"); });
    return () => { alive = false; void task.destroy(); };
  }, [paper.id]);
  useEffect(() => {
    if (!doc) return;
    setError("");
    let alive = true;
    let render: any;
    void doc.getPage(page).then(async (p: any) => {
      if (!alive) return;
      const natural = p.getViewport({ scale: 1 });
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = p.getViewport({ scale: Math.max(100, width - 12) / natural.width * ratio });
      const temporary = document.createElement("canvas");
      temporary.width = viewport.width; temporary.height = viewport.height;
      render = p.render({ canvasContext: temporary.getContext("2d"), viewport });
      await render.promise;
      if (!alive || !canvas.current) return;
      const target = canvas.current;
      target.width = temporary.width; target.height = temporary.height;
      target.style.width = `${viewport.width / ratio}px`;
      target.style.height = `${viewport.height / ratio}px`;
      target.getContext("2d")?.drawImage(temporary, 0, 0);
    }).catch((e: any) => { if (alive && e?.name !== "RenderingCancelledException") setError("此页渲染失败，请切换页码重试"); });
    return () => { alive = false; render?.cancel(); };
  }, [doc, page, width]);
  return <div ref={holder} className="ml-reader"><div className="ml-row"><button disabled={!doc || page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>{page} / {paper.pages}</span><button disabled={!doc || page >= paper.pages} onClick={() => setPage(p => p + 1)}>下一页</button></div>{error && <p role="alert">{error}</p>}<canvas ref={canvas} aria-label={`${paper.name} 第 ${page} 页`} /></div>;
}

export default function LocalApp() {
  const [initial] = useState(loadState);
  const [settings, setSettings] = useState(initial.settings);
  const [conversations, setConversations] = useState(initial.conversations);
  const [current, setCurrent] = useState<string | null>(initial.conversations.find(c => !c.archived)?.id || null);
  const [showSettings, setShowSettings] = useState(!initial.settings.apiKey);
  const [archived, setArchived] = useState(false);
  const [library, setLibrary] = useState<LocalPaper[]>([]);
  const [paperId, setPaperId] = useState("");
  const [includePaper, setIncludePaper] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const conversation = conversations.find(c => c.id === current);
  const paper = library.find(p => p.id === paperId);
  useEffect(() => { void papers("list").then(setLibrary).catch(e => setError(e.message)); }, []);
  useEffect(() => { try { saveState(conversations, settings); } catch { setError("设备存储已满，对话未能保存，请清理已归档对话"); } }, [conversations, settings]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    let alive = true;
    let listener: any;
    const result = window.Capacitor?.Plugins?.App?.addListener("appUrlOpen", (event: { url: string }) => {
      if (!event.url.startsWith("glossx://pair")) return;
      controller.current?.abort();
      window.location.href = `/index.html#pair=${encodeURIComponent(event.url)}`;
    });
    void Promise.resolve(result).then(handle => {
      if (alive) listener = handle;
      else void (handle as any)?.remove?.();
    }).catch(() => {});
    return () => { alive = false; void listener?.remove?.(); };
  }, []);
  function cancel() { controller.current?.abort(); controller.current = null; setBusy(false); }
  function newChat() {
    cancel();
    const item: Conversation = { id: crypto.randomUUID(), title: "新对话", archived: false, messages: [] };
    setConversations(cs => [item, ...cs]); setCurrent(item.id); setArchived(false); setDraft("");
  }
  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!draft.trim() || busy) return;
    setError("");
    const question = draft.trim();
    const target = conversation || { id: crypto.randomUUID(), title: question.slice(0, 40), archived: false, messages: [] };
    const messages = [...target.messages, { role: "user" as const, content: question }];
    setConversations(cs => cs.some(c => c.id === target.id) ? cs.map(c => c.id === target.id ? { ...c, title: c.messages.length ? c.title : question.slice(0, 40), messages } : c) : [{ ...target, messages }, ...cs]);
    setCurrent(target.id); setDraft(""); setBusy(true);
    const abort = new AbortController(); controller.current = abort;
    try {
      const answer = await complete(settings, messages, includePaper && paper ? paper.text.slice(0, 24000) : "", abort.signal);
      if (!abort.signal.aborted) setConversations(cs => cs.map(c => c.id === target.id ? { ...c, messages: [...messages, { role: "assistant", content: answer }] } : c));
    } catch (e: any) { if (!abort.signal.aborted) setError(e instanceof TypeError ? "无法连接 API。请检查网络与 API 是否允许浏览器跨域访问（CORS）" : e.message); }
    finally { if (controller.current === abort) { controller.current = null; setBusy(false); } }
  }
  async function importPdf(file?: File) {
    if (!file) return;
    setError("");
    if (file.size > 50 * 1024 * 1024) { setError("请选择小于 50 MB 的 PDF"); return; }
    setImporting(true);
    let doc: any;
    let loading: any;
    try {
      const bytes = await file.arrayBuffer();
      loading = pdfjsLib.getDocument({ data: bytes.slice(0) });
      doc = await loading.promise;
      let text = "";
      for (let i = 1; i <= Math.min(doc.numPages, 80) && text.length < 120000; i++) {
        const page = await doc.getPage(i);
        const content = await page.getTextContent();
        text += `\n[第 ${i} 页]\n` + content.items.map((item: any) => item.str || "").join(" ");
      }
      const item: LocalPaper = { id: crypto.randomUUID(), name: file.name, bytes, text: text.slice(0, 120000), pages: doc.numPages, truncated: doc.numPages > 80 || text.length > 120000 };
      await papers("put", item); setLibrary(ls => [...ls, item]); setPaperId(item.id); setIncludePaper(false);
    } catch { setError("PDF 导入失败。请检查文件是否损坏、加密，或设备存储是否已满"); }
    finally { try { await loading?.destroy(); } finally { setImporting(false); } }
  }
  function returnToComputer() {
    cancel();
    const url = new URL(window.location.href);
    if (url.pathname.startsWith("/local/")) { window.location.href = "/index.html#returned"; return; }
    url.searchParams.delete("mode"); window.location.href = url.toString();
  }
  return <main className="ml-app">
    <header className="ml-header"><strong>GlossX · 设备本地</strong><button onClick={returnToComputer}>连接电脑</button><button onClick={() => setShowSettings(s => !s)}>API 设置</button></header>
    <p className="ml-notice">本地论文与对话保存在此设备；问答直连你设置的 API，不调用电脑上的 CLI。浏览器清理数据会移除本地记录。</p>
    {showSettings && <section className="ml-settings"><label>API 基础地址<input value={settings.baseUrl} onChange={e => setSettings(s => ({ ...s, baseUrl: e.target.value }))} placeholder="https://api.openai.com/v1" /></label><label>模型名称<input value={settings.model} onChange={e => setSettings(s => ({ ...s, model: e.target.value }))} /></label><label>API Key<input type="password" autoComplete="off" value={settings.apiKey} onChange={e => setSettings(s => ({ ...s, apiKey: e.target.value }))} /></label><label className="ml-check"><input type="checkbox" checked={settings.rememberKey} onChange={e => setSettings(s => ({ ...s, rememberKey: e.target.checked }))} />在此设备保存密钥（明文，仅限可信设备）</label><small>默认只在本次打开期间保留密钥；关闭后需重新填写。支持 OpenAI Chat Completions 及兼容 API，自定义服务需允许跨域请求。</small></section>}
    {error && <p role="alert" className="ml-error">{error}<button onClick={() => setError("")}>关闭</button></p>}
    <div className="ml-layout"><aside className="ml-library"><div className="ml-row"><button onClick={newChat}>新对话</button><button onClick={() => setArchived(a => !a)}>{archived ? "返回对话" : "归档"}</button></div>
      <nav aria-label="本地对话">{conversations.filter(c => c.archived === archived).map(c => <div className="ml-thread" key={c.id}><button className={current === c.id ? "selected" : ""} onClick={() => { cancel(); setCurrent(c.id); }}>{c.title}</button><button aria-label={archived ? "恢复对话" : "归档对话"} onClick={() => { cancel(); setConversations(cs => cs.map(x => x.id === c.id ? { ...x, archived: !x.archived } : x)); if (current === c.id) setCurrent(null); }}>{archived ? "恢复" : "归档"}</button>{archived && <button onClick={() => { if (window.confirm("永久删除此设备上的对话？")) { setConversations(cs => cs.filter(x => x.id !== c.id)); if (current === c.id) setCurrent(null); } }}>删除</button>}</div>)}</nav>
      <label className="ml-file">{importing ? "正在导入…" : "导入本地 PDF"}<input type="file" accept="application/pdf,.pdf" disabled={importing} onChange={e => { void importPdf(e.target.files?.[0]); e.target.value = ""; }} /></label>
      <select aria-label="选择本地论文" value={paperId} onChange={e => { setPaperId(e.target.value); setIncludePaper(false); }}><option value="">选择本地论文</option>{library.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      {paper && <><label className="ml-check"><input type="checkbox" checked={includePaper} onChange={e => setIncludePaper(e.target.checked)} />问答时发送当前论文的前 24000 字符</label><small>只提取前 80 页及最多 120000 字符；扫描 PDF 需要 OCR，本模式暂不提供。{paper.truncated && "此文档提取内容已截取。"}</small><button onClick={async () => { if (window.confirm("从此设备移除此论文？")) { try { await papers("delete", paper.id); setLibrary(ls => ls.filter(p => p.id !== paper.id)); setPaperId(""); } catch (e: any) { setError(e.message); } } }}>移除论文</button></>}
    </aside><section className="ml-content">{paper && <Reader key={paper.id} paper={paper} />}<div className="ml-messages" aria-live="polite">{!conversation?.messages.length && <p>导入 PDF 阅读，或直接提问。此模式提供阅读与 API 问答，电脑工作区、终端与工具运行请切换“连接电脑”。</p>}{conversation?.messages.map((m, i) => <article key={i} className={m.role}><strong>{m.role === "user" ? "你" : "助手"}</strong><div>{m.content}</div></article>)}</div><form className="ml-composer" onSubmit={send}><textarea aria-label="问题" placeholder="提出问题…" value={draft} onChange={e => setDraft(e.target.value)} disabled={conversation?.archived} /><button type="submit" disabled={busy || !draft.trim() || conversation?.archived}>发送</button>{busy && <button type="button" onClick={cancel}>停止</button>}</form></section></div>
  </main>;
}
