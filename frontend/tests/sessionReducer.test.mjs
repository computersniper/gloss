import test from "node:test";
import assert from "node:assert/strict";
import { applySessionEvent, decodeFrame, emptySessionState, normalizeTodos } from "../src/harness/sessionReducer.mjs";
import { diffLines, diffStats, parseUnifiedDiff } from "../src/harness/diff.mjs";

const run = (events, state = emptySessionState()) => events.reduce((s, [name, payload]) => applySessionEvent(s, name, payload), state);

test("browser and cumulative usage survive a per-turn completion and replay", () => {
  const usage = { input_tokens: 2000, cached_input_tokens: 1500, llm_calls: 3, exact: true };
  const browser = { tabs: [{ id: "tab1", url: "https://example.org" }], controlling: "user" };
  const note = { id: "usage-1", type: "system_note", kind: "turn_usage", turn_id: "m1", usage: { input_tokens: 500, llm_calls: 1 } };
  const s = run([
    ["session.updated", { id: "s1", usage: {} }],
    ["browser.updated", browser], ["usage.updated", usage],
    ["message.created", note], ["turn.completed", { message_id: "m1", usage: note.usage }],
    ["message.created", note],
  ]);
  assert.deepEqual(s.browser, browser);
  assert.deepEqual(s.session.usage, usage);
  assert.equal(s.messages.filter((m) => m.kind === "turn_usage").length, 1);
});

test("replay then streaming assistant text", () => {
  const s = run([
    ["session.updated", { id: "a", title: "T", status: "running", mode: "agent", messages: [{ id: "u1", type: "user", text: "hi" }] }],
    ["message.created", { id: "m1", type: "assistant", text: "" }],
    ["message.delta", { id: "m1", text: "Hel" }],
    ["message.delta", { id: "m1", text: "lo" }],
    ["message.completed", { id: "m1" }],
  ]);
  assert.equal(s.loaded, true);
  assert.equal(s.status, "running");
  assert.deepEqual(s.messages.map((m) => [m.id, m.text, !!m.streaming]), [["u1", "hi", false], ["m1", "Hello", false]]);
});

test("a locally echoed user message is replaced by the server copy", () => {
  const start = { ...emptySessionState(), messages: [{ id: "local-1", type: "user", text: "q", pending: true }] };
  const s = run([["message.created", { message: { id: "u9", type: "user", text: "q" } }]], start);
  assert.deepEqual(s.messages.map((m) => m.id), ["u9"]);
});

test("server-side local commands (local: true) are kept; replay keeps an unacknowledged echo", () => {
  const s = run([
    ["message.created", { id: "u1", type: "user", text: "/help", local: true, command: "help" }],
    ["message.created", { id: "n1", type: "system_note", kind: "command", text: "Available commands" }],
    ["message.created", { id: "u2", type: "user", text: "/mode plan", local: true, command: "mode" }],
  ]);
  assert.deepEqual(s.messages.map((m) => m.id), ["u1", "n1", "u2"]);
  const echo = { ...s, messages: [...s.messages, { id: "local-9", type: "user", text: "next", pending: true }] };
  const replay = run([["session.updated", { id: "s", status: "idle", messages: [{ id: "u1", type: "user", text: "/help" }] }]], echo);
  assert.deepEqual(replay.messages.map((m) => m.id), ["u1", "local-9"]);
  const cleared = run([["session.updated", { id: "s", status: "idle", messages: [] }]], s);
  assert.equal(cleared.messages.length, 0);
});

test("message.completed text is authoritative; turn.completed records the stop reason", () => {
  const s = run([
    ["message.created", { id: "a1", type: "assistant", text: "" }],
    ["message.delta", { id: "a1", text: "<tool_call>{", offset: 0 }],
    ["message.completed", { id: "a1", type: "assistant", text: "", tool_calls: ["t1"] }],
    ["turn.completed", { session_id: "s", stop_reason: "budget", rounds: 25, status: "idle" }],
  ]);
  assert.equal(s.messages[0].text, "");
  assert.equal(s.status, "idle");
  assert.deepEqual(s.stop, { reason: "budget", rounds: 25 });
});

test("tool updates upsert and resolve approvals", () => {
  const s1 = run([
    ["tool.updated", { id: "t1", tool: "shell_run", args: { command: "ls" }, status: "awaiting_approval" }],
    ["approval.requested", { tool_call_id: "t1", tool: "shell_run", args: { command: "ls" }, reason: "exec" }],
  ]);
  assert.equal(s1.status, "awaiting_approval");
  assert.equal(s1.approvals.length, 1);
  const s2 = run([["tool.updated", { id: "t1", tool: "shell_run", args: { command: "ls" }, status: "done", result_preview: "ok" }]], s1);
  assert.equal(s2.messages.length, 1);
  assert.equal(s2.messages[0].type, "tool_call");
  assert.equal(s2.messages[0].result_preview, "ok");
  assert.equal(s2.approvals.length, 0);
});

test("input, todos, context, turn end and errors", () => {
  const s = run([
    ["input.requested", { question: "Which?", choices: ["A", "B"] }],
    ["todos.updated", { todos: [{ content: "Read", status: "in_progress" }, "Write"] }],
    ["context.updated", { tokens_est: 500, limit: 1000, compactions: 1 }],
  ]);
  assert.equal(s.status, "awaiting_input");
  assert.deepEqual(s.todos.map((t) => [t.text, t.status]), [["Read", "in_progress"], ["Write", "pending"]]);
  assert.equal(s.context.tokens_est, 500);
  const done = run([["error", { message: "boom" }], ["turn.completed", {}]], s);
  assert.equal(done.error, "boom");
  assert.equal(done.status, "idle");
  assert.equal(done.input, null);
});

test("frames: named events, envelopes and heartbeats", () => {
  assert.deepEqual(decodeFrame("message.delta", '{"id":"m","text":"x"}'), { name: "message.delta", payload: { id: "m", text: "x" } });
  assert.deepEqual(decodeFrame("message", '{"type":"turn.completed","data":{"ok":1}}'), { name: "turn.completed", payload: { ok: 1 } });
  assert.equal(decodeFrame("message", "ping"), null);
  assert.equal(normalizeTodos(null).length, 0);
});

test("line diff and unified diff parsing", () => {
  const rows = diffLines("a\nb\nc", "a\nB\nc\nd");
  assert.deepEqual(diffStats(rows), { added: 2, removed: 1 });
  assert.deepEqual(rows.map((r) => r.kind), ["ctx", "del", "add", "ctx", "add"]);
  const parsed = parseUnifiedDiff("--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n one\n-two\n+2");
  assert.deepEqual(parsed.map((r) => [r.kind, r.text]), [["hunk", "@@ -1,2 +1,2 @@"], ["ctx", "one"], ["del", "two"], ["add", "2"]]);
  assert.equal(parsed[2].a, 2);
});

test("live progress appends thinking and steps once, even when replayed", () => {
  const progress = [
    ["message.progress", { id: "m1", kind: "thinking", text: "Look ", at: 0 }],
    ["message.progress", { id: "m1", kind: "thinking", text: "closer", at: 5 }],
    ["message.progress", { id: "m1", kind: "step", text: "Read notes.md", at: 0 }],
  ];
  const s = run([["message.created", { id: "m1", type: "assistant", text: "" }], ...progress, ...progress]);
  assert.equal(s.messages[0].thinking, "Look closer");
  assert.deepEqual(s.messages[0].steps, ["Read notes.md"]);
});
