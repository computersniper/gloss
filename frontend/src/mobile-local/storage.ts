export type Message = { role: "user" | "assistant"; content: string };
export type Conversation = { id: string; title: string; archived: boolean; messages: Message[] };
export type Settings = { baseUrl: string; model: string; apiKey: string; rememberKey: boolean };
const KEY = "glossx.mobile.local.v1";
export function loadState(): { conversations: Conversation[]; settings: Settings } {
  const defaults = { baseUrl: "https://api.openai.com/v1", model: "gpt-4.1-mini", apiKey: "", rememberKey: false };
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    return { conversations: Array.isArray(saved?.conversations) ? saved.conversations : [], settings: { ...defaults, ...saved?.settings } };
  } catch { return { conversations: [], settings: defaults }; }
}
export function saveState(conversations: Conversation[], settings: Settings) {
  localStorage.setItem(KEY, JSON.stringify({ conversations, settings: { ...settings, apiKey: settings.rememberKey ? settings.apiKey : "" } }));
}
export type LocalPaper = { id: string; name: string; bytes: ArrayBuffer; text: string; pages: number; truncated: boolean };
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("glossx-mobile-papers", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("papers", { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("无法打开设备上的论文存储"));
  });
}
export async function papers(operation: "list" | "put" | "delete", value?: LocalPaper | string): Promise<LocalPaper[]> {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("papers", operation === "list" ? "readonly" : "readwrite");
      const store = tx.objectStore("papers");
      const request = operation === "list" ? store.getAll() : operation === "put" ? store.put(value) : store.delete(value as string);
      let result: LocalPaper[] = [];
      request.onsuccess = () => { if (operation === "list") result = request.result as LocalPaper[]; };
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(new Error("保存论文失败，设备存储空间可能不足"));
      tx.onabort = () => reject(new Error("论文存储操作已取消"));
    });
  } finally { db.close(); }
}
