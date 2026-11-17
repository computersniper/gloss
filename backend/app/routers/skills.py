"""Skill endpoints: list discovered Claude/Codex skills; run one on a paper."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..providers import registry
from ..skills import loader
from ..skills.runner import build_skill_prompt, find_skill

router = APIRouter(prefix="/api/skills", tags=["skills"])


class RunSkillBody(BaseModel):
    skill_id: str
    arguments: str = ""
    paper_id: str | None = None
    selection: str | None = None
    provider: str | None = None
    model: str | None = None


@router.get("")
async def list_skills():
    skills = loader.discover_skills()
    return {"skills": skills, "count": len(skills)}


def _lookup(skill_id: str) -> dict | None:
    return find_skill(skill_id)


@router.post("/run")
async def run_skill(body: RunSkillBody):
    skill = _lookup(body.skill_id)
    if not skill:
        raise HTTPException(404, "skill not found")
    system, user = build_skill_prompt(
        skill, body.arguments or "", body.paper_id, body.selection
    )

    async def gen():
        try:
            with registry.usage_context("skill", paper_id=body.paper_id, plugin_id=body.skill_id):
                async for delta in registry.stream(
                    system, [{"role": "user", "content": user}],
                    provider=body.provider, model=body.model,
                ):
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
