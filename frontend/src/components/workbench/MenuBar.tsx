import { useEffect, useRef, useState } from "react";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { openBrowser } from "../../browserStore";
import { desktop, newWindow } from "../../desktop";
import { zoomIn, zoomOut, zoomReset } from "../../zoom";
import { focusAiPane } from "./focus";
import { openWorkspaceFolder } from "./openFolder";

type Entry = { label: string; keys?: string; run: () => void } | "-";
type Menu = { id: string; label: string; key: string; entries: Entry[] };

function useMenus(): Menu[] {
  const zh = useStore((s) => s.uiLang) === "zh";
  const layout = useStore((s) => s.layout);
  const st = useStore.getState();
  const L = (en: string, cn: string) => (zh ? cn : en);
  const show = (activity: Parameters<typeof st.setLayout>[0]["activity"]) => () => st.setLayout({ activity, sideOpen: true });
  const edit = (label: string, keys: string, action: "undo" | "redo" | "cut" | "copy" | "paste" | "selectAll"): Entry => ({ label, keys, run: () => void desktop?.command(action) });
  const menus: Menu[] = [
    { id: "file", label: L("File", "文件"), key: "F", entries: [
      { label: L("New thread", "新对话"), run: () => { useSessions.getState().newThread(); focusAiPane(); } },
      { label: L("New window", "新建窗口"), keys: "Ctrl+Shift+N", run: newWindow },
      { label: L("Import paper…", "导入论文…"), run: () => st.openPalette("import") },
      { label: L("Open paper…", "打开论文…"), run: () => st.openPalette("papers") },
      { label: L("Open folder…", "打开文件夹…"), run: () => { void openWorkspaceFolder(); } },
      "-",
      { label: L("Switch project…", "切换项目…"), run: () => st.openPalette("projects") },
      { label: L("New project…", "新建项目…"), run: () => st.openPalette("newProject") },
      "-",
      { label: L("Settings", "设置"), run: () => { st.setLayout({ activity: "settings", sideOpen: true }); useStore.setState({ settingsFocus: null }); } },
      ...(desktop ? ["-" as const, { label: L("Quit", "退出"), run: () => void desktop?.command("quit") }] : []),
    ] },
    // Editing commands go to the window; a regular browser has its own.
    ...(desktop ? [{ id: "edit", label: L("Edit", "编辑"), key: "E", entries: [
      edit(L("Undo", "撤销"), "Ctrl+Z", "undo"),
      edit(L("Redo", "重做"), "Ctrl+Y", "redo"),
      "-" as const,
      edit(L("Cut", "剪切"), "Ctrl+X", "cut"),
      edit(L("Copy", "复制"), "Ctrl+C", "copy"),
      edit(L("Paste", "粘贴"), "Ctrl+V", "paste"),
      "-" as const,
      edit(L("Select all", "全选"), "Ctrl+A", "selectAll"),
    ] }] : []),
    { id: "view", label: L("View", "查看"), key: "V", entries: [
      { label: L("Command palette", "命令面板"), keys: "Ctrl+K", run: () => st.openPalette("commands") },
      "-",
      { label: L(`${layout.sideOpen ? "Hide" : "Show"} side bar`, `${layout.sideOpen ? "隐藏" : "显示"}侧边栏`), keys: "Ctrl+B", run: () => st.setLayout({ sideOpen: !layout.sideOpen }) },
      { label: L(`${layout.aiOpen ? "Hide" : "Show"} AI pane`, `${layout.aiOpen ? "隐藏" : "显示"} AI 面板`), keys: "Ctrl+L", run: () => st.setLayout({ aiOpen: !layout.aiOpen }) },
      { label: L(`${layout.bottomOpen ? "Hide" : "Show"} bottom panel`, `${layout.bottomOpen ? "隐藏" : "显示"}底部面板`), keys: "Ctrl+J", run: () => st.setLayout({ bottomOpen: !layout.bottomOpen }) },
      "-",
      { label: L("Switch theme…", "切换主题…"), run: () => st.openPalette("theme") },
      { label: zh ? "Switch to English" : "切换为中文", run: () => st.setUiLang(zh ? "en" : "zh") },
      { label: L("Switch to Gloss mode", "切换到 Gloss 模式"), keys: "Ctrl+Shift+M", run: () => st.setMode("gloss") },
      "-",
      { label: L("Zoom in", "放大"), keys: "Ctrl+=", run: zoomIn },
      { label: L("Zoom out", "缩小"), keys: "Ctrl+-", run: zoomOut },
      { label: L("Reset zoom", "重置缩放"), keys: "Ctrl+0", run: zoomReset },
      ...(desktop ? [{ label: L("Full screen", "全屏"), keys: "F11", run: () => void desktop?.command("fullscreen") }] : []),
    ] },
    { id: "go", label: L("Go", "转到"), key: "G", entries: [
      { label: L("Library", "论文库"), run: show("library") },
      { label: L("Threads", "对话列表"), run: () => { st.setLayout({ aiOpen: true, aiMode: "threads" }); useSessions.getState().setSideView("threads"); } },
      { label: L("Workspace", "工作区"), run: show("workspace") },
      { label: L("Source control", "源代码管理"), run: show("scm") },
      { label: L("Scholar search", "学术搜索"), run: show("search") },
      { label: L("Capabilities & plugins", "能力与插件"), run: show("capabilities") },
      "-",
      { label: L("Browser", "浏览器"), run: () => void openBrowser() },
      { label: L("Welcome", "欢迎页"), run: () => st.openWelcome() },
    ] },
    { id: "help", label: L("Help", "帮助"), key: "H", entries: [
      { label: L("All commands", "全部命令"), keys: "Ctrl+K", run: () => st.openPalette("commands") },
      { label: L("Keyboard shortcuts", "快捷键"), run: () => st.openWelcome() },
      ...(desktop ? ["-" as const, { label: L("Developer tools", "开发者工具"), run: () => void desktop?.command("devtools") }] : []),
    ] },
  ];
  return menus;
}

