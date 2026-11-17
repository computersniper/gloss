import { useStore } from "../../store";

/** Open the AI pane and move keyboard focus into its visible slot. */
export function focusAiPane() {
  useStore.getState().setLayout({ aiOpen: true });
  setTimeout(() => {
    const pane = document.querySelector<HTMLElement>("[data-ai-pane]");
    if (!pane) return;
    const slot = [...pane.querySelectorAll<HTMLElement>(".wb-ai-slot")].find((el) => el.style.display !== "none");
    const target = slot?.querySelector<HTMLElement>("textarea:not([disabled]), input:not([disabled]):not([type=checkbox])");
    (target || pane).focus();
  }, 30);
}

export function aiPaneHasFocus() {
  const pane = document.querySelector("[data-ai-pane]");
  return !!pane && pane.contains(document.activeElement);
}
