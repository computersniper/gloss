"""Harness tools: one table built from the capability registry.

Every harness tool is a :class:`~app.agent.capabilities.Capability` in the
registry -- first-party harness tools are registered as ``harness.<tool>``
(``builtin_tools.py``), and every other capability (library/paper builtins,
skills, MCP tools, CLI delegates, SSH, plugins) is adapted under a
function-style name (``paper.search_spans`` -> ``paper_search_spans``,
``mcp.zotero.search`` -> ``mcp_zotero_search``, ``skill.x`` -> ``skill_x``).
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..agent.capabilities import Capability, CapabilityRegistry, RunContext
from . import permissions

logger = logging.getLogger(__name__)

# registry capabilities the harness replaces or never exposes directly
REPLACED = {"memory.recall", "memory.note", "paper.read"}
HIDDEN_KINDS = {"workflow", "loop", "decider", "provider", "view"}
EXEC_PERMISSIONS = {"code:execute", "remote:ssh", "mcp:call"}
SUBAGENT_EXCLUDED = {"task", "ask_user", "todo_write", "plan_submit"}
_PRIMARY = {"remote.ssh.run": "command", "delegate.claude_code": "task", "delegate.codex": "task"}


@dataclass
class ToolOutput:
    """A handler result: model-facing ``text`` plus fields merged into the tool_call."""

    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    trusted: bool = False  # user-provided text (e.g. an ask_user answer), not tool data


@dataclass
class HarnessContext(RunContext):
    runtime: Any = None
    call: dict[str, Any] | None = None
    workspace_key: str = "default"
    workspace_root: Path | None = None
    mode: str = "agent"
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    category: str
    handler: Callable[[dict[str, Any], Any], Awaitable[Any]]
    source: str = "harness"
    trust: str = "first-party"
    permissions: list[str] = field(default_factory=list)
    primary_arg: str | None = None
    workspace_write: bool = False
    always_ask: bool = False
    parallel_safe: bool = True
    timeout: float | None = 180.0
    capability: str | None = None
    kind: str = "tool"

    def primary_value(self, args: dict[str, Any]) -> str | None:
        if not self.primary_arg:
            return None
        value = (args or {}).get(self.primary_arg)
        return None if value is None else str(value)

    def describe(self, mode: str, settings: dict[str, Any], auto: set[str]) -> dict[str, Any]:
        available = permissions.mode_allows(mode, self.category)
        if available:
            decision, reason = permissions.decide(self, {}, mode, settings, auto)
        else:
            decision, reason = "unavailable", f"not available in {mode} mode"
        return {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
            "category": self.category,
            "source": self.source,
            "trust": self.trust,
            "capability": self.capability,
            "permissions": list(self.permissions),
            "modes": [m for m in ("ask", "agent", "auto", "plan")
                      if permissions.mode_allows(m, self.category)],
            "permission": decision,
            "permission_reason": reason,
            "requires_approval": decision == "ask",
        }

    def prompt_line(self) -> str:
        props = self.parameters.get("properties") or {}
        required = set(self.parameters.get("required") or [])
        args = {}
        for key, spec in props.items():
            kind = spec.get("type", "any")
            if kind == "array" and isinstance(spec.get("items"), dict):
                kind = f"array<{spec['items'].get('type', 'any')}>"
            if spec.get("enum"):
                kind = "|".join(str(e) for e in spec["enum"])
            desc = spec.get("description") or ""
            flag = "required" if key in required else "optional"
            args[key] = f"{kind}, {flag}" + (f": {desc}" if desc else "")
        import json

        return f"- {self.name}: {self.description} args: {json.dumps(args, ensure_ascii=False)}"


def tool_name_for(cap_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", cap_name).strip("_")[:64]


def _json_type(desc: str) -> tuple[dict[str, Any], bool]:
    text = str(desc or "").strip()
    head, _, rest = text.partition(":")
    low = head.lower().strip()
    optional = "optional" in low or "default" in low or "optional" in rest.lower()[:20]
    word = re.split(r"[\s(|]", low, maxsplit=1)[0] if low else "string"
    schema: dict[str, Any]
    if word.startswith(("list", "array")) or low.startswith("["):
        item = "string"
        if "int" in low:
            item = "integer"
        elif "object" in low or "dict" in low or "{" in low:
            item = "object"
        schema = {"type": "array", "items": {"type": item}}
    elif word.startswith(("int", "integer")):
        schema = {"type": "integer"}
    elif word.startswith(("number", "float")):
        schema = {"type": "number"}
    elif word.startswith("bool"):
        schema = {"type": "boolean"}
    elif word.startswith(("object", "dict", "{")):
        schema = {"type": "object"}
    elif word in {"str", "string", "text", "markdown"}:
        schema = {"type": "string"}
    else:
        schema = {}
    description = " ".join((rest or ("" if schema else text)).split())[:200]
    if description:
        schema["description"] = description
    return schema, optional


def schema_from_capability(cap: Capability) -> dict[str, Any]:
    if cap.source == "mcp":
        real = _mcp_schema(cap)
        if real is not None:
            return real
    props: dict[str, Any] = {}
    required: list[str] = []
    for key, desc in (cap.input_schema or {}).items():
        schema, optional = _json_type(desc)
        props[str(key)] = schema
        if not optional:
            required.append(str(key))
    return {"type": "object", "properties": props, "required": required}


def _mcp_schema(cap: Capability) -> dict[str, Any] | None:
    try:
        from ..agent.adapters import mcp_client

        client = mcp_client.MANAGER.clients.get(cap.meta.get("server"))
        for tool in (client.tools or []) if client else []:
            if tool.get("name") == cap.meta.get("tool"):
                schema = tool.get("inputSchema")
                if isinstance(schema, dict):
                    schema = copy.deepcopy(schema)
                    schema.setdefault("type", "object")
                    schema.setdefault("properties", {})
                    return schema
    except Exception:
        return None
    return None


def _adapt(cap: Capability) -> ToolSpec:
    perms = set(cap.permissions)
    if perms & EXEC_PERMISSIONS:
        category = "exec"
    elif perms & {"library:write", "memory:write"}:
        category = "write"
    else:
        category = "read"
    handler = cap.handler

    async def run(args: dict[str, Any], ctx: Any) -> Any:
        return await handler(args, ctx)

    return ToolSpec(
        name=tool_name_for(cap.name),
        description=cap.card,
        parameters=schema_from_capability(cap),
        category=category,
        handler=run,
        source=cap.source,
        trust=cap.trust,
        permissions=list(cap.permissions),
        primary_arg=_PRIMARY.get(cap.name),
        parallel_safe=category == "read",
        timeout=float(cap.timeout or 180.0),
        capability=cap.name,
        kind=cap.kind,
    )


def _from_harness(cap: Capability) -> ToolSpec:
    meta = cap.meta
    return ToolSpec(
        name=meta["tool"],
        description=cap.card,
        parameters=copy.deepcopy(meta["parameters"]),
        category=meta["category"],
        handler=cap.handler,
        source="harness",
        trust="first-party",
        permissions=list(cap.permissions),
        primary_arg=meta.get("primary_arg"),
        workspace_write=bool(meta.get("workspace_write")),
        always_ask=bool(meta.get("always_ask")),
        parallel_safe=bool(meta.get("parallel_safe", meta["category"] in {"read", "agent"})),
        timeout=meta.get("timeout", 180.0),
        capability=cap.name,
        kind=cap.kind,
    )


def register_harness_tools(registry: CapabilityRegistry) -> None:
    from .builtin_tools import harness_capabilities

    for cap in harness_capabilities():
        registry.register(cap)


async def harness_registry(settings: dict[str, Any] | None = None) -> CapabilityRegistry:
    """Registry with every adapter's capabilities plus the harness tools."""
    from ..agent import connections as conn_mod
    from ..agent.capabilities import abuild_registry
    from . import extensions

    conns = conn_mod.load()
    if settings is not None:
        extra = extensions.plugin_mcp_servers(settings)
        names = {s["name"] for s in conns.get("mcp_servers") or []}
        for server in extra:
            if server["name"] not in names:
                try:
                    conns["mcp_servers"].append(conn_mod._mcp_server(server))
                except ValueError:
                    logger.warning("skipping invalid plugin MCP server %s", server.get("name"))
    registry = await abuild_registry(connections=conns)
    register_harness_tools(registry)
    return registry


