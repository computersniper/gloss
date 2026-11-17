// The Windows taskbar jump list (desktop/main.js): the app tells the shell
// which papers and projects were opened recently, and runs the entries the
// user picks there.
import { desktop } from "./desktop";
import { useStore } from "./store";
import { useSessions } from "./sessionStore";

function run(action: string, id: string) {
  const st = useStore.getState();
  if (st.mode !== "glossx") st.setMode("glossx");
  switch (action) {
    case "new-thread": useSessions.getState().newThread(); break;
    case "import": st.openPalette("import"); break;
    case "library": st.setLayout({ activity: "library", sideOpen: true }); break;
    case "scholar": st.setLayout({ activity: "search", sideOpen: true }); break;
    case "open-paper": if (id) void st.openPaper(id).catch(() => st.notify(st.uiLang === "zh" ? "找不到这篇论文" : "Paper not found")); break;
    case "open-project": if (id) st.setAgentProjectId(id); break;
  }
}

export function initDesktopActions() {
  const api = desktop;
  if (!api) return;
  window.addEventListener("glossx:action", (event) => {
    const { action, id } = (event as CustomEvent<{ action: string; id: string }>).detail || {};
    if (action) run(action, id || "");
  });
  if (!api.recent) return;
  let paper = "", project = "";
  const report = () => {
    const st = useStore.getState();
    const current = st.current;
    if (current && current.id !== paper) {
      paper = current.id;
      void api.recent!({ kind: "paper", id: current.id, title: current.title || "Untitled" });
    }
    const pid = st.agentProjectId || "";
    const name = st.projects.find((p) => p.id === pid)?.name;
    if (pid && name && pid !== project) {
      project = pid;
      void api.recent!({ kind: "project", id: pid, title: name });
    }
  };
  useStore.subscribe(report);
  report();
}
