import { useRef, useState } from "react";
import type { PluginManifest } from "../api/client";
import { api } from "../api/client";
import { clearAiTasksForFeature } from "../api/aiTasks";
import { useStore } from "../store";

export function ExtensionsPanel() {
  const snapshot = useStore((s) => s.pluginSnapshot);
  const loadPlugins = useStore((s) => s.loadPlugins);
  const setTab = useStore((s) => s.setTab);
  const openFeatureSettings = useStore((s) => s.openFeatureSettings);
  const notify = useStore((s) => s.notify);
  const uiLang = useStore((s) => s.uiLang);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackTitle, setFeedbackTitle] = useState("");
  const [feedbackDetail, setFeedbackDetail] = useState("");
  const [template, setTemplate] = useState<Record<string, unknown> | null>(null);
  const [templateCopied, setTemplateCopied] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const T = uiLang === "zh"
    ? {
        title: "插件市场", installed: "已安装", builtIn: "内置模块", market: "推荐插件", comingSoon: "即将推出",
        install: "安装", uninstall: "卸载", open: "打开", settings: "设置", import: "导入自定义插件",
        developer: "插件 API", feedback: "提交需求", feedbackTitle: "想添加什么插件或功能？", feedbackDetail: "请描述使用场景、预期结果或参考链接", feedbackSend: "发送需求邮件", feedbackHint: "将通过本机默认邮件客户端发往 2651159710@qq.com。", feedbackNeedText: "请填写标题和说明。", apiVersion: "插件 API 版本", copyTemplate: "复制示例 manifest", copied: "已复制", schema: "打开 JSON Schema",
        safe: "插件采用声明式清单；声明权限后可读取论文、调用 AI 或使用主机控制的学术检索，但不会执行任意脚本。",
        imported: "插件已安装", uninstalled: "插件已卸载", uninstallConfirm: "卸载后将移除插件入口，并清除该插件生成的缓存内容。确定卸载吗？", failed: "插件操作失败",
      }
    : {
        title: "Extension Marketplace", installed: "Installed", builtIn: "Built-in modules", comingSoon: "Coming soon",
        market: "Featured extensions", install: "Install", uninstall: "Uninstall", open: "Open", settings: "Settings", developer: "Plugin API",
        feedback: "Request an extension", feedbackTitle: "What should GlossX add?", feedbackDetail: "Describe the use case, expected output, or a reference link", feedbackSend: "Send request by email", feedbackHint: "Opens your default mail app addressed to 2651159710@qq.com.", feedbackNeedText: "Please add a title and description.",
        apiVersion: "Plugin API version", copyTemplate: "Copy sample manifest", copied: "Copied", schema: "Open JSON Schema",
        import: "Import custom plugin", safe: "Plugins use declarative manifests. Declared permissions may read the paper, call AI, or use host-controlled scholarly search; arbitrary scripts are not executed.",
        imported: "Plugin installed", uninstalled: "Plugin uninstalled", uninstallConfirm: "Uninstalling removes the plugin entry and its generated cache. Continue?", failed: "Plugin operation failed",
      };

  async function install(plugin: PluginManifest) {
    setBusy(plugin.id);
    setError("");
    try {
      await api.installMarketplacePlugin(plugin.id);
      await loadPlugins();
      notify(T.imported);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setBusy(null);
    }
  }

  async function uninstall(plugin: PluginManifest) {
    if (!window.confirm(T.uninstallConfirm)) return;
    setBusy(plugin.id);
    setError("");
    try {
      await api.uninstallPlugin(plugin.id);
      clearAiTasksForFeature(`plugin:${plugin.id}`);
      setTab("extensions");
      await loadPlugins();
      notify(T.uninstalled);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setBusy(null);
    }
  }

  async function importManifest(file: File | undefined) {
    if (!file) return;
    setBusy("import");
    setError("");
    try {
      const manifest = JSON.parse(await file.text());
      await api.installPluginManifest(manifest);
      await loadPlugins();
      notify(T.imported);
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function toggleDeveloper() {
    const next = !developerOpen;
    setDeveloperOpen(next);
    if (!next || template) return;
    try {
      setTemplate(await api.getPluginManifestTemplate());
    } catch (err: any) {
      setError(String(err?.message || err));
    }
  }

  async function copyTemplate() {
    if (!template) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(template, null, 2));
      setTemplateCopied(true);
      window.setTimeout(() => setTemplateCopied(false), 1400);
    } catch {
      setError(T.failed);
    }
  }

  function submitFeedback(event: React.FormEvent) {
    event.preventDefault();
    const title = feedbackTitle.trim();
    const detail = feedbackDetail.trim();
    if (!title || !detail) {
      setError(T.feedbackNeedText);
      return;
    }
    const subject = `[GlossX request] ${title}`;
    const body = `${detail}\n\n---\nSent from GlossX Extension Marketplace`;
    window.location.href = `mailto:2651159710@qq.com?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    setFeedbackOpen(false);
    setFeedbackTitle("");
    setFeedbackDetail("");
  }

  const card = (plugin: PluginManifest, source: "market" | "installed") => (
    <div className="extension-card" key={`${source}:${plugin.id}`}>
      <div className="extension-icon">{plugin.icon || "🧩"}</div>
      <div className="extension-main">
        <div className="extension-title">
          <strong>{uiLang === "zh" && plugin.name_zh ? plugin.name_zh : plugin.name}</strong>
          <span>v{plugin.version}</span>
        </div>
        <div className="extension-author">{plugin.author}</div>
        <div className="extension-desc">
          {uiLang === "zh" && plugin.description_zh ? plugin.description_zh : plugin.description}
        </div>
        <div className="extension-permissions">
          {plugin.permissions.map((permission) => <code key={permission}>{permission}</code>)}
        </div>
      </div>
      <div className="extension-actions">
        {source === "installed" || plugin.installed ? (
          <>
            <button onClick={() => setTab(`plugin:${plugin.id}`)}>{T.open}</button>
            <button onClick={() => openFeatureSettings(`plugin:${plugin.id}`)}>{T.settings}</button>
            <button className="danger-ghost" disabled={busy === plugin.id} onClick={() => uninstall(plugin)}>
              {T.uninstall}
            </button>
          </>
        ) : (
          <button disabled={busy === plugin.id} onClick={() => install(plugin)}>{T.install}</button>
        )}
      </div>
    </div>
  );

  return (
    <div className="panel-body extensions-panel">
      <div className="extensions-heading">
        <div><span className="extensions-mark">▦</span><strong>{T.title}</strong></div>
        <div className="extensions-heading-actions">
        <button onClick={() => setFeedbackOpen((open) => !open)}>{T.feedback}</button>
        <button onClick={() => void toggleDeveloper()}>{T.developer}</button>
        <button onClick={() => fileRef.current?.click()} disabled={busy === "import"}>{T.import}</button>
        </div>
        <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={(e) => importManifest(e.target.files?.[0])} />
      </div>
      <p className="extension-safety">🔒 {T.safe}</p>
      {error && <div className="error">{T.failed}: {error}</div>}
      {feedbackOpen && (
        <form className="extension-feedback" onSubmit={submitFeedback}>
          <label>
            <span>{T.feedbackTitle}</span>
            <input value={feedbackTitle} onChange={(event) => setFeedbackTitle(event.target.value)} maxLength={120} autoFocus />
          </label>
          <label>
            <span>{T.feedbackDetail}</span>
            <textarea value={feedbackDetail} onChange={(event) => setFeedbackDetail(event.target.value)} maxLength={2000} rows={4} />
          </label>
          <div className="extension-feedback-actions">
            <small>{T.feedbackHint}</small>
            <button type="submit">✉ {T.feedbackSend}</button>
          </div>
        </form>
      )}
      {developerOpen && (
        <div className="extension-developer">
          <div className="extension-dev-head">
            <strong>{T.apiVersion} v{snapshot.api_version}</strong>
            <div>
              <button onClick={() => void copyTemplate()} disabled={!template}>
                {templateCopied ? T.copied : T.copyTemplate}
              </button>
              <a href="/api/plugins/schema" target="_blank" rel="noreferrer">{T.schema} ↗</a>
            </div>
          </div>
          <div className="extension-points">
            {snapshot.contribution_points.map((point) => (
              <span key={point.id} className={point.status} title={point.description}>
                <code>{point.id}</code> · {point.status}
              </span>
            ))}
          </div>
          {template && <pre>{JSON.stringify(template, null, 2)}</pre>}
        </div>
      )}

      <h5>{T.builtIn}</h5>
      <div className="extension-grid">
        {snapshot.core.map((item) => (
          <div className="extension-card builtin" key={item.id}>
            <div className="extension-icon">{item.icon}</div>
            <div className="extension-main">
              <div className="extension-title"><strong>{uiLang === "zh" ? item.name_zh : item.name}</strong><span>Core</span></div>
              <div className="extension-desc">{uiLang === "zh" ? item.description_zh : item.description}</div>
            </div>
          </div>
        ))}
      </div>

      {snapshot.installed.length > 0 && <><h5>{T.installed}</h5><div className="extension-grid">{snapshot.installed.map((plugin) => card(plugin, "installed"))}</div></>}
      <h5>{T.market}</h5>
      <div className="extension-grid">{snapshot.marketplace.filter((plugin) => !plugin.installed).map((plugin) => card(plugin, "market"))}</div>
      <h5>{T.comingSoon}</h5>
      <div className="extension-grid coming-grid">
        {snapshot.coming_soon.map((plugin) => (
          <div className="extension-card coming" key={plugin.id}>
            <div className="extension-icon">{plugin.icon}</div>
            <div className="extension-main">
              <div className="extension-title">
                <strong>{uiLang === "zh" ? plugin.name_zh : plugin.name}</strong>
                <span className="extension-soon">{T.comingSoon}</span>
              </div>
              <div className="extension-desc">{uiLang === "zh" ? plugin.description_zh : plugin.description}</div>
              <div className="extension-permissions"><code>{plugin.planned_contribution}</code></div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