def build_table(
    registry: CapabilityRegistry,
    *,
    mode: str,
    has_project: bool,
    allowlist: list[str] | None = None,
    subagent: bool = False,
    include_unavailable: bool = False,
) -> dict[str, ToolSpec]:
    """Tool name -> spec, filtered by mode, project and subagent allowlist."""
    table: dict[str, ToolSpec] = {}
    adapted: list[ToolSpec] = []
    for cap in registry.all():
        if cap.meta.get("harness"):
            spec = _from_harness(cap)
            if spec.name in {"memory_recall", "memory_note"} and not has_project:
                continue
            table[spec.name] = spec
            continue
        if cap.kind in HIDDEN_KINDS or cap.name in REPLACED:
            continue
        try:
            adapted.append(_adapt(cap))
        except Exception:
            logger.exception("could not adapt capability %s", cap.name)
    for spec in adapted:
        table.setdefault(spec.name, spec)
    out: dict[str, ToolSpec] = {}
    for name, spec in table.items():
        if subagent and name in SUBAGENT_EXCLUDED:
            continue
        if allowlist is not None and not any(
                permissions.matches(p, name, None) for p in allowlist if "(" not in p):
            continue
        if not include_unavailable and not permissions.mode_allows(mode, spec.category):
            continue
        out[name] = spec
    return out


