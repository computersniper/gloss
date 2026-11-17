"""Slash commands, subagent types and hooks -- all discovered live from plugins.

Sources (later sources never shadow earlier ones with the same name, except
project files, which override user files):

* commands: built-ins; ``<workspace>/.glossx/commands/*.md``;
  ``<DATA_DIR>/commands/*.md``; Agent Skills (``/<skill>``); v2 manifests of
  kind ``command``.
* subagent types: built-ins ``general``, ``reader``, ``reviewer``, ``coder``,
  ``searcher``; ``<workspace>/.glossx/agents/*.md``; ``<DATA_DIR>/agents/*.md``;
  v2 manifests of kind ``agent`` that carry a prompt.
* hooks: settings ``hooks``; v2 manifests of kind ``hook`` from plugins listed
  in ``trusted_plugins``.

Markdown files have YAML frontmatter (``description``, ``argument-hint`` for
commands; ``name, description, tools, model, max_turns`` for agents) and a body
(the command prompt with ``$ARGUMENTS`` / the agent's system prompt).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .. import config
from ..skills.loader import _frontmatter

logger = logging.getLogger(__name__)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

BUILTIN_COMMANDS: list[dict[str, Any]] = [
    {"name": "help", "description": "List the available commands.", "argument_hint": ""},
    {"name": "clear", "description": "Clear the conversation (messages and todos).", "argument_hint": ""},
    {"name": "compact", "description": "Summarize older turns now to free context.",
     "argument_hint": "[focus instructions]"},
    {"name": "mode", "description": "Show or switch the mode: ask, agent, auto or plan.",
     "argument_hint": "<ask|agent|auto|plan>"},
    {"name": "model", "description": "Show or switch the model: <provider>[:<model>].",
     "argument_hint": "[provider[:model]]"},
    {"name": "review", "description": "Run the literature-review workflow on a question.",
     "argument_hint": "<question>"},
    {"name": "summarize", "description": "Summarize the attached paper(s) or the conversation so far.",
     "argument_hint": "[focus]"},
    {"name": "todos", "description": "Show the current todo list.", "argument_hint": ""},
]
LOCAL_COMMANDS = {"help", "clear", "compact", "mode", "model", "todos"}

READ_TOOLS = [
    "library_search", "library_list", "paper_read", "paper_search_spans", "paper_summary",
    "paper_explain", "paper_references", "result_read",
]
BUILTIN_AGENTS: list[dict[str, Any]] = [
    {
        "name": "general",
        "description": "General-purpose subagent for multi-step research or workspace tasks.",
        "tools": ["*"],
        "prompt": (
            "You are a GlossX subagent working on one delegated task. Work autonomously "
            "with the tools you have, then reply with a concise, self-contained final "
            "answer for the parent agent (it cannot see your intermediate steps)."
        ),
        "max_turns": 15,
    },
    {
        "name": "reader",
        "description": "Reads one paper in depth and returns an evidence digest with span citations.",
        "tools": READ_TOOLS + ["agent_reader"],
        "prompt": (
            "You are the GlossX reader subagent. Focus on ONE paper. Use paper_read, "
            "paper_search_spans and agent_reader to collect evidence, then return a digest: "
            "relevance to the question, key findings each followed by [S:<span_id>] "
            "citations, and open questions. Never invent span ids."
        ),
        "max_turns": 10,
    },
    {
        "name": "reviewer",
        "description": "Critiques a draft or claim set and verifies claims against cited spans.",
        "tools": READ_TOOLS + ["verify_claims", "agent_reader", "fs_read", "fs_list", "fs_glob",
                               "fs_grep", "scholar_search"],
        "prompt": (
            "You are the GlossX reviewer subagent. Critique the given draft or claims: check "
            "every factual claim against its cited spans with verify_claims, flag unsupported "
            "or overstated claims, missing evidence and logical gaps, and return a structured "
            "review with concrete fixes."
        ),
        "max_turns": 12,
    },
    {
        "name": "coder",
        "description": "Writes and runs code in the project workspace (analysis scripts, experiments).",
        "tools": ["fs_list", "fs_read", "fs_glob", "fs_grep", "fs_write", "fs_edit", "shell_run",
                  "python_run", "result_read"],
        "prompt": (
            "You are the GlossX coder subagent. Implement the task inside the project "
            "workspace: read before you edit, make small exact edits, run the code to check "
            "it, and finish with a summary of the files you changed and the results."
        ),
        "max_turns": 20,
    },
    {
        "name": "searcher",
        "description": "Finds papers and web sources (Semantic Scholar, arXiv, web) for a topic.",
        "tools": ["scholar_search", "web_search", "web_fetch", "library_search", "library_list",
                  "paper_read", "result_read"],
        "prompt": (
            "You are the GlossX searcher subagent. Find the most relevant papers and sources "
            "for the topic, prefer primary sources, and return a ranked list with title, "
            "year, identifier (arXiv id / DOI / URL) and one line on why each matters. Note "
            "which ones are already in the library."
        ),
        "max_turns": 10,
    },
]


def _md_files(directory: Path) -> list[Path]:
    try:
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.md"))[:200]
    except OSError:
        return []


def _read_md(path: Path) -> tuple[dict, str] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    meta, body = _frontmatter(text)
    return (meta if isinstance(meta, dict) else {}), body


def _tools_list(value: Any) -> list[str]:
    if value is None:
        return ["*"]
    if isinstance(value, str):
        items = [t.strip() for t in re.split(r"[,\s]+", value) if t.strip()]
    elif isinstance(value, list):
        items = [str(t).strip() for t in value if str(t).strip()]
    else:
        return ["*"]
    return items[:64] or ["*"]


def _v2_manifests(kind: str) -> list[dict[str, Any]]:
    try:
        from ..plugins import manager

        return [m for m in manager.list_installed_v2() if m.get("kind") == kind]
    except Exception:
        logger.exception("could not list v2 plugins")
        return []


def _skills() -> list[dict[str, Any]]:
    try:
        from ..agent.adapters import skills as skill_adapter

        return [c for c in skill_adapter.skill_capabilities()]
    except Exception:
        return []


# -- commands -----------------------------------------------------------------------
def list_commands(workspace_root: Path | None = None) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cmd in BUILTIN_COMMANDS:
        out[cmd["name"]] = {**cmd, "source": "builtin", "kind": "builtin"}

    def add_md(directory: Path, source: str, override: bool) -> None:
        for path in _md_files(directory):
            name = path.stem
            if not _NAME.fullmatch(name) or (name in out and out[name]["kind"] == "builtin"):
                continue
            if name in out and not override:
                continue
            parsed = _read_md(path)
            if parsed is None:
                continue
            meta, body = parsed
            out[name] = {
                "name": name,
                "description": (" ".join(str(meta.get("description") or "").split())[:300]
                                or (body.strip().splitlines()[0][:200] if body.strip() else "")),
                "argument_hint": str(meta.get("argument-hint") or meta.get("argument_hint") or "")[:200],
                "source": source,
                "kind": "markdown",
                "path": str(path),
                "prompt": body,
                "mode": meta.get("mode") if meta.get("mode") in {"ask", "agent", "auto", "plan"} else None,
            }

    from . import home
    for directory in home.global_dirs("commands"):
        add_md(directory, "user", override=False)
    if workspace_root is not None:
        add_md(workspace_root / ".glossx" / "commands", "project", override=True)
    for cap in _skills():
        name = cap.name.split(".", 1)[1]
        if name in out:
            continue
        tool = "skill_" + re.sub(r"[^A-Za-z0-9_]", "_", name)
        out[name] = {
            "name": name, "description": cap.card, "argument_hint": "[arguments]",
            "source": "skill", "kind": "skill", "tool": tool,
            "prompt": (f"The user invoked the '{name}' skill. Call the {tool} tool with "
                       f"arguments \"$ARGUMENTS\" (and the relevant paper_id if a paper is "
                       f"attached), then present its output."),
        }
    for manifest in _v2_manifests("command"):
        name = manifest["id"]
        if name in out:
            continue
        out[name] = {
            "name": name, "description": manifest.get("description") or manifest.get("name"),
            "argument_hint": manifest.get("argument_hint") or "", "source": "plugin",
            "kind": "plugin", "plugin_id": manifest["id"], "prompt": manifest.get("prompt") or "",
        }
    return sorted(out.values(), key=lambda c: (c["kind"] != "builtin", c["name"]))


def public_command(cmd: dict[str, Any]) -> dict[str, Any]:
    return {k: cmd.get(k) for k in ("name", "description", "argument_hint", "source", "kind")}


def find_command(name: str, workspace_root: Path | None) -> dict[str, Any] | None:
    for cmd in list_commands(workspace_root):
        if cmd["name"] == name:
            return cmd
    return None


def expand(cmd: dict[str, Any], arguments: str) -> str:
    body = str(cmd.get("prompt") or "")
    if "$ARGUMENTS" in body:
        return body.replace("$ARGUMENTS", arguments)
    if arguments:
        return f"{body.rstrip()}\n\nARGUMENTS: {arguments}"
    return body


def parse_command(text: str) -> tuple[str, str] | None:
    """``"/name args"`` -> ``("name", "args")``; None if not a command."""
    stripped = (text or "").lstrip()
    if not stripped.startswith("/") or stripped.startswith("//"):
        return None
    head, _, rest = stripped[1:].partition(" ")
    head = head.strip()
    if "\n" in head:
        head, _, extra = head.partition("\n")
        rest = extra + (" " + rest if rest else "")
    if not _NAME.fullmatch(head):
        return None
    return head, rest.strip()


# -- subagent types ---------------------------------------------------------------
def list_agents(workspace_root: Path | None = None) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {a["name"]: {**a, "source": "builtin"} for a in BUILTIN_AGENTS}

    def add_md(directory: Path, source: str, override: bool) -> None:
        for path in _md_files(directory):
            parsed = _read_md(path)
            if parsed is None:
                continue
            meta, body = parsed
            name = str(meta.get("name") or path.stem).strip()
            if not _NAME.fullmatch(name) or (name in out and not override):
                continue
            try:
                max_turns = max(1, min(50, int(meta.get("max_turns") or 15)))
            except (TypeError, ValueError):
                max_turns = 15
            out[name] = {
                "name": name,
                "description": " ".join(str(meta.get("description") or "").split())[:300],
                "tools": _tools_list(meta.get("tools")),
                "model": str(meta.get("model")).strip() if meta.get("model") else None,
                "prompt": body.strip() or BUILTIN_AGENTS[0]["prompt"],
                "max_turns": max_turns,
                "source": source,
                "path": str(path),
            }

    from . import home
    for directory in home.global_dirs("agents"):
        add_md(directory, "user", override=False)
    if workspace_root is not None:
        add_md(workspace_root / ".glossx" / "agents", "project", override=True)
    for manifest in _v2_manifests("agent"):
        name = manifest["id"]
        if name in out or not manifest.get("prompt"):
            continue
        out[name] = {
            "name": name,
            "description": manifest.get("description") or manifest.get("name") or name,
            "tools": list(manifest.get("tools") or ["*"]),
            "model": manifest.get("model"),
            "prompt": manifest["prompt"],
            "max_turns": 15,
            "source": "plugin",
        }
    return list(out.values())


def public_agent(agent: dict[str, Any]) -> dict[str, Any]:
    return {k: agent.get(k) for k in ("name", "description", "tools", "model", "max_turns", "source")}


def find_agent(name: str, workspace_root: Path | None) -> dict[str, Any] | None:
    for agent in list_agents(workspace_root):
        if agent["name"] == name:
            return agent
    return None


# -- hooks ----------------------------------------------------------------------------
def hooks_for(event: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = [dict(h, source="settings") for h in (settings.get("hooks") or {}).get(event) or []]
    trusted = set(settings.get("trusted_plugins") or [])
    for manifest in _v2_manifests("hook"):
        hook = manifest.get("hook") or {}
        if hook.get("event") != event or manifest["id"] not in trusted:
            continue
        hooks.append({"matcher": hook.get("matcher") or "*", "command": hook["command"],
                      "timeout": 30, "source": f"plugin:{manifest['id']}"})
    return hooks


def plugin_hooks(settings: dict[str, Any]) -> list[dict[str, Any]]:
    trusted = set(settings.get("trusted_plugins") or [])
    return [{"plugin_id": m["id"], **(m.get("hook") or {}), "trusted": m["id"] in trusted}
            for m in _v2_manifests("hook")]


def plugin_mcp_servers(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """MCP servers declared by trusted ``kind: mcp`` plugins (connections shape)."""
    trusted = set(settings.get("trusted_plugins") or [])
    out = []
    for manifest in _v2_manifests("mcp"):
        server = manifest.get("server") or {}
        if manifest["id"] not in trusted or not server.get("command"):
            continue
        name = re.sub(r"[^A-Za-z0-9_-]", "-", manifest["id"])[:40]
        out.append({"name": name, "command": server["command"], "args": list(server.get("args") or []),
                    "env": dict(server.get("env") or {}), "enabled": True})
    return out
