"""External connections for the GlossX agent (``config.json`` key ``agent_connections``).

Shape (also the HTTP contract of ``GET/PUT /api/agent/connections``)::

    {
      "mcp_servers": [{"name", "command", "args": [], "env": {}, "enabled": true}],
      "ssh_hosts":   [{"name", "host", "user", "port": 22, "workdir": "", "enabled": true}],
      "cli_agents":  {"claude": {"enabled": false}, "codex": {"enabled": false}},
      "auto_approve": ["code:execute" | "remote:ssh" | "mcp:call" | "library:write"]
    }
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .. import config
from .capabilities import APPROVAL_REQUIRED

CONFIG_KEY = "agent_connections"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
# hostnames / IPv4 / bracket-less IPv6; never starting with '-' (ssh option injection)
_HOST = re.compile(r"^[A-Za-z0-9_.:%\[\]][A-Za-z0-9_.:%\[\]-]{0,252}$")
_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

DEFAULT_CONNECTIONS: dict[str, Any] = {
    "mcp_servers": [],
    "ssh_hosts": [],
    "cli_agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
    "auto_approve": [],
}


def _name(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if not _NAME.fullmatch(text):
        raise ValueError(f"{what} name must be 1-40 letters, digits, '-' or '_'")
    return text


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError("enabled must be a boolean")


def _mcp_server(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each MCP server must be an object")
    name = _name(raw.get("name"), "MCP server")
    command = str(raw.get("command") or "").strip()
    if not command or len(command) > 1000:
        raise ValueError(f"MCP server '{name}' needs a command")
    args = raw.get("args") or []
    if not isinstance(args, list) or len(args) > 64:
        raise ValueError(f"MCP server '{name}' args must be a list of strings")
    args = [str(a) for a in args]
    if any(len(a) > 2000 for a in args):
        raise ValueError(f"MCP server '{name}' has an argument that is too long")
    env = raw.get("env") or {}
    if not isinstance(env, dict) or len(env) > 64:
        raise ValueError(f"MCP server '{name}' env must be an object of strings")
    clean_env: dict[str, str] = {}
    for key, value in env.items():
        if not _ENV_KEY.fullmatch(str(key)):
            raise ValueError(f"MCP server '{name}' has an invalid env key: {key}")
        clean_env[str(key)] = "" if value is None else str(value)
    return {
        "name": name,
        "command": command,
        "args": args,
        "env": clean_env,
        "enabled": _bool(raw.get("enabled"), True),
    }


def _ssh_host(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each SSH host must be an object")
    name = _name(raw.get("name"), "SSH host")
    host = str(raw.get("host") or "").strip()
    if not _HOST.fullmatch(host):
        raise ValueError(f"SSH host '{name}' has an invalid host")
    user = str(raw.get("user") or "").strip()
    if not _USER.fullmatch(user):
        raise ValueError(f"SSH host '{name}' has an invalid user")
    port = raw.get("port", 22)
    if port in (None, ""):
        port = 22
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"SSH host '{name}' port must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"SSH host '{name}' port must be between 1 and 65535")
    workdir = str(raw.get("workdir") or "").strip()
    if len(workdir) > 500 or "\n" in workdir or "\0" in workdir:
        raise ValueError(f"SSH host '{name}' has an invalid workdir")
    return {
        "name": name,
        "host": host,
        "user": user,
        "port": port,
        "workdir": workdir,
        "enabled": _bool(raw.get("enabled"), True),
    }


def normalize(raw: Any) -> dict[str, Any]:
    """Validate a connections object; raise ``ValueError`` with a short message."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("connections must be an object")
    out = copy.deepcopy(DEFAULT_CONNECTIONS)

    servers = raw.get("mcp_servers") or []
    if not isinstance(servers, list) or len(servers) > 32:
        raise ValueError("mcp_servers must be a list (at most 32)")
    out["mcp_servers"] = [_mcp_server(s) for s in servers]
    if len({s["name"] for s in out["mcp_servers"]}) != len(out["mcp_servers"]):
        raise ValueError("MCP server names must be unique")

    hosts = raw.get("ssh_hosts") or []
    if not isinstance(hosts, list) or len(hosts) > 32:
        raise ValueError("ssh_hosts must be a list (at most 32)")
    out["ssh_hosts"] = [_ssh_host(h) for h in hosts]
    if len({h["name"] for h in out["ssh_hosts"]}) != len(out["ssh_hosts"]):
        raise ValueError("SSH host names must be unique")

    cli = raw.get("cli_agents") or {}
    if not isinstance(cli, dict):
        raise ValueError("cli_agents must be an object")
    unknown = set(cli) - {"claude", "codex"}
    if unknown:
        raise ValueError(f"unknown cli agent: {', '.join(sorted(unknown))}")
    for agent in ("claude", "codex"):
        entry = cli.get(agent) or {}
        if not isinstance(entry, dict):
            raise ValueError(f"cli_agents.{agent} must be an object")
        out["cli_agents"][agent] = {"enabled": _bool(entry.get("enabled"), False)}

    auto = raw.get("auto_approve") or []
    if not isinstance(auto, list):
        raise ValueError("auto_approve must be a list of permissions")
    auto = list(dict.fromkeys(str(p) for p in auto))
    bad = [p for p in auto if p not in APPROVAL_REQUIRED]
    if bad:
        raise ValueError(
            f"auto_approve accepts only {', '.join(sorted(APPROVAL_REQUIRED))}; got {', '.join(bad)}"
        )
    out["auto_approve"] = auto
    return out


def load() -> dict[str, Any]:
    """Current connections; invalid stored data falls back to defaults per field."""
    raw = config.load_config().get(CONFIG_KEY)
    try:
        return normalize(raw)
    except ValueError:
        # keep whatever parts are valid rather than losing everything
        out = copy.deepcopy(DEFAULT_CONNECTIONS)
        if isinstance(raw, dict):
            for key in DEFAULT_CONNECTIONS:
                try:
                    out[key] = normalize({key: raw.get(key)})[key]
                except ValueError:
                    pass
        return out


def save(raw: Any) -> dict[str, Any]:
    clean = normalize(raw)
    config.save_config({CONFIG_KEY: clean})
    # save_config deep-merges dicts; lists are replaced, which is what we want
    return load()


def auto_approve() -> set[str]:
    return set(load().get("auto_approve") or [])
