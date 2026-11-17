// A Chrome DevTools Protocol endpoint backed by Electron's own Chromium.
//
// The Python backend drives its shared browser over CDP. In the desktop app it
// connects here instead of launching Edge or Chrome: every page is a
// WebContentsView inside the GlossX window, so the browser always exists, and
// the right-hand pane shows the real page instead of a screencast.
"use strict";

const crypto = require("node:crypto");
const { session, WebContentsView } = require("electron");
const { WebSocketServer } = require("ws");

const id = () => crypto.randomBytes(12).toString("hex");

// Electron and app tokens make some sites serve a degraded page or refuse
// sign-in; present the plain Chromium user agent instead.
function plainUserAgent(agent) {
  return agent.replace(/\s(Electron|glossx-desktop|GlossX)\/\S+/g, "");
}

class BrowserBridge {
  constructor(getWindow) {
    this.getWindow = getWindow;
    this.token = id();
    this.contexts = new Map(); // browserContextId -> { partition, session }
    this.targets = new Map(); // targetId -> { view, window, contextId, sessionId }
    this.sessions = new Map(); // sessionId -> targetId
    this.socket = null;
  }

  start() {
    return new Promise((resolve, reject) => {
      this.server = new WebSocketServer({ host: "127.0.0.1", port: 0, maxPayload: 64 * 1024 * 1024 });
      this.server.once("error", reject);
      this.server.on("listening", () => {
        const { port } = this.server.address();
        this.url = `ws://127.0.0.1:${port}/devtools/browser/${this.token}`;
        resolve(this.url);
      });
      this.server.on("connection", (socket, request) => {
        // Only the backend this app started knows the token.
        if (request.url !== `/devtools/browser/${this.token}`) return socket.close(1008, "Unknown endpoint");
        if (this.socket) this.reset();
        this.socket = socket;
        socket.on("message", (raw) => void this.receive(socket, raw));
        socket.on("close", () => { if (this.socket === socket) { this.socket = null; this.reset(); } });
      });
    });
  }

  close() {
    this.reset();
    this.server?.close();
  }

  send(message) {
    if (this.socket?.readyState === 1) this.socket.send(JSON.stringify(message));
  }

  async receive(socket, raw) {
    let message;
    try { message = JSON.parse(raw); } catch { return; }
    const { id: commandId, method, params = {}, sessionId } = message;
    try {
      const result = sessionId ? await this.page(sessionId, method, params) : await this.browser(method, params);
      if (socket === this.socket) this.send({ id: commandId, result: result || {}, ...(sessionId ? { sessionId } : {}) });
    } catch (error) {
      if (socket === this.socket) this.send({ id: commandId, error: { code: -32000, message: String(error?.message || error) }, ...(sessionId ? { sessionId } : {}) });
    }
  }

  async browser(method, params) {
    switch (method) {
      case "Browser.getVersion":
        return { product: `Electron/${process.versions.electron}`, protocolVersion: "1.3" };
      case "Target.setAutoAttach":
      case "Browser.setDownloadBehavior":
        // Pages are only ever created here, and downloads are cancelled per session.
        return {};
      case "Target.getTargets":
        return { targetInfos: [...this.targets.keys()].map((targetId) => this.info(targetId)) };
      case "Target.createBrowserContext":
        return this.createContext(params);
      case "Target.disposeBrowserContext":
        return this.disposeContext(params.browserContextId);
      case "Target.createTarget":
        return this.createTarget(params);
      case "Target.closeTarget":
        this.closeTarget(params.targetId);
        return { success: true };
      default:
        throw new Error(`${method} is not supported by the GlossX browser`);
    }
  }

  async page(sessionId, method, params) {
    const targetId = this.sessions.get(sessionId);
    const target = targetId && this.targets.get(targetId);
    if (!target) throw new Error("No target with given id found");
    // The page is sized by the pane that shows it; emulating a desktop
    // viewport would draw a page that does not fit the view.
    if (method === "Emulation.setDeviceMetricsOverride" || method === "Runtime.runIfWaitingForDebugger") return {};
    return target.view.webContents.debugger.sendCommand(method, params);
  }

