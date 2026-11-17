// Pure reducer for a session's SSE event stream (docs/glossx-harness.md §7).
// Kept free of DOM/React so it can be unit-tested with `node --test`.

export const EVENT_NAMES = [
  "message.created", "message.delta", "message.completed", "tool.updated", "todos.updated",
  "approval.requested", "approval.resolved", "input.requested", "input.resolved", "context.updated", "session.updated",
  "turn.completed", "error", "browser.updated", "usage.updated", "message.progress",
];

const TERMINAL_TOOL = new Set(["done", "error", "denied"]);

export function emptySessionState() {
  return {
    session: null,
    messages: [],
    todos: [],
    context: { tokens_est: 0, limit: 0, compactions: 0 },
    status: "idle",
    approvals: [],
    input: null,
    error: null,
    loaded: false,
  };
}

/** Message objects may omit `type` for tool calls (spec lists them by shape). */
export function normalizeMessage(raw) {
  if (!raw || typeof raw !== "object") return null;
  const m = { ...raw };
  if (!m.type) m.type = m.role || (m.tool ? "tool_call" : "assistant");
  if (m.type === "tool" || m.type === "tool_use") m.type = "tool_call";
  if (m.type === "system") m.type = "system_note";
  if (m.id == null) return null;
  m.id = String(m.id);
  if (m.type === "tool_call") {
    m.args = m.args && typeof m.args === "object" ? m.args : {};
    m.status = m.status || "pending";
  } else {
    m.text = typeof m.text === "string" ? m.text : m.text == null ? "" : String(m.text);
  }
  return m;
}

export function normalizeTodos(raw) {
  const list = Array.isArray(raw) ? raw : Array.isArray(raw?.todos) ? raw.todos : [];
  return list
    .map((t, i) => {
      if (typeof t === "string") return { id: String(i), text: t, status: "pending" };
      if (!t || typeof t !== "object") return null;
      const status = String(t.status || (t.done ? "completed" : "pending")).toLowerCase();
      return {
        id: String(t.id ?? i),
        text: String(t.text ?? t.content ?? t.title ?? ""),
        active: t.active_form || t.activeForm || "",
        status: status === "done" ? "completed" : status === "doing" || status === "active" ? "in_progress" : status,
      };
    })
    .filter((t) => t && t.text);
}

function normalizeInput(raw) {
  if (!raw || typeof raw !== "object") return null;
  return { ...raw, question: String(raw.question ?? ""), choices: Array.isArray(raw.choices) ? raw.choices.map(String) : [] };
}

function upsert(messages, msg) {
  const i = messages.findIndex((m) => m.id === msg.id);
  if (i < 0) return [...messages, msg];
  const next = messages.slice();
  next[i] = { ...messages[i], ...msg };
  return next;
}

function unwrap(payload, key) {
  return payload && typeof payload === "object" && payload[key] && typeof payload[key] === "object" ? payload[key] : payload;
}

/** `turn.completed` may say the turn's changes were auto-committed: `commit: {id, short, message}` (or just an id). */
export function commitOf(p) {
  const raw = p?.commit ?? p?.auto_commit ?? p?.vcs?.commit;
  if (!raw || raw === true) return null;
  const c = typeof raw === "string" ? { id: raw } : raw;
  const id = String(c.id ?? c.sha ?? c.short ?? "");
  if (!id) return null;
  return { ...c, id, short: String(c.short ?? id.slice(0, 7)), message: typeof c.message === "string" ? c.message : "" };
}

