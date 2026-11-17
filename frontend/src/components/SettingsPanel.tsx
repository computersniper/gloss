import { useEffect, useState } from "react";
import { api, type AiUsageSummary } from "../api/client";
import { useStore } from "../store";
import { PROVIDER_EFFORTS, effortsFor, refreshModelCatalog, useModelCatalog } from "../modelCatalog";
import { ThemePicker } from "./ThemePicker";
import { PdfModeToggle } from "./PdfModeToggle";
import { FeatureSettingsEditor } from "./FeatureSettingsEditor";
import { ConnectionsSection } from "./ConnectionsSection";

const LANGS = [
  "中文 (Simplified Chinese)",
  "English",
  "日本語",
  "한국어",
  "Français",
  "Deutsch",
  "Español",
];

// [English name, Chinese name, English hint, Chinese hint]
const PROVIDER_NAMES: Record<string, [string, string, string, string]> = {
  local_claude: ["Claude Code (local CLI)", "Claude Code（本机 CLI）", "Uses your local claude login; no API key", "使用本机 claude 的登录，无需 API key"],
  local_codex: ["Codex (local CLI)", "Codex（本机 CLI）", "Uses your local codex login; no API key", "使用本机 codex 的登录，无需 API key"],
  anthropic: ["Anthropic API", "Anthropic API", "API key, or any Anthropic-compatible endpoint", "API key，或任意 Anthropic 兼容端点"],
  openai: ["OpenAI-compatible API", "OpenAI 兼容 API", "OpenAI, a local vLLM, or any compatible endpoint", "OpenAI、本地 vLLM 或任意兼容端点"],
};

