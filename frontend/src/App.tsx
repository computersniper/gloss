import { useEffect, useRef, useState } from "react";
import { useStore } from "./store";
import { Library } from "./components/Library";
import { Outline } from "./components/Outline";
import { SidePanel } from "./components/SidePanel";
import { Logo } from "./components/Logo";
import { ProviderSwitcher } from "./components/ProviderSwitcher";
import { ExitConfirmDialog } from "./components/ExitConfirmDialog";
import { DoctorDialog } from "./components/DoctorDialog";
import { PdfViewer } from "./pdf/PdfViewer";
import { startImportJobPolling } from "./importQueue.mjs";

export default function App() {
  const view = useStore((s) => s.view);
  const current = useStore((s) => s.current);
  const closePaper = useStore((s) => s.closePaper);
  const loadPapers = useStore((s) => s.loadPapers);
  const loadSettings = useStore((s) => s.loadSettings);
  const loadPlugins = useStore((s) => s.loadPlugins);
  const checkProvider = useStore((s) => s.checkProvider);
  const refreshProviderStatuses = useStore((s) => s.refreshProviderStatuses);
  const loadImportJobs = useStore((s) => s.loadImportJobs);
  const toast = useStore((s) => s.toast);
  const notify = useStore((s) => s.notify);
  const uiLang = useStore((s) => s.uiLang);

  const clampWidth = (w: number) => Math.min(900, Math.max(320, w));
  const [sideWidth, setSideWidth] = useState(() =>
    clampWidth(Number(localStorage.getItem("gloss.sideWidth")) || 440)
  );
  const dragging = useRef(false);

  const clampOutline = (w: number) => Math.min(420, Math.max(120, w));
  const [outlineWidth, setOutlineWidth] = useState(() =>
    clampOutline(Number(localStorage.getItem("gloss.outlineWidth")) || 190)
  );
  const [outlineCollapsed, setOutlineCollapsed] = useState(
    () => localStorage.getItem("gloss.outlineCollapsed") === "1"
  );
  const [doctorOpen, setDoctorOpen] = useState(false);

  useEffect(() => {
    // apply the saved theme + PDF night mode on load (controls live in Settings)
    document.documentElement.dataset.theme = localStorage.getItem("gloss.theme") || "midnight";
    document.documentElement.dataset.pdf = localStorage.getItem("gloss.pdfMode") || "light";
    loadPapers();
    loadPlugins();
    loadSettings().then(() => {
      const activeProvider = useStore.getState().provider;
      void checkProvider(activeProvider);
    });
  }, []);

  useEffect(() => startImportJobPolling(loadImportJobs, 1000), [loadImportJobs]);

  useEffect(() => {
    const timer = window.setInterval(() => void refreshProviderStatuses(), 5000);
    return () => window.clearInterval(timer);
  }, [refreshProviderStatuses]);

  useEffect(() => {
    localStorage.setItem("gloss.sideWidth", String(sideWidth));
  }, [sideWidth]);
  useEffect(() => {
    localStorage.setItem("gloss.outlineWidth", String(outlineWidth));
  }, [outlineWidth]);
  useEffect(() => {
    localStorage.setItem("gloss.outlineCollapsed", outlineCollapsed ? "1" : "0");
  }, [outlineCollapsed]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => notify(null), 2600);
    return () => clearTimeout(t);
  }, [toast]);

  const startDrag = () => {
    dragging.current = true;
    document.body.style.userSelect = "none";
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      setSideWidth(clampWidth(window.innerWidth - e.clientX));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const startOutlineDrag = () => {
    document.body.style.userSelect = "none";
    const onMove = (e: MouseEvent) => setOutlineWidth(clampOutline(e.clientX));
    const onUp = () => {
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand" onClick={closePaper} style={{ cursor: "pointer" }}>
          <Logo size={20} />
          <span className="brand-name">Gloss</span>
          <span className="brand-zh">旁注</span>
        </div>
        {current && (
          <div className="crumbs">
            <button className="link" onClick={closePaper}>{uiLang === "zh" ? "← 论文库" : "← Library"}</button>
          </div>
        )}
        <div className="spacer" />
        <button
          className="doctor-launch"
          title={uiLang === "zh" ? "环境与自检" : "Environment and self-check"}
          onClick={() => setDoctorOpen(true)}
        >
          <span>＋</span>{uiLang === "zh" ? "环境自检" : "Doctor"}
        </button>
        <ProviderSwitcher />
      </header>

      {view === "library" ? (
        <Library />
      ) : (
        <div className="reader">
          {outlineCollapsed ? (
            <button className="outline-expand" title={uiLang === "zh" ? "显示大纲" : "Show outline"} onClick={() => setOutlineCollapsed(false)}>
              »
            </button>
          ) : (
            <>
              <div className="outline-host" style={{ width: outlineWidth }}>
                <Outline onCollapse={() => setOutlineCollapsed(true)} />
              </div>
              <div className="divider" onMouseDown={startOutlineDrag} />
            </>
          )}
          <PdfViewer />
          <div className="divider" onMouseDown={startDrag} />
          <div className="side-host" style={{ width: sideWidth, flex: "0 0 auto", display: "flex" }}>
            <SidePanel />
          </div>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
      <DoctorDialog open={doctorOpen} onClose={() => setDoctorOpen(false)} />
      <ExitConfirmDialog />
    </div>
  );
}