  async createContext(params) {
    const contextId = id();
    // A partition without "persist:" lives in memory only, so each thread's
    // cookies are isolated and gone when the thread or the app closes.
    const partition = `glossx-browser-${contextId}`;
    const ses = session.fromPartition(partition);
    ses.setUserAgent(plainUserAgent(ses.getUserAgent()));
    if (params.proxyServer) await ses.setProxy({ proxyRules: params.proxyServer, proxyBypassRules: params.proxyBypassList || "<-loopback>" });
    ses.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    ses.setPermissionCheckHandler(() => false);
    ses.on("will-download", (event, item) => { event.preventDefault(); item.cancel(); });
    this.contexts.set(contextId, { partition, session: ses });
    return { browserContextId: contextId };
  }

  async disposeContext(contextId) {
    const context = this.contexts.get(contextId);
    if (!context) return {};
    for (const [targetId, target] of this.targets) if (target.contextId === contextId) this.closeTarget(targetId);
    this.contexts.delete(contextId);
    await context.session.clearStorageData().catch(() => {});
    return {};
  }

  async createTarget(params) {
    const contextId = params.browserContextId;
    const context = this.contexts.get(contextId);
    if (!context) throw new Error("Unknown browser context");
    const window = this.getWindow();
    if (!window) throw new Error("The GlossX window is not open");
    const view = new WebContentsView({
      webPreferences: { partition: context.partition, sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false, spellcheck: false },
    });
    const contents = view.webContents;
    contents.setWindowOpenHandler(() => ({ action: "deny" }));
    contents.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
    view.setVisible(false);
    window.contentView.addChildView(view);
    const targetId = id().toUpperCase();
    const sessionId = id().toUpperCase();
    contents.debugger.attach("1.3");
    contents.debugger.on("message", (_event, method, eventParams) => this.send({ method, params: eventParams, sessionId }));
    contents.debugger.on("detach", () => {
      if (this.targets.has(targetId)) this.closeTarget(targetId);
    });
    this.targets.set(targetId, { view, window, contextId, sessionId });
    this.sessions.set(sessionId, targetId);
    this.send({ method: "Target.attachedToTarget", params: { sessionId, targetInfo: this.info(targetId), waitingForDebugger: false } });
    await contents.loadURL(params.url || "about:blank").catch(() => {});
    return { targetId };
  }

  closeTarget(targetId) {
    const target = this.targets.get(targetId);
    if (!target) return;
    this.targets.delete(targetId);
    this.sessions.delete(target.sessionId);
    const contents = target.view.webContents;
    try { if (contents.debugger.isAttached()) contents.debugger.detach(); } catch {}
    if (!target.window.isDestroyed()) target.window.contentView.removeChildView(target.view);
    if (!contents.isDestroyed()) contents.close();
    this.send({ method: "Target.detachedFromTarget", params: { sessionId: target.sessionId, targetId } });
  }

  info(targetId) {
    const target = this.targets.get(targetId);
    const contents = target.view.webContents;
    return { targetId, type: "page", title: contents.getTitle(), url: contents.getURL(), attached: true, browserContextId: target.contextId };
  }

  // Place a page over the rectangle a window's pane reserves (CSS pixels of
  // that window's app page). With several windows open, the page moves to
  // whichever window shows it last.
  show(window, targetId, rect, zoom = 1) {
    this.hide(window, targetId);
    const target = this.targets.get(targetId);
    if (!target || !rect || rect.width < 1 || rect.height < 1) return false;
    if (target.window !== window) {
      target.view.setVisible(false);
      if (!target.window.isDestroyed()) target.window.contentView.removeChildView(target.view);
      window.contentView.addChildView(target.view);
      target.window = window;
    }
    target.view.setBounds({ x: Math.round(rect.x * zoom), y: Math.round(rect.y * zoom), width: Math.round(rect.width * zoom), height: Math.round(rect.height * zoom) });
    target.view.setVisible(true);
    return true;
  }

  // Hide the pages placed in a window (all windows when none is given).
  hide(window = null, except = null) {
    for (const [targetId, target] of this.targets) {
      if (targetId !== except && (!window || target.window === window)) target.view.setVisible(false);
    }
  }

  // A window is closing while others stay open: its pages live on in another.
  adopt(from, to) {
    for (const target of this.targets.values()) {
      if (target.window !== from) continue;
      target.view.setVisible(false);
      if (!from.isDestroyed()) from.contentView.removeChildView(target.view);
      to.contentView.addChildView(target.view);
      target.window = to;
    }
  }

  reset() {
    for (const targetId of [...this.targets.keys()]) this.closeTarget(targetId);
    for (const { session: ses } of this.contexts.values()) ses.clearStorageData().catch(() => {});
    this.contexts.clear();
  }
}

module.exports = { BrowserBridge, plainUserAgent };
