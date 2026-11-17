// Gloss desktop shell: starts the local backend, shows the app in an
// Electron window, and lends the backend Electron's Chromium as its browser.
"use strict";

const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { BrowserBridge } = require("./bridge");

const APP_NAME = "Gloss";
const STARTUP_TIMEOUT_MS = 60_000;
const TITLEBAR_HEIGHT = 40;
const REPO_ROOT = path.resolve(__dirname, "..");
const smokeArg = process.argv.find((arg) => arg.startsWith("--glossx-smoke="));
// Jump list entries start GlossX again with an action (handled by the running app).
const ACTION_ARG = "--glossx-action=";
const ACTIONS = new Set(["new-window", "new-thread", "import", "library", "scholar", "open-paper", "open-project"]);
const RECENT_LIMIT = 8;
const SMOKE_REPORT = smokeArg ? path.resolve(smokeArg.slice("--glossx-smoke=".length)) : null;

// The browser pages reach the network only through the backend's guard proxy;
// QUIC and direct DNS would bypass it. Loopback stays resolvable for the app.
app.commandLine.appendSwitch("disable-quic");
app.commandLine.appendSwitch("host-resolver-rules", "MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost");
app.commandLine.appendSwitch("force-webrtc-ip-handling-policy", "disable_non_proxied_udp");

const windows = new Set(); // every open app window, as in VS Code
let backend = null;
let backendUrl = null;
let quitting = false;
// New pages open in the window the user is working in.
const activeWindow = () => {
  const focused = BrowserWindow.getFocusedWindow();
  return focused && windows.has(focused) ? focused : [...windows].pop() || null;
};
const bridge = new BrowserBridge(activeWindow);

function logPath() {
  return path.join(app.getPath("logs"), "desktop.log");
}

function log(...parts) {
  const line = `${new Date().toISOString()} ${parts.join(" ")}\n`;
  try {
    fs.mkdirSync(path.dirname(logPath()), { recursive: true });
    fs.appendFileSync(logPath(), line);
  } catch {}
  if (!app.isPackaged) process.stderr.write(line);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

// The packaged app carries the frozen backend; a source checkout runs Python.
function backendCommand() {
  if (app.isPackaged) {
    const name = process.platform === "win32" ? "glossx-backend.exe" : "glossx-backend";
    return { command: path.join(process.resourcesPath, "backend", name), args: [] };
  }
  const python = process.env.GLOSSX_PYTHON
    || [path.join(REPO_ROOT, "backend", ".venv", "Scripts", "python.exe"), path.join(REPO_ROOT, "backend", ".venv", "bin", "python")].find((p) => fs.existsSync(p))
    || (process.platform === "win32" ? "python" : "python3");
  return { command: python, args: [path.join(REPO_ROOT, "packaging", "windows_launcher.py")] };
}

async function startBackend(browserEndpoint) {
  const port = await freePort();
  const readyFile = path.join(os.tmpdir(), `glossx-ready-${process.pid}-${Date.now()}`);
  const { command, args } = backendCommand();
  const env = { ...process.env, GLOSSX_BROWSER_CDP: browserEndpoint, GLOSSX_READY_FILE: readyFile };
  log("Starting backend:", command);
  backend = spawn(command, [...args, "--headless", "--port", String(port)], {
    env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true, detached: process.platform !== "win32",
  });
  const tail = [];
  const keep = (chunk) => { tail.push(String(chunk)); if (tail.length > 40) tail.shift(); };
  backend.stdout.on("data", keep);
  backend.stderr.on("data", keep);
  const exited = new Promise((resolve) => backend.once("exit", (code) => resolve(code)));
  backend.once("exit", (code) => {
    log("Backend exited with code", code);
    if (!quitting && backendUrl) {
      dialog.showErrorBox(APP_NAME, `The Gloss backend stopped unexpectedly (code ${code}).\n\nDetails: ${logPath()}`);
      app.quit();
    }
  });
  // The backend may move to another port if this one was taken meanwhile; it
  // reports the address it actually serves.
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const code = await Promise.race([exited, new Promise((resolve) => setTimeout(() => resolve(undefined), 150))]);
    if (code !== undefined) throw new Error(`The backend exited during startup (code ${code}).\n\n${tail.join("").slice(-3000)}`);
    let url = null;
    try { url = fs.readFileSync(readyFile, "utf8").trim(); } catch {}
    if (url) {
      fs.rmSync(readyFile, { force: true });
      return url;
    }
  }
  throw new Error(`The backend did not become ready in time.\n\n${tail.join("").slice(-3000)}`);
}

