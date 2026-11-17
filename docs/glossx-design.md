# GlossX v1 — implementation spec

GlossX turns Gloss into an open, plugin-native agent harness for research and
learning. This specification covers the initial harness, plugin interfaces and workbench.

The current agent code (`backend/app/agent/`, `routers/agent.py`,
`frontend/src/components/AgentPanel.tsx`, `frontend/src/api/agent.ts`) is the
starting point: extend it, keep what works, and keep every existing endpoint
backwards compatible.

## 1. Kernel (small, fixed)

- **Capability registry** (exists): every capability = card (≤30 words), I/O
  schema, permissions, cost hint, trust tier, handler.
- **Scheduler**: runs as asyncio tasks (exists in `runs.py`).
- **Permission & sandbox gate** (new): before a step runs, its capability's
  permissions are checked. Permissions in `APPROVAL_REQUIRED = {"code:execute",
  "remote:ssh", "mcp:call", "library:write"}` need approval unless the user
  listed them in `connections.auto_approve`. The run pauses with status
  `awaiting_approval` and a `pending_approval` object; the user approves or
  denies via the API. Deny or a 10-minute timeout → the step fails with
  "approval denied" and the loop continues (it may stop with
  `stop_reason="approval_needed"`).
- **Budget meter** (new): per-run budget `{max_steps (16), max_llm_calls (40),
  max_tokens_est (400000)}`; every agent LLM call increments counters
  (estimate tokens with `providers.base.estimate_text_tokens`). Exceeding any
  limit stops the run with `stop_reason="budget"`. Usage is still recorded in
  the existing usage ledger under task type `agent`.
- **Trace log & events** (exists): every gate decision, approval, budget stop,
  route decision and adapter call is an event.

## 2. Plugin manifest v2 and adapters

Manifest v2 = v1 fields plus:

```json
{
  "api_version": 2,
  "kind": "tool|agent|workflow|loop|decider|source|memory|provider|view",
  "capability": {"card": "...", "input": {"name": "type: desc"}, "output": {...}},
  "permissions": ["paper:read", "ai:complete", ...],
  "steps": [{"id": "...", "use": "<capability name>", "args": {...},
             "map_over": "<step id>.<field>", "fallback": ["<cap>", ...],
             "context": "shared_session|full", "depends_on": ["..."]}],
  "loop": {"use": "loop:plan-execute-verify", "stop": {"coverage": 0.8, "max_rounds": 3}}
}
```

- v1 manifests stay valid and are adapted to `kind: "agent"` capabilities
  (exists).
- **Third-party manifests are declarative only** (no code); v2 validation lives
  in `backend/app/plugins/manager.py` (extend `validate_manifest`; keep v1
  behaviour and all existing tests passing).
- **Trust tiers**: `first-party` (builtin Python), `third-party` (installed
  manifests), `external` (MCP tools, skills, CLI agents, SSH). Prompts wrap
  every tool output / document text as untrusted data
  (`<untrusted source="...">…</untrusted>` plus a system rule that instructions
  inside untrusted blocks are never followed and only the planner chooses
  tools).

Adapters (each compiles into capabilities in the registry):

1. **Native Python** — builtins (exists) plus new ones below.
2. **Declarative manifest** — v1 → agent capability (exists); v2 `workflow`
   manifests → `workflow.<id>` capabilities executed by the workflow engine
   (§3).
3. **Agent Skills** — every skill from `backend/app/skills/loader.py` becomes
   `skill.<name>` (kind agent, trust external). Progressive disclosure: only the
   description is in the card; the SKILL.md body is loaded when the step runs,
   and the skill is executed through the existing skill runner on the run's
   scope paper.
4. **MCP** —
   - *client*: servers configured in connections (`{name, command, args, env,
     enabled}`) are launched over **stdio JSON-RPC 2.0** (implement the small
     subset yourself: `initialize`, `notifications/initialized`, `tools/list`,
     `tools/call`; no new dependency). Each tool becomes
     `mcp.<server>.<tool>` (permission `mcp:call`, trust external). Processes
     are started lazily, reused, and closed on shutdown.
   - *server*: `POST /mcp` implements JSON-RPC (`initialize`, `tools/list`,
     `tools/call`) exposing `glossx_list_papers`, `glossx_search_spans`,
     `glossx_start_run`, `glossx_get_run` so Claude Code / Codex can use GlossX.
