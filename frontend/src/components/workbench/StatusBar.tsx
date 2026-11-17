import { useStore } from "../../store";
import { ProviderSwitcher } from "../ProviderSwitcher";
import { THEMES } from "../ThemePicker";
import { useAllAiTasks } from "../../api/aiTasks";
import { Activity, BellRing, Languages, Palette, Terminal } from "lucide-react";
import { useActiveSession, useSessions } from "../../sessionStore";
import { formatTokens } from "../session/toolMeta";
import { MenuButton, MenuItem, MenuLabel, Spinner } from "../ui";
import { BranchSwitcher, ProjectSwitcher } from "./BranchSwitcher";
import { CacheIndicator } from "../session/CacheUsage";
import { QuotaIndicator } from "./QuotaIndicator";
import { isTouchClient } from "../../mobile";
import { useZoom, zoomReset } from "../../zoom";

/** Three representative colors per theme for the picker swatches. */
const SWATCH: Record<string, [string, string, string]> = {
  midnight: ["#0f1216", "#1c2230", "#7aa2f7"],
  graphite: ["#17181a", "#292c30", "#6ea8fe"],
  nord: ["#2e3440", "#434c5e", "#88c0d0"],
  solarized: ["#002b36", "#0e4b5a", "#268bd2"],
  rose: ["#1c1620", "#35283d", "#f38ba8"],
  emerald: ["#0c1512", "#1b2f27", "#34d399"],
  daylight: ["#f7f8fa", "#dfe3e8", "#3b6fe0"],
};

const ACTIVE_IMPORT = new Set(["queued", "downloading", "parsing", "saving", "cancelling"]);

