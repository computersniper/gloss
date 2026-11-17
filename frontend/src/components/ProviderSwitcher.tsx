import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { useSessions } from "../sessionStore";

const PROVIDER_META: Record<string, { label: string; kind: "openai" | "anthropic" | "local" }> = {
  local_codex: { label: "Codex", kind: "openai" },
  local_claude: { label: "Claude", kind: "anthropic" },
  anthropic: { label: "Anthropic API", kind: "anthropic" },
  openai: { label: "Local API", kind: "local" },
};

function ProviderMark({ provider }: { provider: string }) {
  const kind = PROVIDER_META[provider]?.kind || "local";
  if (kind === "anthropic") {
    return <span className="provider-mark anthropic" aria-label="Anthropic">✳</span>;
  }
  if (kind === "openai") {
    return (
      <svg className="provider-mark openai" viewBox="0 0 24 24" aria-label="OpenAI" role="img">
        <path d="M12 3.2a4.2 4.2 0 0 1 4 2.9 4.2 4.2 0 0 1 3.3 6.7 4.2 4.2 0 0 1-3.9 6.4 4.2 4.2 0 0 1-7.3-.2 4.2 4.2 0 0 1-3.4-6.6 4.2 4.2 0 0 1 3.8-6.3A4.2 4.2 0 0 1 12 3.2Z" />
        <path d="m8.5 6.1 7.8 4.5v5.6M4.7 12.4l7.8-4.5 4.8 2.8M8.1 19l.1-9 4.8-2.8m2.4 12-.1-9-4.8-2.8m8.8 5.4-7.8 4.5-4.8-2.8" />
      </svg>
    );
  }
  return <span className="provider-mark local" aria-label="Local API">⚡</span>;
}

function statusClass(status?: string) {
  if (status === "connected") return "connected";
  if (status === "checking") return "checking";
  return "error";
}

export function ProviderSwitcher() {
  const provider = useStore((state) => state.provider);
  const providers = useStore((state) => state.providers);
  const statuses = useStore((state) => state.providerStatuses);
  const switchProvider = useStore((state) => state.switchProvider);
  const uiLang = useStore((state) => state.uiLang);
  const [open, setOpen] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const current = PROVIDER_META[provider] || { label: provider, kind: "local" as const };
  const currentStatus = statuses[provider];

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!host.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  const statusTitle = currentStatus?.status === "connected"
    ? (uiLang === "zh" ? "连接正常" : "Connected")
    : currentStatus?.status === "checking"
      ? (uiLang === "zh" ? "正在检测连接" : "Checking connection")
      : (currentStatus?.error || (uiLang === "zh" ? "连接不可用" : "Not connected"));

  return (
    <div className="provider-switcher" ref={host}>
      <button
        className={"provider-badge" + (open ? " open" : "")}
        title={uiLang === "zh" ? "切换模型渠道（Claude Code、Codex、本地 API…），输入框的模型列表随之切换" : "Switch the model channel (Claude Code, Codex, local API…); the composer's model list follows"}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <ProviderMark provider={provider} />
        <span className="provider-badge-label">{current.label}</span>
        <span className="provider-badge-indicators">
          <span className={`provider-status-dot ${statusClass(currentStatus?.status)}`} title={statusTitle} />
          <svg className="provider-chevron" viewBox="0 0 12 12" aria-hidden>
            <path d="m2.5 4.25 3.5 3.5 3.5-3.5" />
          </svg>
        </span>
      </button>
      {open && (
        <div className="provider-menu" role="menu">
          {(providers.length ? providers : [provider]).map((name) => {
            const meta = PROVIDER_META[name] || { label: name, kind: "local" as const };
            const state = statuses[name];
            return (
              <button
                key={name}
                className={"provider-option" + (name === provider ? " selected" : "")}
                role="menuitemradio"
                aria-checked={name === provider}
                onClick={() => {
                  setOpen(false);
                  // the composer's model list follows the channel: the current
                  // thread (or the next one) moves to it with its default model
                  void switchProvider(name).then(() => {
                    if (useStore.getState().provider !== name) return;
                    const draft = useSessions.getState().draft.model;
                    if (draft.provider || draft.model || draft.effort) void useSessions.getState().setModel({ provider: null, model: null, effort: null });
                  });
                }}
              >
                <ProviderMark provider={name} />
                <span className="provider-option-copy">
                  <strong>{meta.label}</strong>
                  <small>{name}</small>
                </span>
                <span className={`provider-status-dot ${statusClass(state?.status)}`} />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
