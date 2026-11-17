# GlossX Harness — spec

GlossX is a full **agent harness** for research and learning, in the spirit of
Claude Code, Cursor's agent and DSH ("everything is a plugin"). The user works
with long-lived **agent sessions**: the model itself decides, turn by turn,
which tools to call, the harness executes them under permissions, streams
everything to the UI, and keeps the session's context healthy. The earlier
goal→report pipeline (`/api/agent/runs`, workflows, reader sub-agents,
verification) stays available but becomes *tools and commands inside sessions*.

This document complements `docs/glossx-design.md` (kernel, capability ABI,
adapters, workflows, projects, connections). Reuse those pieces.

## 1. Sessions

- A session = `{id, title, project_id, created_at, updated_at, mode, model:
  {provider, model}, messages: [...], todos: [...], status:
  idle|running|awaiting_approval|awaiting_input, usage: {input_tokens_est,
  output_tokens_est, llm_calls}, context: {tokens_est, limit, compactions}}`.
- Persisted under `<DATA_DIR>/sessions/<id>.json` (append-friendly; rewrite on
  each turn is fine). List, rename, delete, fork (copy up to a message).
- Message types (one list, in order):
  - `user` `{id, text, attachments: [{kind: paper|span|file|run, ref}]}`
  - `assistant` `{id, text}` (streamed)
  - `tool_call` `{id, tool, args, status: pending|awaiting_approval|running|done|error|denied, result_preview, result_ref, started_at, finished_at}`
  - `system_note` (compaction summaries, hook output, mode changes)
- Several sessions can run concurrently; one turn at a time per session.
  `interrupt` cancels the in-flight model call / tool and returns to idle.

## 2. Agent loop (tool calling)

Provider-agnostic protocol so it works with the local `claude`/`codex` CLIs
and with API providers:

- The system prompt lists available tools (name, one-line description, JSON
  argument schema) and instructs the model to answer with normal markdown and,
  when it needs tools, to emit one or more blocks
  `<tool_call>{"name": "...", "arguments": {...}}</tool_call>` at the end of the
  message. The harness streams the text (`registry.stream`), parses tool-call
  blocks when the message ends, runs them (independent calls in parallel),
  appends `<tool_result name="..." id="...">…</tool_result>` blocks (tool output
  wrapped as untrusted data, truncated with a reference id for large outputs)
  and continues, until the model replies without tool calls, the turn budget
  (default 25 tool rounds) is hit, or the user interrupts.
- Malformed tool calls produce a short corrective tool_result instead of
  crashing.
- **Modes** (like Claude Code): `ask` (read-only tools only; no file writes,
  no execution), `agent` (default; writes/exec need approval per permission
  rules), `auto` (auto-approve file edits inside the workspace; exec still
  asks), `plan` (read-only; the model must end with a plan the user can accept,
  which switches the session to `agent`).

## 3. Tools (all registered through the capability registry as plugins)

Core tools (first-party):
- Papers & library: `library_search`, `library_list`, `paper_read`
  (sections/pages), `paper_search_spans`, `paper_summary`, `paper_explain`,
  `paper_references`, `scholar_search`, `library_import` (approval).
- Research: `run_workflow` (e.g. `literature-review`, returns the cited
  report), `verify_claims` (claims + span ids → verdicts), `agent_reader`.
- Workspace files (sandboxed to the project workspace dir
  `<DATA_DIR>/workspaces/<project_id or "default">/`): `fs_list`, `fs_read`,
  `fs_glob`, `fs_grep`, `fs_write` (approval unless auto mode), `fs_edit`
  (exact string replace; approval unless auto mode). Every write records a
  **checkpoint** (previous content) so the UI can show a diff and the user can
  accept/revert.
