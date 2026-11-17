# GlossX — threads, paper workspaces and version control (spec)

Extends `docs/glossx-harness.md`. Three features: Codex-style organisation of
sessions ("threads") with an inbox, a workspace per paper, and git-backed
version control with branches.

## 1. Threads, projects and the inbox

- A session belongs to a project (`project_id`, or none = "General").
- New session fields: `last_activity_at` (last message/tool/status change),
  `last_viewed_at`, `unread` (bool: activity the user has not seen), and
  `attention`: `"approval"` | `"input"` | `"error"` | `"done"` | `null`
  (what the newest unseen event is; approval/input are *blocking*).
- `POST /api/sessions/{id}/read` marks a session viewed (clears `unread` and a
  non-blocking `attention`). Opening the SSE stream does **not** mark it read;
  the UI calls `/read` when the thread is actually shown and focused.
- `GET /api/sessions` items include those fields plus `preview` (last assistant
  text, ≤160 chars) and `project` `{id, name}`; `?view=inbox` returns sessions
  ordered for the inbox: blocking attention first, then unread, then the rest,
  each group by `last_activity_at` descending. Default order stays by project
  then recency.
- `GET /api/sessions/inbox/summary` → `{unread, blocking, items: [{session_id,
  title, project, attention, last_activity_at, preview}]}` (top 20).
- `GET /api/sessions/inbox/events` (SSE): `inbox.updated` with the summary
  whenever any session's unread/attention changes, so the badge is live even
  for sessions whose stream is not open. Replays the summary on connect.
- UI: a **Threads** activity in the side bar. Default view groups threads under
  their project (collapsible; "General" for none; each project row has "new
  thread" and shows its workspace branch). A bell icon with an unread badge
  toggles the **Inbox** view: a flat list ordered as above with attention chips
  (Needs approval / Needs answer / Finished / Failed), unread dots, relative
  times and previews; "mark all read". Clicking a thread opens it in the AI
  pane and marks it read. The status bar shows the blocking count.

## 2. A workspace per paper

Workspace location extension: `paper_workspace_root` in `/api/settings`
sets the base folder for newly created paper projects only (blank retains
`DATA_DIR/workspaces`). Existing locations remain unchanged. An explicit
`POST /api/workspace/{project_id}/location {path, confirm:true}` copies a
workspace to a new or empty absolute directory, verifies SHA-256 manifests
including Git metadata and `.glossx` checkpoints, then updates project metadata.
The old directory is preserved. Running sessions and concurrent workspace access
are blocked while relocation runs; nested or overlapping destinations are rejected.

- When a paper is added to the library (upload or import job completion), a
  project is created for it automatically: `{name: <paper title, trimmed>,
  kind: "paper", paper_ids: [paper_id]}` with its workspace directory
  `<DATA_DIR>/workspaces/<project_id>/` seeded with `GLOSSX.md` (title,
  authors, year, paper id, how to cite spans) and `notes/README.md`.
- Projects gain `kind` (`"paper"` | `"research"`), `paper_ids` (a research
  project can hold several papers) and `workspace: {key, path, vcs}`.
  Endpoints: `GET /api/agent/projects?paper_id=`, `POST
  /api/agent/projects/{id}/papers` `{paper_id}`, `DELETE
  /api/agent/projects/{id}/papers/{paper_id}`, and `GET
  /api/papers/{id}/project` (creates the paper project lazily for papers that
  predate this feature).
- Deleting a paper keeps its project and files (only unlinks the paper), so
  notes are never lost silently.
- A session created while a paper is the active tab defaults to that paper's
  project and has the paper in its scope. The workspace explorer follows the
  active project.

## 3. Version control (git-backed)

- Every workspace is a real git repository, initialised on creation (`git
  init -b main`, local identity `GlossX <glossx@local>` unless the user has a
  git identity, a `.gitignore` for caches). Uses the system `git` binary via
  `platform_support` helpers; when git is not installed `vcs.available` is
  false, every VCS endpoint returns 409 with a clear message, and the existing
  checkpoint mechanism keeps working.
- **Auto-commit**: when a session turn ends and the workspace has changes, the
  harness commits them (`<session title>: <first line of the user request>`
  with trailers `Session:` and `Turn:`); toggle `vcs.auto_commit` in harness
  settings (default on). User edits saved from the editor are left uncommitted
  until the next auto-commit or a manual commit.
- Checkpoints stay as the fine-grained undo within a turn; commits are the
  durable history.
- API (all under `/api/workspace/{project_id}/vcs`):
  `GET status` → `{available, branch, branches: [{name, current, head,
  updated_at}], changes: [{path, status, staged}], ahead_of: {...}|null,
  clean}`; `GET log?branch=&limit=&path=` → `[{id, short, message, author,
  time, parents, refs, files_changed, session_id?}]`; `GET diff?from=&to=&path=`
  (working tree when `to` is empty) → unified diff text; `GET
  file?rev=&path=`; `POST commit` `{message, paths?}`; `POST branches`
  `{name, from?}`; `POST checkout` `{branch}` (refuses with 409 when there are
  uncommitted changes unless `{stash: true}` / `{discard: true}`); `POST merge`
  `{branch}` → `{merged, fast_forward, conflicts: [paths]}` (on conflict the
  merge is aborted and reported, never left half-done); `DELETE
  branches/{name}` (not the current branch); `POST restore` `{rev, paths?}`
  (restores files from a revision as a new commit); `POST discard` `{paths}`.
- Sessions record the branch they ran on (`branch` in the session summary); a
  session may be started with `{branch: "<new or existing>"}` to work on a
  branch ("try another approach on a branch").
- Agent tools (category write/exec rules apply; reads are allowed in ask
  mode): `vcs_status`, `vcs_log`, `vcs_diff`, `vcs_commit`, `vcs_branch`
  (create), `vcs_checkout`, `vcs_merge`.
- UI: a **Source control** activity (changes list with per-file diff, commit
  message box, discard, history list with per-commit diff and "restore this
  version"), a branch switcher in the status bar (current branch, create /
  switch / merge / delete), a branch chip on threads, and a compact branch
  graph in the history. All state-changing actions confirm when they would
  lose work.
