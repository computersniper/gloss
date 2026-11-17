import { useEffect, useMemo, useRef, useState } from "react";
import qrcode from "qrcode-generator";
import { isMobileClient, leaveComputer } from "../mobile";

type RemoteStatus = {
  enabled: boolean;
  reachable: boolean;
  restart_required: boolean;
  auth: boolean;
  port: number;
  token: string;
  urls: string[];
  pair_urls: string[];
  tunnel: { state: "off" | "downloading" | "starting" | "running" | "error"; url: string | null; error: string; enabled: boolean };
  tunnel_pair_url: string | null;
  pair_expires_in: number;
  devices: { id: string; name: string; created: number; last_seen: number }[];
};

function ago(seconds: number, zh: boolean) {
  const s = Math.max(0, Date.now() / 1000 - seconds);
  if (s < 90) return zh ? "刚刚" : "just now";
  if (s < 3600) return zh ? `${Math.round(s / 60)} 分钟前` : `${Math.round(s / 60)} min ago`;
  if (s < 86400) return zh ? `${Math.round(s / 3600)} 小时前` : `${Math.round(s / 3600)} h ago`;
  return zh ? `${Math.round(s / 86400)} 天前` : `${Math.round(s / 86400)} d ago`;
}

function QrCode({ text }: { text: string }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [text]);
  // black on white whatever the theme: phone cameras need the contrast
  return <div className="mobile-qr" role="img" aria-label="QR code" dangerouslySetInnerHTML={{ __html: svg }} />;
}

/**
 * Settings → Mobile access. On the computer: turn it on and show the address,
 * pairing code and a QR code for the phone/tablet app. In the app itself: which
 * computer this is, and a way back to the computer list.
 */