/** File / Edit / View / Go / Help, as in a desktop editor's title bar. Alt+letter opens a menu. */
export function MenuBar() {
  const menus = useMenus();
  const zh = useStore((s) => s.uiLang) === "zh";
  const [open, setOpen] = useState<string | null>(null);
  const [alt, setAlt] = useState(false);
  const bar = useRef<HTMLDivElement>(null);
  const menusRef = useRef(menus);
  menusRef.current = menus;
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "Alt") { setAlt(true); return; }
      if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const menu = menusRef.current.find((m) => e.code === `Key${m.key}`);
      if (!menu) return;
      e.preventDefault();
      setOpen(menu.id);
    };
    const up = (e: KeyboardEvent) => { if (e.key === "Alt") setAlt(false); };
    const blur = () => setAlt(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", blur); };
  }, []);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!bar.current?.contains(e.target as Node)) setOpen(null); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(null); };
    const blur = () => setOpen(null);
    window.addEventListener("mousedown", away);
    window.addEventListener("keydown", key);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("mousedown", away); window.removeEventListener("keydown", key); window.removeEventListener("blur", blur); };
  }, [open]);
  // 文件(F) in Chinese; an underlined letter (shown while Alt is held) in English.
  const label = (m: Menu) => {
    if (zh) return `${m.label}(${m.key})`;
    const i = m.label.toUpperCase().indexOf(m.key);
    return i < 0 ? m.label : <>{m.label.slice(0, i)}<u>{m.label[i]}</u>{m.label.slice(i + 1)}</>;
  };
  return (
    <div ref={bar} className={`wb-menubar${alt ? " keys" : ""}`} role="menubar">
      {menus.map((m) => (
        <div key={m.id} className="wb-mb-item">
          <button type="button" role="menuitem" aria-haspopup="menu" aria-expanded={open === m.id} aria-keyshortcuts={`Alt+${m.key}`} className={open === m.id ? "on" : ""}
            onClick={() => setOpen(open === m.id ? null : m.id)} onMouseEnter={() => { if (open && open !== m.id) setOpen(m.id); }}>
            {label(m)}
          </button>
          {open === m.id && (
            <div className="wb-mb-list" role="menu">
              {m.entries.map((e, i) => e === "-" ? <div key={i} className="wb-mb-sep" role="separator" /> : (
                <button key={i} type="button" role="menuitem" onClick={() => { setOpen(null); e.run(); }}>
                  <span>{e.label}</span>{e.keys && <kbd>{e.keys}</kbd>}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