function stopBackend() {
  const child = backend;
  backend = null;
  if (!child || child.exitCode !== null) return;
  try {
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
    else process.kill(-child.pid, "SIGTERM");
  } catch {
    try { child.kill(); } catch {}
  }
}

async function settings() {
  const response = await fetch(`${backendUrl}/api/settings`, { signal: AbortSignal.timeout(2000) });
  return (await response.json()).config || {};
}

// Closing one of several windows just closes it; closing the last one quits
// the app, which asks first when work could be interrupted.
async function requestClose(win, all = false) {
  if (win.glossx.promptOpen) return;
  win.glossx.all = all;
  if (windows.size > 1 && !all) {
    closeNow(win);
    return;
  }
  let confirm = false;
  try { confirm = win.webContents.isLoading() ? false : (await settings()).confirm_exit !== false; } catch (error) { log("Exit check failed:", error); }
  if (!confirm) {
    closeNow(win);
    return;
  }
  win.glossx.promptOpen = true;
  // Native pages draw above the app; the dialog must not sit underneath one.
  bridge.hide(win);
  win.webContents.send("glossx:close-request");
}

function closeNow(win) {
  if (win.glossx.all) for (const other of windows) if (other !== win) { other.glossx.allowClose = true; other.close(); }
  win.glossx.allowClose = true;
  win.close();
}

// The app window an IPC message came from (never a browsed page).
function senderWindow(event) {
  const win = BrowserWindow.fromWebContents(event.sender);
  return win && windows.has(win) && event.sender === win.webContents ? win : null;
}

ipcMain.handle("glossx:open-folder", async (event) => {
  const win = senderWindow(event);
  if (!win) return null;
  bridge.hide(win);
  const result = await dialog.showOpenDialog(win, {
    title: "Open workspace folder", properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0] || null;
});

ipcMain.handle("glossx:exit-cancel", (event) => {
  const win = senderWindow(event);
  if (win) win.glossx.promptOpen = false;
  return true;
});

ipcMain.handle("glossx:exit-confirm", async (event, dontAskAgain) => {
  const win = senderWindow(event);
  if (!win) return false;
  if (dontAskAgain === true) {
    try {
      await fetch(`${backendUrl}/api/settings`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm_exit: false }) });
    } catch (error) { log("Could not save the exit preference:", error); }
  }
  win.glossx.promptOpen = false;
  closeNow(win);
  return true;
});

// The title bar follows the app theme and zoom; the page reports both.
ipcMain.handle("glossx:titlebar", (event, bar) => {
  const win = senderWindow(event);
  if (!win || process.platform === "darwin") return false;
  const color = /^#[0-9a-f]{6}$/i.test(bar?.color) ? bar.color : "#111820";
  const symbolColor = /^#[0-9a-f]{6}$/i.test(bar?.symbolColor) ? bar.symbolColor : "#c9d1d9";
  const height = Number.isFinite(bar?.height) ? Math.min(100, Math.max(20, Math.round(bar.height))) : TITLEBAR_HEIGHT;
  win.setTitleBarOverlay({ color, symbolColor, height });
  return true;
});

