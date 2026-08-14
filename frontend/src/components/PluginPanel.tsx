import { useEffect, useState } from "react";
import type { PluginManifest, PluginRunResponse } from "../api/client";
import { api } from "../api/client";
import { useAiTask } from "../api/aiTasks";
import { useStore } from "../store";
import { AgentTaskCard } from "./AgentTaskCard";
import { Markdown } from "./Markdown";
import { preflightPluginRequirements } from "../formulaDetection.mjs";

export function PluginPanel({ plugin }: { plugin: PluginManifest }) {
  const current = useStore((s) => s.current);
  const pages = useStore((s) => s.pages);
  const uiLang = useStore((s) => s.uiLang);
  const outputLanguage = useStore((s) => s.outputLanguage);
  const loadPlugins = useStore((s) => s.loadPlugins);
  const setTab = useStore((s) => s.setTab);
  const notify = useStore((s) => s.notify);
  const [uninstalling, setUninstalling] = useState(false);
  const key = current
    ? `plugin:${plugin.id}:${plugin.version}:${current.id}:${current.reading_lens || "auto"}:${outputLanguage}`
    : null;
  const localPreflight = pages ? preflightPluginRequirements(plugin.requirements, pages.pages) : null;
  const { task, running, error, start, restore, cancel, clear } = useAiTask(key);
  useEffect(() => {
    if (!current) return;
    void restore({
      feature_id: `plugin:${plugin.id}`,
      paper_id: current.id,
      language: outputLanguage,
    });
  }, [key]);
  const data = task?.result as PluginRunResponse | undefined;
  const unavailable = localPreflight?.status === "unavailable" ? localPreflight : data?.status === "unavailable" ? data : null;
  const markdown = unavailable ? "" : data?.markdown || "";
  const research = unavailable ? undefined : data?.research;
  const runPlugin = () => {
    if (unavailable) return;
    if (!current) return;
    void start({
      feature_id: `plugin:${plugin.id}`,
      paper_id: current.id,
      refresh: !!markdown,
      language: outputLanguage,
    });
  };
  const T = uiLang === "zh"
    ? {
        run: "运行插件", rerun: "↻ 重新运行", running: "插件运行中…", copy: "⧉ 复制",
        uninstall: "卸载", uninstalling: "卸载中…", uninstalled: "插件已卸载",
        confirm: "卸载后将移除插件入口，并清除该插件生成的缓存内容。确定卸载吗？",
        unavailable: {
          no_body_text: "未检测到可用论文正文",
          no_formulas: "未检测到公式",
          no_method_content: "未检测到可复现的方法内容",
          no_claims_or_evidence: "未检测到可用的结论或证据",
          no_terms: "未检测到可提取的正文术语",
          no_figures_or_tables: "未检测到图表",
        } as Record<string, string>,
        unavailableHint: "此插件不会调用 AI，直到论文中检测到所需内容。图片或特殊字体中的内容可能无法被文本提取识别。",
      }
    : {
        run: "Run plugin", rerun: "↻ Run again", running: "Running plugin…", copy: "⧉ Copy",
        uninstall: "Uninstall", uninstalling: "Uninstalling…", uninstalled: "Plugin uninstalled",
        confirm: "Uninstalling removes the plugin entry and its generated cache. Continue?",
        unavailable: {
          no_body_text: "No usable paper text detected",
          no_formulas: "No formulas detected",
          no_method_content: "No reproducible method content detected",
          no_claims_or_evidence: "No usable claims or evidence detected",
          no_terms: "No body terms detected",
          no_figures_or_tables: "No figures or tables detected",
        } as Record<string, string>,
        unavailableHint: "This plugin will not call AI until its required paper content is detected. Content embedded as images or special fonts may not be recognized by text extraction.",
      };

  async function uninstall() {
    if (!window.confirm(T.confirm)) return;
    setUninstalling(true);
    try {
      await api.uninstallPlugin(plugin.id);
      clear();
      setTab("extensions");
      await loadPlugins();
      notify(T.uninstalled);
    } catch (error: any) {
      notify(String(error?.message || error));
    } finally {
      setUninstalling(false);
    }
  }

  return (
    <div className="panel-body">
      <div className="plugin-panel-head">
        <span className="plugin-icon">{plugin.icon}</span>
        <div>
          <strong>{uiLang === "zh" && plugin.name_zh ? plugin.name_zh : plugin.name}</strong>
          <div className="muted">{plugin.author} · v{plugin.version}</div>
        </div>
        <button className="danger-ghost plugin-uninstall" disabled={uninstalling || running} onClick={() => void uninstall()}>
          {uninstalling ? T.uninstalling : T.uninstall}
        </button>
      </div>
      <div className="panel-actions">
        <button onClick={runPlugin} disabled={running || !current || !!unavailable}>
          {markdown ? T.rerun : T.run}
        </button>
        <button
          onClick={() => navigator.clipboard?.writeText(markdown)}
          disabled={!markdown || running}
        >
          {T.copy}
        </button>
      </div>
      {unavailable && <div className="plugin-preflight-status empty" role="status">
        <strong>{T.unavailable[unavailable.reason?.code || ""] || T.unavailable.no_body_text}</strong>
        <span>{T.unavailableHint}</span>
      </div>}
      {error && <div className="error">{error}</div>}
      {task && (running || task.status === "failed" || task.status === "cancelled") && (
        <AgentTaskCard
          task={task}
          uiLang={uiLang}
          agent={plugin.agent}
          onCancel={running ? () => void cancel() : undefined}
        />
      )}
      {research && <div className="plugin-research-status" role="status">
        <strong>
          {uiLang === "zh"
            ? `🌐 已联网研究 · ${research.queries.length} 个检索式 · ${research.source_count} 个来源`
            : `🌐 Live research · ${research.queries.length} queries · ${research.source_count} sources`}
        </strong>
        <span>{research.providers.join(" · ")}</span>
        {research.partial && <span className="warning">
          {uiLang === "zh"
            ? `部分检索服务失败：${research.failed_providers?.join("、") || "未知服务"}`
            : `Partial results: ${research.failed_providers?.join(", ") || "a search service failed"}`}
        </span>}
      </div>}
      {markdown && <Markdown text={markdown} />}
    </div>
  );
}
