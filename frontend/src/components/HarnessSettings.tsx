// Settings → Agent harness: default mode, context limit, permission rules, hooks,
// and read-only lists of the registered tools, commands and subagent types.
import { useEffect, useMemo, useState } from "react";
import { Bot, ChevronRight, FolderCog, Globe, NotebookText, Plus, RefreshCw, Search, ShieldAlert, ShieldCheck, ShieldQuestion, Slash, Trash2, Webhook, Wrench, X, type LucideIcon } from "lucide-react";
import {
  HOOK_EVENTS, harnessApi, isMissing,
  type HarnessAgentType, type HarnessCommand, type HarnessSettings as Settings, type HarnessTool, type HookEvent, type SessionMode,
} from "../api/harness";
import { useStore } from "../store";
import { MODES } from "./session/Composer";
import { formatTokens } from "./session/toolMeta";
import { EmptyState, Segmented, Spinner, Switch } from "./ui";
import { useVcsEntry } from "../vcsStore";

const RULES: { key: "allow" | "ask" | "deny"; icon: typeof ShieldCheck; en: string; zh: string; hintEn: string; hintZh: string }[] = [
  { key: "allow", icon: ShieldCheck, en: "Allow", zh: "允许", hintEn: "Runs without asking", hintZh: "无需询问直接执行" },
  { key: "ask", icon: ShieldQuestion, en: "Ask", zh: "询问", hintEn: "Pauses for your approval", hintZh: "暂停并等待你批准" },
  { key: "deny", icon: ShieldAlert, en: "Deny", zh: "拒绝", hintEn: "Always blocked (wins over the others)", hintZh: "始终阻止（优先级最高）" },
];

const HOOK_LABEL: Record<HookEvent, { en: string; zh: string }> = {
  pre_tool_use: { en: "Before a tool runs", zh: "工具执行前" },
  post_tool_use: { en: "After a tool runs", zh: "工具执行后" },
  session_start: { en: "Session start", zh: "会话开始" },
  stop: { en: "Turn finished", zh: "一轮结束" },
};

const LIMITS = [60_000, 120_000, 200_000, 400_000];

/** Where the harness keeps its files (`paths` of GET /api/harness/settings). */
const PATHS: { key: string; icon: LucideIcon; scope: "global" | "project"; en: string; zh: string; hintEn: string; hintZh: string }[] = [
  { key: "home", icon: Globe, scope: "global", en: "GlossX home", zh: "GlossX 主目录", hintEn: "Settings, commands, subagents and skills shared by every project", hintZh: "所有项目共用的设置、命令、子智能体与技能" },
  { key: "global_instructions", icon: NotebookText, scope: "global", en: "Global instructions", zh: "全局说明", hintEn: "GLOSSX.md the agent reads in every thread", hintZh: "智能体在每个对话里都会读取的 GLOSSX.md" },
  { key: "workspace_config", icon: FolderCog, scope: "project", en: "Project config", zh: "项目配置", hintEn: "The .glossx folder of the active project: adds to and overrides the global files", hintZh: "当前项目的 .glossx 目录：补充并覆盖全局配置" },
];

function RuleList({ kind, rules, onChange, zh }: { kind: (typeof RULES)[number]; rules: string[]; onChange: (r: string[]) => void; zh: boolean }) {
  const [draft, setDraft] = useState("");
  const I = kind.icon;
  const add = () => {
    const p = draft.trim();
    if (!p) return;
    if (!rules.includes(p)) onChange([...rules, p]);
    setDraft("");
  };
  return (
    <div className={`gx-rules rule-${kind.key}`}>
      <div className="gx-rules-head">
        <I size={14} />
        <strong>{zh ? kind.zh : kind.en}</strong>
        <span>{zh ? kind.hintZh : kind.hintEn}</span>
      </div>
      <div className="gx-rules-body">
        {rules.map((r) => (
          <span key={r} className="gx-rule">
            <code>{r}</code>
            <button type="button" aria-label="Remove" onClick={() => onChange(rules.filter((x) => x !== r))}><X size={11} /></button>
          </span>
        ))}
        <input
          className="gx-rule-input"
          value={draft}
          placeholder={rules.length ? (zh ? "添加规则…" : "Add a rule…") : kind.key === "allow" ? "shell_run(git status*)" : kind.key === "ask" ? "fs_write(notes/*)" : "web_fetch"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              add();
            } else if (e.key === "Backspace" && !draft && rules.length) onChange(rules.slice(0, -1));
          }}
          onBlur={add}
        />
      </div>
    </div>
  );
}

