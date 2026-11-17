import { useCallback, useEffect, useRef, useState } from "react";
import { CircleStop, Plus, RotateCw, Send, Trash2, X } from "lucide-react";
import { useStore } from "../../store";
import "./TerminalTab.css";

const MAX_OUTPUT = 200_000;
const ANSI = /\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07/g;

interface TerminalRow { id: string; title: string; alive: boolean; attached: boolean; created: number }

// Shells live in the backend like tmux sessions: closing the panel (or the
// app) only detaches. The tab list comes from the backend, and reopening a
// session replays its scrollback; after a restart it continues in a fresh
// shell below the saved output. Command history is kept per workspace.
const activeKey = (project: string) => `glossx.terminal.active.${project}`;
const readActive = (project: string) => { try { return localStorage.getItem(activeKey(project)) || ""; } catch { return ""; } };
const writeActive = (project: string, id: string) => { try { localStorage.setItem(activeKey(project), id); } catch { /* private mode */ } };

export function TerminalTab({ visible }: { visible: boolean }) {
  const project = useStore((s) => s.agentProjectId) || "default";
  const zh = useStore((s) => s.uiLang) === "zh";
  const [generation, setGeneration] = useState(0);
  const [started, setStarted] = useState(false);
  const [sessions, setSessions] = useState<TerminalRow[]>([]);
  // Session shown in the tabs, and the one the socket attaches to: "" = not
  // chosen yet, "new:<n>" = start a new shell (which then becomes active).
  const [active, setActive] = useState("");
  const [target, setTarget] = useState("");
  const [status, setStatus] = useState("connecting");
  const [output, setOutput] = useState("");
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState("");
  const [shell, setShell] = useState("");
  const [pty, setPty] = useState(false);
  const [interruptCloses, setInterruptCloses] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const display = useRef<HTMLPreElement>(null);
  const history = useRef<string[]>([]);
  const historyIndex = useRef(0);
  const historyPrefix = useRef<string | null>(null);
  const base = `/api/workspace/${encodeURIComponent(project)}/terminals`;

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(base);
      if (!response.ok) return null;
      const data = await response.json() as { sessions: TerminalRow[]; history: string[] };
      setSessions(data.sessions || []);
      return data;
    } catch { return null; }
  }, [base]);

  useEffect(() => { if (visible) setStarted(true); }, [visible]);
  const open = (id: string) => { setActive(id.startsWith("new:") ? "" : id); setTarget(id); };
  const openNew = () => open(`new:${Date.now()}`);
  useEffect(() => { setActive(""); setTarget(""); setSessions([]); }, [project]);
  // Pick the session to attach to: the last one used here, else the newest running one.
  useEffect(() => {
    if (!started || target) return;
    let cancelled = false;
    refresh().then((data) => {
      if (cancelled) return;
      const rows = data?.sessions || [];
      const remembered = readActive(project);
      const pick = rows.find((row) => row.id === remembered) || [...rows].reverse().find((row) => row.alive) || rows[rows.length - 1];
      if (pick) open(pick.id); else openNew();
    });
    return () => { cancelled = true; };
  }, [started, target, project, refresh]);

  useEffect(() => {
    if (!started || !target) return;
    setOutput(""); setStatus("connecting"); setCwd(""); setShell(""); setPty(false); setCommand("");
    historyIndex.current = history.current.length; historyPrefix.current = null;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const query = target.startsWith("new:") ? "" : `?session=${encodeURIComponent(target)}`;
    const ws = new WebSocket(`${protocol}//${location.host}/api/workspace/${encodeURIComponent(project)}/terminal${query}`);
    socket.current = ws;
    ws.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "ready") {
        setStatus("ready"); setCwd(message.cwd); setShell(message.shell); setPty(!!message.pty);
        setInterruptCloses(message.interrupt_mode === "close");
        if (Array.isArray(message.history)) { history.current = message.history; historyIndex.current = message.history.length; }
        writeActive(project, message.id);
        setActive(message.id);
        refresh();
      } else if (message.type === "output") {
        const text = String(message.data || "").replace(ANSI, "");
        setOutput((previous) => (message.replay ? text : previous + text).slice(-MAX_OUTPUT));
      } else if (message.type === "error") {
        setStatus("error"); setOutput((previous) => `${previous}\n${message.message}\n`);
      } else if (message.type === "exit") {
        setStatus("closed"); setOutput((previous) => `${previous}\n${zh ? "终端已退出" : "Shell exited"} (${message.code ?? "—"})\n`);
        refresh();
      }
    };
    ws.onerror = () => {
      setStatus("error");
      setOutput((previous) => `${previous}\n${zh ? "终端连接失败，请确认后端已更新并重试。" : "Terminal connection failed. Check the backend and retry."}\n`);
    };
    ws.onclose = () => setStatus((previous) => previous === "error" ? previous : "closed");
    return () => {
      ws.onmessage = null; ws.onerror = null; ws.onclose = null;
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "detach" }));
      ws.close();
      if (socket.current === ws) socket.current = null;
    };
  }, [project, generation, started, target]);
  useEffect(() => { if (display.current && visible) display.current.scrollTop = display.current.scrollHeight; }, [output, visible]);
  const send = (message: object) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(message));
  };
  const closeSession = async (id: string) => {
    if (id === active) send({ type: "close" });
    else await fetch(`${base}/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => null);
    const rest = sessions.filter((row) => row.id !== id);
    setSessions(rest);
    if (id === active) { if (rest.length) open(rest[rest.length - 1].id); else openNew(); }
  };
  const recall = (up: boolean) => {
    // Up/Down walk the saved history; with text typed, only matching commands (like zsh).
    if (historyPrefix.current === null) historyPrefix.current = command;
    const prefix = historyPrefix.current;
    const list = history.current;
    let index = historyIndex.current;
    do { index += up ? -1 : 1; } while (index >= 0 && index < list.length && prefix && !list[index].startsWith(prefix));
    index = Math.max(-1, Math.min(list.length, index));
    if (index < 0) return;
    historyIndex.current = index;
    setCommand(index >= list.length ? prefix : list[index]);
  };
  const label = (row: TerminalRow) => row.title.replace(/^Terminal (\d+)$/, zh ? "终端 $1" : "Terminal $1") || (zh ? "终端" : "Terminal");
  return <section className="gx-terminal" hidden={!visible}>
    <div className="gx-terminal-tabs" role="tablist" aria-label={zh ? "终端会话" : "Terminal sessions"}>
      {sessions.map((row) => (
        <div key={row.id} className={`gx-terminal-tab${row.id === active ? " on" : ""}${row.alive ? "" : " saved"}`} role="tab" aria-selected={row.id === active}
          title={row.alive ? (zh ? "运行中，切换后会重新连接" : "Running; switching reattaches") : (zh ? "已保存的历史，打开后在新 shell 中继续" : "Saved history; opens in a new shell")}
          onClick={() => row.id !== active && open(row.id)}>
          <span className="dot" aria-hidden="true" />{label(row)}
          <button type="button" title={zh ? "关闭终端并删除历史" : "Kill terminal and delete its history"} onClick={(event) => { event.stopPropagation(); closeSession(row.id); }}><X size={11} /></button>
        </div>
      ))}
      <button type="button" className="gx-terminal-new" title={zh ? "新建终端" : "New terminal"} onClick={openNew}><Plus size={13} /></button>
    </div>
    <div className="gx-terminal-head">
      <span className={`gx-terminal-status ${status}`}>{status === "ready" ? (zh ? "已连接" : "Connected") : status === "connecting" ? (zh ? "连接中…" : "Connecting…") : status === "error" ? (zh ? "连接失败" : "Connection failed") : (zh ? "已断开" : "Disconnected")}</span>
      <code title={`${zh ? "启动目录" : "Starting directory"}: ${cwd}`}>{cwd || (zh ? "当前工作区" : "Current workspace")}</code>
      {shell && <small>{shell.split(/[\\/]/).pop()}</small>}
      <button type="button" title={interruptCloses ? (zh ? "结束终端及其命令" : "Stop terminal and its commands") : (zh ? "中断命令" : "Interrupt command")} disabled={status !== "ready"} onClick={() => send({ type: "interrupt" })}><CircleStop size={14} /></button>
      <button type="button" title={zh ? "清空显示" : "Clear display"} onClick={() => setOutput("")}><Trash2 size={14} /></button>
      <button type="button" title={zh ? "重新连接" : "Reconnect"} onClick={() => setGeneration((n) => n + 1)}><RotateCw size={14} /></button>
    </div>
    <pre className="gx-terminal-output" ref={display} tabIndex={0} aria-label={zh ? "终端输出" : "Terminal output"}>{output || (zh ? "输入命令，在当前论文工作区运行。" : "Enter a command to run in the current workspace.")}</pre>
    <form onSubmit={(event) => {
      event.preventDefault(); if (!command.trim() || status !== "ready") return;
      if (!pty) setOutput((previous) => `${previous}\n> ${command}\n`);
      const trimmed = command.trim();
      if (history.current[history.current.length - 1] !== trimmed) history.current = [...history.current.slice(-999), trimmed];
      historyIndex.current = history.current.length; historyPrefix.current = null;
      send({ type: "input", data: command + "\n" }); setCommand("");
    }}>
      <span aria-hidden="true">›</span>
      <input aria-label={zh ? "终端命令" : "Terminal command"} placeholder={zh ? "输入命令，Enter 执行，↑↓ 历史" : "Enter a command, press Enter to run, ↑↓ for history"} value={command} disabled={status !== "ready"} onChange={(e) => { setCommand(e.target.value); historyPrefix.current = null; historyIndex.current = history.current.length; }} onKeyDown={(event) => {
        if (event.ctrlKey && event.key.toLowerCase() === "c" && !command) { event.preventDefault(); send({ type: "interrupt" }); }
        if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); recall(event.key === "ArrowUp"); }
      }} />
      <button type="submit" disabled={status !== "ready" || !command.trim()} title={zh ? "执行命令" : "Run command"}><Send size={14} /></button>
    </form>
  </section>;
}
