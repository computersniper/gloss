// Pure helpers for the Source control views: split a multi-file unified diff,
// and lay a commit list out as a compact branch graph. No DOM (node --test).

/**
 * Split `git diff` output into per-file sections:
 * [{ path, oldPath, status: "A"|"D"|"R"|"M", binary, text }].
 * A diff without `diff --git` headers is one section.
 */
export function splitDiff(diff) {
  const lines = String(diff || "").split("\n");
  const sections = [];
  let cur = null;
  const start = (path, oldPath) => {
    cur = { path: path || "", oldPath: oldPath || path || "", status: "M", binary: false, lines: [] };
    sections.push(cur);
  };
  const unquote = (p) => (p.startsWith('"') && p.endsWith('"') ? p.slice(1, -1) : p);
  for (const line of lines) {
    const head = /^diff --git (?:"?a\/(.*?)"?) (?:"?b\/(.*?)"?)$/.exec(line);
    if (head) {
      start(head[2], head[1]);
      continue;
    }
    if (!cur) {
      if (!line.trim()) continue;
      start("", "");
    }
    if (line.startsWith("new file mode")) cur.status = "A";
    else if (line.startsWith("deleted file mode")) cur.status = "D";
    else if (line.startsWith("rename from ")) {
      cur.status = "R";
      cur.oldPath = unquote(line.slice(12));
    } else if (line.startsWith("rename to ")) cur.path = unquote(line.slice(10));
    else if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) cur.binary = true;
    else if (line.startsWith("--- ")) {
      const p = unquote(line.slice(4).trim());
      if (p === "/dev/null") cur.status = "A";
      else if (!cur.oldPath) cur.oldPath = p.replace(/^a\//, "");
    } else if (line.startsWith("+++ ")) {
      const p = unquote(line.slice(4).trim());
      if (p === "/dev/null") cur.status = "D";
      else if (!cur.path) cur.path = p.replace(/^b\//, "");
    }
    cur.lines.push(line);
  }
  return sections.map(({ lines: body, ...s }) => ({ ...s, path: s.path || s.oldPath, text: body.join("\n") }));
}

/**
 * Lane layout for a newest-first commit list.
 * Each row: { lane, top: [{from, to}], bottom: [{from, to}], width }, where `top`
 * are the line segments entering the row (lane at the row's top edge → lane at
 * its middle) and `bottom` the ones leaving it (middle → bottom edge).
 */
export function layoutGraph(commits, maxLanes = 8) {
  /** @type {(string|null)[]} */
  let lanes = [];
  const free = () => {
    const i = lanes.indexOf(null);
    if (i >= 0) return i;
    lanes.push(null);
    return lanes.length - 1;
  };
  const rows = [];
  for (const c of commits) {
    const before = lanes.slice();
    let lane = before.indexOf(c.id);
    if (lane < 0) {
      lane = free(); // a branch tip nobody pointed at yet
      before[lane] = null;
    }
    const top = [];
    before.forEach((id, k) => {
      if (id === null) return;
      top.push({ from: k, to: id === c.id ? lane : k });
    });
    // every lane that was waiting for this commit ends here
    lanes = lanes.map((id) => (id === c.id ? null : id));
    const bottom = [];
    lanes.forEach((id, k) => {
      if (id !== null) bottom.push({ from: k, to: k });
    });
    const parents = Array.isArray(c.parents) ? c.parents : [];
    parents.forEach((p, n) => {
      let k = lanes.indexOf(p);
      if (k < 0) {
        k = n === 0 && lanes[lane] === null ? lane : free();
        lanes[k] = p;
      } else if (n === 0 && k > lane && lanes[lane] === null) {
        // the first parent is already awaited further right: pull that lane in, so the mainline stays left
        for (const seg of bottom) if (seg.from === k && seg.to === k) seg.to = lane;
        lanes[k] = null;
        lanes[lane] = p;
        k = lane;
      }
      bottom.push({ from: lane, to: k });
    });
    while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
    const used = Math.max(lane, ...top.map((s) => Math.max(s.from, s.to)), ...bottom.map((s) => Math.max(s.from, s.to))) + 1;
    rows.push({ lane, top, bottom, width: Math.min(used, maxLanes) });
  }
  return rows;
}
