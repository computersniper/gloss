import { useEffect } from "react";
import { useAiTask } from "../api/aiTasks";
import { useStore } from "../store";
import { AgentTaskCard } from "./AgentTaskCard";
import { Markdown } from "./Markdown";

/** AI-generated Deep Paper Note. Personal notes live in PersonalNotesPanel. */
export function NotesPanel() {
  const current = useStore((state) => state.current);
  const outputLanguage = useStore((state) => state.outputLanguage);
  const uiLang = useStore((state) => state.uiLang);
  const T = uiLang === "zh"
    ? {
        run: "生成 Deep Paper Note",
        regenerate: "↻ 重新生成",
        copy: "⧉ 复制",
        download: "⬇ 下载 .md",
      }
    : {
        run: "Generate Deep Paper Note",
        regenerate: "↻ Regenerate",
        copy: "⧉ Copy",
        download: "⬇ Download .md",
      };
  const key = current
    ? `notes:${current.id}:${current.reading_lens || "auto"}:${outputLanguage}`
    : null;
  const { task, running, error, start, restore, cancel } = useAiTask(key);
  useEffect(() => {
    if (!current) return;
    void restore({
      feature_id: "core.notes",
      paper_id: current.id,
      language: outputLanguage,
    });
  }, [key]);
  const raw = task?.result as string | { markdown?: string } | null | undefined;
  const markdown = typeof raw === "string" ? raw : raw?.markdown || "";
  const run = (refresh = false) => {
    if (!current) return;
    void start({
      feature_id: "core.notes",
      paper_id: current.id,
      refresh,
      language: outputLanguage,
    });
  };

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
    } catch {
      /* Clipboard access may be unavailable in a restricted webview. */
    }
  }

  function download() {
    const name = `${(current?.title || "deep-paper-note").slice(0, 60)}.md`;
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }

  return (
    <div className="panel-body">
      <div className="panel-actions">
        <button onClick={() => run(!!markdown)} disabled={running || !current}>
          {markdown ? T.regenerate : T.run}
        </button>
        <button onClick={copy} disabled={running || !markdown}>{T.copy}</button>
        <button onClick={download} disabled={running || !markdown}>{T.download}</button>
      </div>
      {error && <div className="error">{error}</div>}
      {task && (running || task.status === "failed" || task.status === "cancelled") && (
        <AgentTaskCard
          task={task}
          uiLang={uiLang}
          agent={{
            name: "Deep Note Scholar",
            name_zh: "深度笔记学者",
            icon: "📚",
            messages: {
              generating: { en: "Writing the deep paper note", zh: "正在撰写深度论文笔记" },
            },
          }}
          onCancel={running ? () => void cancel() : undefined}
        />
      )}
      {markdown && <Markdown text={markdown} />}
    </div>
  );
}
