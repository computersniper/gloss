"""Harness settings: ``~/.glossx/settings.json`` (global) merged with a
workspace's ``<workspace>/.glossx/settings.json`` (project overrides).

Before ``~/.glossx`` existed the settings lived in ``config.json`` under the key
``glossx_harness``; that key is still read until the new file is written.

Shape (also the contract of ``GET/PUT /api/harness/settings``; ``vcs`` is
``{"auto_commit": true}``)::

    {
      "permissions": {"allow": [pattern], "deny": [pattern], "ask": [pattern]},
      "hooks": {"pre_tool_use": [{"matcher", "command", "timeout"}], "post_tool_use": [...],
                "session_start": [...], "stop": [...]},
      "context_limit": 120000,
      "default_mode": "agent",
      "max_tool_rounds": 25,
      "mention_budget": 12000,
      "trusted_plugins": [plugin id],      # lets plugin hooks / MCP servers run
      "allow_private_network": false       # web_fetch to localhost / LAN hosts
    }

A permission pattern is a tool name (``fs_write``), a glob over tool names
(``mcp_*``, ``fs_write:*``) or a tool with an argument glob
(``shell_run(git status*)``, ``fs_write(notes/*)``).
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .. import config

CONFIG_KEY = "glossx_harness"
MODES = ("ask", "agent", "auto", "plan")
HOOK_EVENTS = ("pre_tool_use", "post_tool_use", "session_start", "stop")
MAX_RULES = 200
_PATTERN = re.compile(r"^[A-Za-z0-9_.*:-]{1,120}(\(.{0,400}\))?$", re.S)
_PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "permissions": {"allow": [], "deny": [], "ask": []},
    "hooks": {event: [] for event in HOOK_EVENTS},
    "context_limit": 120_000,
    "default_mode": "agent",
    "max_tool_rounds": 25,
    "mention_budget": 12_000,
    "trusted_plugins": [],
    "allow_private_network": False,
    "vcs": {"auto_commit": True},
    "browser": {"path": None, "headed": False, "allow_private": False, "idle_timeout": 300, "navigation_budget": 60},
}
_KEYS = set(DEFAULT_SETTINGS)
# keys a workspace's ``.glossx/settings.json`` may override (the rest is global
# only: a project file must not be able to trust plugins or open the network)
PROJECT_KEYS = ("permissions", "hooks", "context_limit", "default_mode", "max_tool_rounds",
                "mention_budget", "vcs")


def _int(raw: Any, key: str, lo: int, hi: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{key} must be an integer")
    if not lo <= raw <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return raw


def normalize_pattern(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or not _PATTERN.fullmatch(text):
        raise ValueError(f"invalid permission pattern: {raw!r}")
    return text


def _rules(raw: Any, key: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_RULES:
        raise ValueError(f"permissions.{key} must be a list of at most {MAX_RULES} patterns")
    return list(dict.fromkeys(normalize_pattern(p) for p in raw))


def _hooks(raw: Any) -> dict[str, list[dict[str, Any]]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("hooks must be an object")
    unknown = set(raw) - set(HOOK_EVENTS)
    if unknown:
        raise ValueError(f"unknown hook event: {', '.join(sorted(unknown))}")
    out: dict[str, list[dict[str, Any]]] = {}
    for event in HOOK_EVENTS:
        items = raw.get(event) or []
        if not isinstance(items, list) or len(items) > 32:
            raise ValueError(f"hooks.{event} must be a list of at most 32 hooks")
        clean = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"each hooks.{event} entry must be an object")
            command = str(item.get("command") or "").strip()
            if not command or len(command) > 2000:
                raise ValueError(f"hooks.{event} entries need a command (<= 2000 chars)")
            matcher = str(item.get("matcher") or "*").strip() or "*"
            if matcher != "*":
                matcher = normalize_pattern(matcher)
            timeout = item.get("timeout", 30)
            clean.append({
                "matcher": matcher,
                "command": command,
                "timeout": _int(timeout, f"hooks.{event}.timeout", 1, 600),
            })
        out[event] = clean
    return out


def normalize(raw: Any) -> dict[str, Any]:
    """Validate a full settings object; raise ``ValueError`` with a short message."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ValueError(f"unknown settings field: {', '.join(sorted(unknown))}")
    out = copy.deepcopy(DEFAULT_SETTINGS)
    perms = raw.get("permissions") or {}
    if not isinstance(perms, dict):
        raise ValueError("permissions must be an object")
    bad = set(perms) - {"allow", "deny", "ask"}
    if bad:
        raise ValueError(f"unknown permissions field: {', '.join(sorted(bad))}")
    out["permissions"] = {k: _rules(perms.get(k), k) for k in ("allow", "deny", "ask")}
    out["hooks"] = _hooks(raw.get("hooks"))
    if raw.get("context_limit") is not None:
        out["context_limit"] = _int(raw["context_limit"], "context_limit", 4_000, 2_000_000)
    if raw.get("max_tool_rounds") is not None:
        out["max_tool_rounds"] = _int(raw["max_tool_rounds"], "max_tool_rounds", 1, 200)
    if raw.get("mention_budget") is not None:
        out["mention_budget"] = _int(raw["mention_budget"], "mention_budget", 500, 200_000)
    mode = raw.get("default_mode")
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"default_mode must be one of: {', '.join(MODES)}")
        out["default_mode"] = mode
    trusted = raw.get("trusted_plugins") or []
    if not isinstance(trusted, list) or len(trusted) > 200:
        raise ValueError("trusted_plugins must be a list of plugin ids")
    trusted = list(dict.fromkeys(str(t).strip() for t in trusted))
    if any(not _PLUGIN_ID.fullmatch(t) for t in trusted):
        raise ValueError("trusted_plugins must contain plugin ids")
    out["trusted_plugins"] = trusted
    private = raw.get("allow_private_network")
    if private is not None:
        if not isinstance(private, bool):
            raise ValueError("allow_private_network must be a boolean")
        out["allow_private_network"] = private
    vcs = raw.get("vcs")
    if vcs is not None:
        if not isinstance(vcs, dict):
            raise ValueError("vcs must be an object")
        bad = set(vcs) - {"auto_commit"}
        if bad:
            raise ValueError(f"unknown vcs field: {', '.join(sorted(bad))}")
        auto = vcs.get("auto_commit")
        if auto is not None:
            if not isinstance(auto, bool):
                raise ValueError("vcs.auto_commit must be a boolean")
            out["vcs"] = {"auto_commit": auto}
    browser = raw.get("browser")
    if browser is not None:
        if not isinstance(browser, dict) or set(browser)-set(DEFAULT_SETTINGS["browser"]):
            raise ValueError("Unknown browser settings field")
        for key in ("headed", "allow_private"):
            if key in browser:
                if not isinstance(browser[key], bool): raise ValueError("browser."+key+" must be boolean")
                out["browser"][key]=browser[key]
        if "path" in browser:
            value=browser["path"]
            if value is not None and (not isinstance(value,str) or len(value)>2000):raise ValueError("Invalid browser path")
            out["browser"]["path"]=value or None
        for key in ("idle_timeout", "navigation_budget"):
            if key in browser:out["browser"][key]=_int(browser[key],"browser."+key,1,10000)
    return out