// Menu commands that only the window itself can carry out. Zoom is the
// app's own (frontend/src/zoom.ts), so the shell adds none of its own.
ipcMain.handle("glossx:window", (event, action) => {
  const win = senderWindow(event);
  if (!win) return false;
  const contents = win.webContents;
  switch (action) {
    case "undo": case "redo": case "cut": case "copy": case "paste": case "selectAll":
      contents[action]();
      return true;
    case "newWindow": void openWindow(); return true;
    case "fullscreen": win.setFullScreen(!win.isFullScreen()); return true;
    case "devtools": contents.toggleDevTools(); return true;
    case "quit": void quit(win); return true;
    default: return false;
  }
});

// Quit from the menu: every window goes, after the same question as closing the last one.
const quit = (win) => requestClose(win, true);

// -- Windows taskbar jump list (right-click on the taskbar icon), as in VS Code --
function recentPath() {
  return path.join(app.getPath("userData"), "recent.json");
}

function readRecent() {
  try {
    const data = JSON.parse(fs.readFileSync(recentPath(), "utf8"));
    return { papers: Array.isArray(data.papers) ? data.papers : [], projects: Array.isArray(data.projects) ? data.projects : [] };
  } catch {
    return { papers: [], projects: [] };
  }
}

function actionArgs(action, id) {
  const args = [`${ACTION_ARG}${action}${id ? `:${id}` : ""}`];
  // A source checkout runs electron.exe, which needs the app folder first.
  return app.isPackaged ? args : [app.getAppPath(), ...args];
}

function jumpItem(title, description, action, id) {
  // The portable build runs from a temp folder; jump to the .exe the user started.
  const program = process.env.PORTABLE_EXECUTABLE_FILE || process.execPath;
  return {
    type: "task", title: title.slice(0, 120), description: description.slice(0, 260), program,
    args: actionArgs(action, id).map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" "),
    iconPath: program, iconIndex: 0,
  };
}

function updateJumpList() {
  if (process.platform !== "win32" || SMOKE_REPORT) return;
  const recent = readRecent();
  const categories = [];
  if (recent.papers.length) {
    categories.push({ type: "custom", name: "最近阅读的论文 Recent papers", items: recent.papers.map((p) => jumpItem(p.title || p.id, "在 GlossX 中打开这篇论文 / Open this paper", "open-paper", p.id)) });
  }
  if (recent.projects.length) {
    categories.push({ type: "custom", name: "最近的研究项目 Recent projects", items: recent.projects.map((p) => jumpItem(p.title || p.id, "切换到这个项目的工作区 / Switch to this project", "open-project", p.id)) });
  }
  categories.push({
    type: "tasks",
    items: [
      jumpItem("新建研究对话 New research thread", "向智能体提问或布置研究任务", "new-thread"),
      jumpItem("导入论文 Import a paper", "arXiv、DOI、链接或 PDF", "import"),
      jumpItem("论文库 Library", "浏览和筛选已导入的论文", "library"),
      jumpItem("学术搜索 Scholar search", "检索文献与引用", "scholar"),
      jumpItem("新窗口 New window", "再打开一个 GlossX 窗口", "new-window"),
    ],
  });
  try {
    const result = app.setJumpList(categories);
    if (result !== "ok") log("Jump list:", result);
  } catch (error) {
    log("Jump list failed:", error);
  }
}

// The app reports what was just opened; the newest goes first.
ipcMain.handle("glossx:recent", (event, entry) => {
  if (!senderWindow(event) || !entry || !["paper", "project"].includes(entry.kind)) return false;
  const id = String(entry.id || "");
  if (!/^[\w-]{1,64}$/.test(id)) return false;
  const title = String(entry.title || "").replace(/\s+/g, " ").trim().slice(0, 120);
  const recent = readRecent();
  const key = entry.kind === "paper" ? "papers" : "projects";
  if (recent[key][0]?.id === id && recent[key][0]?.title === title) return true;
  recent[key] = [{ id, title }, ...recent[key].filter((x) => x.id !== id)].slice(0, RECENT_LIMIT);
  try {
    fs.mkdirSync(path.dirname(recentPath()), { recursive: true });
    fs.writeFileSync(recentPath(), JSON.stringify(recent));
  } catch (error) { log("Could not save recent items:", error); }
  updateJumpList();
  return true;
});