export function StatusBar() {
  const provider = useStore((s) => s.provider);
  const model = useStore((s) => s.providerModels[s.provider] || "");
  const runningRuns = useStore((s) => s.recentRuns.filter((r) => r.status === "running" || r.status === "awaiting_approval").length);
  const imports = useStore((s) => s.importJobs.filter((j) => ACTIVE_IMPORT.has(j.status)).length);
  const budget = useStore((s) => (s.agentRunId ? s.runs[s.agentRunId]?.budget : null));
  const runStatus = useStore((s) => (s.agentRunId ? s.runs[s.agentRunId]?.status : undefined));
  const uiLang = useStore((s) => s.uiLang);
  const setUiLang = useStore((s) => s.setUiLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const setLayout = useStore((s) => s.setLayout);
  const aiRunning = useAllAiTasks().filter((t) => t.running).length;
  const zh = uiLang === "zh";
  const appZoom = useZoom((s) => s.app);
  const liveSessions = useSessions((s) => s.list.filter((x) => x.status && x.status !== "idle").length);
  const hasSession = useSessions((s) => !!s.activeId);
  const blocking = useSessions((s) => s.inbox.blocking);
  const waitingLocal = useSessions((s) => s.list.filter((x) => x.status === "awaiting_approval" || x.status === "awaiting_input" || x.attention === "approval" || x.attention === "input").length);
  // the inbox stream is authoritative; the list covers backends without it
  const waiting = Math.max(blocking, waitingLocal);
  const session = useActiveSession();
  const busy = runningRuns + imports + aiRunning + liveSessions;
  const ctxPct = session.context.limit ? Math.round((session.context.tokens_est / session.context.limit) * 100) : 0;
  const pct = budget ? Math.round(Math.max(
    budget.max_steps ? budget.steps / budget.max_steps : 0,
    budget.max_llm_calls ? budget.llm_calls / budget.max_llm_calls : 0,
    budget.max_tokens_est ? budget.tokens_est / budget.max_tokens_est : 0,
  ) * 100) : 0;

  return (
    <footer className="wb-status">
      <ProviderSwitcher />
      {!isTouchClient() && <QuotaIndicator />}
      {model && <span className="wb-status-item muted" title={`${provider} · ${model}`}>{model}</span>}
      <ProjectSwitcher className="wb-status-item" placement="top-start" />
      <BranchSwitcher />
      {appZoom !== 1 && <button className="wb-status-item" title={zh ? "界面缩放，点击重置 (Ctrl+0)" : "Window zoom; click to reset (Ctrl+0)"} onClick={zoomReset}><span>{Math.round(appZoom * 100)}%</span></button>}
      <button className="wb-status-item" title={zh ? "打开当前工作区终端" : "Open workspace terminal"} onClick={() => setLayout({ bottomOpen: true, bottomTab: "terminal" })}><Terminal size={13} /><span>{zh ? "终端" : "Terminal"}</span></button>
      <CacheIndicator zh={zh} status />
      {waiting > 0 && (
        <button
          className="wb-status-item attention"
          title={zh ? `${waiting} 个对话在等你批准或回答` : `${waiting} thread${waiting === 1 ? " is" : "s are"} waiting for your approval or answer`}
          onClick={() => useSessions.getState().openInbox()}
        >
          <BellRing size={13} />
          <span>{zh ? `${waiting} 个在等你` : `${waiting} waiting for you`}</span>
        </button>
      )}
      <button
        className={`wb-status-item${busy ? " busy" : ""}`}
        title={zh ? `运行中：${liveSessions} 个对话，${runningRuns} 个智能体运行，${imports} 个导入，${aiRunning} 个 AI 任务` : `Running: ${liveSessions} chats, ${runningRuns} agent runs, ${imports} imports, ${aiRunning} AI tasks`}
        onClick={() => setLayout({ bottomOpen: true, bottomTab: "tasks" })}
      >
        {busy ? <Spinner size={12} /> : <Activity size={13} />}
        <span>{busy ? (zh ? `${busy} 个任务进行中` : `${busy} running`) : zh ? "空闲" : "Idle"}</span>
      </button>
      {hasSession && session.context.limit > 0 && (
        <button
          className="wb-status-item"
          title={`${zh ? "对话上下文" : "Chat context"}: ${formatTokens(session.context.tokens_est)} / ${formatTokens(session.context.limit)} tokens`}
          onClick={() => setLayout({ aiOpen: true, aiMode: "agent" })}
        >
          <span className={`wb-mini-meter${ctxPct >= 80 ? " hot" : ""}`}><i style={{ width: `${Math.min(100, ctxPct)}%` }} /></span>
          <span>{zh ? "上下文" : "ctx"} {ctxPct}%</span>
        </button>
      )}
      {budget && (
        <button
          className="wb-status-item"
          title={`${zh ? "预算" : "Budget"}: ${budget.steps}/${budget.max_steps} steps · ${budget.llm_calls}/${budget.max_llm_calls} LLM · ${budget.tokens_est}/${budget.max_tokens_est} tokens`}
          onClick={() => setLayout({ bottomOpen: true, bottomTab: "trace" })}
        >
          <span className={`wb-mini-meter${pct >= 90 ? " hot" : ""}${runStatus === "running" ? " live" : ""}`}><i style={{ width: `${Math.min(100, pct)}%` }} /></span>
          {pct}%
        </button>
      )}
      <span className="spacer" />
      <button className="wb-status-item" title={zh ? "界面语言 · 切换为 English" : "Interface language · switch to 中文"} onClick={() => setUiLang(zh ? "en" : "zh")}>
        <Languages size={13} /> <span>{zh ? "中文" : "English"}</span>
      </button>
      <MenuButton
        className="wb-status-item"
        chevron={false}
        placement="top-end"
        width={200}
        title={zh ? "主题" : "Theme"}
        label={<><Palette size={13} /> <span>{THEMES.find((t) => t.id === theme)?.name || theme}</span></>}
      >
        {(close) => (
          <>
            <MenuLabel>{zh ? "主题" : "Theme"}</MenuLabel>
            {THEMES.map((t) => (
              <MenuItem
                key={t.id}
                icon={<span className="gx-swatch">{(SWATCH[t.id] || []).map((c) => <i key={c} style={{ background: c }} />)}</span>}
                label={t.name}
                selected={t.id === theme}
                onSelect={() => {
                  setTheme(t.id);
                  close();
                }}
              />
            ))}
          </>
        )}
      </MenuButton>
    </footer>
  );
}