/** Apply one event; returns the same object when nothing changed. */
export function applySessionEvent(state, name, payload) {
  const p = payload ?? {};
  switch (name) {
    case "browser.updated": return { ...state, browser: p };
    case "usage.updated": return { ...state, session: { ...(state.session || {}), usage: p.usage || p } };
    case "session.updated": {
      const s = unwrap(p, "session");
      if (!s || typeof s !== "object") return state;
      const { messages, todos, pending_approvals, pending_input, ...summary } = s;
      const next = { ...state, session: { ...(state.session || {}), ...summary }, loaded: true };
      if (summary.status) next.status = summary.status;
      if (summary.context) next.context = { ...state.context, ...summary.context };
      // `messages` comes only on replay and after /clear: it replaces the list (an unacknowledged local echo survives)
      if (Array.isArray(messages)) next.messages = [...messages.map(normalizeMessage).filter(Boolean), ...state.messages.filter((m) => m.pending)];
      if (Array.isArray(todos)) next.todos = normalizeTodos(todos);
      if (Array.isArray(pending_approvals)) next.approvals = pending_approvals;
      if (pending_input !== undefined) next.input = normalizeInput(pending_input);
      if (next.status === "idle") {
        next.approvals = Array.isArray(pending_approvals) ? pending_approvals : [];
        next.input = normalizeInput(pending_input);
      }
      return next;
    }
    case "message.created": {
      const m = normalizeMessage(unwrap(p, "message"));
      if (!m) return state;
      if (m.type === "assistant" && m.streaming === undefined) m.streaming = true;
      // a locally echoed user message (`pending`) is replaced by the server's copy.
      // NB: the server's own `local: true` marks a slash command that ran without a model turn.
      const messages = m.type === "user" ? state.messages.filter((x) => !x.pending) : state.messages;
      return { ...state, messages: upsert(messages, m), error: m.type === "user" ? null : state.error };
    }
    case "message.delta": {
      const id = String(p.id ?? p.message_id ?? "");
      const delta = typeof p.text === "string" ? p.text : typeof p.delta === "string" ? p.delta : "";
      if (!id || !delta) return state;
      const i = state.messages.findIndex((m) => m.id === id);
      const messages = state.messages.slice();
      if (i < 0) messages.push({ id, type: "assistant", text: delta, streaming: true });
      else {
        // `offset` (when sent) makes deltas idempotent across a replay/reconnect
        const prev = messages[i].text || "";
        const base = typeof p.offset === "number" && p.offset >= 0 && p.offset <= prev.length ? prev.slice(0, p.offset) : prev;
        messages[i] = { ...messages[i], text: base + delta, streaming: true };
      }
      return { ...state, messages };
    }
    case "message.progress": {
      // what the model is doing while it works: reasoning text and steps
      const id = String(p.id ?? "");
      const text = typeof p.text === "string" ? p.text : "";
      const i = state.messages.findIndex((m) => m.id === id);
      if (i < 0 || !text) return state;
      const messages = state.messages.slice();
      const prev = messages[i];
      const at = typeof p.at === "number" && p.at >= 0 ? p.at : null;
      if (p.kind === "thinking") {
        const before = prev.thinking || "";
        if (at !== null && at < before.length) return state; // already have it (replay)
        messages[i] = { ...prev, thinking: before + text };
      } else if (p.kind === "step") {
        const steps = prev.steps || [];
        if (at !== null && at < steps.length) return state;
        messages[i] = { ...prev, steps: [...steps, text] };
      } else return state;
      return { ...state, messages };
    }
    case "message.completed": {
      const raw = unwrap(p, "message");
      const id = String(raw?.id ?? p.id ?? "");
      if (!id) return state;
      const i = state.messages.findIndex((m) => m.id === id);
      const full = normalizeMessage({ type: "assistant", ...raw, id });
      if (i < 0) return full && full.text ? { ...state, messages: [...state.messages, { ...full, streaming: false }] } : state;
      const messages = state.messages.slice();
      const prev = messages[i];
      messages[i] = { ...prev, ...full, text: typeof raw?.text === "string" ? full.text : prev.text, type: prev.type, streaming: false };
      return { ...state, messages };
    }
    case "tool.updated": {
      const m = normalizeMessage({ ...unwrap(p, "tool_call"), type: "tool_call" });
      if (!m) return state;
      let approvals = state.approvals;
      if (m.status !== "awaiting_approval") approvals = approvals.filter((a) => String(a.tool_call_id) !== m.id);
      let input = state.input;
      if (input && String(input.tool_call_id) === m.id && TERMINAL_TOOL.has(m.status)) input = null;
      return { ...state, messages: upsert(state.messages, m), approvals, input };
    }
    case "todos.updated":
      return { ...state, todos: normalizeTodos(p) };
    case "approval.requested": {
      if (p.tool_call_id == null) return state;
      const id = String(p.tool_call_id);
      return {
        ...state,
        status: state.status === "awaiting_input" ? state.status : "awaiting_approval",
        approvals: [...state.approvals.filter((a) => String(a.tool_call_id) !== id), { ...p, tool_call_id: id }],
      };
    }
    case "approval.resolved": {
      const id = String(p.tool_call_id ?? "");
      const approvals = state.approvals.filter((a) => String(a.tool_call_id) !== id);
      return approvals.length === state.approvals.length ? state : { ...state, approvals };
    }
    case "input.resolved":
      return state.input && (!p.tool_call_id || String(state.input.tool_call_id) === String(p.tool_call_id)) ? { ...state, input: null } : state;
    case "input.requested":
      return { ...state, status: "awaiting_input", input: normalizeInput(p) };
    case "context.updated": {
      const c = unwrap(p, "context");
      return { ...state, context: { ...state.context, ...c } };
    }
    case "turn.completed": {
      let messages = state.messages.some((m) => m.streaming)
        ? state.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m))
        : state.messages;
      // a small "committed <id>" marker at the end of a turn that auto-committed
      const commit = commitOf(p);
      if (commit && !messages.some((m) => m.type === "system_note" && m.commit && (m.commit.id === commit.id || m.commit.short === commit.short))) {
        messages = [...messages, { id: `commit-${commit.id}`, type: "system_note", kind: "commit", text: commit.message || "", commit }];
      }
      const next = { ...state, messages, status: "idle", approvals: [], input: null, stop: { reason: p.stop_reason || "completed", rounds: p.rounds ?? null } };
      if (p.stop_reason === "error" && p.error && !state.error) next.error = String(p.error);
      // turn usage is not the session total; usage.updated carries that total.
      if (p.context) next.context = { ...state.context, ...p.context };
      return next;
    }
    case "error": {
      const text = typeof p === "string" ? p : p.message || p.error || p.detail || "Unknown error";
      return { ...state, error: String(text) };
    }
    default:
      return state;
  }
}

/** Parse one SSE frame's data, accepting named events and `{type|event, data|payload}` envelopes. */
export function decodeFrame(eventName, data) {
  let parsed;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null; // heartbeat / comment frames
  }
  if (eventName && eventName !== "message") return { name: eventName, payload: parsed };
  if (parsed && typeof parsed === "object") {
    const name = parsed.type || parsed.event || parsed.name;
    if (typeof name === "string" && EVENT_NAMES.includes(name)) {
      return { name, payload: parsed.data ?? parsed.payload ?? parsed };
    }
  }
  return null;
}