function parseAction(argv) {
  const raw = argv.find((arg) => arg.startsWith(ACTION_ARG));
  if (!raw) return null;
  const [action, id = ""] = raw.slice(ACTION_ARG.length).split(/:(.*)/s);
  return ACTIONS.has(action) && /^[\w-]{0,64}$/.test(id) ? { action, id } : null;
}

// Run a jump list action in the window the user was working in.
async function runAction(request) {
  if (!request || !backendUrl) return;
  if (request.action === "new-window") { await openWindow(); return; }
  let win = activeWindow();
  if (!win) win = await openWindow();
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.focus();
  const send = () => win.webContents.send("glossx:action", request);
  if (win.webContents.isLoading()) win.webContents.once("did-finish-load", send);
  else send();
}

ipcMain.handle("glossx:browser-show", (event, targetId, rect) => {
  const win = senderWindow(event);
  if (!win || typeof targetId !== "string") return false;
  return bridge.show(win, targetId, rect, win.webContents.getZoomFactor());
});

ipcMain.handle("glossx:browser-hide", (event) => {
  const win = senderWindow(event);
  if (win) bridge.hide(win);
  return true;
});

async function openWindow() {
  if (!backendUrl) return null;
  const win = createWindow(backendUrl);
  await win.webContents.loadURL(backendUrl);
  return win;
}

function createWindow(url) {
  const icon = app.isPackaged ? path.join(process.resourcesPath, "gloss.ico") : path.join(REPO_ROOT, "packaging", "gloss.ico");
  // Each new window opens a little below and right of the current one.
  const from = activeWindow();
  const offset = from && !from.isMaximized() && !from.isFullScreen() ? from.getBounds() : null;
  const win = new BrowserWindow({
    title: "Gloss · 旁注",
    width: offset?.width || 1500,
    height: offset?.height || 950,
    ...(offset ? { x: offset.x + 28, y: offset.y + 28 } : {}),
    minWidth: 1024,
    minHeight: 720,
    backgroundColor: "#111820",
    icon,
    // The app draws its own title bar; the system keeps only its window
    // buttons, drawn over the bar's right end (traffic lights on macOS).
    titleBarStyle: "hidden",
    ...(process.platform === "darwin" ? { trafficLightPosition: { x: 14, y: 13 } } : { titleBarOverlay: { color: "#00000000", symbolColor: "#c9d1d9", height: TITLEBAR_HEIGHT } }),
    show: !SMOKE_REPORT,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false, spellcheck: false },
  });
  win.glossx = { allowClose: false, promptOpen: false, all: false };
  windows.add(win);
  const origin = new URL(url).origin;
  const external = (target) => { if (/^https?:/i.test(target)) void shell.openExternal(target); };
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    // The app opening itself (File > New window in a plain browser) is a new window here too.
    if (new URL(target).origin === origin) void openWindow();
    else external(target);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, target) => {
    if (new URL(target).origin !== origin) { event.preventDefault(); external(target); }
  });
  // Pinch zoom would scale the page under the app's own zoom.
  void win.webContents.setVisualZoomLevelLimits(1, 1);
  // Without an application menu, the window shortcuts are wired here.
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    const mod = input.control || input.meta;
    if (input.key === "F11") win.setFullScreen(!win.isFullScreen());
    else if (mod && input.shift && input.code === "KeyN") void openWindow();
    else return;
    event.preventDefault();
  });
  win.on("close", (event) => {
    if (win.glossx.allowClose || SMOKE_REPORT || quitting) return;
    event.preventDefault();
    void requestClose(win);
  });
  win.on("closed", () => {
    windows.delete(win);
    const next = [...windows].pop();
    if (next) bridge.adopt(win, next);
  });
  return win;
}