export function MobileAccess({ zh }: { zh: boolean }) {
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const remote = isMobileClient();

  // the QR holds a one-time sign-in link; a new one replaces the last
  const [pairUrls, setPairUrls] = useState<string[]>([]);
  const [tunnelPairUrl, setTunnelPairUrl] = useState<string | null>(null);
  const [via, setVia] = useState<"lan" | "internet">("lan");
  const deviceCount = useRef(-1);
  const tunnelUrl = useRef<string | null>(null);
  const load = async (pair: boolean) => {
    const r = await fetch(`/api/remote${pair ? "?pair=1" : ""}`).catch(() => null);
    const next: RemoteStatus | null = r?.ok ? await r.json() : null;
    setStatus(next);
    if (next && pair) {
      setPairUrls(next.pair_urls);
      setTunnelPairUrl(next.tunnel_pair_url);
    }
    const newDevice = next && deviceCount.current >= 0 && next.devices.length > deviceCount.current;
    // the tunnel came up (or got a new address): the QR needs its link
    const newTunnel = next && next.tunnel.url && next.tunnel.url !== tunnelUrl.current;
    if (next) {
      deviceCount.current = next.devices.length;
      tunnelUrl.current = next.tunnel.url;
    }
    // a phone just used the QR: show a fresh one
    if ((newDevice || newTunnel) && !pair) void load(true);
  };

  useEffect(() => {
    if (remote) return;
    void load(true);
    // new QR shortly before the old one expires; watch for phones pairing meanwhile
    const qr = window.setInterval(() => void load(true), 8 * 60 * 1000);
    const watch = window.setInterval(() => { if (document.visibilityState === "visible") void load(false); }, 4000);
    return () => { window.clearInterval(qr); window.clearInterval(watch); };
  }, [remote]);

  const change = async (patch: { enabled?: boolean; tunnel?: boolean; rotate_token?: boolean; remove_device?: string; remove_all_devices?: boolean }) => {
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/remote", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
      if (!r.ok) throw new Error(String(r.status));
      const next: RemoteStatus = await r.json();
      setStatus(next);
      deviceCount.current = next.devices.length;
      if (patch.rotate_token) void load(true);
      if (patch.tunnel) setVia("internet");
    } catch {
      setError(zh ? "保存失败" : "Couldn't save");
    } finally {
      setBusy(false);
    }
  };

  if (remote) {
    return (
      <section className="usage-card" aria-label={zh ? "移动端" : "Mobile"}>
        <div className="usage-head">
          <div>
            <strong>{zh ? "已连接的电脑" : "Connected computer"}</strong>
            <span>{window.location.host}</span>
          </div>
          <button className="small" type="button" onClick={() => leaveComputer()}>{zh ? "切换电脑" : "Switch computer"}</button>
        </div>
        <p className="usage-note">{zh ? "论文、对话和 Claude Code / Codex 都在这台电脑上运行，手机和平板只是遥控。" : "Papers, threads and Claude Code / Codex all run on this computer; this device drives them remotely."}</p>
      </section>
    );
  }
  // older backend, or opened from another device without the app
  if (!status) return null;

  const tunnelOn = status.tunnel.state === "running" && !!tunnelPairUrl;
  // LAN when the computer listens on the network, the tunnel otherwise or when picked
  const mode = !status.reachable ? "internet" : !tunnelOn ? "lan" : via;
  const pairUrl = mode === "internet" ? (tunnelOn ? tunnelPairUrl : null) : pairUrls[0];
  const paired = status.reachable || tunnelOn;
  const tunnelNote: Record<string, [string, string]> = {
    downloading: ["正在下载 cloudflared（只需一次）…", "Downloading cloudflared (once)…"],
    starting: ["正在连接 Cloudflare…", "Connecting to Cloudflare…"],
  };
  return (
    <section className="usage-card mobile-access" aria-label={zh ? "移动端访问" : "Mobile access"}>
      <div className="usage-head">
        <div>
          <strong>{zh ? "移动端访问" : "Mobile access"}</strong>
          <span>{zh ? "用 iPhone、iPad 或 Android 连接这台电脑（家里 Wi‑Fi）" : "Use this computer from an iPhone, iPad or Android phone (home Wi-Fi)"}</span>
        </div>
        <label className="mobile-switch">
          <input type="checkbox" checked={status.enabled} disabled={busy} onChange={(e) => void change({ enabled: e.target.checked })} />
          <span>{zh ? "允许" : "Allow"}</span>
        </label>
      </div>
      {status.restart_required && (
        <p className="usage-note" role="alert">{zh ? "重启 GlossX 后生效（会固定在端口 " : "Restart GlossX to apply (it will listen on port "}{status.port}{zh ? "）。" : ")."}</p>
      )}
      <div className="usage-head mobile-tunnel">
        <div>
          <strong>{zh ? "外网访问" : "Internet access"}</strong>
          <span>
            {zh
              ? "不在同一个 Wi‑Fi 也能用：经 Cloudflare 免费隧道转发，不用开端口、不用注册。"
              : "Works away from home too: through a free Cloudflare tunnel, no open ports, no account."}
          </span>
        </div>
        <label className="mobile-switch">
          <input type="checkbox" checked={status.tunnel.enabled} disabled={busy} onChange={(e) => void change({ tunnel: e.target.checked })} />
          <span>{zh ? "开启" : "On"}</span>
        </label>
      </div>
      {status.tunnel.enabled && tunnelNote[status.tunnel.state] && <p className="usage-note">{tunnelNote[status.tunnel.state][zh ? 0 : 1]}</p>}
      {status.tunnel.enabled && status.tunnel.state === "error" && (
        <p className="usage-note" role="alert">
          {zh ? "隧道没有连上：" : "The tunnel didn't connect: "}{status.tunnel.error}{" "}
          <button className="small" type="button" disabled={busy} onClick={() => void change({ tunnel: true })}>{zh ? "重试" : "Retry"}</button>
        </p>
      )}
      {paired && (
        <>
          {!status.auth && <p className="usage-note" role="alert">{zh ? "GLOSS_REMOTE_AUTH=off：任何设备都能直接访问。" : "GLOSS_REMOTE_AUTH=off: any device can connect without a code."}</p>}
          {status.reachable && tunnelOn && (
            <div className="mobile-via" role="tablist">
              <button type="button" role="tab" aria-selected={mode === "lan"} className={mode === "lan" ? "active" : ""} onClick={() => setVia("lan")}>{zh ? "家里 Wi‑Fi" : "Home Wi-Fi"}</button>
              <button type="button" role="tab" aria-selected={mode === "internet"} className={mode === "internet" ? "active" : ""} onClick={() => setVia("internet")}>{zh ? "外网" : "Internet"}</button>
            </div>
          )}
          {pairUrl ? (
            <div className="mobile-pair">
              <QrCode text={pairUrl} />
              <div className="mobile-pair-text">
                <p className="usage-note">
                  {zh
                    ? "用 iPhone、iPad 或安卓手机的相机扫码，在浏览器里打开就自动登录，不用装 App。之后点“分享 → 添加到主屏幕”，就能像 App 一样全屏使用。"
                    : "Scan with the camera on an iPhone, iPad or Android phone: it opens in the browser, already signed in, no app needed. Then Share → Add to Home Screen to use it full screen like an app."}
                </p>
                <p className="usage-note">{zh ? "二维码只能用一次，10 分钟内有效。" : "Each QR code works once, for 10 minutes."}</p>
                {mode === "internet" ? (
                  <p className="usage-note">
                    {zh
                      ? "外网地址每次重启 GlossX 或隧道都会变，变了之后手机要重新扫码。内容经过 Cloudflare 转发（https 加密）。"
                      : "The internet address changes whenever GlossX or the tunnel restarts; scan again after that. Traffic passes through Cloudflare (encrypted with https)."}
                  </p>
                ) : (
                  <p className="usage-note">{zh ? "手机和电脑要连同一个 Wi‑Fi。" : "The phone and this computer need to be on the same Wi-Fi."}</p>
                )}
                <button className="small" type="button" onClick={() => void load(true)}>{zh ? "刷新二维码" : "New QR code"}</button>
              </div>
            </div>
          ) : (
            <p className="usage-note">{zh ? "没找到局域网地址，请确认电脑已联网。" : "No network address found; check that this computer is online."}</p>
          )}
          {status.devices.length > 0 && (
            <div className="mobile-devices">
              <label>{zh ? "已配对的设备" : "Paired devices"}</label>
              {status.devices.map((d) => (
                <div className="mobile-device" key={d.id}>
                  <span>{d.name}</span>
                  <span className="usage-note">{zh ? "最近使用 " : "last used "}{ago(d.last_seen, zh)}</span>
                  <button className="small" type="button" disabled={busy} onClick={() => void change({ remove_device: d.id })}>{zh ? "移除" : "Remove"}</button>
                </div>
              ))}
            </div>
          )}
          <details className="mobile-manual">
            <summary>{zh ? "手动输入（GlossX App 或扫不了码时）" : "Enter by hand (GlossX app, or when the QR won't scan)"}</summary>
            <label>{zh ? "地址" : "Address"}</label>
            {status.reachable && status.urls.map((u) => <code key={u}>{u.replace(/^http:\/\//, "")}</code>)}
            {tunnelOn && status.tunnel.url && <code>{status.tunnel.url}</code>}
            <label>{zh ? "配对码" : "Pairing code"}</label>
            <code className="mobile-code">{status.token}</code>
            <button className="small" type="button" disabled={busy} onClick={() => void change({ rotate_token: true })}>
              {zh ? "换配对码并移除所有设备" : "New code, sign out every device"}
            </button>
          </details>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
