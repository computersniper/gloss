import { create } from "zustand";
import { browserApi, type BrowserState } from "./api/browser";
import { sessionsApi } from "./api/harness";
import { useSessions } from "./sessionStore";
import { useStore } from "./store";

export const useBrowser = create<{ states: Record<string, BrowserState>; errors: Record<string, string>; error: (id: string, value: string) => void; put: (id: string, state: BrowserState) => void }>((set) => ({
  states: {},
  errors: {},
  error: (id, value) => set((s) => ({ errors: { ...s.errors, [id]: value } })),
  put: (id, state) => set((s) => ({ states: { ...s.states, [id]: { ...s.states[id], ...state } } })),
}));
let creating: Promise<string> | null = null;
/** A browser belongs to a real harness session, including a browser opened before chat starts. */
export async function openBrowser(url?: string, sessionId?: string) {
  let target: string | null = null;
  try {
    let id = sessionId || useSessions.getState().activeId;
    if (!id) {
      if (!creating) creating = (async () => {
        const { draft, pendingBranch } = useSessions.getState();
        const s = await sessionsApi.create({ project_id: useStore.getState().agentProjectId, provider: draft.model.provider, model: draft.model.model, mode: draft.mode, branch: pendingBranch });
        await useSessions.getState().refresh();
        useSessions.getState().select(s.id);
        return s.id;
      })().finally(() => { creating = null; });
      id = await creating;
    }
    useStore.getState().openBrowserTab(id);
    target = id;
    useBrowser.getState().error(id, "");
    if (url) {
      const state = await browserApi.input(id, { type: "navigate", url });
      if (state?.tabs) useBrowser.getState().put(id, state);
    } else {
      const state = await browserApi.state(id);
      useBrowser.getState().put(id, state);
      if (!state.tabs?.length) {
        const initial = await browserApi.input(id, { type: "navigate", url: "about:blank" });
        if (initial?.tabs) useBrowser.getState().put(id, initial);
      }
    }
    return id;
  } catch (e: any) { const message = String(e?.message || e); if (target) useBrowser.getState().error(target, message); useStore.getState().notify(message); return null; }
}
