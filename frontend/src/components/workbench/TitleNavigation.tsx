import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useStore, tabKey } from "../../store";
import { useSessions } from "../../sessionStore";

type Place = { kind: "tab" | "thread"; id: string };

/** Workbench visit history, separate from the browser's URL history. */
export function TitleNavigation({ zh }: { zh: boolean }) {
  const [history, setHistory] = useState<{ items: Place[]; index: number }>({ items: [], index: -1 });
  const navigating = useRef(false);
  useEffect(() => {
    const record = (place: Place) => {
      if (navigating.current) return;
      setHistory(h => {
        const current = h.items[h.index];
        if (current?.kind === place.kind && current.id === place.id) return h;
        const items = [...h.items.slice(0, h.index + 1), place].slice(-100);
        return { items, index: items.length - 1 };
      });
    };
    const initial = useStore.getState();
    const sessionId = useSessions.getState().activeId;
    if (initial.layout.aiOpen && initial.layout.aiMode === "agent" && sessionId) record({ kind: "thread", id: sessionId });
    else if (initial.activeTabKey) record({ kind: "tab", id: initial.activeTabKey });
    const stopTabs = useStore.subscribe((next, prev) => {
      if (next.activeTabKey && next.activeTabKey !== prev.activeTabKey) record({ kind: "tab", id: next.activeTabKey });
      else if (next.layout.aiOpen && next.layout.aiMode === "agent" && (!prev.layout.aiOpen || prev.layout.aiMode !== "agent")) {
        const id = useSessions.getState().activeId;
        if (id) record({ kind: "thread", id });
      }
    });
    const stopThreads = useSessions.subscribe((next, prev) => {
      if (next.activeId && next.activeId !== prev.activeId) record({ kind: "thread", id: next.activeId });
    });
    return () => { stopTabs(); stopThreads(); };
  }, []);
  const tabs = useStore(s => s.openTabs);
  const sessions = useSessions(s => s.list);
  const destination = (direction: number) => {
    for (let i = history.index + direction; i >= 0 && i < history.items.length; i += direction) {
      const p = history.items[i];
      if (p.kind === "tab" ? tabs.some(t => tabKey(t) === p.id) : sessions.some(s => s.id === p.id && !s.archived)) return i;
    }
    return -1;
  };
  const go = (index: number) => {
    if (index < 0) return;
    const place = history.items[index];
    navigating.current = true;
    try {
      if (place.kind === "thread") useSessions.getState().openThread(place.id);
      else {
        useStore.getState().setLayout({ aiOpen: false, sideOpen: false });
        void useStore.getState().activateTab(place.id);
      }
      setHistory(h => ({ ...h, index }));
    } finally { navigating.current = false; }
  };
  const back = destination(-1), forward = destination(1);
  return <div className="wb-title-navigation">
    <button type="button" className="wb-icon-btn" title={zh ? "返回" : "Back"} aria-label={zh ? "返回" : "Back"} disabled={back < 0} onClick={() => go(back)}><ArrowLeft size={16} /></button>
    <button type="button" className="wb-icon-btn" title={zh ? "前进" : "Forward"} aria-label={zh ? "前进" : "Forward"} disabled={forward < 0} onClick={() => go(forward)}><ArrowRight size={16} /></button>
  </div>;
}
