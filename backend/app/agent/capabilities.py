"""Capability ABI and registry for the GlossX agent.

A capability is anything the planner may call: a builtin tool wrapping an
existing Gloss feature, a sub-agent, a declarative workflow, a Skill, an MCP
tool, a CLI delegate or a remote host. The planner only ever sees the short
``card`` plus the input schema, so cards must stay terse (<= 30 words).

Adapters (``build_registry``) compile every source into capabilities:

* first-party builtins (``builtin.py``)                  trust ``first-party``
* installed v1 plugins -> ``plugin.<id>``                trust ``third-party``
* v2 workflow manifests -> ``workflow.<id>``             trust first/third-party
* Agent Skills -> ``skill.<name>``                       trust ``external``
* MCP tools -> ``mcp.<server>.<tool>``                   trust ``external``
* Claude Code / Codex -> ``delegate.*``                  trust ``external``
* SSH hosts -> ``remote.ssh.*``                          trust ``external``
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..features.retrieval import _tok

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .context import SpanStore

logger = logging.getLogger(__name__)

KINDS = {
    "tool", "agent", "source", "workflow", "loop", "decider", "memory", "provider", "view",
}
COSTS = {"free", "llm", "network"}
TRUST_TIERS = {"first-party", "third-party", "external"}
APPROVAL_REQUIRED = frozenset({"code:execute", "remote:ssh", "mcp:call", "library:write"})
DEFAULT_TIMEOUT = 180.0
CARD_WORDS = 30


@dataclass
class RunContext:
    """Per-run state handed to every capability handler."""

    run_id: str
    paper_ids: list[str]
    spans: "SpanStore"
    import_jobs: Any | None = None
    provider: str | None = None
    project_id: str | None = None
    goal: str = ""
    # source (arXiv id / DOI / URL / title) -> imported library paper id
    imported: dict[str, str] = field(default_factory=dict)
    # set by the loop: shared decider for capabilities that need judgements
    decider: Any | None = None
    emit: Callable[[dict[str, Any]], None] | None = None


Handler = Callable[[dict[str, Any], RunContext], Awaitable[Any]]


def clip_card(text: str, words: int = CARD_WORDS) -> str:
    parts = " ".join(str(text or "").split()).split()
    out = " ".join(parts[:words])
    if len(parts) > words:
        out = out.rstrip(".,;:") + "..."
    return out


@dataclass
class Capability:
    name: str
    kind: str
    card: str
    input_schema: dict[str, str]
    handler: Handler
    permissions: list[str] = field(default_factory=list)
    cost: str = "free"
    source: str = "builtin"
    trust: str = "first-party"
    output_schema: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    meta: dict[str, Any] = field(default_factory=dict)
    # what must be installed or configured for the handler to work (e.g. "codex CLI")
    requirements: list[str] = field(default_factory=list)
    # what the call changes outside the run, in a few words ("" = read-only)
    side_effects: str = ""

    def approval_permissions(self, auto_approve: set[str] | frozenset[str] = frozenset()) -> list[str]:
        """Permissions of this capability that still need a human approval."""
        return [p for p in self.permissions if p in APPROVAL_REQUIRED and p not in auto_approve]

    def describe(self, auto_approve: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
        """Public, JSON-serialisable description (no handler)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "card": self.card,
            "input_schema": dict(self.input_schema),
            "source": self.source,
            "permissions": list(self.permissions),
            "requires_approval": bool(self.approval_permissions(auto_approve)),
            "trust": self.trust,
            "cost": self.cost,
            "requirements": list(self.requirements),
            "side_effects": self.side_effects,
        }


