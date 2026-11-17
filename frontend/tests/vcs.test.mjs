import test from "node:test";
import assert from "node:assert/strict";
import { layoutGraph, splitDiff } from "../src/harness/vcs.mjs";
import { applySessionEvent, emptySessionState } from "../src/harness/sessionReducer.mjs";

test("splitDiff separates files and reads their status", () => {
  const diff = [
    "diff --git a/notes/a.md b/notes/a.md",
    "index 111..222 100644",
    "--- a/notes/a.md",
    "+++ b/notes/a.md",
    "@@ -1,2 +1,2 @@",
    " keep",
    "-old",
    "+new",
    "diff --git a/new.txt b/new.txt",
    "new file mode 100644",
    "--- /dev/null",
    "+++ b/new.txt",
    "@@ -0,0 +1 @@",
    "+hello",
    "diff --git a/gone.txt b/gone.txt",
    "deleted file mode 100644",
    "--- a/gone.txt",
    "+++ /dev/null",
    "@@ -1 +0,0 @@",
    "-bye",
    "diff --git a/old name.md b/new name.md",
    "similarity index 100%",
    "rename from old name.md",
    "rename to new name.md",
    "diff --git a/fig.png b/fig.png",
    "Binary files a/fig.png and b/fig.png differ",
  ].join("\n");
  const s = splitDiff(diff);
  assert.deepEqual(s.map((x) => [x.path, x.status, x.binary]), [
    ["notes/a.md", "M", false], ["new.txt", "A", false], ["gone.txt", "D", false], ["new name.md", "R", false], ["fig.png", "M", true],
  ]);
  assert.equal(s[3].oldPath, "old name.md");
  assert.ok(s[0].text.includes("+new") && !s[0].text.includes("hello"));
});

test("splitDiff accepts a plain single-file diff and empty input", () => {
  const s = splitDiff("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n");
  assert.equal(s.length, 1);
  assert.equal(s[0].path, "x.py");
  assert.deepEqual(splitDiff(""), []);
  assert.deepEqual(splitDiff("\n"), []);
});

test("layoutGraph: linear history stays in lane 0", () => {
  const rows = layoutGraph([{ id: "c", parents: ["b"] }, { id: "b", parents: ["a"] }, { id: "a", parents: [] }]);
  assert.deepEqual(rows.map((r) => r.lane), [0, 0, 0]);
  assert.deepEqual(rows[0].top, []);
  assert.deepEqual(rows[1].top, [{ from: 0, to: 0 }]);
  assert.deepEqual(rows[2].bottom, []);
  assert.ok(rows.every((r) => r.width === 1));
});

test("layoutGraph: a merge opens a second lane that closes at the fork point", () => {
  // m merges b (main) and x (side); both descend from a
  const rows = layoutGraph([
    { id: "m", parents: ["b", "x"] },
    { id: "x", parents: ["a"] },
    { id: "b", parents: ["a"] },
    { id: "a", parents: [] },
  ]);
  assert.deepEqual(rows.map((r) => r.lane), [0, 1, 0, 0]);
  assert.deepEqual(rows[0].bottom, [{ from: 0, to: 0 }, { from: 0, to: 1 }]);
  // below `b` the side lane bends back into lane 0, where `a` sits
  assert.deepEqual(rows[2].bottom, [{ from: 1, to: 0 }, { from: 0, to: 0 }]);
  assert.deepEqual(rows[3].top, [{ from: 0, to: 0 }]);
  assert.equal(rows[0].width, 2);
  assert.equal(rows[3].width, 1);
});

test("layoutGraph: an unrelated branch tip takes a free lane", () => {
  const rows = layoutGraph([
    { id: "t", parents: ["a"] },
    { id: "u", parents: ["a"] },
    { id: "a", parents: [] },
  ]);
  assert.deepEqual(rows.map((r) => r.lane), [0, 1, 0]);
  assert.deepEqual(rows[1].top, [{ from: 0, to: 0 }]);
});

test("turn.completed with a commit leaves a marker note", () => {
  let s = { ...emptySessionState(), status: "running" };
  s = applySessionEvent(s, "turn.completed", { stop_reason: "completed", commit: { id: "abcdef1234567", short: "abcdef1", message: "notes: add summary" } });
  const note = s.messages[s.messages.length - 1];
  assert.equal(note.type, "system_note");
  assert.equal(note.kind, "commit");
  assert.equal(note.commit.short, "abcdef1");
  // replaying the same turn end does not duplicate it
  s = applySessionEvent(s, "turn.completed", { stop_reason: "completed", commit: { id: "abcdef1234567", short: "abcdef1" } });
  assert.equal(s.messages.filter((m) => m.kind === "commit").length, 1);
  // no commit, no note
  const plain = applySessionEvent({ ...emptySessionState(), status: "running" }, "turn.completed", { stop_reason: "completed" });
  assert.equal(plain.messages.length, 0);
});
