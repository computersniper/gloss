"""Agent Skills adapter: every discovered SKILL.md becomes ``skill.<name>``.

Progressive disclosure: only the skill's description is in the card; the
SKILL.md body is loaded when the step runs, through the shared skill runner,
on the run's scope paper.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ...library import store
from ...providers import registry as providers
from ...skills import loader
from ...skills.runner import build_skill_prompt, find_skill
from ..capabilities import Capability, RunContext, clip_card
from ..context import UNTRUSTED_RULE, wrap_untrusted

logger = logging.getLogger(__name__)

CACHE_SECONDS = 30.0
_cache: dict[str, Any] = {"ts": 0.0, "skills": []}


def _discover() -> list[dict]:
    now = time.monotonic()
    if now - _cache["ts"] < CACHE_SECONDS:
        return _cache["skills"]
    try:
        skills = [s for s in loader.discover_skills() if s.get("type") == "skill"]
    except Exception as error:  # unreadable skill roots must not break the agent
        logger.warning("skill discovery failed: %s", error)
        skills = []
    _cache.update(ts=now, skills=skills)
    return skills


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(text).lower()).strip("-")[:48]


def _handler(skill_id: str, cap_name: str):
    async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
        skill = find_skill(skill_id)
        if not skill:
            raise ValueError("skill is no longer installed")
        paper_id = str(args.get("paper_id") or (ctx.paper_ids[0] if ctx.paper_ids else "")).strip()
        if paper_id and not store.get_paper(paper_id):
            raise ValueError(f"paper not found: {paper_id}")
        arguments = str(args.get("arguments") or args.get("request") or "")[:4000]
        system, user = build_skill_prompt(
            skill, arguments, paper_id or None, None, wrap=wrap_untrusted
        )
        system += f"\n\n{UNTRUSTED_RULE}"
        with providers.usage_context("agent", paper_id=paper_id or None, plugin_id=skill_id):
            text, _ = await providers.complete(
                system, [{"role": "user", "content": user}], provider=ctx.provider
            )
        return {"skill": cap_name, "paper_id": paper_id or None, "output": (text or "")[:6000]}

    return handler


def skill_capabilities() -> list[Capability]:
    caps: list[Capability] = []
    used: set[str] = set()
    for skill in _discover():
        base = _slug(skill.get("name") or "")
        if not base:
            continue
        name = f"skill.{base}"
        if name in used:
            name = f"skill.{_slug(skill.get('source') or 'x')}-{base}"
            if name in used:
                continue
        used.add(name)
        caps.append(Capability(
            name=name,
            kind="agent",
            card=clip_card(skill.get("description") or "") or f"Run the {base} skill.",
            input_schema={
                "arguments": "str: the request or arguments for the skill",
                "paper_id": "str (optional): library paper id; defaults to the first scope paper",
            },
            handler=_handler(skill["id"], name),
            permissions=["paper:read", "ai:complete"],
            cost="llm",
            source="skill",
            trust="external",
            meta={"skill_id": skill["id"]},
        ))
    return caps
