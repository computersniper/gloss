import { useEffect, useState } from "react";
import {
  agentApi, APPROVAL_PERMISSIONS,
  type AgentConnections, type ConnectionsStatus, type McpServerCfg, type SshHostCfg,
} from "../api/agent";
import { useStore } from "../store";

const EMPTY: AgentConnections = {
  mcp_servers: [], ssh_hosts: [], cli_agents: { claude: { enabled: false }, codex: { enabled: false } }, auto_approve: [],
};

const envToText = (env: Record<string, string>) => Object.entries(env || {}).map(([k, v]) => `${k}=${v}`).join("\n");
const textToEnv = (t: string) => {
  const out: Record<string, string> = {};
  t.split("\n").forEach((l) => {
    const i = l.indexOf("=");
    if (i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1);
  });
  return out;
};

export function ConnectionsSection() {
  const uiLang = useStore((s) => s.uiLang);
  const notify = useStore((s) => s.notify);
  const zh = uiLang === "zh";
  const T = zh
    ? {
        title: "连接（MCP / SSH / CLI 智能体）", mcp: "MCP 服务器", ssh: "SSH 主机", cli: "CLI 智能体", auto: "自动批准的权限",
        add: "添加", remove: "删除", enabled: "启用", save: "保存连接", refresh: "刷新状态", name: "名称", command: "命令",
        args: "参数（空格分隔）", env: "环境变量（每行 KEY=VALUE）", host: "主机", user: "用户", port: "端口", workdir: "工作目录",
        saved: "连接已保存", tools: "工具", available: "可用", unavailable: "不可用", status: "状态",
      }
    : {
        title: "Connections (MCP / SSH / CLI agents)", mcp: "MCP servers", ssh: "SSH hosts", cli: "CLI agents",
        auto: "Auto-approve permissions", add: "Add", remove: "Remove", enabled: "Enabled", save: "Save connections",
        refresh: "Refresh status", name: "Name", command: "Command", args: "Args (space separated)",
        env: "Env (one KEY=VALUE per line)", host: "Host", user: "User", port: "Port", workdir: "Workdir",
        saved: "Connections saved", tools: "tools", available: "available", unavailable: "unavailable", status: "Status",
      };
  const [cfg, setCfg] = useState<AgentConnections | null>(null);
  const [status, setStatus] = useState<ConnectionsStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshStatus = () => {
    setError("");
    return agentApi.connectionsStatus().then(setStatus).catch((e) => setError(String(e?.message || e)));
  };
  useEffect(() => {
    agentApi
      .getConnections()
      .then((c) => setCfg({ ...EMPTY, ...c, cli_agents: { ...EMPTY.cli_agents, ...(c.cli_agents || {}) } }))
      .catch((e) => setError(String(e?.message || e)));
  }, []);

  if (!cfg) return <div className="muted">{error || "…"}</div>;

  const setMcp = (i: number, patch: Partial<McpServerCfg>) =>
    setCfg({ ...cfg, mcp_servers: cfg.mcp_servers.map((m, j) => (j === i ? { ...m, ...patch } : m)) });
  const setSsh = (i: number, patch: Partial<SshHostCfg>) =>
    setCfg({ ...cfg, ssh_hosts: cfg.ssh_hosts.map((m, j) => (j === i ? { ...m, ...patch } : m)) });

  async function save() {
    if (!cfg) return;
    setBusy(true);
    setError("");
    try {
      setCfg(await agentApi.putConnections(cfg));
      notify(T.saved);
      void refreshStatus();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  const mcpStat = (n: string) => status?.mcp.find((x) => x.name === n);
  const sshStat = (n: string) => status?.ssh.find((x) => x.name === n);

  return (
    <fieldset className="conn">
      <legend>{T.title}</legend>

      <label>{T.mcp}</label>
      {cfg.mcp_servers.map((m, i) => {
        const st = mcpStat(m.name);
        return (
          <div className="conn-block" key={i}>
            <input placeholder={T.name} value={m.name} onChange={(e) => setMcp(i, { name: e.target.value })} />
            <input placeholder={T.command} value={m.command} onChange={(e) => setMcp(i, { command: e.target.value })} />
            <input
              placeholder={T.args}
              value={(m.args || []).join(" ")}
              onChange={(e) => setMcp(i, { args: e.target.value.split(/\s+/).filter(Boolean) })}
            />
            <textarea rows={2} placeholder={T.env} value={envToText(m.env)} onChange={(e) => setMcp(i, { env: textToEnv(e.target.value) })} />
            <div className="conn-row">
              <label className="setting-toggle">
                <input type="checkbox" checked={m.enabled} onChange={(e) => setMcp(i, { enabled: e.target.checked })} />
                <span>{T.enabled}</span>
              </label>
              {st && (
                <span className={"conn-status " + (st.ok ? "ok" : "bad")}>
                  {st.ok ? `✓ ${Array.isArray(st.tools) ? st.tools.length : st.tools} ${T.tools}` : `✗ ${st.error || ""}`}
                </span>
              )}
              <button className="small" onClick={() => setCfg({ ...cfg, mcp_servers: cfg.mcp_servers.filter((_, j) => j !== i) })}>{T.remove}</button>
            </div>
          </div>
        );
      })}
      <button className="small" onClick={() => setCfg({ ...cfg, mcp_servers: [...cfg.mcp_servers, { name: "", command: "", args: [], env: {}, enabled: true }] })}>
        + {T.add}
      </button>

      <label>{T.ssh}</label>
      {cfg.ssh_hosts.map((h, i) => {
        const st = sshStat(h.name);
        return (
          <div className="conn-block" key={i}>
            <input placeholder={T.name} value={h.name} onChange={(e) => setSsh(i, { name: e.target.value })} />
            <input placeholder={T.host} value={h.host} onChange={(e) => setSsh(i, { host: e.target.value })} />
            <input placeholder={T.user} value={h.user} onChange={(e) => setSsh(i, { user: e.target.value })} />
            <input type="number" placeholder={T.port} value={h.port} onChange={(e) => setSsh(i, { port: Number(e.target.value) || 22 })} />
            <input placeholder={T.workdir} value={h.workdir} onChange={(e) => setSsh(i, { workdir: e.target.value })} />
            <div className="conn-row">
              <label className="setting-toggle">
                <input type="checkbox" checked={h.enabled !== false} onChange={(e) => setSsh(i, { enabled: e.target.checked })} />
                <span>{T.enabled}</span>
              </label>
              {st && <span className={"conn-status " + (st.ok ? "ok" : "bad")}>{st.ok ? "✓" : `✗ ${st.error || ""}`}</span>}
              <button className="small" onClick={() => setCfg({ ...cfg, ssh_hosts: cfg.ssh_hosts.filter((_, j) => j !== i) })}>{T.remove}</button>
            </div>
          </div>
        );
      })}
      <button className="small" onClick={() => setCfg({ ...cfg, ssh_hosts: [...cfg.ssh_hosts, { name: "", host: "", user: "", port: 22, workdir: "", enabled: true }] })}>
        + {T.add}
      </button>

      <label>{T.cli}</label>
      {(["claude", "codex"] as const).map((k) => (
        <div className="conn-row" key={k}>
          <label className="setting-toggle">
            <input
              type="checkbox"
              checked={!!cfg.cli_agents[k]?.enabled}
              onChange={(e) => setCfg({ ...cfg, cli_agents: { ...cfg.cli_agents, [k]: { enabled: e.target.checked } } })}
            />
            <span>{k === "claude" ? "Claude Code" : "Codex"}</span>
          </label>
          {status && (
            <span className={"conn-status " + (status.cli[k]?.available ? "ok" : "bad")}>
              {status.cli[k]?.available ? `✓ ${T.available}` : `✗ ${T.unavailable}`}
            </span>
          )}
        </div>
      ))}

      <label>{T.auto}</label>
      {APPROVAL_PERMISSIONS.map((p) => (
        <label className="setting-toggle" key={p}>
          <input
            type="checkbox"
            checked={cfg.auto_approve.includes(p)}
            onChange={(e) =>
              setCfg({ ...cfg, auto_approve: e.target.checked ? [...cfg.auto_approve, p] : cfg.auto_approve.filter((x) => x !== p) })
            }
          />
          <span><code>{p}</code></span>
        </label>
      ))}

      {error && <div className="error">{error}</div>}
      <div className="conn-row">
        <button className="primary small" onClick={() => void save()} disabled={busy}>{T.save}</button>
        <button className="small" onClick={() => void refreshStatus()}>{T.refresh}</button>
      </div>
    </fieldset>
  );
}