export function SettingsPanel() {
  const loadSettings = useStore((s) => s.loadSettings);
  const notify = useStore((s) => s.notify);
  const uiLang = useStore((s) => s.uiLang);
  const setUiLang = useStore((s) => s.setUiLang);
  const checkProvider = useStore((s) => s.checkProvider);
  const settingsFocus = useStore((s) => s.settingsFocus);
  const openFeatureSettings = useStore((s) => s.openFeatureSettings);
  const [cfg, setCfg] = useState<any>(null);
  const [providers, setProviders] = useState<string[]>([]);
  const [allProviders, setAllProviders] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [localTools, setLocalTools] = useState<Record<string, {path: string; version: string | null; source: string}[]>>({});
  const [detecting, setDetecting] = useState(false);
  const [detectionError, setDetectionError] = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [usage, setUsage] = useState<AiUsageSummary | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  const T = {
    en: {
      testing: "Testing…",
      testBtn: "🔌 Test connection",
      ok: (provider: string, ms: number) => `✓ ${provider} OK (${ms}ms)`,
      failed: (provider: string, error: string) => `✗ ${provider} failed: ${error}`,
    },
    zh: {
      testing: "测试中…",
      testBtn: "🔌 测试连通性",
      ok: (provider: string, ms: number) => `✓ ${provider} 连通（${ms}ms）`,
      failed: (provider: string, error: string) => `✗ ${provider} 失败：${error}`,
    },
  }[uiLang];

  useEffect(() => {
    api.getSettings().then((s) => {
      setCfg(s.config);
      setProviders(s.available_providers);
      setAllProviders(s.all_providers || s.available_providers);
    });
    void refreshUsage();
    void detectCli();
  }, []);

  async function detectCli(refresh = false) {
    setDetecting(true);
    setDetectionError("");
    try { setLocalTools((await api.detectLocalCli(refresh)).tools); }
    catch (error: any) { setDetectionError(String(error?.message || error)); }
    finally { setDetecting(false); }
  }

  async function refreshUsage() {
    setUsageLoading(true);
    try {
      setUsage(await api.getAiUsage({ limit: 20 }));
    } catch {
      // An older backend may not have the optional local usage endpoint yet.
    } finally {
      setUsageLoading(false);
    }
  }

  const formatTokens = (value: number) => new Intl.NumberFormat().format(value || 0);

  if (!cfg) return <div className="panel-body muted">Loading…</div>;

  function setProv(name: string, field: string, val: any) {
    setCfg((c: any) => ({
      ...c,
      providers: { ...c.providers, [name]: { ...c.providers[name], [field]: val } },
    }));
  }

  async function save(checkConnection = true) {
    setSaving(true);
    try {
      await api.updateSettings({
        confirm_exit: cfg.confirm_exit !== false,
        provider: cfg.provider,
        output_language: cfg.output_language,
        target_language: cfg.target_language,
        providers: cfg.providers,
        feature_settings: cfg.feature_settings || {},
        paper_workspace_root: cfg.paper_workspace_root || "",
      });
      await loadSettings();
      void refreshModelCatalog();
      if (checkConnection) await checkProvider(cfg.provider);
      notify("Settings saved");
    } finally {
      setSaving(false);
    }
  }

  async function testConn() {
    setTesting(true);
    setTestResult(null);
    try {
      await save(false); // persist current selection/keys so we test exactly what's shown
      const r = await checkProvider(cfg.provider);
      setTestResult(
        r.ok
          ? { ok: true, text: T.ok(r.provider, r.latency_ms) }
          : { ok: false, text: T.failed(r.provider, r.error) },
      );
    } catch (e: any) {
      setTestResult({ ok: false, text: `✗ ${String(e.message || e)}` });
    } finally {
      setTesting(false);
    }
  }

  if (settingsFocus) {
    return (
      <div className="panel-body settings settings-feature-page">
        <FeatureSettingsEditor
          cfg={cfg}
          providers={providers}
          saving={saving}
          setCfg={setCfg}
          onSave={() => void save(false)}
        />
      </div>
    );
  }

  return (
    <div className="panel-body settings">
      <button className="settings-feature-entry" onClick={() => openFeatureSettings("core.summary")}>
        <span>🧩</span>
        <span>
          <strong>{uiLang === "zh" ? "插件与功能设置" : "Plugins & feature settings"}</strong>
          <small>{uiLang === "zh" ? "为总结、翻译、笔记和每个插件选择模型、推理强度及专属选项" : "Choose models, reasoning, context, and extension-specific options per feature"}</small>
        </span>
        <b>›</b>
      </button>

      <label>Interface language / 界面语言</label>
      <div className="seg">
        <button className={"seg-btn" + (uiLang === "en" ? " active" : "")} onClick={() => setUiLang("en")}>
          English
        </button>
        <button className={"seg-btn" + (uiLang === "zh" ? " active" : "")} onClick={() => setUiLang("zh")}>
          中文
        </button>
      </div>

      <label>Theme / 主题</label>
      <ThemePicker />

      <label>Reading mode / 阅读模式</label>
      <PdfModeToggle />

      <label>Exit confirmation / 退出确认</label>
      <label className="setting-toggle">
        <input
          type="checkbox"
          checked={cfg.confirm_exit !== false}
          onChange={(event) => setCfg({ ...cfg, confirm_exit: event.target.checked })}
        />
        <span>{uiLang === "zh" ? "关闭应用时显示退出提醒" : "Show a confirmation before quitting the app"}</span>
      </label>

      <label>{uiLang === "zh" ? "新论文工作区默认目录" : "Default folder for new paper workspaces"}</label>
      <input aria-label={uiLang === "zh" ? "新论文工作区默认目录" : "Default folder for new paper workspaces"} value={cfg.paper_workspace_root || ""} placeholder={uiLang === "zh" ? "留空使用应用数据目录" : "Blank uses the app data folder"} onChange={(e) => setCfg({ ...cfg, paper_workspace_root: e.target.value })} />
      <p className="usage-note">{uiLang === "zh" ? "输入本机绝对路径，保存后仅用于新建论文工作区；已有目录不会移动。单个项目可在工作区中更改位置。" : "Enter an absolute local path and save. This affects new paper workspaces; existing folders stay in place. Change an existing project’s folder in Workspace."}</p>

      <label>{uiLang === "zh" ? "模型接入方式" : "Model providers"}</label>
      <div className="provider-switches">
        {allProviders.map((p) => {
          const on = cfg.providers?.[p]?.enabled !== false;
          const onCount = allProviders.filter((x) => cfg.providers?.[x]?.enabled !== false).length;
          const last = on && onCount <= 1;
          return (
            <label key={p} className={"setting-toggle provider-switch" + (on ? " on" : "")} title={last ? (uiLang === "zh" ? "至少保留一种" : "At least one must stay on") : undefined}>
              <input
                type="checkbox"
                role="switch"
                checked={on}
                disabled={last}
                onChange={(e) => {
                  const enabled = e.target.checked;
                  setCfg((c: any) => {
                    const next = { ...c, providers: { ...c.providers, [p]: { ...c.providers[p], enabled } } };
                    // turning the active provider off moves the selection to the first one still on
                    if (!enabled && c.provider === p) {
                      next.provider = allProviders.find((x) => x !== p && next.providers?.[x]?.enabled !== false) || c.provider;
                    }
                    return next;
                  });
                }}
              />
              <span className="provider-switch-text">
                <b>{PROVIDER_NAMES[p]?.[uiLang === "zh" ? 1 : 0] || p}</b>
                <small>{PROVIDER_NAMES[p]?.[uiLang === "zh" ? 3 : 2] || ""}</small>
              </span>
            </label>
          );
        })}
      </div>

      <label>{uiLang === "zh" ? "当前使用" : "Active AI provider"}</label>
      <select value={cfg.provider} onChange={(e) => setCfg({ ...cfg, provider: e.target.value })}>
        {allProviders.filter((p) => cfg.providers?.[p]?.enabled !== false).map((p) => (
          <option key={p} value={p}>{PROVIDER_NAMES[p]?.[uiLang === "zh" ? 1 : 0] || p}</option>
        ))}
      </select>
      <section className="usage-card" aria-label={uiLang === "zh" ? "本地 CLI 识别" : "Local CLI detection"}>
        <div className="usage-head">
          <strong>{uiLang === "zh" ? "本地 CLI 自动识别" : "Detect local CLIs"}</strong>
          <button className="small" type="button" disabled={detecting} onClick={() => void detectCli(true)}>
            {detecting ? (uiLang === "zh" ? "识别中…" : "Detecting…") : uiLang === "zh" ? "重新识别" : "Scan again"}
          </button>
        </div>
        <p className="usage-note">{uiLang === "zh" ? "Codex 自动选择检测到的最新版本；手动路径优先。Claude Code 仅检查文件与安装信息，不会执行。" : "Codex automatically uses the newest detected version; manual paths take priority. Claude Code detection reads files only and never runs it."}</p>
        {detectionError && <p role="alert">{detectionError}</p>}
        {[{name: "codex", provider: "local_codex", title: "Codex"}, {name: "claude", provider: "local_claude", title: "Claude Code"}].map(tool => {
          const items = localTools[tool.name] || [];
          const manual = cfg.providers?.[tool.provider]?.cli_path || "";
          return <div key={tool.name}>
            <label>{tool.title} CLI</label>
            <select aria-label={`${tool.title} installation`} value={manual}
              onChange={e => setProv(tool.provider, "cli_path", e.target.value)}>
              <option value="">{uiLang === "zh" ? "自动识别" : "Auto-detect"}{items[0] ? ` · ${items[0].version || items[0].source}` : ""}</option>
              {manual && !items.some(item => item.path === manual) && <option value={manual}>{uiLang === "zh" ? "手动路径" : "Manual path"} · {manual}</option>}
              {items.map(item => <option key={item.path} value={item.path}>{item.version || (uiLang === "zh" ? "版本未知" : "Unknown version")} · {item.source} · {item.path}</option>)}
            </select>
            <input aria-label={`${tool.title} CLI path`} value={manual} placeholder={uiLang === "zh" ? "留空自动识别，或手动填写 CLI 路径" : "Blank auto-detects; or enter a CLI path"}
              onChange={e => setProv(tool.provider, "cli_path", e.target.value)} />
            <p className="usage-note" style={{overflowWrap: "anywhere"}}>{manual || items[0]?.path || (uiLang === "zh" ? "未找到本地安装" : "No local installation found")}</p>
          </div>;
        })}
        {["gemini", "opencode"].filter(name => localTools[name]?.length).map(name => <p className="usage-note" key={name} style={{overflowWrap: "anywhere"}}>
          {name}: {localTools[name][0].version || ""} · {localTools[name][0].path} · {uiLang === "zh" ? "已检测，暂未接入模型渠道" : "Detected; no model provider integration yet"}
        </p>)}
      </section>

      <div className="test-conn">
        <button className="small" onClick={testConn} disabled={testing}>
          {testing ? T.testing : T.testBtn}
        </button>
        {testResult && (
          <span className={"test-result " + (testResult.ok ? "ok" : "bad")}>{testResult.text}</span>
        )}
      </div>

      <ModelsSection cfg={cfg} providers={allProviders.filter((p) => cfg.providers?.[p]?.enabled !== false)} setProv={setProv} zh={uiLang === "zh"} />

      <label>Answer language (summary / explain / chat)</label>
      <select
        value={cfg.output_language}
        onChange={(e) => setCfg({ ...cfg, output_language: e.target.value })}
      >
        {LANGS.map((l) => <option key={l}>{l}</option>)}
      </select>

      <label>Translation target (translate feature)</label>
      <select
        value={cfg.target_language}
        onChange={(e) => setCfg({ ...cfg, target_language: e.target.value })}
      >
        {LANGS.map((l) => <option key={l}>{l}</option>)}
      </select>

      <section className="usage-card" aria-label="AI token usage">
        <div className="usage-head">
          <div>
            <strong>{uiLang === "zh" ? "AI 用量统计" : "AI usage"}</strong>
            <span>{uiLang === "zh" ? "本机保存，按完成的模型调用统计" : "Stored locally for completed model calls"}</span>
          </div>
          <button className="small" onClick={() => void refreshUsage()} disabled={usageLoading}>
            {usageLoading ? (uiLang === "zh" ? "加载中…" : "Loading…") : (uiLang === "zh" ? "刷新" : "Refresh")}
          </button>
        </div>
        {usage ? (
          <>
            <div className="usage-total">
              <span>{uiLang === "zh" ? "总计" : "Total"}<b>{formatTokens(usage.total.total_tokens)}</b></span>
              <span>{uiLang === "zh" ? "平均 / 任务" : "Average / task"}<b>{formatTokens(usage.total.average_total_tokens)}</b></span>
              <span>{uiLang === "zh" ? "任务数" : "Tasks"}<b>{formatTokens(usage.total.tasks)}</b></span>
              <span>{uiLang === "zh" ? "缓存输入" : "Cached input"}<b>{formatTokens(usage.total.cached_input_tokens || 0)}</b></span>
              <span>{uiLang === "zh" ? "缓存命中率" : "Cache hit rate"}<b>{Math.round((usage.total.cache_hit_rate || 0) * 100)}%</b></span>
            </div>
            {usage.by_task.length > 0 && (
              <div className="usage-by-task">
                {usage.by_task.map((row) => (
                  <div key={row.task_type}>
                    <span>{row.task_type.replaceAll("_", " ")}</span>
                    <b>{formatTokens(row.average_total_tokens)} <small>{uiLang === "zh" ? "平均" : "avg"} · {Math.round((row.cache_hit_rate || 0) * 100)}% {uiLang === "zh" ? "缓存" : "cached"}</small></b>
                  </div>
                ))}
              </div>
            )}
            {[{ label: uiLang === "zh" ? "按提供方" : "By provider", rows: usage.by_provider || [], key: "provider" }, { label: uiLang === "zh" ? "按日期" : "By day", rows: usage.by_day || [], key: "day" }].map((group) => group.rows.length > 0 && <div className="usage-by-task" key={group.key}><strong>{group.label}</strong>{group.rows.map((row: any) => <div key={row[group.key]}><span>{row[group.key]}</span><b>{formatTokens(row.total_tokens)} · {Math.round((row.cache_hit_rate || 0) * 100)}% <small>{uiLang === "zh" ? "缓存" : "cached"}</small></b></div>)}</div>)}
            <p className="usage-note">
              {usage.total.estimated_tasks > 0
                ? (uiLang === "zh"
                  ? `${usage.total.estimated_tasks} 个任务为估算值（本地 CLI/流式调用未返回精确 token）。`
                  : `${usage.total.estimated_tasks} task(s) are estimated because the local CLI or stream did not report exact tokens.`)
                : (uiLang === "zh" ? "所有记录均由 provider 返回精确 token。" : "All recorded tasks include provider-reported token usage.")}
            </p>
          </>
        ) : (
          <p className="usage-note">{usageLoading ? "…" : (uiLang === "zh" ? "暂无已完成的 AI 任务。" : "No completed AI tasks yet.")}</p>
        )}
      </section>

      <ConnectionsSection />

      <div className="adv-head">{uiLang === "zh" ? "API 连接" : "API connections"}</div>

      <fieldset>
        <legend>anthropic API</legend>
        <label>base_url</label>
        <input value={cfg.providers.anthropic.base_url || ""} placeholder="https://api.anthropic.com"
          onChange={(e) => setProv("anthropic", "base_url", e.target.value)} />
        <label>api key</label>
        <input type="password" placeholder={cfg.providers.anthropic.api_key === "set" ? "•••• saved" : "sk-ant-…"}
          onChange={(e) => setProv("anthropic", "api_key", e.target.value)} />
      </fieldset>

      <fieldset>
        <legend>openai-compatible</legend>
        <label>base_url</label>
        <input value={cfg.providers.openai.base_url}
          onChange={(e) => setProv("openai", "base_url", e.target.value)} />
        <label>api key</label>
        <input type="password" placeholder={cfg.providers.openai.api_key === "set" ? "•••• saved" : "sk-…"}
          onChange={(e) => setProv("openai", "api_key", e.target.value)} />
      </fieldset>

      <button className="primary" onClick={() => void save().catch((e) => notify(String(e?.message || e)))} disabled={saving}>
        {saving ? "Saving…" : "Save settings"}
      </button>
    </div>
  );
}

