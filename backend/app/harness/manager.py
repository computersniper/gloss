"""API-facing session manager: owns every session's runtime in this process.

Concurrency model: everything runs on the server's event loop. Each session has
at most one running task (a turn or a manual compaction); many sessions run
side by side. Subagent sessions are driven inline by their parent's task.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, AsyncIterator

from ..agent import projects
from ..providers import registry as providers
from . import context as ctx_mod
from . import engine, extensions, vcs, workspace
from . import settings as settings_mod

REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
from .session import (
    TERMINAL_TOOL,
    Runtime,
    copy_result,
    delete_files,
    load_all,
    new_id,
    new_session,
    now,
    public_message,
    read_result,
    recover,
    valid_id,
)

logger = logging.getLogger(__name__)

DECISIONS = ("once", "always", "deny")
KEEPALIVE_SECONDS = 15.0
INBOX_ITEMS = 20
BLOCKING = ("approval", "input")


class SessionNotFoundError(LookupError):
    pass


class SessionBusyError(RuntimeError):
    """A turn is already running in this session (HTTP 409)."""


class NotPendingError(RuntimeError):
    """No such approval / input request is pending (HTTP 409)."""


class SessionManager:
    def __init__(self, *, agent_runs: Any | None = None, import_jobs: Any | None = None) -> None:
        self.agent_runs = agent_runs
        self.import_jobs = import_jobs
        self._sessions: dict[str, Runtime] = {}
        self._accepting = True
        self._streams_open = True
        self._inbox_subscribers: list[asyncio.Queue] = []
        self._inbox_seq = 0
        self._inbox_scheduled = False
        for data in load_all():
            self._sessions[data["id"]] = Runtime(data, self)

    # -- lookup ---------------------------------------------------------------------------
    def _rt(self, session_id: str) -> Runtime:
        rt = self._sessions.get(session_id) if valid_id(session_id) else None
        if rt is None:
            raise SessionNotFoundError(session_id)
        return rt

    def register_child(self, rt: Runtime) -> None:
        self._sessions[rt.id] = rt

    def session_data(self, session_id: Any) -> dict[str, Any] | None:
        rt = self._sessions.get(session_id) if isinstance(session_id, str) else None
        return rt.session if rt is not None else None

    # -- validation -----------------------------------------------------------------------
    @staticmethod
    def _check_mode(mode: Any) -> str:
        if mode not in settings_mod.MODES:
            raise ValueError(f"mode must be one of: {', '.join(settings_mod.MODES)}")
        return mode

    @staticmethod
    def _check_model(provider: Any, model: Any) -> tuple[str | None, str | None]:
        provider = (str(provider).strip() or None) if provider is not None else None
        model = (str(model).strip() or None) if model is not None else None
        if provider and provider not in providers.available():
            raise ValueError(f"unknown provider: {provider}")
        if model and len(model) > 120:
            raise ValueError("model is too long")
        return provider, model

    @staticmethod
    def _check_effort(effort: Any) -> str | None:
        effort = (str(effort).strip().lower() or None) if effort is not None else None
        if effort and effort not in REASONING_EFFORTS:
            raise ValueError(f"unknown reasoning effort: {effort}")
        return effort

    @staticmethod
    def _check_project(project_id: Any) -> str | None:
        if project_id in (None, "", "default"):
            return None
        if not isinstance(project_id, str) or not projects.exists(project_id):
            raise LookupError(f"project not found: {project_id}")
        if projects.get(project_id).get("archived"):
            raise ValueError("restore the project before starting a thread")
        return project_id

    # -- projects / workspaces ---------------------------------------------------------------
    @staticmethod
    def project_ref(project_id: str | None) -> dict[str, Any] | None:
        """``{id, name, kind}`` of a session's project (``None`` = "General")."""
        return projects.ref(project_id)

    def busy_sessions(self, project_id: str | None) -> list[dict[str, Any]]:
        """Root sessions with a turn in flight in this project's workspace."""
        return [{"id": rt.id, "title": rt.session["title"], "status": rt.session["status"]}
                for rt in self._sessions.values()
                if rt.parent is None and not rt.session.get("parent_id")
                and (rt.session.get("project_id") or None) == (project_id or None) and rt.busy()]

    def workspace_changed(self, project_id: str | None) -> None:
        """The workspace's branch changed outside a turn: refresh open threads."""
        for rt in self._sessions.values():
            if rt.parent is None and (rt.session.get("project_id") or None) == (project_id or None):
                rt.emit("session.updated", rt.public())

    # -- inbox ----------------------------------------------------------------------------
    @staticmethod
    def _inbox_rank(item: dict[str, Any]) -> tuple[int, float]:
        """Blocking attention first, then unread, then the rest; newest first."""
        group = 0 if item.get("attention") in BLOCKING else 1 if item.get("unread") else 2
        return group, -float(item.get("last_activity_at") or 0)

    def _roots(self) -> list[Runtime]:
        return [rt for rt in self._sessions.values() if not rt.session.get("parent_id")]

    def inbox_summary(self) -> dict[str, Any]:
        items = sorted((rt.public() for rt in self._roots() if not rt.session.get("archived")), key=self._inbox_rank)
        return {
            "unread": sum(1 for s in items if s["unread"]),
            "blocking": sum(1 for s in items if s["attention"] in BLOCKING),
            "total": len(items),
            "items": [{"session_id": s["id"], "title": s["title"], "project": s["project"],
                       "project_id": s["project_id"], "attention": s["attention"],
                       "unread": s["unread"], "status": s["status"], "branch": s["branch"],
                       "last_activity_at": s["last_activity_at"], "preview": s["preview"]}
                      for s in items[:INBOX_ITEMS]],
        }

    def inbox_changed(self) -> None:
        """Publish ``inbox.updated`` (coalesced: many changes in one loop step
        produce one event)."""
        if not self._inbox_subscribers or self._inbox_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._publish_inbox()
            return
        self._inbox_scheduled = True
        loop.call_soon(self._publish_inbox)

    def _publish_inbox(self) -> None:
        self._inbox_scheduled = False
        if not self._inbox_subscribers:
            return
        try:
            summary = self.inbox_summary()
        except Exception:
            logger.exception("could not build the inbox summary")
            return
        self._inbox_seq += 1
        for q in list(self._inbox_subscribers):
            q.put_nowait(("inbox.updated", summary, self._inbox_seq))

    async def subscribe_inbox(self) -> AsyncIterator[tuple[str | None, Any, int]]:
        """Yield ``("inbox.updated", summary, seq)``; the summary is replayed first."""
        queue: asyncio.Queue = asyncio.Queue()
        self._inbox_subscribers.append(queue)
        try:
            yield "inbox.updated", self.inbox_summary(), self._inbox_seq
            while self._streams_open:
                try:
                    item = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except (asyncio.TimeoutError, TimeoutError):
                    yield None, None, 0
                    continue
                if item is None:
                    return
                yield item
        finally:
            if queue in self._inbox_subscribers:
                self._inbox_subscribers.remove(queue)

    def mark_read(self, session_id: str) -> dict[str, Any]:
        rt = self._rt(session_id)
        rt.mark_read()
        return rt.public()

    def mark_all_read(self, project_id: str | None = None) -> dict[str, Any]:
        marked = 0
        for rt in self._roots():
            if project_id is not None:
                want = None if project_id in ("", "default") else project_id
                if rt.session.get("project_id") != want:
                    continue
            if rt.mark_read():
                marked += 1
        return {"marked": marked, "summary": self.inbox_summary()}

    # -- CRUD -----------------------------------------------------------------------------
    def list(self, *, project_id: str | None = None, include_subagents: bool = False,
             view: str | None = None, archived: bool = False) -> list[dict[str, Any]]:
        out = []
        for rt in self._sessions.values():
            if bool(rt.session.get("archived")) != archived:
                continue
            if rt.session.get("parent_id") and not include_subagents:
                continue
            if project_id is not None:
                want = None if project_id in ("", "default") else project_id
                if rt.session.get("project_id") != want:
                    continue
            out.append(rt.public())
        if view == "inbox":
            return sorted(out, key=self._inbox_rank)
        return sorted(out, key=lambda s: ((s.get("project") or {}).get("name", "").casefold(), -s["last_activity_at"]))

    def create(self, *, title: str | None = None, project_id: str | None = None,
               mode: str | None = None, provider: str | None = None,
               model: str | None = None, branch: str | None = None,
               paper_id: str | None = None, effort: str | None = None) -> dict[str, Any]:
        """``branch`` must already be checked out (see ``prepare_branch``);
        ``paper_id`` puts that paper in the session's scope."""
        if not self._accepting:
            raise SessionBusyError("the server is shutting down")
        project_id = self._check_project(project_id)
        root = workspace.root_for(project_id)
        settings = settings_mod.load(root)
        mode = self._check_mode(mode or settings["default_mode"])
        provider, model = self._check_model(provider, model)
        session = new_session(title=title, project_id=project_id, mode=mode,
                              provider=provider, model=model,
                              context_limit=int(settings["context_limit"]))
        effort = self._check_effort(effort)
        if effort:
            session["model"]["effort"] = effort
        scope: list[str] = []
        if project_id:
            try:
                scope.extend(projects.get(project_id).get("paper_ids") or [])
            except LookupError:
                pass
        if paper_id and paper_id not in scope:
            scope.insert(0, paper_id)
        session["scope_papers"] = scope[:30]
        session["branch"] = branch or vcs.head_branch(root)
        vcs.ensure_repo_in_background(workspace.key_for(project_id))
        rt = Runtime(session, self)
        self._sessions[rt.id] = rt
        rt.persist()
        self.inbox_changed()
        return rt.public(with_messages=True)

    def resolve_new_session(self, project_id: Any, paper_id: Any) -> tuple[str | None, str | None]:
        """Validate ``project_id`` / ``paper_id`` of a create request. A paper
        without a project selects the paper's own project."""
        from ..library import store

        project = self._check_project(project_id)
        paper = None
        if paper_id not in (None, ""):
            data = store.get_paper(str(paper_id))
            if not data:
                raise LookupError(f"paper not found: {paper_id}")
            paper = data["id"]
            if project is None and project_id in (None, ""):
                own = projects.ensure_paper_project_quietly(data)
                project = own["id"] if own else None
        return project, paper

    async def prepare_branch(self, project_id: str | None, branch: Any) -> str | None:
        """Put the workspace on ``branch`` (created when new) for a new session."""
        if branch in (None, ""):
            return None
        name = vcs.check_branch_name(branch)
        if self.busy_sessions(project_id) and vcs.head_branch(workspace.root_for(project_id)) != name:
            raise SessionBusyError("another thread is working in this workspace; wait for it to "
                                   "finish before you start a thread on a different branch")
        name = await asyncio.to_thread(vcs.start_branch, workspace.key_for(project_id), name)
        self.workspace_changed(project_id)
        return name

    def get(self, session_id: str) -> dict[str, Any]:
        return self._rt(session_id).public(with_messages=True)

    def digest(self, session_id: str, *, max_messages: int = 10, max_chars: int = 9000) -> str:
        """A read-only recap of a session's recent conversation, for a side chat:
        the latest user and assistant texts (no tool output, no attachment bodies)."""
        rt = self._rt(session_id)
        session = rt.session
        lines: list[str] = []
        for msg in reversed(session.get("messages") or []):
            if msg.get("local") or msg.get("type") not in ("user", "assistant"):
                continue
            text = " ".join(str(msg.get("text") or "").split())
            if not text:
                continue
            who = "User" if msg["type"] == "user" else "Agent"
            labels = [a.get("label") or a.get("ref") for a in msg.get("attachments") or [] if a.get("ok")]
            extra = f" [attached: {', '.join(str(l)[:60] for l in labels[:5])}]" if labels else ""
            lines.append(f"{who}: {text[:900]}{'…' if len(text) > 900 else ''}{extra}")
            if len(lines) >= max_messages:
                break
        lines.reverse()
        head = f"Thread: {session.get('title') or 'untitled'}"
        papers = [p for p in session.get("scope_papers") or []][:8]
        if papers:
            from ..library import store as library
            titles = [(library.get_paper(p) or {}).get("title") or p for p in papers]
            head += "\nPapers in scope: " + "; ".join(t[:80] for t in titles)
        body = "\n".join(lines) or "(no messages yet)"
        return (head + "\n" + body)[-max_chars:]

    def patch(self, session_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        rt = self._rt(session_id)
        session = rt.session
        if fields.get("archived") is not None:
            if rt.busy():
                raise SessionBusyError("wait for the thread to finish before archiving it")
            if not fields["archived"] and session.get("project_id"):
                project = projects.get(session["project_id"])
                if project.get("archived") and not project.get("removed"):
                    raise ValueError("restore the project before restoring its threads")
            session["archived"] = bool(fields["archived"])
            session.pop("archived_by_project", None)
            self.inbox_changed()
        if "title" in fields and fields["title"] is not None:
            title = str(fields["title"]).strip()
            if not title:
                raise ValueError("title must not be empty")
            session["title"] = title[:200]
            session["title_set"] = True
        if "model" in fields or "provider" in fields:
            if rt.parent is not None:
                raise ValueError("subagent sessions cannot be reconfigured")
            current = dict(session["model"])
            value = fields.get("model")
            if isinstance(value, dict):
                for key in ("provider", "model", "effort"):
                    if key in value:
                        current[key] = value[key]
            elif "model" in fields:
                current["model"] = value
            if "provider" in fields:
                current["provider"] = fields["provider"]
            provider, model = self._check_model(current.get("provider"), current.get("model"))
            effort = self._check_effort(current.get("effort"))
            session["model"] = {"provider": provider, "model": model, **({"effort": effort} if effort else {})}
        if fields.get("mode") is not None:
            if rt.parent is not None:
                raise ValueError("subagent sessions cannot be reconfigured")
            engine.set_mode(rt, self._check_mode(fields["mode"]))
        rt.emit("session.updated", rt.public())
        rt.persist()
        return rt.public(with_messages=True)

    def move_project(self, session_id: str, project_id: str | None) -> dict[str, Any]:
        rt = self._rt(session_id)
        if rt.parent is not None or rt.session.get("parent_id"):
            raise ValueError("move the parent thread instead of a subagent")
        if rt.busy():
            raise SessionBusyError("wait for this thread to finish before moving it")
        target = self._check_project(project_id)
        old = rt.session.get("project_id")
        if target == old:
            return rt.public(with_messages=True)
        if workspace.is_relocating(old) or workspace.is_relocating(target):
            raise SessionBusyError("wait for workspace relocation before moving this thread")
        root = workspace.root_for(target)
        branch = vcs.head_branch(root)
        scope = projects.get(target).get("paper_ids", []) if target else []
        changed_at = now()
        descendants = [c for c in self._sessions.values() if c.session.get("parent_id") == rt.id]
        for item in [rt, *descendants]:
            session = item.session
            session["project_id"] = target
            session["branch"] = branch
            session["scope_papers"] = list(dict.fromkeys([*scope, *session.get("scope_papers", [])]))[:30]
            session["workspace_changed_at"] = changed_at
            session.pop("provider_thread", None)
            session.pop("archived_by_project", None)
            engine.add_note(item, "cache_reset", "Thread moved to a different project. Future tools use the new workspace; earlier files and commits remain in the original workspace.", reason="project_changed")
            item.persist()
            item.emit("session.updated", item.public(with_messages=True))
        vcs.ensure_repo_in_background(workspace.key_for(target))
        self.inbox_changed()
        return rt.public(with_messages=True)

    def project_archived(self, project_id: str, archived: bool) -> None:
        """Restore only threads archived by this project, not individually
        archived threads. Called after all project busy checks have passed."""
        for rt in self._sessions.values():
            if rt.session.get("project_id") != project_id:
                continue
            if archived and not rt.session.get("archived"):
                rt.session["archived"] = True
                rt.session["archived_by_project"] = project_id
            elif not archived and rt.session.get("archived_by_project") == project_id:
                rt.session["archived"] = False
                rt.session.pop("archived_by_project", None)
            rt.persist()
            rt.emit("session.updated", rt.public())
        self.inbox_changed()

    async def delete(self, session_id: str) -> None:
        rt = self._rt(session_id)
        await self._stop(rt)
        doomed = [rt] + [c for c in self._sessions.values() if c.session.get("parent_id") == rt.id]
        for item in doomed:
            item.close_subscribers()
            self._sessions.pop(item.id, None)
            delete_files(item.id)
        self.inbox_changed()

    def fork(self, session_id: str, message_id: str | None) -> dict[str, Any]:
        src = self._rt(session_id)
        if src.session.get("archived"):
            raise ValueError("restore the archived thread before forking it")
        messages = src.session["messages"]
        if message_id:
            index = next((i for i, m in enumerate(messages) if m.get("id") == message_id), None)
            if index is None:
                raise LookupError("message not found in this session")
            messages = messages[: index + 1]
        data = copy.deepcopy({k: v for k, v in src.session.items() if k != "messages"})
        fresh = new_session(title=None, project_id=data.get("project_id"), mode=data["mode"],
                            provider=data["model"].get("provider"), model=data["model"].get("model"),
                            context_limit=int(data["context"].get("limit") or 120_000))
        fresh["title"] = (src.session["title"][:190] + " (fork)")
        fresh["title_set"] = True
        fresh["todos"] = data.get("todos") or []
        fresh["scope_papers"] = data.get("scope_papers") or []
        fresh["branch"] = data.get("branch")
        fresh["forked_from"] = {"session_id": src.id, "message_id": message_id}
        fresh["context"]["compactions"] = int(data["context"].get("compactions") or 0)
        fresh["messages"] = copy.deepcopy(messages)
        recover(fresh)
        for msg in fresh["messages"]:
            if msg.get("type") == "tool_call":
                msg["session_id"] = fresh["id"]
                if msg.get("result_ref"):
                    try:
                        copy_result(src.id, fresh["id"], msg["result_ref"])
                    except OSError:
                        msg["result_ref"] = None
        through = None
        for msg in fresh["messages"]:
            if msg.get("compacted") and msg.get("type") != "system_note":
                through = msg["id"]
        if through:
            fresh["context"]["compacted_through"] = through
        ctx_mod.recount(fresh)
        rt = Runtime(fresh, self)
        engine.add_note(rt, "cache_reset", "Prompt cache reset: conversation forked.", reason="fork")
        self._sessions[rt.id] = rt
        rt.persist()
        return rt.public(with_messages=True)

    async def rewind(self, session_id: str, message_id: str, *, include: bool = False,
                     restore_files: bool = False) -> dict[str, Any]:
        """Go back to ``message_id`` in place: drop every later message (and the
        message itself unless ``include``). With ``restore_files`` the file edits
        this thread (and its subagents) made since then are reverted too."""
        rt = self._rt(session_id)
        if rt.parent is not None or rt.session.get("parent_id"):
            raise ValueError("subagent sessions are read-only")
        if rt.busy():
            raise SessionBusyError("a turn is running in this session; stop it before you rewind")
        session = rt.session
        messages = session["messages"]
        index = next((i for i, m in enumerate(messages) if m.get("id") == message_id), None)
        if index is None:
            raise LookupError("message not found in this session")
        cut = index + 1 if include else index
        removed = messages[cut:]
        if not removed:
            return {"removed": 0, "reverted": [], "conflicts": [], "session": rt.public(with_messages=True)}
        since = min(float(m.get("created_at") or 0) for m in removed) or None
        if restore_files and since and since < float(session.get("workspace_changed_at") or 0):
            raise ValueError("cannot restore file edits across a project move; use the original workspace history")
        reverted: list[str] = []
        conflicts: list[str] = []
        if restore_files and since:
            key = workspace.key_for(session.get("project_id"))
            owners = {rt.id} | {c.id for c in self._sessions.values() if c.session.get("parent_id") == rt.id}

            def restore() -> None:
                for cp in workspace.list_checkpoints(key, limit=workspace.MAX_CHECKPOINTS):  # newest first
                    if (cp.get("session_id") in owners and cp.get("status") == "applied"
                            and cp.get("source") != "revert" and (cp.get("created_at") or 0) >= since):
                        out = workspace.revert_checkpoint(key, cp["id"])
                        reverted.append(cp["path"])
                        if out.get("conflict"):
                            conflicts.append(cp["path"])

            await asyncio.to_thread(restore)
        session["messages"] = messages[:cut]
        session.pop("provider_thread", None)  # the provider's own thread still holds the dropped turns
        through = None
        for msg in session["messages"]:
            if msg.get("compacted") and msg.get("type") != "system_note":
                through = msg["id"]
        if through:
            session["context"]["compacted_through"] = through
        else:
            session["context"].pop("compacted_through", None)
        ctx_mod.recount(session)
        engine.add_note(rt, "cache_reset", "Prompt cache reset: conversation rewound.", reason="rewind")
        rt.emit("context.updated", dict(session["context"]))
        rt.emit("session.updated", rt.public(with_messages=True))
        rt.persist()
        if reverted:
            self.workspace_changed(session.get("project_id"))
        return {"removed": len(removed), "reverted": sorted(set(reverted)),
                "conflicts": sorted(set(conflicts)), "session": rt.public(with_messages=True)}

    def result(self, session_id: str, ref: str) -> str:
        self._rt(session_id)
        text = read_result(session_id, ref)
        if text is None:
            raise LookupError("result not found")
        return text

    # -- turns ----------------------------------------------------------------------------
    def _reserve(self, rt: Runtime) -> None:
        if rt.session.get("archived"):
            raise ValueError("restore this archived thread before sending a message")
        if rt.session.get("project_id"):
            project = projects.get(rt.session["project_id"])
            if project.get("archived") and not project.get("removed"):
                raise ValueError("restore this archived project before sending a message")
        if workspace.is_relocating(rt.session.get("project_id")):
            raise SessionBusyError("workspace relocation is in progress")
        if not self._accepting:
            raise SessionBusyError("the server is shutting down")
        if rt.parent is not None or rt.session.get("parent_id"):
            raise ValueError("subagent sessions are read-only")
        if rt.busy():
            raise SessionBusyError("a turn is already running in this session")
        rt.running = True

    async def send_message(self, session_id: str, text: str,
                           attachments: Any = None) -> dict[str, Any]:
        rt = self._rt(session_id)
        text = (text or "").strip()
        if not text and not attachments:
            raise ValueError("text must not be empty")
        if len(text) > 100_000:
            raise ValueError("text is too long (max 100000 characters)")
        self._reserve(rt)
        started = False
        try:
            root = workspace.root_for(rt.session.get("project_id"))
            settings = settings_mod.load(root)
            parsed = extensions.parse_command(text)
            command = extensions.find_command(parsed[0], root) if parsed else None
            if command is not None and command["name"] in extensions.LOCAL_COMMANDS:
                if command["name"] != "clear":
                    self._add_user(rt, text, [], command=command["name"], settings=settings, root=root)
                out = await self._local_command(rt, command["name"], parsed[1], root)
                return {"started": False, "command": command["name"], **out,
                        "session": rt.public()}
            expanded = None
            if command is not None:
                expanded = self._expand(command, parsed[1])
                if command.get("mode"):
                    engine.set_mode(rt, command["mode"])
            items = ctx_mod.normalize_attachments(attachments, text, root)
            msg = self._add_user(rt, text, items, command=command["name"] if command else None,
                                 expanded=expanded, settings=settings, root=root)
            rt.session["branch"] = vcs.head_branch(root) or rt.session.get("branch")
            rt.task = asyncio.create_task(engine.drive_turn(rt), name=f"glossx-turn-{rt.id[:8]}")
            started = True
            rt.refresh_status()
            return {"started": True, "command": command["name"] if command else None,
                    "message": public_message(msg), "session": rt.public()}
        finally:
            if not started and (rt.task is None or rt.task.done()):
                rt.running = False
                rt.refresh_status()

    @staticmethod
    def _expand(command: dict[str, Any], arguments: str) -> str:
        name = command["name"]
        if command.get("kind") == "builtin":
            if name == "review":
                if not arguments:
                    raise ValueError("usage: /review <question>")
                return ("Run the literature-review workflow with the run_workflow tool on this "
                        f"question, then present its cited report and the claim verdicts:\n\n{arguments}")
            if name == "summarize":
                focus = f" Focus on: {arguments}." if arguments else ""
                return ("Summarize the paper(s) attached to this session (use paper_summary and "
                        "paper_read as needed and cite spans). If no paper is attached, summarize "
                        f"this conversation so far instead.{focus}")
        prompt = extensions.expand(command, arguments).strip()
        if not prompt:
            raise ValueError(f"command /{name} has an empty prompt")
        return prompt

    def _add_user(self, rt: Runtime, text: str, attachments: list[dict[str, Any]], *,
                  command: str | None = None, expanded: str | None = None,
                  settings: dict[str, Any], root: Any) -> dict[str, Any]:
        session = rt.session
        msg: dict[str, Any] = {"id": new_id("m_"), "type": "user", "text": text,
                               "attachments": attachments, "created_at": now()}
        if command:
            msg["command"] = command
        if expanded:
            msg["expanded"] = expanded
        if attachments:
            msg["context"] = ctx_mod.materialize(
                rt, attachments, budget_tokens=int(settings.get("mention_budget") or 12_000),
                workspace_root=root)
        if command in extensions.LOCAL_COMMANDS:
            msg["local"] = True  # local commands never reach the model
        ctx_mod.stamp(msg)
        engine.add_message(rt, msg)
        if not session.get("title_set") and not command:
            line = " ".join(text.split())
            if line:
                session["title"] = line[:60] + ("..." if len(line) > 60 else "")
                session["title_set"] = True
                rt.emit("session.updated", rt.public())
        rt.persist()
        return msg

    async def _local_command(self, rt: Runtime, name: str, arguments: str, root: Any
                             ) -> dict[str, Any]:
        session = rt.session

        def note(text: str) -> dict[str, Any]:
            n = engine.add_note(rt, "command", text, command=name)
            rt.persist()
            return {"note": public_message(n)}

        if name == "help":
            lines = ["Available commands:"]
            for cmd in extensions.list_commands(root):
                hint = f" {cmd['argument_hint']}" if cmd.get("argument_hint") else ""
                lines.append(f"- `/{cmd['name']}{hint}` - {cmd.get('description') or ''} ({cmd['source']})")
            return note("\n".join(lines))
        if name == "todos":
            marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
            todos = session.get("todos") or []
            return note("Todos:\n" + ("\n".join(f"- {marks.get(t['status'], '[ ]')} {t['content']}"
                                                for t in todos) or "(none)"))
        if name == "mode":
            if not arguments:
                return note(f"Mode: {session['mode']} (available: {', '.join(settings_mod.MODES)})")
            engine.set_mode(rt, self._check_mode(arguments.split()[0].lower()))
            return {"mode": session["mode"]}
        if name == "model":
            if not arguments:
                current = session["model"]
                return note(f"Model: {current.get('provider') or '(default provider)'}"
                            f"{':' + current['model'] if current.get('model') else ''}. "
                            f"Providers: {', '.join(providers.available())}")
            head, _, tail = arguments.strip().partition(":")
            if head in providers.available():
                provider, model = self._check_model(head, tail or None)
            else:
                provider, model = self._check_model(session["model"].get("provider"), arguments.strip())
            session["model"] = {"provider": provider, "model": model,
                                **({"effort": session["model"]["effort"]} if session["model"].get("effort") else {})}
            rt.emit("session.updated", rt.public())
            return note(f"Model set to {provider or '(default provider)'}{':' + model if model else ''}.")
        if name == "clear":
            session["messages"] = []
            session["todos"] = []
            session["scope_papers"] = []
            session["context"].update(tokens_est=0, percent=0.0, system_tokens_est=0)
            session["context"].pop("compacted_through", None)
            rt.emit("todos.updated", {"todos": []})
            rt.emit("context.updated", dict(session["context"]))
            rt.emit("session.updated", rt.public(with_messages=True))
            rt.persist()
            return {"cleared": True}
        if name == "compact":
            rt.task = asyncio.create_task(engine.drive_compaction(rt, arguments),
                                          name=f"glossx-compact-{rt.id[:8]}")
            rt.refresh_status()
            return await asyncio.shield(rt.task)
        raise ValueError(f"unknown command: /{name}")

    async def compact(self, session_id: str, focus: str = "") -> dict[str, Any]:
        rt = self._rt(session_id)
        self._reserve(rt)
        try:
            rt.task = asyncio.create_task(engine.drive_compaction(rt, focus),
                                          name=f"glossx-compact-{rt.id[:8]}")
        except BaseException:
            rt.running = False
            raise
        rt.refresh_status()
        result = await asyncio.shield(rt.task)
        return {**result, "session": rt.public()}

    async def _stop(self, rt: Runtime) -> None:
        task = rt.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=15)
            except (asyncio.CancelledError, asyncio.TimeoutError, TimeoutError):
                pass
            except Exception:
                logger.exception("turn task failed while stopping")
        if rt.task is not None and rt.task.done():
            rt.task = None
        if rt.task is None and rt.running and rt.parent is None:
            # cancelled before the task body ran: clean up here
            engine._abort_pending(rt)
            engine.finish_turn(rt, {"stop_reason": "interrupted", "rounds": 0})

    async def interrupt(self, session_id: str) -> dict[str, Any]:
        rt = self._rt(session_id)
        if rt.parent is not None:
            rt = rt.root
        was_running = rt.busy()
        await self._stop(rt)
        return {"interrupted": was_running, "session": rt.public()}

    # -- approvals / input ------------------------------------------------------------------
    def resolve_approval(self, session_id: str, tool_call_id: str, decision: str,
                         feedback: str | None = None) -> dict[str, Any]:
        rt = self._rt(session_id)
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of: {', '.join(DECISIONS)}")
        entry = next((a for a in rt.all_approvals() if a["tool_call_id"] == tool_call_id), None)
        future = entry.get("future") if entry else None
        if entry is None or future is None or future.done():
            raise NotPendingError("no approval is pending for this tool call")
        future.set_result((decision, (feedback or "").strip()[:4000]))
        return {"tool_call_id": tool_call_id, "decision": decision,
                "rule_added": entry.get("suggested_rule") if decision == "always" else None,
                "session": rt.public()}

    def answer_input(self, session_id: str, answer: str,
                     tool_call_id: str | None = None) -> dict[str, Any]:
        rt = self._rt(session_id)
        answer = (answer or "").strip()
        if not answer:
            raise ValueError("answer must not be empty")
        pending = rt.all_inputs()
        if tool_call_id:
            pending = [p for p in pending if p["tool_call_id"] == tool_call_id]
        entry = pending[0] if pending else None
        future = entry.get("future") if entry else None
        if entry is None or future is None or future.done():
            raise NotPendingError("the session is not waiting for input")
        future.set_result(answer[:20_000])
        return {"tool_call_id": entry["tool_call_id"], "session": rt.public()}

    # -- events ---------------------------------------------------------------------------
    async def subscribe(self, session_id: str) -> AsyncIterator[tuple[str | None, Any, int]]:
        """Yield ``(event, payload, seq)``; the current state is replayed first.
        ``(None, None, 0)`` is a keep-alive tick."""
        rt = self._rt(session_id)
        queue = rt.subscribe()
        replay = rt.replay()  # no await since subscribing: no gap, no duplicate
        seq = rt.seq
        try:
            for name, payload in replay:
                yield name, payload, seq
            while self._streams_open:
                try:
                    item = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except (asyncio.TimeoutError, TimeoutError):
                    yield None, None, 0
                    continue
                if item is None:
                    return
                yield item
        finally:
            rt.unsubscribe(queue)

    def close_streams(self) -> None:
        """End every SSE stream (the server is about to stop: open event streams
        would otherwise keep it waiting for connections to close)."""
        self._streams_open = False
        for rt in list(self._sessions.values()):
            rt.close_subscribers()
        for q in list(self._inbox_subscribers):
            q.put_nowait(None)
        self._inbox_subscribers.clear()

    async def shutdown(self) -> None:
        self._accepting = False
        self.close_streams()
        roots = [rt for rt in self._sessions.values() if rt.parent is None]
        await asyncio.gather(*(self._stop(rt) for rt in roots), return_exceptions=True)
        for rt in self._sessions.values():
            for msg in rt.session["messages"]:
                if msg.get("type") == "tool_call" and msg.get("status") not in TERMINAL_TOOL:
                    msg["status"] = "error"
            rt.close_subscribers()