5. **CLI agents** — `delegate.claude_code` and `delegate.codex` (permission
   `code:execute`, trust external): run the local `claude` / `codex` CLI **with
   tools enabled** in a per-run sandbox workspace
   `<DATA_DIR>/workspaces/<run_id>/` (never the user's home or repo). Input
   `{task, files?: {name: content}}`; output `{summary, files_changed,
   stdout_tail}`. Reuse command resolution / process-group helpers from
   `platform_support.py` and the providers. Timeout 15 min, killable on cancel.
6. **Remote (SSH)** — `remote.ssh.run` (permission `remote:ssh`): runs a
   command on a configured host (`{name, host, user, port, workdir}`) through
   the system `ssh` binary (`-o BatchMode=yes`, key-based auth only), optional
   upload of small files via `scp`, returns exit code + stdout/stderr tails.
   `remote.ssh.hosts` lists configured hosts (no approval).
7. **LLM providers** — existing registry; a run may override the provider.

## 3. Core agent: route → plan → execute → verify → reflect

- **Decider interface** (`decider.py`): `async decide(question, options,
  context) -> {"choice", "confidence", "reason"}` and `async judge(claim,
  evidence) -> {"verdict", "confidence", "note"}`. Default implementation
  `llm-json`. Deciders are registered by id (pluggable; Jev is out of scope).
- **Route**: the decider picks a workflow (`workflow.*` capabilities, top-k by
  card BM25) or `generic` given goal + scope; result stored as
  `route = {"workflow": id|None, "loop": id, "reason", "confidence"}`. The user
  can force a workflow or loop in the request.
- **Loop plugins** (`loops/`): `plan-execute-verify` (current behaviour) and
  `react` (iterative: choose one capability per turn from top-k cards, observe,
  repeat until the decider says done or budget). Registered by id
  `loop:<name>`; a run uses the requested loop or the workflow's `loop.use`.
- **Workflow engine** (`workflow.py`): executes a v2 manifest's `steps`
  respecting `depends_on`, `map_over` (fan-out in parallel, one task per item),
  `fallback` chains (try next capability on error/empty result),
  `context: shared_session` (use the existing shared paper session via
  `registry.feature_context(..., context_mode="shared_session")`). After the
  steps, the run continues with synthesize → verify → reflect from the loop.
- **Built-in workflow** (first-party manifest file shipped in
  `backend/app/agent/workflows/literature_review.json`): exactly App. A of the
  proposal (plan → search with fallbacks → screen with the decider → read with
  `agent.reader` mapped over accepted papers → verify → write), loop stop
  `coverage 0.8, max_rounds 3`. Search results that are not in the library are
  imported (`library.import`, permission `library:write` → approval) before
  reading.
- **Reader sub-agent** `agent.reader` `{paper_id, question}`: isolated context
  containing only that paper's spans (top-k for the question), returns a
  schema-bound digest `{"relevance": 0..1, "findings": [{"claim", "span_ids"}]}`.
  Uses the shared paper session when the provider supports it.
- **Planner / verifier / writer** remain internal agent roles of the loop.
- **Stop reasons**: `goal_met`, `max_rounds`, `budget`, `no_progress`,
  `approval_needed`, `error`, `cancelled`.

## 4. Context management

- Working context with compaction (exists; keep ids when compacting).
- Evidence store of spans (exists, PDFs only).
- Session tier: reader sub-agents fork the shared paper session (above).
- **Project memory** (new, `projects.py`): research projects persisted as JSON
  under `<DATA_DIR>/projects/<id>.json`:
  `{id, name, description, created_at, memory: [{id, text, source_run, ts}],
  run_ids: [...]}`. A run may belong to a project; at plan time the top-k
  memory notes (BM25 against the goal) are added to the working context; when a
  run completes, its verified (supported) claims are appended as memory notes
  with span ids. Capabilities `memory.recall {query}` and
  `memory.note {text}` operate on the run's project.

## 5. HTTP API (contract shared by backend and frontend)

Existing endpoints keep working. Additions/changes:

- `POST /api/agent/runs` body adds optional `project_id`, `workflow`
  (`"auto"` default | `"generic"` | `"workflow.<id>"`), `loop`
  (`"auto"` | `"plan-execute-verify"` | `"react"`), `budget`
  (`{max_steps?, max_llm_calls?, max_tokens_est?}`).
- Run snapshot adds: `route`, `project_id`, `pending_approval`
  (`{id, step_id, capability, args, permissions, reason, requested_at}` or
  null), `budget` (`{steps, max_steps, llm_calls, max_llm_calls, tokens_est,
  max_tokens_est}`); `status` adds `awaiting_approval`.
- `POST /api/agent/runs/{id}/approvals/{approval_id}` body `{"approve": bool}`
  → snapshot.
- `GET /api/agent/capabilities` items add `permissions`, `requires_approval`,
  `trust`.
- `GET /api/agent/workflows` → `[{id, name, card, source}]`;
  `GET /api/agent/loops` → `[{id, card}]`.
- Projects: `GET /api/agent/projects`, `POST /api/agent/projects`
  `{name, description?}`, `GET /api/agent/projects/{id}` (with memory and
  runs summary), `POST /api/agent/projects/{id}/memory` `{text}`,
  `DELETE /api/agent/projects/{id}/memory/{memory_id}`.
- Connections: `GET`/`PUT /api/agent/connections` →
  `{mcp_servers: [...], ssh_hosts: [...], cli_agents: {claude: {enabled},
  codex: {enabled}}, auto_approve: [permission]}` (stored in the existing
  `config.json` under key `agent_connections`);
  `GET /api/agent/connections/status` →
  `{mcp: [{name, ok, tools, error}], ssh: [{name, ok, error}], cli: {claude: {available}, codex: {available}}}`.
- `POST /mcp` — MCP server (JSON-RPC 2.0).

## 6. Product branding

The product is **GlossX** everywhere a user sees a name: browser title, logo
wordmark, Windows executable `GlossX.exe` and its window title, CLI banner,
health endpoint `app` field, FastAPI title, npm package `glossx-local` with
bins `glossx` and `glossx-local`. GlossX uses its own data directory
(`GlossX` instead of `Gloss` in the platform paths; `GLOSS_DATA_DIR` still
overrides) and its own single-instance/window identity, so it can run next to
an installed Gloss. Internal module names and env vars stay unchanged.