/** Settings › Models: per channel, the default model, the default reasoning
 *  effort, and extra model ids that show up in the composer's model list. */
function ModelsSection({ cfg, providers, setProv, zh }: { cfg: any; providers: string[]; setProv: (name: string, field: string, val: any) => void; zh: boolean }) {
  const catalog = useModelCatalog();
  const [adding, setAdding] = useState<Record<string, string>>({});
  return (
    <section className="settings-models" id="settings-models" aria-label={zh ? "模型设置" : "Models"}>
      <div className="adv-head">{zh ? "模型设置" : "Models"}</div>
      <p className="usage-note">{zh ? "在左下角切换渠道后，输入框的模型列表会换成该渠道的模型。这里设置每个渠道的默认模型、默认推理强度，以及额外的自定义模型。" : "Switching the channel in the bottom-left corner swaps the composer's model list. Set each channel's default model, default reasoning effort and extra custom models here."}</p>
      {providers.map((p) => {
        const pc = cfg.providers?.[p] || {};
        const custom: string[] = Array.isArray(pc.models) ? pc.models : [];
        const suggested = (catalog.models[p] || []).filter((m) => !m.custom);
        const ids = [...new Set([...suggested.map((m) => m.id), ...custom, pc.model || ""].filter(Boolean))];
        const efforts = effortsFor(catalog, p, pc.model);
        const add = () => {
          const id = (adding[p] || "").trim();
          if (!id || custom.includes(id)) return;
          setProv(p, "models", [...custom, id]);
          setAdding((a) => ({ ...a, [p]: "" }));
        };
        return (
          <fieldset key={p}>
            <legend>{PROVIDER_NAMES[p]?.[zh ? 1 : 0] || p}</legend>
            <label>{zh ? "默认模型" : "Default model"}</label>
            <select value={pc.model || ""} onChange={(e) => setProv(p, "model", e.target.value)}>
              <option value="">{zh ? "（渠道默认）" : "(channel default)"}</option>
              {ids.map((id) => <option key={id} value={id}>{suggested.find((m) => m.id === id)?.label || id}</option>)}
            </select>
            {PROVIDER_EFFORTS[p] && (
              <>
                <label>{zh ? "默认推理强度" : "Default reasoning effort"}</label>
                <select value={pc.effort || ""} onChange={(e) => setProv(p, "effort", e.target.value)}>
                  {[...new Set(["", ...efforts, pc.effort || ""])].map((v) => <option key={v} value={v}>{v || (zh ? "（CLI 默认）" : "(CLI default)")}</option>)}
                </select>
              </>
            )}
            <label>{zh ? "自定义模型" : "Custom models"}</label>
            <div className="settings-model-chips">
              {custom.map((id) => (
                <span key={id} className="settings-model-chip">{id}<button type="button" title={zh ? "移除" : "Remove"} onClick={() => setProv(p, "models", custom.filter((x) => x !== id))}>×</button></span>
              ))}
              <input value={adding[p] || ""} placeholder={zh ? "输入模型 ID，回车添加" : "Model id, Enter to add"} onChange={(e) => setAdding((a) => ({ ...a, [p]: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
            </div>
          </fieldset>
        );
      })}
      <p className="usage-note">{zh ? "模型是否可用取决于账号和接入端点，保存后可用上方的连通性测试核实。" : "Availability depends on your account and endpoint; verify with the connection test above after saving."}</p>
    </section>
  );
}