- Execution: `shell_run` (runs a command inside the workspace dir with a
  timeout; approval always unless the permission is allow-listed), `python_run`
  (runs a script file in the workspace with the backend's Python; approval).
- Web: `web_search` (Semantic Scholar/arXiv for papers + a simple DuckDuckGo
  HTML search for the open web, degrade gracefully offline), `web_fetch`
  (fetch URL → readable text, size-limited).
- Agent utilities: `todo_write` (replace the session todo list; shown in the
  UI), `ask_user` (pauses with a question + optional choices; status
  `awaiting_input`), `task` (spawn a **subagent**: a nested session with its own
  context, a chosen subagent type, a tool allowlist and a turn budget; returns
  its final answer), `memory_recall`, `memory_note` (project memory).
- Adapted tools: MCP tools, Claude Code / Codex delegates, SSH, skills (as
  `skill_<name>` tools), installed plugin capabilities — all from the existing
  adapters.

## 4. Permissions

- Rules in connections/settings: `permissions: {allow: [patterns], deny:
  [patterns], ask: [patterns]}` where a pattern is a tool name, `tool:*`, or
  `shell_run(<prefix>*)`. Deny > ask > allow; defaults: read-only tools allow,
  writes ask (auto mode allows workspace writes), execution/network-writes ask.
- Approval prompts are inline in the session: approve once, approve always
  (adds an allow rule), deny (with optional feedback text the model sees).

## 5. Context management

- Token estimate per message; the session shows `context.tokens_est / limit`
  (limit from settings, default 120k).
- **Auto-compaction** at 80%: older turns are summarized into a `system_note`
  (keeping file paths, paper/span ids, decisions, open todos) and dropped from
  the prompt; `/compact` triggers it manually.
- **Project instructions**: `GLOSSX.md` at the workspace root (and a global one
  in the data dir) is always injected, like CLAUDE.md.
- **@-mentions** in user messages attach papers, spans, workspace files or
  runs; their content is materialized under a token budget.
- Large tool results are stored and referenced by id; the model can re-read
  them with `result_read`.

## 6. Commands, subagents, skills, hooks — all plugins

- **Slash commands**: built-in `/clear`, `/compact`, `/mode <m>`, `/model`,
  `/review <question>` (runs the literature-review workflow), `/summarize`,
  `/todos`, `/help`; plus custom commands from markdown files with frontmatter
  in `<DATA_DIR>/commands/*.md` and `<workspace>/.glossx/commands/*.md`, from
  skills, and from v2 plugin manifests (`kind: "command"`). A command expands
  into a prompt (with `$ARGUMENTS`).
- **Subagent types**: markdown with frontmatter (`name, description, tools,
  model`) in `<DATA_DIR>/agents/*.md` and `<workspace>/.glossx/agents/*.md`;
  built-ins: `reader` (one paper, evidence digests), `reviewer` (critique +
  verification), `coder` (workspace + execution tools), `searcher` (scholar +
  web).
- **Skills**: existing SKILL.md adapter (progressive disclosure).
- **Hooks**: configured in settings `hooks: {pre_tool_use: [...],
  post_tool_use: [...], session_start: [...], stop: [...]}` each
  `{matcher: tool pattern, command: shell command}` run in the workspace with
  the event JSON on stdin; a non-zero exit from a pre_tool_use hook blocks the
  call and its stderr is returned to the model.
- **Plugin kinds** (manifest v2): `tool`, `command`, `agent` (subagent type),
  `skill`, `hook`, `mcp`, `workflow`, `loop`, `decider`, `provider`, `view`.
  Installing/uninstalling a plugin updates the registry live.

## 7. HTTP API

- `GET/POST /api/sessions` (create `{title?, project_id?, mode?, provider?,
  model?}`), `GET/PATCH/DELETE /api/sessions/{id}` (PATCH: title, mode, model),
  `POST /api/sessions/{id}/fork` `{message_id}`.
- `POST /api/sessions/{id}/messages` `{text, attachments?}` → starts a turn
  (409 if a turn is running).
- `GET /api/sessions/{id}/events` (SSE): `message.created`,
  `message.delta` `{id, text}`, `message.completed`, `tool.updated` (full
  tool_call object), `todos.updated`, `approval.requested` `{tool_call_id,
  tool, args, reason}`, `input.requested` `{question, choices}`,
  `context.updated`, `session.updated` (status), `turn.completed`,
  `error`. Replays the current state first on connect.
- `POST /api/sessions/{id}/interrupt`.
- `POST /api/sessions/{id}/approvals/{tool_call_id}` `{decision: "once" |
  "always" | "deny", feedback?}`.
- `POST /api/sessions/{id}/input` `{answer}` (for ask_user).
- `POST /api/sessions/{id}/compact`.
- `GET /api/harness/tools` (with permission state), `GET /api/harness/commands`,
  `GET /api/harness/agents`, `GET/PUT /api/harness/settings` (permissions,
  hooks, context limit, default mode).
- Workspace: `GET /api/workspace/{project_id}/tree`,
  `GET/PUT /api/workspace/{project_id}/file?path=`,
  `GET /api/workspace/{project_id}/checkpoints`,
  `POST /api/workspace/{project_id}/checkpoints/{id}/revert`.
- Mentions: `GET /api/harness/mentions?q=` → papers, files, runs matching.

## 8. UI (Cursor/Claude-Code-like, inside the workbench)

- AI pane = **session chat**: session switcher/history, new chat, mode picker
  (Ask / Agent / Auto / Plan), model picker, context meter.
- Messages render markdown + KaTeX; assistant text streams; each tool call is a
  compact card (icon by tool kind, name, short arg summary, status spinner,
  expandable args/result; file edits show a diff with Accept/Revert; paper span
  results show clickable citations that open the paper tab and flash the span).
- Inline approval cards (Approve once / Always / Deny + feedback), ask_user
  cards with choice buttons, todo list docked above the composer.
- Composer: multiline, Enter to send / Shift+Enter newline, `@` mention picker
  (papers, files, runs), `/` command picker, attach current paper/selection,
  stop button while running.
- Workspace explorer in the side bar (files of the active project workspace),
  files open as editor tabs (markdown with preview toggle, code with
  monospaced editor), checkpoints/diff view.
- Visual design: a coherent modern design system — consistent spacing scale,
  radius, typography, subtle borders and elevation, icon set (`lucide-react`),
  smooth but restrained motion, polished dark and light defaults, good empty
  states. It must look deliberate and professional, not like a form.