def validate_args(spec: ToolSpec, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Light JSON-schema check with friendly coercions; returns (args, error)."""
    schema = spec.parameters or {}
    props = schema.get("properties") or {}
    clean = dict(args)
    missing = [k for k in schema.get("required") or [] if clean.get(k) is None]
    if missing:
        return clean, f"missing required argument(s): {', '.join(missing)}"
    for key, prop in props.items():
        if key not in clean or clean[key] is None or not isinstance(prop, dict):
            continue
        kind = prop.get("type")
        value = clean[key]
        if kind == "integer" and not isinstance(value, int):
            try:
                clean[key] = int(str(value).strip())
            except (TypeError, ValueError):
                return clean, f"'{key}' must be an integer"
        elif kind == "number" and not isinstance(value, (int, float)):
            try:
                clean[key] = float(str(value).strip())
            except (TypeError, ValueError):
                return clean, f"'{key}' must be a number"
        elif kind == "boolean" and not isinstance(value, bool):
            if str(value).lower() in {"true", "1", "yes"}:
                clean[key] = True
            elif str(value).lower() in {"false", "0", "no"}:
                clean[key] = False
            else:
                return clean, f"'{key}' must be a boolean"
        elif kind == "string" and not isinstance(value, str):
            if isinstance(value, (int, float)):
                clean[key] = str(value)
            else:
                return clean, f"'{key}' must be a string"
        elif kind == "array" and not isinstance(value, list):
            if isinstance(value, str):
                clean[key] = [value]
            else:
                return clean, f"'{key}' must be an array"
        elif kind == "object" and not isinstance(value, dict):
            return clean, f"'{key}' must be an object"
        if prop.get("enum") and clean[key] not in prop["enum"]:
            return clean, f"'{key}' must be one of: {', '.join(map(str, prop['enum']))}"
    return clean, None
