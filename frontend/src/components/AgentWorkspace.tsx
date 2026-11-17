import { useEffect, useState } from "react";
import { agentApi, type AgentCapability, type AgentProject } from "../api/agent";
import { useStore } from "../store";
import { AgentPanel } from "./AgentPanel";

/** Memory notes (add/delete) and runs of one project. Shared by the classic
 *  agent workspace and the GlossX Projects side bar. */
export function ProjectDetail({ projectId, onOpenRun, showTitle = true }: {
  projectId: string | null;
  onOpenRun?: (runId: string) => void;
  showTitle?: boolean;
}) {
  const uiLang = useStore((s) => s.uiLang);
  const tick = useStore((s) => s.agentTick);
  const agentRunId = useStore((s) => s.agentRunId);
  const setAgentRunId = useStore((s) => s.setAgentRunId);
  const zh = uiLang === "zh";
  const T = zh
    ? { title: "项目", none: "在右侧选择或新建一个项目以使用项目记忆。", memory: "项目记忆", add: "添加", ph: "添加一条笔记…", runs: "项目运行", del: "删除", empty: "暂无" }
    : { title: "Project", none: "Pick or create a project in the run options to use project memory.", memory: "Memory notes", add: "Add", ph: "Add a note…", runs: "Project runs", del: "Delete", empty: "None" };
  const [project, setProject] = useState<AgentProject | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  const load = () => {
    if (!projectId) return setProject(null);
    void agentApi.getProject(projectId).then(setProject).catch((e) => setError(String(e?.message || e)));
  };
  useEffect(load, [projectId, tick]);

  async function add() {
    if (!projectId || !text.trim()) return;
    try {
      await agentApi.addMemory(projectId, text.trim());
      setText("");
      load();
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }
  async function remove(id: string) {
    if (!projectId) return;
    try {
      await agentApi.deleteMemory(projectId, id);
      load();
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }

  return (
    <>
      {showTitle && <h4 className="agent-h">{T.title}{project ? `: ${project.name}` : ""}</h4>}
      {!projectId && showTitle && <div className="muted">{T.none}</div>}
      {error && <div className="error">{error}</div>}
      {project && (
        <>
          {project.description && <div className="muted">{project.description}</div>}
          <h4 className="agent-h">{T.memory}</h4>
          {(project.memory || []).length === 0 && <div className="muted">{T.empty}</div>}
          {(project.memory || []).map((m) => (
            <div key={m.id} className="agent-note">
              <span>{m.text}</span>
              <button className="small" title={T.del} onClick={() => void remove(m.id)}>×</button>
            </div>
          ))}
          <div className="agent-note-add">
            <input
              value={text}
              placeholder={T.ph}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void add()}
            />
            <button className="small" onClick={() => void add()} disabled={!text.trim()}>{T.add}</button>
          </div>
          <h4 className="agent-h">{T.runs}</h4>
          {(project.runs || []).length === 0 && <div className="muted">{T.empty}</div>}
          {(project.runs || []).map((r) => (
            <div
              key={r.id}
              className={`agent-recent${agentRunId === r.id ? " active" : ""}`}
              onClick={() => (onOpenRun ? onOpenRun(r.id) : setAgentRunId(r.id))}
            >
              <span className={`agent-dot ${r.status}`} />
              <span className="agent-recent-goal">{r.goal}</span>
            </div>
          ))}
        </>
      )}
    </>
  );
}

function ProjectSidebar() {
  const projectId = useStore((s) => s.agentProjectId);
  return (
    <aside className="agent-side">
      <ProjectDetail projectId={projectId} />
    </aside>
  );
}

function useCapabilities() {
  const [caps, setCaps] = useState<AgentCapability[]>([]);
  useEffect(() => {
    void agentApi.capabilities().then(setCaps).catch(() => {});
  }, []);
  return caps;
}

/** Capabilities grouped by kind, with trust / needs-approval badges. */
export function CapabilityGroups({ caps: given }: { caps?: AgentCapability[] } = {}) {
  const uiLang = useStore((s) => s.uiLang);
  const zh = uiLang === "zh";
  const loaded = useCapabilities();
  const caps = given || loaded;
  const groups = new Map<string, AgentCapability[]>();
  caps.forEach((c) => groups.set(c.kind, [...(groups.get(c.kind) || []), c]));
  return (
    <>
      {[...groups.entries()].map(([kind, items]) => (
        <div key={kind} className="agent-phase">
          <div className="agent-phase-name">{kind}</div>
          {items.map((c) => (
            <div key={c.name} className="agent-cap" title={c.card}>
              <code>{c.name}</code>
              {c.trust && <span className={`agent-badge trust-${c.trust}`}>{c.trust}</span>}
              {c.requires_approval && <span className="agent-badge partial">{zh ? "需审批" : "needs approval"}</span>}
              <div className="muted">{c.card}</div>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}

function Capabilities() {
  const zh = useStore((s) => s.uiLang) === "zh";
  const caps = useCapabilities();
  return (
    <details className="agent-section">
      <summary>{zh ? `能力 (${caps.length})` : `Capabilities (${caps.length})`}</summary>
      <CapabilityGroups caps={caps} />
    </details>
  );
}

export function AgentWorkspace() {
  const uiLang = useStore((s) => s.uiLang);
  return (
    <div className="agent-workspace">
      <div className="agent-main">
        <h2 className="agent-title">{uiLang === "zh" ? "GlossX 智能体工作区" : "GlossX Agent workspace"}</h2>
        <AgentPanel workspace />
      </div>
      <div className="agent-sidebar-col">
        <ProjectSidebar />
        <Capabilities />
      </div>
    </div>
  );
}
