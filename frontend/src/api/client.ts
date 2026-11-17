// Typed API client for the Gloss backend, including POST-based SSE streaming.

import { fetchWithTimeout } from "./fetchWithTimeout.mjs";

export type ReadingLens = "auto" | "humanities" | "social_sciences" | "stem";

export interface Paper {
  id: string;
  title: string;
  authors: string[];
  year: string;
  abstract: string;
  source: string;
  arxiv_id: string;
  doi: string;
  n_pages: number;
  tags: string[];
  reading_lens: ReadingLens;
  added_at: number;
}
export interface Chat {
  id: string;
  paper_id: string;
  title: string;
  created_at: number;
}

export type ImportJobStatus =
  | "queued"
  | "downloading"
  | "parsing"
  | "saving"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

export interface ImportJob {
  id: string;
  query: string;
  status: ImportJobStatus;
  progress: number;
  stage_detail: string;
  title: string;
  paper_id: string | null;
  error: string | null;
  created_at: number;
  updated_at: number;
  revision: number;
}

export interface Block {
  id: number;
  bbox: [number, number, number, number];
  text: string;
  size: number;
  bold: boolean;
}
export interface PageInfo {
  index: number;
  width: number;
  height: number;
  blocks: Block[];
  images: number[][];
}
export interface Section {
  title: string;
  page: number;
  block_id: number | null;
  level: number;
}
export interface PagesResponse {
  n_pages: number;
  pages: PageInfo[];
  sections: Section[];
  toc: any[];
}
export interface Highlight {
  id: string;
  paper_id: string;
  page: number;
  rects: [number, number, number, number][];
  color: string;
  category: string;
  text: string;
  note: string;
  kind: string;
}
export type DrawingTool = "pencil" | "pen" | "highlighter";
export interface Drawing {
  id: string;
  paper_id: string;
  page: number;
  points: [number, number][];
  color: string;
  width: number;
  tool: DrawingTool;
  note: string;
  created_at: number;
}
export interface PersonalNote {
  paper_id: string;
  content: string;
  updated_at: number | null;
}
export interface ChatAttachment {
  id: string;
  kind: "pdf_region";
  page: number;
  image_data_url: string;
  extracted_text: string;
  bounds: [number, number, number, number];
}
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  attachments: ChatAttachment[];
}
export interface PluginManifest {
  api_version: 1;
  id: string;
  name: string;
  name_zh?: string;
  version: string;
  author: string;
  icon: string;
  description: string;
  description_zh?: string;
  tab_name: string;
  tab_name_zh?: string;
  contributes: {
    paper_sidebar?: {
      tab_name: string;
      tab_name_zh?: string;
      icon: string;
    };
    configuration?: {
      title?: string;
      title_zh?: string;
      properties?: Record<string, PluginConfigurationProperty>;
    };
  };
  agent?: PluginAgent;
  permissions: string[];
  requirements: string[];
  prompt: string;
  output: "markdown" | "visual";
  builtin: boolean;
  installed?: boolean;
}
export interface PluginAgent {
  name?: string;
  name_zh?: string;
  icon?: string;
  messages?: Record<string, string | { en?: string; zh?: string }>;
}
export interface PluginConfigurationProperty {
  type?: "boolean" | "string" | "number" | "integer" | "array";
  title?: string;
  title_zh?: string;
  description?: string;
  description_zh?: string;
  markdownDescription?: string;
  default?: unknown;
  enum?: Array<string | number>;
  enumDescriptions?: string[];
  markdownEnumDescriptions?: string[];
  enumItemLabels?: string[];
  enumItemLabels_zh?: string[];
  items?: { type?: string };
  minimum?: number;
  maximum?: number;
  order?: number;
}
export interface PluginRunResponse {
  status: "ready" | "unavailable";
  markdown: string;
  reason: { code: string; requirement: string } | null;
  research?: {
    searched_at: string;
    queries: string[];
    providers: string[];
    source_count: number;
    partial?: boolean;
    failed_providers?: string[];
  };
  visual_explanation?: VisualExplanation;
}
export interface VisualExplanationImage {
  url: string;
  media_type: "image/png" | "image/jpeg" | "image/webp";
  alt: string;
  caption: string;
  heading: string;
  frame: number;
}
export interface VisualExplanation {
  style: "diagram" | "animated" | "teacher";
  image_provider: "codex" | "openai";
  model: "gpt-image-2" | string;
  quality: "low" | "medium" | "high";
  frame_count: number;
  images: VisualExplanationImage[];
}
export interface CoreExtension {
  id: string;
  name: string;
  name_zh: string;
  icon: string;
  description: string;
  description_zh: string;
  builtin: true;
}
export interface PluginContributionPoint {
  id: string;
  status: "stable" | "planned";
  description: string;
}
export interface ComingSoonExtension {
  id: string;
  name: string;
  name_zh: string;
  icon: string;
  description: string;
  description_zh: string;
  planned_contribution: string;
}
export interface PluginSnapshot {
  api_version: number;
  permissions: string[];
  contribution_points: PluginContributionPoint[];
  core: CoreExtension[];
  marketplace: PluginManifest[];
  installed: PluginManifest[];
  coming_soon: ComingSoonExtension[];
}
export type AiTaskStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";
export interface AiTaskAgent {
  name?: string;
  name_zh?: string;
  icon?: string;
  messages?: Record<string, string | { en?: string; zh?: string }>;
}
export interface AiTaskSnapshot {
  id: string;
  feature_id: string;
  paper_id?: string | null;
  status: AiTaskStatus;
  progress: number;
  stage?: string;
  detail?: string;
  agent?: AiTaskAgent | string | null;
  result?: unknown;
  error?: string | null;
  created_at?: number | string;
  started_at?: number | string | null;
  updated_at?: number | string;
}
export interface CreateAiTaskRequest {
  feature_id: string;
  paper_id?: string;
  refresh?: boolean;
  language?: string;
  input?: Record<string, unknown> | string;
}
export interface AiArtifactRequest {
  feature_id: string;
  paper_id: string;
  language?: string;
  input?: Record<string, unknown>;
}
export interface AiArtifactResponse {
  found: boolean;
  result?: unknown;
}
export interface Reference {
  id: string;
  idx: number;
  raw: string;
  title: string;
  authors: string[];
  year: string;
  doi: string;
  arxiv_id: string;
  url: string;
  abstract: string;
  resolved: number;
}
export interface Skill {
  id: string;
  name: string;
  source: string;
  type: string;
  description: string;
  argument_hint: string;
  allowed_tools: string[];
}
export type ProviderConnectionState = "unknown" | "checking" | "connected" | "error";
export interface ProviderConnectionStatus {
  status: ProviderConnectionState;
  connected: boolean;
  error: string;
  checked_at: number | null;
}
export interface ProviderTestResult {
  ok: boolean;
  provider: string;
  latency_ms?: number;
  reply?: string;
  error?: string;
}
export type DoctorStatus = "pass" | "warning" | "fail";
export interface DoctorReport {
  status: DoctorStatus;
  checked_at: number;
  version: string;
  summary: { passed: number; warnings: number; failed: number };
  system: {
    platform: string;
    python: string;
    pid: number;
    packaged: boolean;
    data_dir: string;
    cache_dir: string;
    frontend_dist: string;
    active_provider: string;
    paper_count: number;
    plugin_count: number;
  };
  checks: Array<{ id: string; status: DoctorStatus; detail: string }>;
}
export interface AiUsageAggregate {
  cached_input_tokens?: number;
  cache_write_tokens?: number;
  reasoning_tokens?: number;
  cache_hit_rate?: number;
  tasks: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  average_total_tokens: number;
  estimated_tasks: number;
}
export interface AiUsageRecord {
  id: string;
  task_type: string;
  provider: string;
  model: string | null;
  paper_id: string | null;
  plugin_id: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated: boolean;
  created_at: number;
}
export interface AiUsageSummary {
  by_provider?: (AiUsageAggregate & { provider: string })[];
  by_day?: (AiUsageAggregate & { day: string })[];
  total: AiUsageAggregate;
  by_task: (AiUsageAggregate & { task_type: string })[];
  recent: AiUsageRecord[];
}
export interface Summary {
  tldr: string;
  problem: string;
  method: string;
  results: string;
  contributions: string[];
  key_points: string[];
  limitations: string[];
}
export interface MindNode {
  title: string;
  kind?: string;
  summary?: string;
  children?: MindNode[];
}
export interface TransSentence {
  page: number;
  original: string;
  translation: string;
}
export interface TexSection {
  title: string;
  count: number;
  done: number;
  units: { original: string; translation: string }[];
}
export interface ScholarResult {
  title: string;
  abstract: string;
  year: any;
  authors: string[];
  url: string;
  arxiv_id: string;
  doi: string;
  source: string;
  citations?: number;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status}: ${body.slice(0, 300)}`);
  }
  return r.json();
}

export const api = {
  health: () => fetch("/api/health").then((r) => j<any>(r)),
  runDoctor: () =>
    fetch("/api/doctor", { method: "POST" }).then((r) => j<DoctorReport>(r)),

  listPapers: () => fetch("/api/papers").then((r) => j<{ papers: Paper[] }>(r)),
  getPaper: (id: string) => fetch(`/api/papers/${id}`).then((r) => j<Paper>(r)),
  updatePaper: (id: string, fields: Partial<Pick<Paper, "title" | "tags" | "year" | "reading_lens">>) =>
    fetch(`/api/papers/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(fields),
    }).then((r) => j<Paper>(r)),
  deletePaper: (id: string) =>
    fetch(`/api/papers/${id}`, { method: "DELETE" }).then((r) => j<any>(r)),
  uploadPaper: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/papers/upload", { method: "POST", body: fd }).then((r) => j<Paper>(r));
  },
  importPaper: (query: string) =>
    fetchWithTimeout("/api/papers/import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query }),
    }, 300_000, (r) => j<Paper>(r)),
  enqueueImport: (query: string) =>
    fetchWithTimeout(
      "/api/papers/import-jobs",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query }),
      },
      15_000,
      (r) => j<ImportJob>(r),
    ),
  listImportJobs: () =>
    fetchWithTimeout(
      "/api/papers/import-jobs",
      {},
      10_000,
      (r) => j<{ jobs: ImportJob[] }>(r),
    ),
  cancelImportJob: (id: string) =>
    fetch(`/api/papers/import-jobs/${id}`, { method: "DELETE" }).then((r) => j<{ ok: boolean }>(r)),
  pdfUrl: (id: string) => `/api/papers/${id}/pdf`,
  getPages: (id: string) => fetch(`/api/papers/${id}/pages`).then((r) => j<PagesResponse>(r)),

  summarize: (id: string, opts: { refresh?: boolean; language?: string } = {}) =>
    fetch(`/api/papers/${id}/summarize`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(opts),
    }).then((r) => j<Summary>(r)),

  explain: (body: { paper_id?: string; selection: string; context?: string; language?: string }) =>
    fetch("/api/explain", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<{ explanation: string }>(r)),

  translateText: (text: string, language?: string) =>
    fetch("/api/translate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, language }),
    }).then((r) => j<{ translation: string }>(r)),
  translatePages: (paper_id: string, page_start: number, page_end: number, language?: string) =>
    fetch("/api/translate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ paper_id, page_start, page_end, language }),
    }).then((r) => j<{ sentences: TransSentence[] }>(r)),
  getTex: (paper_id: string) =>
    fetch(`/api/papers/${paper_id}/tex`).then((r) =>
      j<{ available: boolean; reason?: string; main: string | null; files: { name: string; tex: string }[] }>(r),
    ),
  locate: (paper_id: string, text: string) =>
    fetch(`/api/papers/${paper_id}/locate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => j<{ page: number | null; rects: [number, number, number, number][] }>(r)),
  getTranslations: (paper_id: string, language?: string) =>
    fetch(`/api/papers/${paper_id}/translations${language ? `?lang=${encodeURIComponent(language)}` : ""}`)
      .then((r) => j<{ sentences: TransSentence[]; pages: number[] }>(r)),
  getTexSections: (paper_id: string, language?: string) =>
    fetch(`/api/papers/${paper_id}/tex_sections${language ? `?lang=${encodeURIComponent(language)}` : ""}`)
      .then((r) => j<{ sections: TexSection[] }>(r)),
  translateTex: (paper_id: string, section: number | null, language?: string) =>
    fetch(`/api/papers/${paper_id}/translate_tex`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ section, language }),
    }).then((r) => j<{ sections: TexSection[] }>(r)),

  autohighlight: (id: string) =>
    fetch(`/api/papers/${id}/autohighlight`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    }).then((r) => j<{ highlights: Highlight[] }>(r)),

  mindmap: (id: string, refresh = false) =>
    fetch(`/api/papers/${id}/mindmap`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh }),
    }).then((r) => j<{ tree: MindNode }>(r)),

  notes: (id: string, refresh = false) =>
    fetch(`/api/papers/${id}/notes`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh }),
    }).then((r) => j<{ markdown: string }>(r)),

  listHighlights: (id: string) =>
    fetch(`/api/papers/${id}/highlights`).then((r) => j<{ highlights: Highlight[] }>(r)),
  addHighlight: (id: string, h: Partial<Highlight>) =>
    fetch(`/api/papers/${id}/highlights`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(h),
    }).then((r) => j<Highlight>(r)),
  deleteHighlight: (hid: string) =>
    fetch(`/api/highlights/${hid}`, { method: "DELETE" }).then((r) => j<any>(r)),
  patchHighlight: (hid: string, fields: any) =>
    fetch(`/api/highlights/${hid}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(fields),
    }).then((r) => j<Highlight>(r)),
  listDrawings: (id: string) =>
    fetch(`/api/papers/${id}/drawings`).then((r) => j<{ drawings: Drawing[] }>(r)),
  addDrawing: (id: string, drawing: Pick<Drawing, "page" | "points" | "color" | "width" | "tool">) =>
    fetch(`/api/papers/${id}/drawings`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(drawing),
    }).then((r) => j<Drawing>(r)),
  deleteDrawing: (drawingId: string) =>
    fetch(`/api/drawings/${drawingId}`, { method: "DELETE" }).then((r) => j<any>(r)),
  getPersonalNote: (id: string) =>
    fetch(`/api/papers/${id}/personal-note`).then((r) => j<PersonalNote>(r)),
  savePersonalNote: (id: string, content: string) =>
    fetch(`/api/papers/${id}/personal-note`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content }),
    }).then((r) => j<PersonalNote>(r)),

  listPlugins: () => fetch("/api/plugins").then((r) => j<PluginSnapshot>(r)),
  getPluginManifestSchema: () => fetch("/api/plugins/schema").then((r) => j<Record<string, unknown>>(r)),
  getPluginManifestTemplate: () => fetch("/api/plugins/template").then((r) => j<Record<string, unknown>>(r)),
  installMarketplacePlugin: (pluginId: string) =>
    fetch(`/api/plugins/${encodeURIComponent(pluginId)}/install`, { method: "POST" })
      .then((r) => j<PluginManifest>(r)),
  installPluginManifest: (manifest: Record<string, unknown>) =>
    fetch("/api/plugins/install", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ manifest }),
    }).then((r) => j<PluginManifest>(r)),
  uninstallPlugin: (pluginId: string) =>
    fetch(`/api/plugins/${encodeURIComponent(pluginId)}`, { method: "DELETE" })
      .then((r) => j<any>(r)),
  runPlugin: (pluginId: string, paperId: string, refresh = false) =>
    fetch(`/api/plugins/${encodeURIComponent(pluginId)}/papers/${paperId}/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh }),
    }).then((r) => j<PluginRunResponse>(r)),
  createAiTask: (request: CreateAiTaskRequest) =>
    fetch("/api/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
    }).then((r) => j<AiTaskSnapshot>(r)),
  getAiTask: (id: string) =>
    fetch(`/api/tasks/${encodeURIComponent(id)}`).then((r) => j<AiTaskSnapshot>(r)),
  cancelAiTask: (id: string) =>
    fetch(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => j<AiTaskSnapshot>(r)),
  getAiArtifact: (request: AiArtifactRequest) => {
    const query = new URLSearchParams({
      feature_id: request.feature_id,
      paper_id: request.paper_id,
    });
    if (request.language) query.set("language", request.language);
    const configuration = request.input?.configuration;
    if (configuration && typeof configuration === "object") {
      query.set("configuration", JSON.stringify(configuration));
    }
    return fetch(`/api/tasks/artifact?${query}`).then((r) => j<AiArtifactResponse>(r));
  },
  aiTaskEventsUrl: (id: string) => `/api/tasks/${encodeURIComponent(id)}/events`,

  getReferences: (id: string) =>
    fetch(`/api/papers/${id}/references`).then((r) => j<{ references: Reference[] }>(r)),
  resolveReferences: (id: string) =>
    fetch(`/api/papers/${id}/references/resolve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enrich: true }),
    }).then((r) => j<{ references: Reference[] }>(r)),

  scholarSearch: (q: string, k = 10) =>
    fetch(`/api/scholar/search?q=${encodeURIComponent(q)}&k=${k}`).then((r) =>
      j<{ results: ScholarResult[] }>(r),
    ),
  scholarRecommend: (id: string, k = 10) =>
    fetch(`/api/scholar/recommend?paper_id=${id}&k=${k}`).then((r) =>
      j<{ results: ScholarResult[] }>(r),
    ),

  // per-paper chat history
  listChats: (paperId: string) =>
    fetch(`/api/papers/${paperId}/chats`).then((r) => j<{ chats: Chat[] }>(r)),
  createChat: (paperId: string, title = "Chat") =>
    fetch(`/api/papers/${paperId}/chats`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    }).then((r) => j<Chat>(r)),
  getChatMessages: (chatId: string) =>
    fetch(`/api/chats/${chatId}/messages`).then((r) =>
      j<{ messages: ChatMessage[] }>(r),
    ),
  updateChatTitle: (chatId: string, title: string) =>
    fetch(`/api/chats/${chatId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    }).then((r) => j<Chat>(r)),
  deleteChat: (chatId: string) =>
    fetch(`/api/chats/${chatId}`, { method: "DELETE" }).then((r) => j<any>(r)),

  listSkills: () => fetch("/api/skills").then((r) => j<{ skills: Skill[]; count: number }>(r)),

  testProvider: (provider: string) =>
    fetch("/api/settings/test", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ provider }),
    }).then((r) => j<ProviderTestResult>(r)),

  detectLocalCli: (refresh = false) => fetch(`/api/settings/local-cli?refresh=${refresh}`).then((r) => j<{ tools: Record<string, {path: string; version: string | null; source: string}[]> }>(r)),
  getSettings: () =>
    fetch("/api/settings").then((r) => j<{
      config: any;
      available_providers: string[];
      all_providers?: string[];
      provider_statuses: Record<string, ProviderConnectionStatus>;
    }>(r)),
  getProviderStatuses: () =>
    fetch("/api/settings/status").then((r) =>
      j<{ provider_statuses: Record<string, ProviderConnectionStatus> }>(r),
    ),
  getAiUsage: (opts: { paper_id?: string; task_type?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (opts.paper_id) query.set("paper_id", opts.paper_id);
    if (opts.task_type) query.set("task_type", opts.task_type);
    if (opts.limit) query.set("limit", String(opts.limit));
    const suffix = query.size ? `?${query.toString()}` : "";
    return fetch(`/api/usage${suffix}`).then((r) => j<AiUsageSummary>(r));
  },
  updateSettings: (patch: any) =>
    fetch("/api/settings", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    }).then((r) => j<any>(r)),
};

// POST-based SSE: reads a text/event-stream, parses `data: {json}` frames.
export async function streamPost(
  url: string,
  body: any,
  handlers: { onDelta?: (s: string) => void; onError?: (s: string) => void; onDone?: () => void },
  signal?: AbortSignal,
) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    handlers.onError?.(`HTTP ${resp.status}`);
    handlers.onDone?.();
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() || "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      try {
        const d = JSON.parse(line.slice(5).trim());
        if (d.delta) handlers.onDelta?.(d.delta);
        if (d.error) handlers.onError?.(d.error);
        if (d.done) handlers.onDone?.();
      } catch {
        /* ignore partial */
      }
    }
  }
  handlers.onDone?.();
}
