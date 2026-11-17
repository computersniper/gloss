// A small promise-based confirmation dialog for choices that can lose work
// (window.confirm cannot offer "stash / discard / cancel").
import { useEffect, useRef } from "react";
import { create } from "zustand";
import { TriangleAlert } from "lucide-react";

export type DialogAction = { id: string; label: string; hint?: string; primary?: boolean; danger?: boolean };
export type DialogSpec = {
  title: string;
  body?: string;
  /** Monospaced block: file lists, conflict paths. */
  detail?: string;
  tone?: "warn";
  actions: DialogAction[];
  /** Cancel button label; null hides it. */
  cancel?: string | null;
};

const useDialog = create<{ spec: (DialogSpec & { resolve: (id: string | null) => void }) | null }>(() => ({ spec: null }));

/** Resolves with the chosen action id, or null when cancelled / dismissed. */
export function ask(spec: DialogSpec): Promise<string | null> {
  return new Promise((resolve) => {
    useDialog.getState().spec?.resolve(null);
    useDialog.setState({ spec: { ...spec, resolve } });
  });
}

export function DialogHost() {
  const spec = useDialog((s) => s.spec);
  const box = useRef<HTMLDivElement>(null);
  const finish = (id: string | null) => {
    spec?.resolve(id);
    useDialog.setState({ spec: null });
  };

  useEffect(() => {
    if (!spec) return;
    const prev = document.activeElement as HTMLElement | null;
    // focus the safe choice: the primary action unless every action is destructive
    const target = box.current?.querySelector<HTMLElement>("[data-autofocus]") || box.current?.querySelector<HTMLElement>("button");
    target?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Tab") {
        const controls = [...(box.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex='0']") || [])];
        const first = controls[0], last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        finish(null);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      prev?.focus?.();
    };
  }, [spec]);

  if (!spec) return null;
  const safe = spec.actions.find((a) => a.primary && !a.danger);
  return (
    <div className="gx-dialog-backdrop" onMouseDown={() => finish(null)}>
      <div ref={box} className={`gx-dialog${spec.tone ? ` ${spec.tone}` : ""}`} role="alertdialog" aria-modal="true" aria-label={spec.title} onMouseDown={(e) => e.stopPropagation()}>
        <h3>{spec.tone === "warn" && <TriangleAlert size={15} />}{spec.title}</h3>
        {spec.body && <p>{spec.body}</p>}
        {spec.detail && <pre>{spec.detail}</pre>}
        <div className="gx-dialog-actions">
          {spec.cancel !== null && (
            <button type="button" className="gx-btn ghost" data-autofocus={safe ? undefined : ""} onClick={() => finish(null)}>{spec.cancel || "Cancel"}</button>
          )}
          <span className="spacer" />
          {spec.actions.map((a) => (
            <button
              key={a.id}
              type="button"
              className={`gx-btn${a.danger ? " danger" : a.primary ? " primary" : ""}`}
              title={a.hint}
              data-autofocus={a === safe ? "" : undefined}
              onClick={() => finish(a.id)}
            >
              {a.label}
            </button>
          ))}
        </div>
        {spec.actions.some((a) => a.hint) && (
          <ul className="gx-dialog-hints">
            {spec.actions.filter((a) => a.hint).map((a) => <li key={a.id}><b>{a.label}</b> — {a.hint}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}
