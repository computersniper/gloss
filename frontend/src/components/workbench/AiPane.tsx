import { useEffect, useState, type ReactNode } from "react";
import { useStore, type AiMode } from "../../store";
import { SessionChat } from "../session/SessionChat";
import { SideChat } from "../session/SideChat";
import { SidePanel } from "../SidePanel";
import { FileText } from "lucide-react";
import { EmptyState } from "../ui";
import { Icon } from "./icons";
import { BrowserView } from "./BrowserView";
import { openBrowser } from "../../browserStore";
import { useSessions } from "../../sessionStore";
import { ThreadsHeadActions, ThreadsView } from "./ThreadsView";

const MODES: { id: AiMode; icon: string; en: string; zh: string }[] = [
  { id: "agent", icon: "agent", en: "Agent", zh: "智能体" },
  { id: "threads", icon: "threads", en: "Threads", zh: "对话列表" },
  { id: "tools", icon: "tools", en: "Tools", zh: "论文工具" },
  { id: "browser", icon: "browser", en: "Browser", zh: "浏览器" },
];

function NeedsPaper() {
  const zh = useStore((s) => s.uiLang) === "zh";
  return (
    <EmptyState
      icon={<FileText size={20} />}
      title={zh ? "先打开一篇论文" : "Open a paper first"}
      hint={zh ? "论文工具针对当前打开的论文工作。" : "The paper tools work on the paper in the active tab."}
    />
  );
}

/** The agent thread, with its side chat docked below when open. */
function AgentSlot() {
  const open = useStore((s) => s.sideChatOpen);
  return (
    <div className={`wb-ai-agent${open ? " with-side" : ""}`}>
      <div className="wb-ai-main"><SessionChat /></div>
      {open && <SideChat />}
    </div>
  );
}

function ToolsSlot() {
  const current = useStore((s) => s.current);
  return current ? <SidePanel variant="tools" /> : <NeedsPaper />;
}

/** Every thread (and the inbox), next to the agent chat they open in. */
function ThreadsSlot() {
  return (
    <div className="wb-ai-threads">
      <div className="wb-pane-head"><span className="spacer" /><ThreadsHeadActions /></div>
      <ThreadsView />
    </div>
  );
}

function BrowserSlot() {
  const browserId = useStore((s) => s.browserSessionId);
  const activeId = useSessions((s) => s.activeId);
  const id = browserId || activeId;
  const visible = useStore((s) => s.layout.aiOpen && s.layout.aiMode === "browser");
  const zh = useStore((s) => s.uiLang) === "zh";
  return id ? <BrowserView key={id} sessionId={id} visible={visible} /> : <EmptyState title={zh ? "打开共享浏览器" : "Open the shared browser"} hint={zh ? "输入网址，与智能体一起浏览。" : "Browse a page together with your agent."} action={<button className="gx-btn" onClick={() => void openBrowser()}>{zh ? "打开浏览器" : "Open browser"}</button>} />;
}

/** Default slot contents. The pane itself is only a container, so any slot can
 *  be swapped (e.g. a session-based agent chat) without touching the layout. */
export const DEFAULT_AI_SLOTS: Record<AiMode, () => ReactNode> = {
  agent: () => <AgentSlot />,
  threads: () => <ThreadsSlot />,
  tools: () => <ToolsSlot />,
  browser: () => <BrowserSlot />,
};

export function AiPane({ slots = DEFAULT_AI_SLOTS, onClose }: { slots?: Record<AiMode, () => ReactNode>; onClose?: () => void }) {
  const mode = useStore((s) => s.layout.aiMode);
  const setLayout = useStore((s) => s.setLayout);
  const current = useStore((s) => s.current);
  const zh = useStore((s) => s.uiLang) === "zh";
  // Panes stay mounted after first use so scroll position and in-flight work survive switches.
  const [visited, setVisited] = useState<Set<AiMode>>(() => new Set([mode]));
  useEffect(() => {
    setVisited((v) => (v.has(mode) ? v : new Set(v).add(mode)));
  }, [mode]);

  return (
    <div className="wb-ai" data-ai-pane tabIndex={-1}>
      <div className="wb-ai-head">
        <div className="wb-ai-tabs" role="tablist">
          {MODES.map((m) => (
            <button
              key={m.id}
              role="tab"
              aria-selected={mode === m.id}
              className={mode === m.id ? "on" : ""}
              onClick={() => setLayout({ aiMode: m.id })}
              title={zh ? m.zh : m.en}
            >
              <Icon name={m.icon} size={14} />
              <span>{zh ? m.zh : m.en}</span>
            </button>
          ))}
        </div>
        <span className="wb-ai-context" title={current?.title}>{mode === "tools" ? current?.title || "" : ""}</span>
        {onClose && (
          <button className="wb-icon-btn" title={zh ? "收起 AI 面板 (Ctrl+L)" : "Hide AI pane (Ctrl+L)"} onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        )}
      </div>
      <div className="wb-ai-body">
        {MODES.filter((m) => visited.has(m.id)).map((m) => (
          <div key={m.id} className="wb-ai-slot" style={{ display: mode === m.id ? "flex" : "none" }}>
            {slots[m.id]()}
          </div>
        ))}
      </div>
    </div>
  );
}
