"""Paper summarization: 3-sentence TL;DR + structured overview + key points."""
from __future__ import annotations

from ..config import output_language
from ..library import store
from .common import json_complete, truncate_to_tokens
from .reading_lens import cache_key as lens_cache_key
from .reading_lens import guidance_for, resolve_paper_lens

SYSTEM = (
    "You are Gloss, an expert research colleague. You read academic papers and "
    "produce faithful, concrete summaries. Never invent results; only use the paper. "
    "Write in the SAME language the user asks for (default: the paper's language)."
)

SHAPE = (
    '{"tldr": "<=3 sentence plain-language summary", '
    '"problem": "the problem addressed", '
    '"method": "the core method/approach", '
    '"results": "main quantitative/qualitative results", '
    '"contributions": ["novel contribution", "..."], '
    '"key_points": ["5-min read bullet", "..."], '
    '"limitations": ["stated or likely limitation", "..."]}'
)


async def summarize_paper(
    paper_id: str, *, language: str | None = None, refresh: bool = False,
    provider: str | None = None, model: str | None = None,
) -> dict:
    lang = language or output_language()
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    text = truncate_to_tokens(parsed["full_text"], 60000)
    resolution = resolve_paper_lens(paper_id, parsed["full_text"])
    cache_key = lens_cache_key(f"summary:{lang}", resolution)
    if not refresh:
        cached = store.cache_get(paper_id, cache_key)
        if cached:
            return cached

    user = (
        f"Summarize this paper.\n\nWrite the ENTIRE summary in: {lang}.\n\n"
        f"=== READING LENS ===\n{guidance_for(resolution)}\n\n"
        f"=== PAPER ===\n{text}"
    )
    result = await json_complete(
        SYSTEM, user, SHAPE, provider=provider, model=model
    )
    store.cache_set(paper_id, cache_key, result)
    return result
