import { useEffect } from "react";
import type { Summary } from "../api/client";
import { useAiTask } from "../api/aiTasks";
import { useStore } from "../store";
import { AgentTaskCard } from "./AgentTaskCard";
import { Markdown } from "./Markdown";

export function SummaryPanel() {
  const current = useStore((s) => s.current);
  const outputLanguage = useStore((s) => s.outputLanguage);
  const uiLang = useStore((s) => s.uiLang);
  const T =
    uiLang === "zh"
      ? { run: "生成摘要", regenerate: "↻ 重新生成" }
      : { run: "Generate summary", regenerate: "↻ Regenerate" };

  const key = current
    ? `summary:${current.id}:${current.reading_lens || "auto"}:${outputLanguage}`
    : null;
  const { task, running, error, start, restore, cancel } = useAiTask(key);
  useEffect(() => {
    if (!current) return;
    void restore({
      feature_id: "core.summary",
      paper_id: current.id,
      language: outputLanguage,
    });
  }, [key]);
  const raw = task?.result as Summary | { summary?: Summary } | null | undefined;
  const sum = raw && "summary" in raw ? raw.summary : raw as Summary | undefined;
  const run = (refresh = false) => {
    if (!current) return;
    void start({
      feature_id: "core.summary",
      paper_id: current.id,
      refresh,
      language: outputLanguage,
    });
  };

  const zh = uiLang === "zh";
  const humanities = current?.reading_lens === "humanities";
  const socialSciences = current?.reading_lens === "social_sciences";
  const L = zh
    ? {
        tldr: "速览",
        problem: humanities ? "核心论题" : "问题",
        method: humanities ? "方法与材料" : socialSciences ? "研究方法" : "方法",
        results: humanities ? "论证与阐释" : socialSciences ? "研究发现" : "结果",
        contributions: "创新点", key: "5 分钟速读", limits: "局限",
      }
    : {
        tldr: "TL;DR",
        problem: humanities ? "Central question" : "Problem",
        method: humanities ? "Approach & sources" : socialSciences ? "Methodology" : "Method",
        results: humanities ? "Argument & interpretation" : socialSciences ? "Findings" : "Results",
        contributions: "Contributions", key: "5-minute key points", limits: "Limitations",
      };

  return (
    <div className="panel-body">
      <div className="panel-actions">
        <button onClick={() => run(!!sum)} disabled={running || !current}>
          {sum ? T.regenerate : T.run}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {task && (running || task.status === "failed" || task.status === "cancelled") && (
        <AgentTaskCard
          task={task}
          uiLang={uiLang}
          agent={{ name: "Summary Scout", name_zh: "摘要侦察员", icon: "🛰️" }}
          onCancel={running ? () => void cancel() : undefined}
        />
      )}
      {sum && (
        <div className="summary">
          <h4>{L.tldr}</h4>
          <Markdown text={sum.tldr} />
          <div className="grid2">
            <div><h5>{L.problem}</h5><Markdown text={sum.problem} /></div>
            <div><h5>{L.method}</h5><Markdown text={sum.method} /></div>
          </div>
          <h5>{L.results}</h5>
          <Markdown text={sum.results} />
          {sum.contributions?.length > 0 && (
            <>
              <h5>{L.contributions}</h5>
              <ul>{sum.contributions.map((c, i) => <li key={i}><Markdown text={c} /></li>)}</ul>
            </>
          )}
          {sum.key_points?.length > 0 && (
            <>
              <h5>{L.key}</h5>
              <ul>{sum.key_points.map((c, i) => <li key={i}><Markdown text={c} /></li>)}</ul>
            </>
          )}
          {sum.limitations?.length > 0 && (
            <>
              <h5>{L.limits}</h5>
              <ul>{sum.limitations.map((c, i) => <li key={i}><Markdown text={c} /></li>)}</ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
