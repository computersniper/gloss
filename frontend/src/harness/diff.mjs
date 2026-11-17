// Small line diff helpers for tool-call cards and the checkpoint view.

/** Parse a unified diff into rows: {kind: "hunk"|"add"|"del"|"ctx", text, a?, b?}. */
export function parseUnifiedDiff(diff) {
  const rows = [];
  let a = 0;
  let b = 0;
  for (const line of String(diff || "").split("\n")) {
    if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("diff ") || line.startsWith("index ")) continue;
    if (line.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line);
      if (m) {
        a = Number(m[1]);
        b = Number(m[2]);
      }
      rows.push({ kind: "hunk", text: line });
    } else if (line.startsWith("+")) rows.push({ kind: "add", text: line.slice(1), b: b++ });
    else if (line.startsWith("-")) rows.push({ kind: "del", text: line.slice(1), a: a++ });
    else if (line.startsWith("\\")) continue;
    else if (rows.length || line) rows.push({ kind: "ctx", text: line.startsWith(" ") ? line.slice(1) : line, a: a++, b: b++ });
  }
  while (rows.length && rows[rows.length - 1].kind === "ctx" && rows[rows.length - 1].text === "") rows.pop();
  return rows;
}

/** LCS line diff of two texts with `context` lines around each change. */
export function diffLines(before, after, context = 3) {
  const A = before == null || before === "" ? [] : String(before).split("\n");
  const B = after == null || after === "" ? [] : String(after).split("\n");
  // very large inputs: skip the quadratic table and show a plain replace
  if (A.length * B.length > 4_000_000) {
    return [
      ...A.map((text, i) => ({ kind: "del", text, a: i + 1 })),
      ...B.map((text, i) => ({ kind: "add", text, b: i + 1 })),
    ];
  }
  const n = A.length;
  const m = B.length;
  const w = m + 1;
  const table = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i * w + j] = A[i] === B[j] ? table[(i + 1) * w + j + 1] + 1 : Math.max(table[(i + 1) * w + j], table[i * w + j + 1]);
    }
  }
  const all = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) all.push({ kind: "ctx", text: A[i], a: ++i, b: ++j });
    else if (table[(i + 1) * w + j] >= table[i * w + j + 1]) all.push({ kind: "del", text: A[i], a: ++i });
    else all.push({ kind: "add", text: B[j], b: ++j });
  }
  while (i < n) all.push({ kind: "del", text: A[i], a: ++i });
  while (j < m) all.push({ kind: "add", text: B[j], b: ++j });

  if (context < 0) return all;
  const keep = new Uint8Array(all.length);
  all.forEach((row, k) => {
    if (row.kind === "ctx") return;
    for (let d = Math.max(0, k - context); d <= Math.min(all.length - 1, k + context); d++) keep[d] = 1;
  });
  const rows = [];
  let skipped = false;
  all.forEach((row, k) => {
    if (!keep[k]) {
      skipped = true;
      return;
    }
    if (skipped && rows.length) rows.push({ kind: "hunk", text: "⋯" });
    skipped = false;
    rows.push(row);
  });
  return rows;
}

export function diffStats(rows) {
  let added = 0;
  let removed = 0;
  for (const r of rows) {
    if (r.kind === "add") added++;
    else if (r.kind === "del") removed++;
  }
  return { added, removed };
}
