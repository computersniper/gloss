// GlossX mobile: pick a computer running GlossX, check the pairing code, then
// open its web app in this same view. Everything after that is the shared
// frontend served by the computer; the CLIs (Claude Code, Codex) run there.
// The bundled /local frontend instead uses the device's own API settings.
"use strict";

const STORE = "glossx.computers";
const LAST = "glossx.last";
const DEFAULT_PORT = 8010;

const zh = /^zh/i.test(navigator.language || "");
const T = {
  tagline: ["Use your API or connect to your computer", "使用自己的 API，或连接电脑"],
  localTitle: ["Use on this device", "本机工作区"],
  localHint: ["Chat with your own OpenAI API key. No computer or local CLI is required.", "填写自己的 OpenAI API 进行对话，无需电脑或本地 CLI。"],
  localOpen: ["Open local API workspace", "打开本机 API 工作区"],
  computers: ["Your computers", "已保存的电脑"],
  add: ["Add a computer", "添加电脑"],
  how: [
    "On the computer, open GlossX → Settings → Mobile access, turn it on, then scan the QR code with this device's camera or type the address and pairing code below.",
    "在电脑上打开 GlossX → 设置 → 移动端访问并开启，然后用本机相机扫码，或在下面填写地址和配对码。",
  ],
  address: ["Address", "地址"],
  code: ["Pairing code", "配对码"],
  name: ["Name (optional)", "名称（可选）"],
  namePh: ["My laptop", "我的电脑"],
  connect: ["Connect", "连接"],
  forget: ["Forget", "移除"],
  checking: ["Connecting…", "正在连接…"],
  badCode: ["The pairing code is wrong, or it was changed on the computer.", "配对码不对，或者电脑上已经换了新的配对码。"],
  unreachable: [
    "Can't reach that computer. Check that GlossX is running with mobile access on, and that both devices are on the same network (or VPN).",
    "连不上这台电脑。请确认电脑上的 GlossX 正在运行并已开启移动端访问，且两台设备在同一网络（或同一 VPN）。",
  ],
  badAddress: ["That address doesn't look right.", "地址格式不对。"],
  expired: ["The computer asked for the pairing code again.", "电脑要求重新输入配对码。"],
};
const t = (key) => T[key][zh ? 1 : 0];

document.documentElement.lang = zh ? "zh-CN" : "en";
for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
for (const el of document.querySelectorAll("[data-t-placeholder]")) el.placeholder = t(el.dataset.tPlaceholder);

const $ = (id) => document.getElementById(id);

function load() {
  try {
    const list = JSON.parse(localStorage.getItem(STORE) || "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}
function save(list) {
  try { localStorage.setItem(STORE, JSON.stringify(list)); } catch { /* private mode */ }
}

/** "192.168.1.5", "192.168.1.5:8010", "http://host:8010/" → "http://host:port" */
function normalizeAddress(raw) {
  let text = String(raw || "").trim();
  if (!text) return null;
  if (!/^https?:\/\//i.test(text)) text = `http://${text}`;
  try {
    const url = new URL(text);
    if (!url.port && url.protocol === "http:") url.port = String(DEFAULT_PORT);
    return url.origin;
  } catch {
    return null;
  }
}

function setStatus(text, bad = false) {
  const el = $("status");
  el.textContent = text || "";
  el.className = `status${bad ? " bad" : ""}`;
}

async function probe(url, code) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 6000);
  try {
    const r = await fetch(`${url}/api/health?token=${encodeURIComponent(code)}`, { signal: controller.signal, cache: "no-store" });
    if (r.status === 401) return "badCode";
    return r.ok ? "ok" : "unreachable";
  } catch {
    return "unreachable";
  } finally {
    clearTimeout(timer);
  }
}

function open(entry) {
  try { localStorage.setItem(LAST, entry.url); } catch { /* ignore */ }
  const shell = location.href.split("#")[0];
  const params = new URLSearchParams({ token: entry.code, client: "mobile", shell });
  location.href = `${entry.url}/?${params}`;
}

async function connect(entry, { silent = false } = {}) {
  setStatus(t("checking"));
  const result = await probe(entry.url, entry.code);
  if (result === "ok") {
    const list = load().filter((c) => c.url !== entry.url);
    list.unshift({ ...entry, name: entry.name || entry.url.replace(/^https?:\/\//, "") });
    save(list);
    open(entry);
    return true;
  }
  if (!silent) setStatus(t(result), true);
  else setStatus("");
  return false;
}

function render() {
  const list = load();
  $("saved").hidden = list.length === 0;
  const ul = $("list");
  ul.textContent = "";
  for (const entry of list) {
    const li = document.createElement("li");
    const openBtn = document.createElement("button");
    openBtn.className = "open";
    openBtn.type = "button";
    const name = document.createElement("strong");
    name.textContent = entry.name || entry.url;
    const addr = document.createElement("span");
    addr.textContent = entry.url;
    openBtn.append(name, addr);
    openBtn.onclick = async () => {
      li.classList.add("busy");
      const ok = await connect(entry);
      li.classList.remove("busy");
      if (!ok) {
        $("addr").value = entry.url.replace(/^http:\/\//, "");
        $("name").value = entry.name || "";
        $("code").value = "";
        $("code").focus();
      }
    };
    const forget = document.createElement("button");
    forget.className = "forget";
    forget.type = "button";
    forget.textContent = t("forget");
    forget.onclick = () => {
      save(load().filter((c) => c.url !== entry.url));
      render();
    };
    li.append(openBtn, forget);
    ul.append(li);
  }
}

$("add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = normalizeAddress($("addr").value);
  if (!url) return setStatus(t("badAddress"), true);
  const button = $("connect");
  button.disabled = true;
  await connect({ url, code: $("code").value.trim(), name: $("name").value.trim() });
  button.disabled = false;
});

/** glossx://pair?u=<address>&t=<code>[&n=<name>] from the QR code on the computer. */
function handlePairLink(link) {
  if (!link || !/^glossx:/i.test(link)) return false;
  let params;
  try {
    params = new URL(link).searchParams;
  } catch {
    return false;
  }
  const url = normalizeAddress(params.get("u"));
  const code = params.get("t") || "";
  if (!url || !code) return false;
  $("addr").value = url.replace(/^http:\/\//, "");
  $("code").value = code;
  $("name").value = params.get("n") || "";
  void connect({ url, code, name: params.get("n") || "" });
  return true;
}

async function start() {
  render();
  const reason = location.hash.slice(1);
  if (reason === "expired") setStatus(t("expired"), true);

  const App = window.Capacitor?.Plugins?.App;
  if (App) {
    App.addListener("appUrlOpen", (event) => handlePairLink(event.url));
    try {
      const launch = await App.getLaunchUrl();
      if (launch?.url && handlePairLink(launch.url)) return;
    } catch { /* not launched from a link */ }
  }
  // a pairing link that arrived while a computer's page was open
  if (reason.startsWith("pair=") && handlePairLink(decodeURIComponent(reason.slice(5)))) return;

  // Keep both modes available on every launch. Saved computers reconnect on tap.
}

void start();
