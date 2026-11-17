import { useEffect, useRef, useState } from "react";
import { agentApi, type AgentLoop, type AgentWorkflow } from "../api/agent";
import { useStore } from "../store";
import { RunResult, RunStatus, RunTrace, agentText } from "./AgentRunViews";

export { BudgetMeter } from "./AgentRunViews";

type Scope = "paper" | "library" | "selected";

export function AgentPanel({ workspace = false }: { workspace?: boolean } = {}) {
  const current = useStore((s) => s.current);
  const papers = useStore((s) => s.papers);
  const uiLang = useStore((s) => s.uiLang);
  const agentRunId = useStore((s) => s.agentRunId);
  const run = useStore((s) => (s.agentRunId ? s.runs[s.agentRunId] : undefined));
  const events = useStore((s) => (s.agentRunId ? s.runEvents[s.agentRunId] : undefined)) || [];
  const recent = useStore((s) => s.recentRuns);
  const projects = useStore((s) => s.projects);
  const projectId = useStore((s) => s.agentProjectId);
  const setProjectId = useStore((s) => s.setAgentProjectId);
  const createProject = useStore((s) => s.createProject);
  const refreshProjects = useStore((s) => s.refreshProjects);
  const refreshRuns = useStore((s) => s.refreshRuns);
  const loadRun = useStore((s) => s.loadRun);
  const startAgentRun = useStore((s) => s.startAgentRun);
  const cancelAgentRun = useStore((s) => s.cancelAgentRun);
  const openRunTab = useStore((s) => s.openRunTab);
  const setAgentRunId = useStore((s) => s.setAgentRunId);
  const prefill = useStore((s) => s.agentPrefill);
  const zh = uiLang === "zh";
  const R = agentText(zh);
  const T = zh
    ? {
        goal: "描述研究目标，例如：比较这些论文的方法差异并给出依据（Ctrl+Enter 运行）", scope: "范围", paper: "当前论文",
        lib: "整个文库", selected: "选中的论文", run: "运行", cancel: "取消", recent: "最近运行", workflow: "工作流",
        loop: "循环", project: "项目", auto: "自动", generic: "通用", noProject: "无项目", newProject: "＋ 新建项目…",
        projectName: "新项目名称", limits: "预算上限", maxSteps: "最大步骤", maxLlm: "最大 LLM 调用", maxTokens: "最大 Token",
        blank: "默认", report: "以报告打开", newRun: "新运行", papersN: (n: number) => `${n} 篇论文`,
      }
    : {
        goal: "Describe a research goal, e.g. compare the methods of these papers with evidence (Ctrl+Enter to run)",
        scope: "Scope", paper: "Active paper", lib: "Whole library", selected: "Selected papers", run: "Run", cancel: "Cancel",
        recent: "Recent runs", workflow: "Workflow", loop: "Loop", project: "Project", auto: "Auto", generic: "Generic",
        noProject: "No project", newProject: "+ New project…", projectName: "New project name", limits: "Budget limits",
        maxSteps: "Max steps", maxLlm: "Max LLM calls", maxTokens: "Max tokens", blank: "default",
        report: "Open as report", newRun: "New run", papersN: (n: number) => `${n} papers`,
      };

  const [goal, setGoal] = useState("");
  // classic agent workspace starts library-wide; elsewhere scope follows the active paper
  const [scope, setScope] = useState<Scope>(current && !workspace ? "paper" : "library");
  const scopeTouched = useRef(workspace);
  const [picked, setPicked] = useState<string[]>([]);
  const [workflow, setWorkflow] = useState("auto");
  const [loop, setLoop] = useState("auto");
  const [workflows, setWorkflows] = useState<AgentWorkflow[]>([]);
  const [loops, setLoops] = useState<AgentLoop[]>([]);
  const [limits, setLimits] = useState({ max_steps: "", max_llm_calls: "", max_tokens_est: "" });
  const [error, setError] = useState("");
  const goalRef = useRef<HTMLTextAreaElement>(null);

  const running = run?.status === "running" || run?.status === "awaiting_approval";

  useEffect(() => {
    void refreshRuns();
    void refreshProjects();
    void agentApi.workflows().then(setWorkflows).catch(() => {});
    void agentApi.loops().then(setLoops).catch(() => {});
  }, []);

  // make sure the active run snapshot is loaded (e.g. after reload or from the Runs list)
  useEffect(() => {
    if (agentRunId && !run) void loadRun(agentRunId).catch(() => {});
  }, [agentRunId]);

  // scope follows the active paper until the user picks one explicitly
  useEffect(() => {
    if (!scopeTouched.current && !running) setScope(current ? "paper" : "library");
  }, [current?.id]);

  // "Ask agent" from the library, a selection or the command palette
  useEffect(() => {
    if (!prefill) return;
    useStore.setState({ agentPrefill: null }); // consume once
    if (prefill.goal !== undefined) setGoal(prefill.goal);
    if (prefill.paperIds?.length) {
      scopeTouched.current = true;
      if (prefill.paperIds.length === 1 && prefill.paperIds[0] === current?.id) setScope("paper");
      else {
        setScope("selected");
        setPicked(prefill.paperIds);
      }
    }
    setTimeout(() => {
      const el = goalRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }, 30);
  }, [prefill?.id]);

  async function start() {
    const text = goal.trim();
    if (!text) return;
    const ids =
      scope === "paper" && current ? [current.id] : scope === "selected" ? picked : papers.map((p) => p.id);
    const budget = Object.fromEntries(
      Object.entries(limits).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)]),
    );
    setError("");
    try {
      await startAgentRun({
        goal: text,
        paper_ids: ids,
        workflow,
        loop,
        ...(projectId ? { project_id: projectId } : {}),
        ...(Object.keys(budget).length ? { budget } : {}),
      });
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }

  async function onProject(v: string) {
    if (v !== "__new") return setProjectId(v || null);
    const name = prompt(T.projectName)?.trim();
    if (!name) return;
    try {
      await createProject(name);
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }

  const canRun = !!goal.trim() && !(scope === "paper" && !current) && !(scope === "selected" && !picked.length);

  return (
    <div className="panel-body agent-panel">
      <textarea
        ref={goalRef}
        className="agent-goal"
        rows={3}
        value={goal}
        placeholder={T.goal}
        onChange={(e) => setGoal(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && canRun && !running) {
            e.preventDefault();
            void start();
          }
        }}
      />
      <div className="panel-actions agent-opts">
        <label className="agent-opt">
          <span className="muted">{T.scope}</span>
          <select
            value={scope}
            onChange={(e) => {
              scopeTouched.current = true;
              setScope(e.target.value as Scope);
            }}
          >
            <option value="paper" disabled={!current}>{T.paper}</option>
            <option value="library">{T.lib}</option>
            <option value="selected">{T.selected}</option>
          </select>
        </label>
        <label className="agent-opt">
          <span className="muted">{T.workflow}</span>
          <select value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
            <option value="auto">{T.auto}</option>
            <option value="generic">{T.generic}</option>
            {workflows.map((w) => <option key={w.id} value={w.id.startsWith("workflow.") ? w.id : `workflow.${w.id}`} title={w.card}>{w.name}</option>)}
          </select>
        </label>
        <label className="agent-opt">
          <span className="muted">{T.loop}</span>
          <select value={loop} onChange={(e) => setLoop(e.target.value)}>
            <option value="auto">{T.auto}</option>
            {(loops.length ? loops.map((l) => l.id.replace(/^loop:/, "")) : ["plan-execute-verify", "react"]).map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </label>
        <label className="agent-opt">
          <span className="muted">{T.project}</span>
          <select value={projectId || ""} onChange={(e) => void onProject(e.target.value)}>
            <option value="">{T.noProject}</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            <option value="__new">{T.newProject}</option>
          </select>
        </label>
      </div>
      <details className="agent-limits">
        <summary className="muted">{T.limits}</summary>
        <div className="agent-limit-row">
          {([["max_steps", T.maxSteps], ["max_llm_calls", T.maxLlm], ["max_tokens_est", T.maxTokens]] as const).map(([k, label]) => (
            <label key={k}>
              <span className="muted">{label}</span>
              <input
                type="number"
                min={1}
                placeholder={T.blank}
                value={limits[k]}
                onChange={(e) => setLimits((l) => ({ ...l, [k]: e.target.value }))}
              />
            </label>
          ))}
        </div>
      </details>
      {scope === "selected" && (
        <div className="agent-picker">
          {papers.map((p) => (
            <label key={p.id} className="agent-pick">
              <input
                type="checkbox"
                checked={picked.includes(p.id)}
                onChange={(e) => setPicked((x) => (e.target.checked ? [...x, p.id] : x.filter((i) => i !== p.id)))}
              />
              <span>{p.title || "Untitled"}</span>
            </label>
          ))}
        </div>
      )}
      <div className="panel-actions">
        <button className="primary" onClick={() => void start()} disabled={!canRun}>{T.run}</button>
        {running && run && (
          <button onClick={() => void cancelAgentRun(run.id).catch((e) => setError(String(e?.message || e)))}>{T.cancel}</button>
        )}
        {run && !running && (
          <button onClick={() => setAgentRunId(null)}>{T.newRun}</button>
        )}
        <span className="muted">
          {scope === "paper" && current ? (current.title || "").slice(0, 48)
            : scope === "selected" ? T.papersN(picked.length) : T.papersN(papers.length)}
        </span>
      </div>
      {error && <div className="error">{error}</div>}

      {run && (
        <div className="agent-active-run">
          <div className="agent-run-head">
            <span className={`agent-dot ${run.status}`} />
            <strong className="agent-run-goal" title={run.goal}>{run.goal}</strong>
            <span className="muted">{R.status_[run.status] || run.status}</span>
            <button className="small" onClick={() => openRunTab(run.id)}>{T.report}</button>
          </div>
          <RunStatus run={run} />
          {events.length > 0 && (
            <details className="agent-section" open={running}>
              <summary>{R.trace}</summary>
              <RunTrace events={events} />
            </details>
          )}
          <RunResult run={run} />
        </div>
      )}

      {recent.length > 0 && (
        <>
          <h4 className="agent-h">{T.recent}</h4>
          {recent.filter((r) => !projectId || r.project_id === projectId).slice(0, 10).map((r) => (
            <div
              key={r.id}
              className={`agent-recent${agentRunId === r.id ? " active" : ""}`}
              onClick={() => {
                setAgentRunId(r.id);
                void loadRun(r.id).catch((e) => setError(String(e?.message || e)));
              }}
              onDoubleClick={() => openRunTab(r.id)}
            >
              <span className={`agent-dot ${r.status}`} />
              <span className="agent-recent-goal">{r.goal}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