const SOURCE_ORDER = ["harness", "builtin", "project", "user", "plugin", "mcp", "skill"];
const SOURCE_LABEL: Record<string, { en: string; zh: string }> = {
  harness: { en: "Agent tools", zh: "智能体工具" },
  builtin: { en: "Built-in", zh: "内置" },
  project: { en: "Project", zh: "项目" },
  user: { en: "User", zh: "用户" },
  plugin: { en: "Plugins", zh: "插件" },
  mcp: { en: "MCP servers", zh: "MCP 服务器" },
  skill: { en: "Skills", zh: "技能" },
};
const GROUP_CAP = 12;

type RegItem = { name: string; description?: string; source?: string };

/**
 * A read-only registry list that stays usable with a hundred entries: search,
 * groups by source, and long groups (e.g. every installed skill) fold to a few rows.
 */
function ReadOnlyList<T extends RegItem>({ icon, title, items, zh, filters, row }: {
  icon: React.ReactNode;
  title: string;
  items: T[];
  zh: boolean;
  /** Optional quick filters shown next to the search box. */
  filters?: { id: string; label: string; test: (item: T) => boolean }[];
  row: (item: T) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const needle = q.trim().toLowerCase();
  const active = filters?.find((f) => f.id === filter);
  const shown = useMemo(
    () => items.filter((it) => (!active || active.test(it)) && (!needle || it.name.toLowerCase().includes(needle) || (it.description || "").toLowerCase().includes(needle))),
    [items, needle, active],
  );
  const groups = useMemo(() => {
    const by = new Map<string, T[]>();
    for (const it of shown) by.set(it.source || "other", [...(by.get(it.source || "other") || []), it]);
    const rank = (s: string) => (SOURCE_ORDER.indexOf(s) + 1 || 99);
    return [...by.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
  }, [shown]);
  return (
    <div className={`gx-fold${open ? " open" : ""}`}>
      <button type="button" className="gx-fold-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={13} className="gx-fold-caret" />
        {icon}
        <strong>{title}</strong>
        <span className="gx-count">{items.length}</span>
      </button>
      {open && (
        <>
          {items.length > 8 && (
            <div className="gx-reg-tools">
              <label className="gx-reg-search">
                <Search size={12} />
                <input value={q} placeholder={zh ? `筛选 ${items.length} 项…` : `Filter ${items.length} entries…`} onChange={(e) => setQ(e.target.value)} />
                {q && <button type="button" aria-label="Clear" onClick={() => setQ("")}><X size={11} /></button>}
              </label>
              {filters?.map((f) => (
                <button key={f.id} type="button" className={`gx-reg-filter${filter === f.id ? " on" : ""}`} onClick={() => setFilter(filter === f.id ? null : f.id)}>
                  {f.label} <i>{items.filter(f.test).length}</i>
                </button>
              ))}
            </div>
          )}
          <div className="gx-fold-body">
            {shown.length === 0 && <div className="gx-picker-empty">{items.length ? (zh ? "没有匹配项" : "No matches") : zh ? "暂无" : "Nothing registered"}</div>}
            {groups.map(([source, list]) => {
              const all = !!needle || expanded[source] || list.length <= GROUP_CAP + 2;
              const visible = all ? list : list.slice(0, GROUP_CAP);
              const label = SOURCE_LABEL[source];
              return (
                <div key={source} className="gx-reg-group">
                  {groups.length > 1 && <div className="gx-reg-group-head">{label ? (zh ? label.zh : label.en) : source}<i>{list.length}</i></div>}
                  {visible.map((it) => <div key={it.name} className="gx-reg-row">{row(it)}</div>)}
                  {!all && (
                    <button type="button" className="gx-link gx-reg-more" onClick={() => setExpanded({ ...expanded, [source]: true })}>
                      {zh ? `显示其余 ${list.length - visible.length} 项` : `Show ${list.length - visible.length} more`}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export function HarnessSettings() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const notify = useStore((s) => s.notify);
  const [saved, setSaved] = useState<Settings | null>(null);
  const [cfg, setCfg] = useState<Settings | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "missing" | "error">("loading");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [tools, setTools] = useState<HarnessTool[]>([]);
  const [commands, setCommands] = useState<HarnessCommand[]>([]);
  const [agents, setAgents] = useState<HarnessAgentType[]>([]);

  async function load() {
    setState("loading");
    try {
      // The editor saves global settings; project values are read only for paths.
      const s = await harnessApi.getSettings();
      const scoped = useStore.getState().agentProjectId;
      if (scoped) {
        const effective = await harnessApi.getSettings(scoped).catch(() => null);
        if (effective?.paths) s.paths = effective.paths;
      }
      setSaved(s);
      setCfg(s);
      setState("ok");
    } catch (e: any) {
      setState(isMissing(e) || e?.status === 0 ? "missing" : "error");
      setError(String(e?.message || e));
    }
    const scope = { projectId: useStore.getState().agentProjectId };
    void harnessApi.tools(undefined, scope).then(setTools).catch(() => setTools([]));
    void harnessApi.commands(scope).then(setCommands).catch(() => setCommands([]));
    void harnessApi.agents(scope).then(setAgents).catch(() => setAgents([]));
  }
  useEffect(() => {
    void load();
  }, []);

  const projectId = useStore((s) => s.agentProjectId);
  const projectName = useStore((s) => s.projects.find((p) => p.id === s.agentProjectId)?.name);
  const vcsPhase = useVcsEntry(projectId).phase;
  // the project config location follows the active project
  useEffect(() => {
    if (state === "ok") void harnessApi.getSettings(projectId).then((s) => {
      setSaved((prev) => prev ? { ...prev, paths: s.paths } : prev);
      setCfg((prev) => prev ? { ...prev, paths: s.paths } : prev);
    }).catch(() => {});
  }, [projectId]);

  const dirty = useMemo(() => !!cfg && !!saved && JSON.stringify(cfg) !== JSON.stringify(saved), [cfg, saved]);
  const patch = (p: Partial<Settings>) => setCfg((c) => (c ? { ...c, ...p } : c));

  async function save() {
    if (!cfg) return;
    setSaving(true);
    try {
      // partial update: send only the keys this panel edits (unknown keys are rejected)
      const clean: Partial<Settings> = {
        default_mode: cfg.default_mode,
        context_limit: Math.max(4000, cfg.context_limit),
        permissions: cfg.permissions,
        hooks: Object.fromEntries(HOOK_EVENTS.map((e) => [e, cfg.hooks[e].filter((h) => h.command.trim()).map((h) => ({ ...h, matcher: h.matcher.trim() || "*", command: h.command.trim() }))])) as Settings["hooks"],
      };
      if (cfg.vcs) clean.vcs = cfg.vcs;
      if (cfg.browser) clean.browser = cfg.browser;
      const next = await harnessApi.putSettings(clean);
      const displayed = { ...next, paths: cfg.paths };
      setSaved(displayed);
      setCfg(displayed);
      notify(zh ? "智能体设置已保存" : "Harness settings saved");
      void harnessApi.tools(undefined, { projectId: useStore.getState().agentProjectId }).then(setTools).catch(() => {});
    } catch (e: any) {
      notify(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  }

  if (state === "loading") {
    return <div className="gx-settings"><div className="gx-chat-loading"><div className="gx-skel w40" /><div className="gx-skel w90" /><div className="gx-skel w75" /><div className="gx-skel w60" /></div></div>;
  }
  if (state !== "ok" || !cfg) {
    return (
      <div className="gx-settings">
        <EmptyState
          icon={<ShieldQuestion size={20} />}
          title={state === "missing" ? (zh ? "智能体设置不可用" : "Harness settings unavailable") : zh ? "无法读取设置" : "Could not load settings"}
          hint={state === "missing" ? (zh ? "当前后端尚未提供 /api/harness/settings。" : "This backend does not expose /api/harness/settings yet.") : error}
          action={<button type="button" className="gx-btn sm" onClick={() => void load()}><RefreshCw size={12} /> {zh ? "重试" : "Retry"}</button>}
        />
      </div>
    );
  }

  const permBadge = (t: HarnessTool) => (t.permission === "allow" ? "ok" : t.permission === "deny" || t.permission === "unavailable" ? "bad" : "warn");
  const customLimit = !LIMITS.includes(cfg.context_limit);

  return (
    <div className="gx-settings">
      <p className="gx-set-scope">{zh ? "这里编辑全局设置。项目 .glossx/settings.json 中的覆盖设置仍会优先应用。" : "Edit global settings here. Overrides in a project’s .glossx/settings.json still take precedence."}</p>
      <section className="gx-set">
        <h3><Globe size={15} /> {zh ? "浏览器" : "Browser"}</h3>
        <p className="gx-set-hint">{zh ? "使用独立的 GlossX 浏览器配置。留空会自动寻找 Edge、Chrome 或 Chromium。" : "Uses a separate GlossX browser profile. Leave the path empty to detect Edge, Chrome or Chromium."}</p>
        <input className="gx-input" aria-label={zh ? "浏览器可执行文件" : "Browser executable"} placeholder={zh ? "自动检测浏览器" : "Detect browser automatically"} value={cfg.browser?.path || ""} onChange={(e) => patch({ browser: { ...cfg.browser, path: e.target.value || null } })} />
        <Switch checked={cfg.browser?.headed ?? false} onChange={(v) => patch({ browser: { ...cfg.browser, headed: v } })} label={zh ? "显示浏览器窗口" : "Show browser window"} />
        <Switch checked={cfg.browser?.allow_private ?? false} onChange={(v) => patch({ browser: { ...cfg.browser, allow_private: v } })} label={zh ? "允许访问本机及私有网络" : "Allow local and private network pages"} />
        <div className="gx-browser-setting-numbers">
          <label>{zh ? "空闲关闭（秒）" : "Idle timeout (seconds)"}<input className="gx-input" type="number" min={1} max={10000} value={cfg.browser?.idle_timeout ?? 300} onChange={(e) => patch({ browser: { ...cfg.browser, idle_timeout: Math.min(10000, Math.max(1, Math.round(Number(e.target.value) || 300))) } })} /></label>
          <label>{zh ? "每轮导航上限" : "Navigation budget per turn"}<input className="gx-input" type="number" min={1} max={10000} value={cfg.browser?.navigation_budget ?? 60} onChange={(e) => patch({ browser: { ...cfg.browser, navigation_budget: Math.min(10000, Math.max(1, Math.round(Number(e.target.value) || 60))) } })} /></label>
        </div>
        <p className="gx-set-hint">{zh ? "默认阻止私有地址。修改后在下一次浏览器启动时生效。" : "Private addresses are blocked by default. Changes apply when the browser next starts."}</p>
      </section>

      <section className="gx-set">
        <h4>{zh ? "默认模式" : "Default mode"}</h4>
        <p>{zh ? "新对话开始时使用的模式。" : "The mode a new chat starts in."}</p>
        <Segmented
          value={cfg.default_mode}
          onChange={(m: SessionMode) => patch({ default_mode: m })}
          options={MODES.map((m) => {
            const I = m.icon;
            return { value: m.id, label: zh ? m.zh : m.en, icon: <I size={13} />, title: zh ? m.hintZh : m.hintEn };
          })}
        />
        <p className="gx-set-hint">{(() => { const m = MODES.find((x) => x.id === cfg.default_mode)!; return zh ? m.hintZh : m.hintEn; })()}</p>
      </section>

      <section className="gx-set">
        <h4>{zh ? "上下文上限" : "Context limit"}</h4>
        <p>{zh ? "达到上限的 80% 时，较早的对话会被自动总结压缩。" : "Older turns are summarized automatically at 80% of this limit."}</p>
        <div className="gx-set-row">
          <Segmented
            size="sm"
            value={customLimit ? "custom" : String(cfg.context_limit)}
            onChange={(v) => v !== "custom" && patch({ context_limit: Number(v) })}
            options={[...LIMITS.map((n) => ({ value: String(n), label: formatTokens(n) })), ...(customLimit ? [{ value: "custom", label: zh ? "自定义" : "Custom" }] : [])]}
          />
          <label className="gx-num">
            <input
              type="number"
              min={4000}
              step={1000}
              value={cfg.context_limit}
              onChange={(e) => patch({ context_limit: Math.max(0, Math.round(Number(e.target.value) || 0)) })}
            />
            <span>tokens</span>
          </label>
        </div>
      </section>

      <section className="gx-set">
        <h4>{zh ? "版本控制" : "Version control"}</h4>
        <Switch
          checked={cfg.vcs?.auto_commit ?? true}
          onChange={(v) => patch({ vcs: { ...(cfg.vcs || {}), auto_commit: v } })}
          label={zh ? "每轮结束后自动提交" : "Commit automatically after each turn"}
          hint={zh ? "智能体一轮工作结束时，把工作区的修改记为一次提交。关闭后仍可在“源代码管理”里手动提交；检查点不受影响。" : "When a turn ends, the workspace changes are recorded as one commit. With this off you can still commit by hand in Source control; checkpoints are unaffected."}
        />
        {vcsPhase === "nogit" && <p className="gx-set-hint">{zh ? "这台电脑上没有安装 Git，所以目前不会产生提交。" : "Git is not installed on this computer, so no commits are made for now."}</p>}
      </section>

      {cfg.paths && Object.values(cfg.paths).some(Boolean) && (
        <section className="gx-set">
          <h4>{zh ? "配置文件的位置" : "Where the agent’s files live"}</h4>
          <p>
            {zh ? "和 Claude Code 的 " : "Like Claude Code’s "}<code>.claude</code>{zh ? " 一样，GlossX 把智能体的文件放在 " : ", GlossX keeps the agent’s files in "}<code>.glossx</code>
            {zh ? " 目录里：全局一份，每个项目的工作区里各一份。" : " folders: one global, and one inside each project’s workspace."}
          </p>
          <div className="gx-paths">
            {[...PATHS, ...Object.keys(cfg.paths).filter((k) => !PATHS.some((p) => p.key === k)).map((k) => ({ key: k, icon: FolderCog, scope: "global" as const, en: k, zh: k, hintEn: "", hintZh: "" }))]
              .filter((p) => cfg.paths?.[p.key])
              .map((p) => {
                const I = p.icon;
                const value = String(cfg.paths![p.key]);
                return (
                  <div key={p.key} className="gx-path">
                    <I size={14} />
                    <div>
                      <strong>{zh ? p.zh : p.en}<span className={`gx-tag${p.scope === "project" ? " warn" : ""}`}>{p.scope === "project" ? (zh ? `本项目${projectName ? ` · ${projectName}` : ""}` : `this project${projectName ? ` · ${projectName}` : ""}`) : zh ? "全局" : "global"}</span></strong>
                      {(zh ? p.hintZh : p.hintEn) && <small>{zh ? p.hintZh : p.hintEn}</small>}
                      <code title={zh ? "点击复制" : "Click to copy"} onClick={() => void navigator.clipboard?.writeText(value).then(() => notify(zh ? "已复制路径" : "Path copied")).catch(() => {})}>{value}</code>
                    </div>
                  </div>
                );
              })}
          </div>
        </section>
      )}

      <section className="gx-set">
        <h4>{zh ? "权限规则" : "Permission rules"}</h4>
        <p>
          {zh ? "规则可以是工具名、通配（" : "A rule is a tool name, a glob ("}<code>mcp_*</code>{zh ? "）或带参数匹配（" : ") or a tool with an argument pattern ("}<code>shell_run(git status*)</code>{zh ? "）。回车添加。" : "). Press Enter to add."}
        </p>
        {RULES.map((k) => (
          <RuleList key={k.key} kind={k} zh={zh} rules={cfg.permissions[k.key]} onChange={(r) => patch({ permissions: { ...cfg.permissions, [k.key]: r } })} />
        ))}
      </section>

      <section className="gx-set">
        <h4>{zh ? "钩子" : "Hooks"}</h4>
        <p>{zh ? "在工作区里运行的 shell 命令，事件 JSON 通过标准输入传入。“工具执行前”的钩子以非零退出码结束时会阻止该调用。" : "Shell commands run in the workspace with the event JSON on stdin. A non-zero exit from a “before” hook blocks the call."}</p>
        {HOOK_EVENTS.map((ev) => (
          <div key={ev} className="gx-hooks">
            <div className="gx-hooks-head">
              <Webhook size={13} />
              <strong>{zh ? HOOK_LABEL[ev].zh : HOOK_LABEL[ev].en}</strong>
              <code>{ev}</code>
              <span className="spacer" />
              <button
                type="button"
                className="gx-icon-btn"
                title={zh ? "添加钩子" : "Add hook"}
                onClick={() => patch({ hooks: { ...cfg.hooks, [ev]: [...cfg.hooks[ev], { matcher: "*", command: "", timeout: 30 }] } })}
              >
                <Plus size={14} />
              </button>
            </div>
            {cfg.hooks[ev].map((h, i) => {
              const set = (p: Partial<typeof h>) => patch({ hooks: { ...cfg.hooks, [ev]: cfg.hooks[ev].map((x, j) => (j === i ? { ...x, ...p } : x)) } });
              return (
                <div key={i} className="gx-hook">
                  {ev.endsWith("tool_use") && (
                    <input className="gx-input mono matcher" value={h.matcher} placeholder="*" title={zh ? "匹配的工具" : "Tool matcher"} onChange={(e) => set({ matcher: e.target.value })} />
                  )}
                  <input className="gx-input mono" value={h.command} placeholder={zh ? "shell 命令" : "shell command"} onChange={(e) => set({ command: e.target.value })} />
                  <button type="button" className="gx-icon-btn" title={zh ? "删除" : "Remove"} onClick={() => patch({ hooks: { ...cfg.hooks, [ev]: cfg.hooks[ev].filter((_, j) => j !== i) } })}>
                    <Trash2 size={13} />
                  </button>
                </div>
              );
            })}
          </div>
        ))}
      </section>

      <section className="gx-set">
        <h4>{zh ? "已注册" : "Registered"}</h4>
        <p>{zh ? "来自内置能力、技能、MCP 服务器和插件，只读。" : "From built-ins, skills, MCP servers and plugins. Read-only."}</p>
        <ReadOnlyList
          icon={<Wrench size={13} />}
          title={zh ? `工具 · ${(() => { const m = MODES.find((x) => x.id === saved?.default_mode); return m ? (zh ? m.zh : m.en) : ""; })()}模式下的权限` : `Tools · permission in ${MODES.find((x) => x.id === saved?.default_mode)?.en || ""} mode`}
          items={tools}
          zh={zh}
          filters={[
            { id: "allow", label: zh ? "允许" : "allow", test: (t) => t.permission === "allow" },
            { id: "ask", label: zh ? "询问" : "ask", test: (t) => t.permission === "ask" },
            { id: "blocked", label: zh ? "不可用" : "blocked", test: (t) => t.permission === "deny" || t.permission === "unavailable" },
          ]}
          row={(t) => (
            <div className="gx-reg" title={[t.permission_reason, t.description].filter(Boolean).join("\n")}>
              <code>{t.name}</code>
              {t.category && <span className="gx-reg-cat">{t.category}</span>}
              <span className={`gx-tag ${permBadge(t)}`}>{t.permission || "—"}</span>
              {t.description && <p>{t.description}</p>}
            </div>
          )}
        />
        <ReadOnlyList
          icon={<Slash size={13} />}
          title={zh ? "命令" : "Commands"}
          items={commands}
          zh={zh}
          row={(c) => (
            <div className="gx-reg">
              <code>/{c.name}{c.argument_hint ? ` ${c.argument_hint}` : ""}</code>
              {c.description && <p>{c.description}</p>}
            </div>
          )}
        />
        <ReadOnlyList
          icon={<Bot size={13} />}
          title={zh ? "子智能体类型" : "Subagent types"}
          items={agents}
          zh={zh}
          row={(a) => (
            <div className="gx-reg">
              <code>{a.name}</code>
              {a.model && <span className="gx-tag muted">{a.model}</span>}
              {a.description && <p>{a.description}</p>}
              {!!a.tools?.length && <p className="tools">{a.tools[0] === "*" ? (zh ? "全部工具" : "all tools") : `${a.tools.slice(0, 12).join(" · ")}${a.tools.length > 12 ? ` +${a.tools.length - 12}` : ""}`}</p>}
            </div>
          )}
        />
      </section>

      <div className={`gx-savebar${dirty ? " show" : ""}`}>
        <span>{zh ? "有未保存的修改" : "Unsaved changes"}</span>
        <button type="button" className="gx-btn ghost sm" disabled={saving} onClick={() => setCfg(saved)}>{zh ? "放弃" : "Discard"}</button>
        <button type="button" className="gx-btn primary sm" disabled={saving || !dirty} onClick={() => void save()}>
          {saving && <Spinner size={12} />} {zh ? "保存" : "Save"}
        </button>
      </div>
    </div>
  );
}
