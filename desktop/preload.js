// The only desktop APIs the app page can reach.
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

ipcRenderer.on("glossx:close-request", () => window.dispatchEvent(new CustomEvent("glossx:close-request")));
ipcRenderer.on("glossx:action", (_event, request) => window.dispatchEvent(new CustomEvent("glossx:action", {
  detail: { action: String(request?.action || ""), id: String(request?.id || "") },
})));

contextBridge.exposeInMainWorld("glossxDesktop", {
  shell: "electron",
  platform: process.platform,
  command: (action) => ipcRenderer.invoke("glossx:window", String(action)),
  openFolder: () => ipcRenderer.invoke("glossx:open-folder"),
  setTitleBar: (bar) => ipcRenderer.invoke("glossx:titlebar", { color: String(bar.color), symbolColor: String(bar.symbolColor), height: Number(bar.height) }),
  recent: (entry) => ipcRenderer.invoke("glossx:recent", { kind: String(entry.kind), id: String(entry.id), title: String(entry.title || "") }),
  cancelExit: () => ipcRenderer.invoke("glossx:exit-cancel"),
  confirmExit: (dontAskAgain) => ipcRenderer.invoke("glossx:exit-confirm", dontAskAgain === true),
  browser: {
    show: (targetId, rect) => ipcRenderer.invoke("glossx:browser-show", String(targetId), {
      x: Number(rect.x), y: Number(rect.y), width: Number(rect.width), height: Number(rect.height),
    }),
    hide: () => ipcRenderer.invoke("glossx:browser-hide"),
  },
});