def bm25_rank(query: str, docs: list[str]) -> list[tuple[int, float]]:
    """Rank ``docs`` by BM25 against ``query``; returns (index, score) for all docs."""
    q_terms = _tok(query or "")
    toks = [_tok(d) for d in docs]
    if not q_terms or not docs:
        return [(i, 0.0) for i in range(len(docs))]
    n = len(docs)
    df: Counter = Counter()
    for t in toks:
        df.update(set(t))
    avgdl = (sum(len(t) for t in toks) / n) or 1.0
    k1, b = 1.5, 0.75
    scored = []
    for i, t in enumerate(toks):
        tf = Counter(t)
        dl = len(t) or 1
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (tf[term] * (k1 + 1)) / (tf[term] + k1 * (1 - b + b * dl / avgdl))
        scored.append((i, score))
    # stable: ties keep registration order (builtins first)
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, cap: Capability) -> None:
        if cap.kind not in KINDS:
            raise ValueError(f"unknown capability kind: {cap.kind}")
        if cap.cost not in COSTS:
            raise ValueError(f"unknown capability cost: {cap.cost}")
        if cap.trust not in TRUST_TIERS:
            raise ValueError(f"unknown trust tier: {cap.trust}")
        self._caps[cap.name] = cap

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def resolve(self, use: str) -> Capability | None:
        """Look up a capability by name, accepting ``kind:name`` aliases.

        Manifests may write ``tool:scholar.search``, ``agent:reader`` or
        ``mcp:zotero.search``; these map to ``scholar.search``,
        ``agent.reader`` and ``mcp.zotero.search``.
        """
        use = str(use or "").strip()
        if not use:
            return None
        if use in self._caps:
            return self._caps[use]
        kind, sep, rest = use.partition(":")
        if sep and rest:
            for candidate in (rest, f"{kind}.{rest}"):
                if candidate in self._caps:
                    return self._caps[candidate]
        return None

    def all(self) -> list[Capability]:
        return list(self._caps.values())

    def ranked(
        self,
        query: str | None,
        k: int = 16,
        *,
        kinds: set[str] | None = None,
        exclude_kinds: set[str] | None = None,
    ) -> list[Capability]:
        caps = [
            c for c in self.all()
            if (kinds is None or c.kind in kinds)
            and (exclude_kinds is None or c.kind not in exclude_kinds)
        ]
        if query:
            order = bm25_rank(query, [f"{c.name.replace('.', ' ')} {c.card}" for c in caps])
            caps = [caps[i] for i, _ in order]
        return caps[:k]

    def cards(
        self,
        query: str | None = None,
        k: int = 16,
        *,
        kinds: set[str] | None = None,
        exclude_kinds: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Planner-facing cards; ranked by BM25 against ``query`` if given."""
        return [c.describe() for c in self.ranked(query, k, kinds=kinds, exclude_kinds=exclude_kinds)]


def _plugin_capability(manifest: dict[str, Any]) -> Capability:
    """Adapt a v1 plugin manifest into a v2 capability."""
    plugin_id = manifest["id"]

    async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
        from ..plugins import manager
        from ..providers import registry as providers

        paper_id = str(args.get("paper_id") or (ctx.paper_ids[0] if ctx.paper_ids else ""))
        if not paper_id:
            raise ValueError("paper_id is required")
        with providers.usage_context("agent", paper_id=paper_id, plugin_id=plugin_id):
            result = await manager.run_plugin(paper_id, plugin_id, provider=ctx.provider)
        if result.get("status") != "ready":
            return {"status": result.get("status"), "reason": result.get("reason")}
        return {"status": "ready", "markdown": (result.get("markdown") or "")[:6000]}

    description = str(manifest.get("description") or manifest.get("name") or "")
    card = clip_card(description) or f"Run the {plugin_id} plugin on a paper."
    permissions = manifest.get("permissions")
    return Capability(
        name=f"plugin.{plugin_id}",
        kind="agent",
        card=card,
        input_schema={"paper_id": "str: library paper id to run the plugin on"},
        handler=handler,
        permissions=[str(p) for p in permissions] if isinstance(permissions, list) else [],
        cost="llm",
        source="plugin",
        trust="first-party" if manifest.get("builtin") else "third-party",
    )


def _safe_register(reg: CapabilityRegistry, cap: Capability) -> None:
    try:
        reg.register(cap)
    except Exception:
        logger.exception("skipping capability %s", getattr(cap, "name", "?"))


def build_registry(*, connections: dict[str, Any] | None = None) -> CapabilityRegistry:
    """Builtins, plugins, workflows, skills and configured external adapters.

    MCP tools are included only for servers whose tool list is already known
    (see :func:`abuild_registry`, which starts enabled servers lazily first).
    """
    from ..plugins import manager
    from . import connections as conn_mod
    from .adapters import cli_delegate, mcp_client, skills, ssh
    from .builtin import register_builtins
    from .workflow import workflow_capabilities

    conns = connections if connections is not None else conn_mod.load()
    reg = CapabilityRegistry()
    register_builtins(reg)
    try:
        manifests = manager.list_installed()
    except Exception:  # plugin dir problems must not break the agent
        logger.exception("could not list installed plugins for the agent")
        manifests = []
    for manifest in manifests:
        try:
            reg.register(_plugin_capability(manifest))
        except Exception:
            logger.exception("skipping plugin %s", manifest.get("id"))
    for adapter in (
        lambda: workflow_capabilities(),
        lambda: manager_v2_agent_capabilities(),
        lambda: skills.skill_capabilities(),
        lambda: mcp_client.MANAGER.capabilities(conns),
        lambda: cli_delegate.delegate_capabilities(conns),
        lambda: ssh.ssh_capabilities(conns),
    ):
        try:
            caps = adapter()
        except Exception:
            logger.exception("capability adapter failed")
            continue
        for cap in caps:
            if reg.get(cap.name) is None:
                _safe_register(reg, cap)
    return reg


async def abuild_registry(*, connections: dict[str, Any] | None = None) -> CapabilityRegistry:
    """Like :func:`build_registry` but first lists tools of enabled MCP servers."""
    from . import connections as conn_mod
    from .adapters import mcp_client

    conns = connections if connections is not None else conn_mod.load()
    try:
        await mcp_client.MANAGER.refresh(conns)
    except Exception:
        logger.exception("could not refresh MCP servers")
    return build_registry(connections=conns)


def manager_v2_agent_capabilities() -> list[Capability]:
    """v2 manifests of kind agent/tool/skill with a prompt become prompt capabilities
    (``plugin.<id>``; skills become ``skill.<id>`` and need no paper)."""
    from ..plugins import manager

    caps: list[Capability] = []
    for manifest in manager.list_installed_v2():
        if manifest.get("kind") not in {"agent", "tool", "skill"} or not manifest.get("prompt"):
            continue
        caps.append(_v2_prompt_capability(manifest))
    return caps


def _v2_prompt_capability(manifest: dict[str, Any]) -> Capability:
    plugin_id = manifest["id"]
    prompt = manifest["prompt"]
    cap_meta = manifest.get("capability") or {}
    is_skill = manifest.get("kind") == "skill"

    async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
        from ..config import output_language
        from ..features.common import text_complete, truncate_to_tokens
        from ..library import store
        from ..providers import registry as providers
        from .context import UNTRUSTED_RULE, wrap_untrusted

        paper_id = str(args.get("paper_id") or (ctx.paper_ids[0] if ctx.paper_ids else ""))
        if is_skill and paper_id and not store.get_paper(paper_id):
            raise ValueError(f"paper not found: {paper_id}")
        if not is_skill and (not paper_id or not store.get_paper(paper_id)):
            raise ValueError("paper_id is required and must be a library paper")
        parsed = (store.load_parsed(paper_id) or {}) if paper_id else {}
        paper_text = truncate_to_tokens(parsed.get("full_text", ""), 30000)
        extra = {k: v for k, v in args.items() if k != "paper_id"}
        system = (
            f"You are running the declarative GlossX plugin '{manifest['name']}'. "
            f"Follow its instructions, use only the provided paper, write in "
            f"{output_language()} and return Markdown.\n\n{UNTRUSTED_RULE}"
        )
        user = (
            f"=== PLUGIN INSTRUCTIONS ===\n{prompt}\n\n"
            f"=== INPUTS ===\n{wrap_untrusted('agent-args', str(extra)[:2000])}"
        )
        if paper_id:
            user += f"\n\n=== PAPER ===\n{wrap_untrusted(f'paper:{paper_id}', paper_text)}"
        with providers.usage_context("agent", paper_id=paper_id, plugin_id=plugin_id):
            text = await text_complete(system, user, provider=ctx.provider)
        return {"status": "ready", "markdown": text[:6000]}

    schema = (
        {"arguments": "str (optional): the request for the skill",
         "paper_id": "str (optional): library paper id"}
        if is_skill else {"paper_id": "str: library paper id"}
    )
    for key, desc in (cap_meta.get("input") or {}).items():
        schema.setdefault(str(key), str(desc))
    return Capability(
        name=f"skill.{plugin_id}" if is_skill else f"plugin.{plugin_id}",
        kind="agent" if is_skill else manifest["kind"],
        card=clip_card(cap_meta.get("card") or manifest.get("description") or plugin_id),
        input_schema=schema,
        output_schema={str(k): str(v) for k, v in (cap_meta.get("output") or {}).items()},
        handler=handler,
        permissions=list(manifest.get("permissions") or []),
        cost="llm",
        source="plugin",
        trust="third-party",
    )
