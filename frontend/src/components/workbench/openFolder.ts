import { desktop } from "../../desktop";
import { agentApi } from "../../api/agent";
import { useStore } from "../../store";
import { useSessions } from "../../sessionStore";

let opening = false;

export async function openWorkspaceFolder() {
  if (opening) return;
  opening = true;
  const st = useStore.getState(), zh = st.uiLang === "zh";
  try {
    const path = desktop?.openFolder ? await desktop.openFolder() : window.prompt(zh
      ? "输入运行 GlossX 的电脑上已有文件夹的绝对路径：" : "Absolute path of an existing folder on the computer running GlossX:");
    if (!path?.trim()) return;
    const project = await agentApi.openFolder(path.trim());
    await st.refreshProjects();
    useSessions.getState().newChat();
    st.setAgentProjectId(project.id);
    st.setLayout({ activity: "workspace", sideOpen: true, aiOpen: false });
    st.bumpAgentTick();
    window.dispatchEvent(new CustomEvent("glossx:workspace-changed"));
  } catch (error) { st.notify(error instanceof Error ? error.message : String(error)); }
  finally { opening = false; }
}
