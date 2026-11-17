import { useEffect, useState } from "react";
import { FolderCog } from "lucide-react";
import { agentApi } from "../../api/agent";
import { workspaceApi } from "../../api/harness";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";
import { ask } from "./dialog";

export function WorkspaceLocation({ projectId }: { projectId: string | null }) {
  const zh = useStore((s) => s.uiLang) === "zh";
  const tick = useStore((s) => s.agentTick);
  const [current, setCurrent] = useState("");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setCurrent(""); setPath(""); setError("");
    if (projectId) void agentApi.getProject(projectId).then((p) => { if (live) { const value = p.workspace?.path || ""; setCurrent(value); setPath(value); } }).catch(() => {});
    return () => { live = false; };
  }, [projectId, tick]);
  const save = async () => {
    if (!projectId || !path.trim() || path.trim() === current) return;
    const selected = await ask({ title: zh ? "更改工作区位置？" : "Change workspace location?", body: zh ? "将复制并校验全部文件（包括 Git 历史与 .glossx）后切换到新位置。目标必须是新的或空目录；原目录会保留。运行中的对话需先停止。" : "All files, including Git history and .glossx, will be copied and verified before switching. The destination must be new or empty. The original folder stays in place. Stop running threads first.", detail: `${current}\n→ ${path.trim()}`, cancel: zh ? "取消" : "Cancel", actions: [{ id: "move", label: zh ? "复制并切换" : "Copy and switch", primary: true }] });
    if (selected !== "move") return;
    setBusy(true); setError("");
    try {
      const result = await workspaceApi.location(projectId, path.trim());
      setCurrent(result.path); setPath(result.path);
      await Promise.all([useStore.getState().refreshProjects(), useSessions.getState().refresh()]);
      useStore.getState().bumpAgentTick();
      window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
      useStore.getState().notify(zh ? `已切换工作区，校验 ${result.verified_files} 个文件。原目录保留。` : `Workspace switched; ${result.verified_files} files verified. Original folder preserved.`);
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  if (!projectId || !current) return null;
  return <details className="gx-ws-location"><summary><FolderCog size={12} />{zh ? "工作区位置" : "Workspace location"}</summary><form onSubmit={(e) => { e.preventDefault(); void save(); }}><input className="gx-input" aria-label={zh ? "项目工作区目录" : "Project workspace folder"} value={path} disabled={busy} onChange={(e) => setPath(e.target.value)} /><p>{zh ? "绝对路径 · 复制、校验并切换，保留原目录" : "Absolute path · copy, verify and switch; preserve the original"}</p>{error && <p role="alert" className="gx-danger">{error}</p>}<button type="submit" className="gx-btn sm" disabled={busy || !path.trim() || path.trim() === current}>{busy ? (zh ? "正在复制…" : "Copying…") : (zh ? "更改位置…" : "Change location…")}</button></form></details>;
}
