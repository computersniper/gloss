"""Modes and permission rules for harness tool calls.

Decision order for one call (``decide``):

1. **Mode**: ``ask`` and ``plan`` sessions only see read-only tools
   (categories ``read``, ``meta``, ``agent``; ``plan`` adds ``plan_submit``).
2. **Rules** from settings, ``deny`` > ``ask`` > ``allow``. A pattern is a tool
   name or glob (``mcp_*``, ``fs_write:*``) optionally with an argument glob on
   the tool's primary argument (``shell_run(git status*)``). For ``shell_run``
   an argument rule must match every ``;``/``&&``/``||``/``|`` segment, and
   commands with substitutions or redirections are never allowed by an
   argument rule (only by a bare ``shell_run`` rule).
3. **Defaults**: read-only tools allow; workspace writes ask (``auto`` mode
   allows them); library writes and execution ask (unless the permission is in
   ``connections.auto_approve``); ``memory_note`` allows.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

from ..agent.capabilities import APPROVAL_REQUIRED

READ_ONLY_CATEGORIES = {"read", "meta", "agent", "browser"}
CATEGORIES = {"read", "meta", "agent", "write", "exec", "plan"}
_SPLIT = re.compile(r"\s*(?:;|&&|\|\||\||\n)\s*")
_UNSAFE = ("`", "$(", ">", "<")


def mode_allows(mode: str, category: str) -> bool:
    if category == "plan":
        return mode == "plan"
    if mode in {"ask", "plan"}:
        return category in READ_ONLY_CATEGORIES
    return True


def _glob(pattern: str) -> re.Pattern:
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")), re.S)


def parse(pattern: str) -> tuple[str, str | None]:
    pattern = pattern.strip()
    arg: str | None = None
    if pattern.endswith(")") and "(" in pattern:
        name, _, rest = pattern.partition("(")
        arg = rest[:-1]
    else:
        name = pattern
    name = name.strip()
    if name.endswith(":*"):
        name = name[:-2]
    return name, arg


def _name_matches(name_glob: str, tool: str) -> bool:
    if name_glob.startswith("browser."):
        name_glob = "browser_" + name_glob[len("browser."):]
    return bool(_glob(name_glob).fullmatch(tool))


def matches(pattern: str, tool: str, primary: str | None) -> bool:
    name_glob, arg_glob = parse(pattern)
    if not _name_matches(name_glob, tool):
        return False
    if arg_glob is None:
        return True
    if primary is None:
        return False
    return bool(_glob(arg_glob.strip()).fullmatch(primary.strip()))


def _shell_segments(command: str) -> list[str]:
    return [seg for seg in _SPLIT.split(command.strip()) if seg]


def _rule_hit(rules: list[str], tool: str, primary: str | None, *, for_allow: bool) -> str | None:
    """The first rule matching this call, with shell-aware semantics."""
    if tool == "shell_run" and primary is not None:
        segments = _shell_segments(primary) or [primary]
        unsafe = any(token in primary for token in _UNSAFE)
        for rule in rules:
            name_glob, arg_glob = parse(rule)
            if not _name_matches(name_glob, tool):
                continue
            if arg_glob is None:
                return rule
            if for_allow:
                if unsafe:
                    continue
                if all(matches(rule, tool, seg) for seg in segments):
                    return rule
            elif any(matches(rule, tool, seg) for seg in segments) or matches(rule, tool, primary):
                return rule
        if for_allow and not unsafe and len(segments) > 1:
            # every segment may be covered by a different allow rule
            if all(any(parse(r)[1] is not None and matches(r, tool, seg) for r in rules)
                   for seg in segments):
                return "segments allowed by rules"
        return None
    for rule in rules:
        if matches(rule, tool, primary):
            return rule
    return None


def suggest_rule(tool: str, primary: str | None) -> str:
    """The allow rule added by an "approve always" decision."""
    if tool == "shell_run" and primary:
        try:
            words = shlex.split(primary)
        except ValueError:
            words = primary.split()
        words = [w for w in words if w][:2]
        if words and not any(t in primary for t in _UNSAFE) and len(_shell_segments(primary)) == 1:
            prefix = " ".join(words)
            if re.fullmatch(r"[A-Za-z0-9_./:=+@%,-]+( [A-Za-z0-9_./:=+@%,-]+)?", prefix):
                return f"shell_run({prefix}*)"
        return f"shell_run({primary.strip()[:300]})"
    return tool


def protects_config(spec: Any, primary: str | None) -> bool:
    """A file-writing tool call whose path is inside ``<workspace>/.glossx``."""
    if primary is None or not getattr(spec, "workspace_write", False):
        return False
    if getattr(spec, "primary_arg", None) != "path":
        return False
    from .workspace import is_config_path

    return is_config_path(primary)


def decide(
    spec: Any,
    args: dict[str, Any],
    mode: str,
    settings: dict[str, Any],
    auto_approve: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Return ``(decision, reason)``; decision is allow | ask | deny | unavailable."""
    tool = spec.name
    if mode in {"ask", "plan"} and tool in {"browser_download", "browser_import_paper"}:
        return "unavailable", "This browser write is unavailable in read-only mode"
    if not mode_allows(mode, spec.category):
        return "unavailable", f"{tool} is not available in {mode} mode (read-only)"
    if spec.category == "plan":
        return "ask", "the user must accept the plan"
    primary = spec.primary_value(args)
    perms = settings.get("permissions") or {}
    hit = _rule_hit(list(perms.get("deny") or []), tool, primary, for_allow=False)
    if hit:
        return "deny", f"blocked by permission rule {hit}"
    if protects_config(spec, primary):
        # never auto-approved: not by auto mode, not by an allow rule
        return "ask", ".glossx holds this workspace's GlossX settings; changes always need approval"
    hit = _rule_hit(list(perms.get("ask") or []), tool, primary, for_allow=False)
    if hit:
        return "ask", f"permission rule {hit} asks for approval"
    hit = _rule_hit(list(perms.get("allow") or []), tool, primary, for_allow=True)
    if hit:
        return "allow", f"allowed by permission rule {hit}"
    return default_decision(spec, mode, auto_approve)


def default_decision(spec: Any, mode: str, auto_approve: set[str] | frozenset[str]) -> tuple[str, str]:
    category = spec.category
    if category == "browser":
        return ("ask", "Browser download or paper import needs approval") if spec.always_ask else ("allow", "Browser navigation/read; sensitive page actions are checked at execution")
    if category in READ_ONLY_CATEGORIES:
        return "allow", "read-only tool"
    if category == "write":
        if spec.workspace_write:
            if mode == "auto":
                return "allow", "auto mode allows workspace edits"
            return "ask", "workspace edits need approval"
        need = [p for p in spec.permissions if p in APPROVAL_REQUIRED and p not in auto_approve]
        if any(p in APPROVAL_REQUIRED for p in spec.permissions):
            if not need:
                return "allow", "auto-approved in connections"
            return "ask", f"needs approval ({', '.join(need)})"
        return "allow", "low-risk write"
    if category == "exec":
        gated = [p for p in spec.permissions if p in APPROVAL_REQUIRED]
        if gated and all(p in auto_approve for p in gated) and not spec.always_ask:
            return "allow", "auto-approved in connections"
        return "ask", "execution needs approval"
    return "ask", "needs approval"
