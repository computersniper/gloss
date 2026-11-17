"""Shared skill runner prompt builder (HTTP skill endpoint and GlossX agent)."""
from __future__ import annotations

from typing import Callable

from ..features.chat import _build_context
from . import loader


def find_skill(skill_id: str) -> dict | None:
    for s in loader.discover_skills():
        if s["id"] == skill_id:
            return s
    return None


def build_skill_prompt(
    skill: dict,
    arguments: str = "",
    paper_id: str | None = None,
    selection: str | None = None,
    *,
    wrap: Callable[[str, str], str] | None = None,
) -> tuple[str, str]:
    """Return ``(system, user)`` for running ``skill``.

    The SKILL.md body is loaded here, i.e. only when the skill actually runs
    (progressive disclosure). ``wrap`` marks paper context as untrusted data.
    """
    _, skill_body = loader.load_skill_body(skill["path"])
    args = arguments or ""
    instructions = skill_body.replace("$ARGUMENTS", args)

    context = ""
    if paper_id:
        context = _build_context(paper_id, args or skill["name"])
    if selection:
        context += f"\n\n[Selected text]:\n{selection}"

    system = (
        f"You are running the '{skill['name']}' skill inside Gloss, a paper reader. "
        f"Follow these skill instructions faithfully:\n\n{instructions}"
    )
    if context:
        if wrap is not None:
            context = wrap(f"paper:{paper_id}" if paper_id else "selection", context)
        system += f"\n\n# Current paper context\n{context}"

    user = args or f"Run the '{skill['name']}' skill on the current paper."
    return system, user
