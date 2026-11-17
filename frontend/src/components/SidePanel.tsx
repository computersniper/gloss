import { useState } from "react";
import { useStore } from "../store";
import { SummaryPanel } from "./SummaryPanel";
import { ExplainPanel } from "./ExplainPanel";
import { TranslatePanel } from "./TranslatePanel";
import { ChatPanel } from "./ChatPanel";
import { AgentPanel } from "./AgentPanel";
import { HighlightsPanel } from "./HighlightsPanel";
import { ReferencesPanel } from "./ReferencesPanel";
import { ScholarPanel } from "./ScholarPanel";
import { SkillsPanel } from "./SkillsPanel";
import { SettingsPanel } from "./SettingsPanel";
import { MindMapPanel } from "./MindMapPanel";
import { NotesPanel } from "./NotesPanel";
import { PersonalNotesPanel } from "./PersonalNotesPanel";
import { TexPanel } from "./TexPanel";
import { ExtensionsPanel } from "./ExtensionsPanel";
import { PluginPanel } from "./PluginPanel";
import type { PluginManifest } from "../api/client";

interface TabDefinition {
  id: string;
  en: string;
  zh: string;
  icon?: string;
  C?: () => JSX.Element;
  plugin?: PluginManifest;
  arxivOnly?: boolean;
}

const TABS: TabDefinition[] = [
  { id: "summary", en: "Summary", zh: "速览", C: SummaryPanel },
  { id: "deep-note", en: "Deep Paper Note", zh: "深度论文笔记", C: NotesPanel },
  { id: "notes", en: "Notes", zh: "我的笔记", C: PersonalNotesPanel },
  { id: "mindmap", en: "Mind Map", zh: "思维导图", C: MindMapPanel },
  { id: "chat", en: "Chat", zh: "对话", C: ChatPanel },
  { id: "agent", en: "Agent", zh: "智能体", C: AgentPanel },
  { id: "explain", en: "Explain", zh: "解释", C: ExplainPanel },
  { id: "translate", en: "Translate", zh: "翻译", C: TranslatePanel },
  { id: "tex", en: "TeX", zh: "TeX 源码", C: TexPanel, arxivOnly: true },
  { id: "highlights", en: "Highlights", zh: "高亮", C: HighlightsPanel },
  { id: "references", en: "References", zh: "参考文献", C: ReferencesPanel },
  { id: "scholar", en: "Scholar", zh: "学术", C: ScholarPanel },
  { id: "skills", en: "Skills", zh: "技能", C: SkillsPanel },
  { id: "extensions", en: "Extensions", zh: "插件", C: ExtensionsPanel },
  { id: "settings", en: "Settings", zh: "设置", C: SettingsPanel },
];

// Tabs that GlossX hosts in their own workbench panes; the AI pane's
// "Paper tools" shows the rest.
const WORKBENCH_HOSTED = new Set(["chat", "agent", "scholar", "extensions", "settings"]);

/** Classic right-hand side panel. `variant="tools"` = GlossX "Paper tools". */
export function SidePanel({ variant = "classic" }: { variant?: "classic" | "tools" } = {}) {
  const activeTab = useStore((s) => s.activeTab);
  const setTab = useStore((s) => s.setTab);
  const askAboutText = useStore((s) => s.askAboutText);
  const uiLang = useStore((s) => s.uiLang);
  const current = useStore((s) => s.current);
  const installedPlugins = useStore((s) => s.pluginSnapshot.installed);
  const [ask, setAsk] = useState<{ x: number; y: number; text: string } | null>(null);
  // TeX tab only for arXiv papers (they have LaTeX source)
  const pluginTabs: TabDefinition[] = installedPlugins.map((plugin) => {
    const panel = plugin.contributes?.paper_sidebar;
    const icon = panel?.icon || plugin.icon || "🧩";
    return {
      id: `plugin:${plugin.id}`,
      en: panel?.tab_name || plugin.tab_name || plugin.name,
      zh: panel?.tab_name_zh || plugin.tab_name_zh || plugin.name_zh || panel?.tab_name || plugin.tab_name || plugin.name,
      icon,
      plugin,
    };
  });
  const extensionIndex = TABS.findIndex((tab) => tab.id === "extensions");
  const tabs = [
    ...TABS.slice(0, extensionIndex),
    ...pluginTabs,
    ...TABS.slice(extensionIndex),
  ]
    .filter((t) => !t.arxivOnly || !!current?.arxiv_id)
    .filter((t) => variant === "classic" || !WORKBENCH_HOSTED.has(t.id));
  // fall back to the first tab if the active one is not in this variant
  const shown = tabs.some((t) => t.id === activeTab) ? activeTab : tabs[0]?.id;

  function onMouseUp(e: React.MouseEvent) {
    // don't offer "add to chat" from within the chat/settings tabs themselves
    if (shown === "chat" || shown === "settings") return setAsk(null);
    const text = window.getSelection()?.toString().trim() || "";
    if (text.length < 2) return setAsk(null);
    setAsk({ x: e.clientX, y: e.clientY, text });
  }

  return (
    <div className={`side-panel${variant === "tools" ? " paper-tools" : ""}`}>
      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`tab${t.plugin ? " plugin-tab" : ""}${shown === t.id ? " active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.plugin && <span className="plugin-tab-icon" aria-hidden="true">{t.icon}</span>}
            <span>{uiLang === "zh" ? t.zh : t.en}</span>
          </button>
        ))}
      </div>
      {/* Every panel stays mounted (hidden when inactive) so scroll position,
          mind-map pan/zoom, and in-flight work are preserved across tab switches. */}
      <div className="tab-content" onMouseUp={onMouseUp} onMouseDown={() => setAsk(null)}>
        {tabs.map((t) => {
          const C = t.C;
          return (
            <div key={t.id} className="tab-pane" style={{ display: shown === t.id ? "flex" : "none" }}>
              {t.plugin ? <PluginPanel plugin={t.plugin} /> : C ? <C /> : null}
            </div>
          );
        })}
      </div>
      {ask && (
        <button
          className="ask-chat-pop"
          style={{ position: "fixed", left: ask.x, top: ask.y + 10, zIndex: 40 }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            askAboutText(ask.text);
            window.getSelection()?.removeAllRanges();
            setAsk(null);
          }}
        >
          💬 {uiLang === "zh" ? "添加到会话" : "Add to chat"}
        </button>
      )}
    </div>
  );
}
