# GlossX — built-in agent browser and prompt-cache efficiency (spec)

Extends `docs/glossx-harness.md` and `docs/glossx-workspaces.md`.

## Part A — Built-in agent browser

Design reference: Playwright MCP's accessibility snapshots with element refs
(and Vercel's agent-browser). Implementation: GlossX drives the user's
installed **Edge or Chrome** directly over the **Chrome DevTools Protocol**
(CDP). No new dependency (the `websockets` package ships with
`uvicorn[standard]`), no browser download, works in the packaged `GlossX.exe`.

### A1. Engine (`backend/app/harness/browser/`)

- Find a Chromium browser (Edge, Chrome, Chromium; Windows registry / common
  paths, macOS app bundles, Linux binaries; override in settings). Launch it
  with `--remote-debugging-port=0`, a GlossX-owned profile directory under the
  data dir (never the user's own profile), headless by default (`headed`
  setting to show the real window). One browser process per GlossX server,
  started lazily, closed on shutdown and after an idle timeout; pages (tabs)
  are per session.
- A small async CDP client (websocket JSON-RPC, event subscriptions, timeouts).
- **Snapshot**: build a compact text outline of the page from the accessibility
  tree (`Accessibility.getFullAXTree`) joined with DOM node ids: one line per
  meaningful node — role, accessible name, value/state, and a ref `[eN]` for
  interactive or readable elements; indentation for structure; long text
  truncated; links show their URL; capped (default 12k chars) with a note when
  truncated and a way to request a subtree or a text search. Refs are valid
  only for the snapshot that produced them (store ref → backendNodeId per
  page, invalidate on navigation; stale refs return a clear error telling the
  model to snapshot again).
- **Readable extraction**: main-content text/markdown of the page (readability
  heuristic in injected JS: article/main/largest text block, strip nav/ads),
  with title, URL, byline when present; size-limited with paging.
- Safety: only `http`/`https` (and `about:blank`); block `file:`, `chrome:`,
  local/private network addresses unless `browser.allow_private` is on;
  downloads only into the workspace `downloads/` folder; page text and
  snapshots are untrusted data (wrapped like other tool output; instructions in
  pages are never followed); a per-session navigation budget.

### A2. Tools (category `browser`; reads allowed in ask/plan mode)

`browser_open {url, new_tab?}`, `browser_snapshot {ref?, find?}`,
`browser_click {ref}`, `browser_type {ref, text, submit?}`,
`browser_select {ref, value}`, `browser_press {key}`,
`browser_scroll {direction|ref}`, `browser_back`, `browser_forward`,
`browser_read {page?}` (readable extraction), `browser_find {text}`,
`browser_screenshot {ref?}` (saved to the workspace, returned as a file ref and
shown in the tool card), `browser_tabs`, `browser_switch_tab {id}`,
`browser_close_tab {id}`, `browser_download {ref|url}` (approval),
`browser_import_paper {url?}` (imports the current page's PDF / arXiv / DOI
into the library through the existing importer; approval `library:write`).
Typing into password fields, submitting forms that send data, and downloads
ask for approval by default (`browser.*` permission patterns work like other
tools). Every action returns the new URL/title and a short diff-style note of
what changed (navigation, new dialog, etc.); the model calls `browser_snapshot`
to see the page.

### A3. API and live view

- `GET /api/browser/status` → `{available, browser, path, headed, running}`.
- `GET /api/sessions/{id}/browser` → `{tabs: [{id, url, title, active}], ...}`.
- `GET /api/sessions/{id}/browser/screencast` (SSE or websocket): JPEG frames
  (CDP `Page.startScreencast`, throttled, only while a viewer is connected).
- `POST /api/sessions/{id}/browser/input` — user takeover: click (x, y),
  scroll, key, text, navigate, back/forward/reload, new/close/switch tab.
- SSE on the session stream: `browser.updated` `{tabs, active, url, title,
  loading}` whenever the agent or the user navigates.
- UI: a **Browser** tab kind in the editor area (opened automatically the
  first time the agent uses the browser in a session, and from the palette /
  activity bar): address bar, back/forward/reload, tab strip, the live page
  (screencast image, pointer and keyboard forwarded when the user takes
  over), an "Agent is controlling / You are controlling" indicator, "Import
  this paper", "Send page to chat" (attaches the readable text), and a compact
  action log. Tool cards for `browser_*` show the action, the URL/title and a
  thumbnail for screenshots. When no browser is installed, show how to point
  GlossX at one.

## Part B — Prompt-cache efficiency and visibility

### B1. Capture real usage (all providers)

Every provider call returns `{input_tokens, cached_input_tokens,
cache_write_tokens?, output_tokens, reasoning_tokens?, exact: bool}`:

- **local_codex**: always run `codex exec --json` and read the
  `turn.completed` usage (`input_tokens`, `cached_input_tokens`,
  `output_tokens`, `reasoning_output_tokens`).
- **local_claude**: use the CLI's JSON output usage
  (`cache_read_input_tokens`, `cache_creation_input_tokens`).
- **anthropic**: API `usage` including cache read/creation.
- **openai**: `usage.prompt_tokens_details.cached_tokens` when present.

Estimates remain the fallback and stay labelled as estimates. The usage ledger
(SQLite) gains cached/cache-write columns (migrate in place); the usage API
and the settings usage view show cached tokens and the **cache hit rate =
cached_input / input**, per provider, per feature and per day.

### B2. Make prompts cache-friendly

- System prompt layout, strictly ordered and byte-stable: (1) identity and
  rules, protocol, untrusted-data rules; (2) tools sorted by name, skills
  sorted by name, subagent types; (3) global `GLOSSX.md`; (4) project
  `GLOSSX.md` and project info. **Everything volatile moves out of the system
  prompt** into a short "session state" block at the end of the newest user
  turn: date, mode rules, attached papers, todo summary, branch.
- Mode changes, attaching a paper or a new day must not change the cached
  prefix. Tool lists must not reorder between turns (deterministic sort; tool
  availability changes are the only allowed prefix change, and are logged).
- History is append-only: never re-render or re-truncate earlier messages;
  large tool results are stored by reference once and their in-prompt preview
  never changes. Compaction is the only event that rewrites the prefix (record
  it as a cache reset).
- **anthropic**: add `cache_control` breakpoints (end of the static system
  block, end of the tools block, and the last stable history message).
- **local_codex**: keep one persistent Codex thread per GlossX session and
  continue it with `codex exec resume <thread>` sending only the new turn
  content (tool results + state block); fall back to a full transcript when
  the thread is gone or after compaction/fork. Also cut Codex's own fixed
  overhead for harness calls where the CLI allows it (its skills, plugins, MCP
  servers and project docs are not used by GlossX): measure before/after with
  `--json` usage and document what worked.
- **local_claude**: same idea with `--resume` where available.
- A unit test asserts prefix stability: two consecutive turns of the same
  session produce prompts whose common prefix covers the whole system prompt
  and all earlier history.

### B3. Show it

- Session summary gains `usage: {input_tokens, cached_input_tokens,
  output_tokens, llm_calls, cache_hit_rate, last_call: {input, cached, output,
  hit_rate}, exact}`; `turn.completed` carries the turn's usage; a
  `usage.updated` SSE event fires after each model call.
- UI: next to the context meter, a cache indicator (hit rate of the last call
  and of the session, tooltip with input / cached / output tokens and whether
  the numbers are exact or estimated); a per-turn usage line under the turn
  ("3 calls · 41k in · 78% cached · 1.2k out · 52s"); the status bar shows the
  session hit rate; Settings → usage shows cached tokens and hit rate; a note
  appears when a cache reset happened (compaction, tool list change).
