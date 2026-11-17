import { useStore, type BottomTab } from "../../store";
import { agentText } from "../AgentRunViews";
import { ImportQueue } from "../ImportQueue";
import { cancelAiTaskByKey, useAllAiTasks } from "../../api/aiTasks";
import { visibleImportJobs } from "../../importQueue.mjs";
import { Icon } from "./icons";
import { TerminalTab } from "./TerminalTab";
import { SessionTrace } from "./SessionTrace";

const live = (status?: string) => status === "running" || status === "awaiting_approval";

function RunsTab() {
  const runs = useStore((s) => s.recentRuns);
  const projects = useStore((s) => s.projects);
  const openRunTab = useStore((s) => s.openRunTab);
  const zh = useStore((s) => s.uiLang) === "zh";
  const T = agentText(zh);
  if (!runs.length) return <div className="muted">{zh ? "暂无运行记录。" : "No runs yet."}</div>;
  return (
    <table className="wb-table">
      <tbody>
        {runs.map((r) => (
          <tr key={r.id} onClick={() => openRunTab(r.id)}>
            <td><span className={`agent-dot ${r.status}`} /></td>
            <td className="wb-table-main" title={r.goal}>{r.goal}</td>
            <td className="muted">{T.status_[r.status] || r.status}</td>
            <td className="muted">{projects.find((p) => p.id === r.project_id)?.name || ""}</td>
            <td className="muted">{r.created_at ? new Date(r.created_at * 1000).toLocaleString() : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TasksTab() {
  const jobs = visibleImportJobs(useStore((s) => s.importJobs));
  const runs = useStore((s) => s.recentRuns).filter((r) => live(r.status));
  const papers = useStore((s) => s.papers);
  const cancelAgentRun = useStore((s) => s.cancelAgentRun);
  const openRunTab = useStore((s) => s.openRunTab);
  const notify = useStore((s) => s.notify);
  const tasks = useAllAiTasks().slice(0, 30);
  const zh = useStore((s) => s.uiLang) === "zh";
  const T = zh
    ? { imports: "导入任务", ai: "AI 后台任务", agent: "运行中的智能体", none: "当前没有任务。", cancel: "取消" }
    : { imports: "Imports", ai: "AI background tasks", agent: "Running agent runs", none: "No tasks right now.", cancel: "Cancel" };
  if (!jobs.length && !runs.length && !tasks.length) return <div className="muted">{T.none}</div>;
  return (
    <div className="wb-tasks">
      {runs.length > 0 && (
        <section>
          <h5>{T.agent}</h5>
          {runs.map((r) => (
            <div key={r.id} className="wb-task">
              <span className={`agent-dot ${r.status}`} />
              <span className="wb-task-name" onClick={() => openRunTab(r.id)} title={r.goal}>{r.goal}</span>
              <button className="small" onClick={() => void cancelAgentRun(r.id).catch((e) => notify(String(e?.message || e)))}>{T.cancel}</button>
            </div>
          ))}
        </section>
      )}
      {jobs.length > 0 && (
        <section>
          <h5>{T.imports}</h5>
          <ImportQueue />
        </section>
      )}
      {tasks.length > 0 && (
        <section>
          <h5>{T.ai}</h5>
          {tasks.map(({ key, task, running }) => {
            const paper = papers.find((p) => p.id === task.paper_id);
            const pct = Math.max(0, Math.min(100, Math.round(task.progress || 0)));
            return (
              <div key={key} className={`wb-task status-${task.status}`}>
                <span className={`agent-dot ${running ? "running" : task.status}`} />
                <span className="wb-task-name" title={`${task.feature_id} · ${paper?.title || ""}`}>
                  <code>{task.feature_id}</code> {paper?.title || ""}
                  <small className="muted"> {task.stage || task.status}{task.error ? ` · ${task.error}` : ""}</small>
                </span>
                <span className="wb-task-bar"><i style={{ width: `${pct}%` }} /></span>
                <span className="muted wb-task-pct">{pct}%</span>
                {running && <button className="small" onClick={() => void cancelAiTaskByKey(key)}>{T.cancel}</button>}
              </div>
            );
          })}
        </section>
      )}
    </div>
  );
}

const TABS: { id: BottomTab; en: string; zh: string; C: () => JSX.Element }[] = [
  { id: "trace", en: "Activity", zh: "操作记录", C: SessionTrace },
  { id: "runs", en: "Runs", zh: "运行记录", C: RunsTab },
  { id: "tasks", en: "Tasks", zh: "任务", C: TasksTab },
];

export function BottomPanel() {
  const tab = useStore((s) => s.layout.bottomTab);
  const setLayout = useStore((s) => s.setLayout);
  const zh = useStore((s) => s.uiLang) === "zh";
  const active = TABS.find((t) => t.id === tab) || TABS[0];
  const C = active.C;
  return (
    <div className="wb-bottom">
      <div className="wb-bottom-head">
        <button className={`wb-bottom-tab${tab === "terminal" ? " on" : ""}`} onClick={() => setLayout({ bottomTab: "terminal" })}>{zh ? "终端" : "Terminal"}</button>
        {TABS.map((t) => (
          <button key={t.id} className={`wb-bottom-tab${t.id === tab ? " on" : ""}`} onClick={() => setLayout({ bottomTab: t.id })}>
            {zh ? t.zh : t.en}
          </button>
        ))}
        <span className="spacer" />
        <button className="wb-icon-btn" title={zh ? "收起面板 (Ctrl+J)" : "Hide panel (Ctrl+J)"} onClick={() => setLayout({ bottomOpen: false })}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="wb-bottom-body"><TerminalTab visible={tab === "terminal"} />{tab !== "terminal" && <C />}</div>
    </div>
  );
}