// A packaged-build check: drives the shared browser through the backend API,
// exactly as the agent does, and writes what happened to a JSON report.
async function smoke(first) {
  const call = async (method, route, body) => {
    const response = await fetch(`${backendUrl}${route}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json();
    if (!response.ok) throw new Error(`${route}: ${JSON.stringify(value)}`);
    return value;
  };
  const report = { ok: false };
  try {
    const status = await call("GET", "/api/browser/status");
    const created = await call("POST", "/api/sessions", { title: "Desktop browser check" });
    const sid = created.id || created.session?.id;
    await call("POST", `/api/sessions/${sid}/browser/input`, { type: "navigate", url: "about:blank" });
    // GLOSSX_SMOKE_URL also checks a real page (it must be allowed by the guard).
    if (process.env.GLOSSX_SMOKE_URL) {
      await call("POST", `/api/sessions/${sid}/browser/input`, { type: "navigate", url: process.env.GLOSSX_SMOKE_URL });
      report.text = (await call("POST", `/api/sessions/${sid}/browser/input`, { type: "read" })).text?.slice(0, 200);
    }
    const snapshot = await call("POST", `/api/sessions/${sid}/browser/input`, { type: "snapshot" });
    report.snapshot = String(snapshot.text || "").slice(0, 300);
    const tabs = snapshot.tabs || [];
    const shown = bridge.show(first, tabs[0]?.id, { x: 0, y: 0, width: 400, height: 300 });
    Object.assign(report, { ok: status.available === true && tabs.length === 1 && shown && bridge.targets.size === 1, browser: status.browser, tabs: tabs.length, native_pages: bridge.targets.size, electron: process.versions.electron });
  } catch (error) {
    report.error = String(error?.stack || error);
  }
  fs.mkdirSync(path.dirname(SMOKE_REPORT), { recursive: true });
  fs.writeFileSync(SMOKE_REPORT, JSON.stringify(report, null, 2));
  return report.ok ? 0 : 1;
}

async function main() {
  Menu.setApplicationMenu(null);
  try {
    const endpoint = await bridge.start();
    backendUrl = await startBackend(endpoint);
    log("Backend ready at", backendUrl);
    const first = createWindow(backendUrl);
    await first.webContents.loadURL(backendUrl);
    updateJumpList();
    const startAction = parseAction(process.argv);
    if (startAction && startAction.action !== "new-window") void runAction(startAction);
    if (SMOKE_REPORT) {
      const code = await smoke(first);
      quitting = true;
      // Close the pages and the window before exiting, as a normal quit does.
      bridge.close();
      stopBackend();
      for (const win of windows) win.destroy();
      setTimeout(() => app.exit(code), 300);
    }
  } catch (error) {
    log("Startup failed:", error?.stack || error);
    quitting = true;
    stopBackend();
    if (SMOKE_REPORT) {
      fs.mkdirSync(path.dirname(SMOKE_REPORT), { recursive: true });
      fs.writeFileSync(SMOKE_REPORT, JSON.stringify({ ok: false, error: String(error?.stack || error) }, null, 2));
      app.exit(1);
      return;
    }
    dialog.showErrorBox(APP_NAME, `Gloss could not start.\n\n${error?.message || error}\n\nDetails: ${logPath()}`);
    app.quit();
  }
}

// Taskbar grouping and the jump list belong to this app id.
if (process.platform === "win32") app.setAppUserModelId("io.github.computersniper.glossx");

if (process.argv.includes("--version")) {
  process.stdout.write(`${APP_NAME} ${app.getVersion()} (Electron ${process.versions.electron})\n`);
  process.exit(0);
} else if (!SMOKE_REPORT && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Starting GlossX again opens another window on the running app; a jump
  // list entry instead runs its action there.
  app.on("second-instance", (_event, argv) => {
    const request = parseAction(argv);
    if (request) void runAction(request);
    else void openWindow();
  });
  app.whenReady().then(main);
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => { quitting = true; });
  app.on("will-quit", () => { bridge.close(); stopBackend(); });
}
