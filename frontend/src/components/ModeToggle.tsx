import { useStore } from "../store";

/** Gloss (classic reader) ⇄ GlossX (agent workbench) segmented switch. */
export function ModeToggle() {
  const mode = useStore((s) => s.mode);
  const setMode = useStore((s) => s.setMode);
  const zh = useStore((s) => s.uiLang) === "zh";
  const title = zh
    ? "在经典阅读器（Gloss）与智能体工作台（GlossX）之间切换 (Ctrl+Shift+M)"
    : "Switch between the classic reader (Gloss) and the agent workbench (GlossX) (Ctrl+Shift+M)";
  return (
    <div className={`mode-toggle ${mode}`} role="radiogroup" title={title} aria-label={title}>
      <span className="mode-toggle-thumb" aria-hidden="true" />
      <button role="radio" aria-checked={mode === "gloss"} className={mode === "gloss" ? "on" : ""} onClick={() => setMode("gloss")}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3.5h4.5a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 0-1.5-1.5H2zM14 3.5H9.5A1.5 1.5 0 0 0 8 5v8a1.5 1.5 0 0 1 1.5-1.5H14z" /></svg>
        <span>Gloss</span>
      </button>
      <button role="radio" aria-checked={mode === "glossx"} className={mode === "glossx" ? "on" : ""} onClick={() => setMode("glossx")}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" /><path d="M5.5 2.75v10.5M10.5 2.75v10.5" /></svg>
        <span>GlossX</span>
      </button>
    </div>
  );
}