def _lenient(raw: Any, keys: Any = None) -> dict[str, Any]:
    """Normalize ``raw``; fields that do not validate fall back to the defaults."""
    keys = list(keys if keys is not None else DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        raw = {k: v for k, v in raw.items() if k in keys}
    try:
        return normalize(raw)
    except ValueError:
        out = copy.deepcopy(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            for key in keys:
                try:
                    out[key] = normalize({key: raw.get(key)})[key]
                except ValueError:
                    pass
        return out


def load_global() -> dict[str, Any]:
    """The user's settings (``~/.glossx/settings.json``; before that file exists,
    the ``glossx_harness`` key of ``config.json``)."""
    from . import home

    return _lenient(home.read_global_settings())


def merge_project(base: dict[str, Any], raw: Any) -> dict[str, Any]:
    """``base`` with a workspace's ``.glossx/settings.json`` merged over it:
    permission rules and hooks are concatenated (project entries first), scalar
    values the file sets override, global-only keys are ignored."""
    if not isinstance(raw, dict) or not raw:
        return base
    present = [k for k in PROJECT_KEYS if raw.get(k) is not None]
    if not present:
        return base
    project = _lenient(raw, present)
    out = copy.deepcopy(base)
    for key in present:
        if key == "permissions":
            given = raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
            for kind in ("allow", "deny", "ask"):
                if given.get(kind) is None:
                    continue
                merged = list(project["permissions"][kind]) + list(out["permissions"][kind])
                out["permissions"][kind] = list(dict.fromkeys(merged))[:MAX_RULES * 2]
        elif key == "hooks":
            for event in HOOK_EVENTS:
                extra = [dict(h, source="project") for h in project["hooks"][event]]
                out["hooks"][event] = extra + list(out["hooks"][event])
        elif key == "vcs":
            if isinstance(raw.get("vcs"), dict) and raw["vcs"].get("auto_commit") is not None:
                out["vcs"] = dict(project["vcs"])
        else:
            out[key] = project[key]
    return out


def load(workspace_root: Any = None) -> dict[str, Any]:
    """Effective settings: the global ones, with the project overrides of
    ``<workspace_root>/.glossx/settings.json`` merged over them when a workspace
    is given."""
    base = load_global()
    if workspace_root is None:
        return base
    from . import home

    return merge_project(base, home.read_workspace_settings(workspace_root))


def save(raw: Any) -> dict[str, Any]:
    """Merge a (possibly partial) settings object over the current global one
    and save it to ``~/.glossx/settings.json``."""
    from . import home

    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    merged = {**load_global(), **raw}
    clean = normalize(merged)
    home.write_global_settings(clean)
    return load_global()


def add_rule(kind: str, pattern: str) -> dict[str, Any]:
    """Append ``pattern`` to ``permissions.<kind>`` (no-op if present)."""
    current = load_global()
    rules = list(current["permissions"].get(kind) or [])
    pattern = normalize_pattern(pattern)
    if pattern not in rules:
        rules.append(pattern)
    perms = {**current["permissions"], kind: rules[-MAX_RULES:]}
    return save({"permissions": perms})
