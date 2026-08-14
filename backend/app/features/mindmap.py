"""Mind map: distill a paper into a hierarchical nested-tree concept map."""
from __future__ import annotations

from ..config import output_language
from ..library import store
from .common import json_complete, truncate_to_tokens
from .reading_lens import cache_key as lens_cache_key
from .reading_lens import guidance_for, resolve_paper_lens

SYSTEM = (
    "You are Gloss, an expert who distills an academic paper into a clear, "
    "richly-annotated mind map. The root is the paper's core topic/title. Branches "
    "are the major aspects; leaves are concrete specifics. For EVERY node provide:\n"
    "  - title: SHORT label (max ~8 words, no trailing period)\n"
    "  - kind: one of problem | method | result | concept | contribution | "
    "background | experiment | limitation | data | context | source | theory | "
    "argument | interpretation | evidence  (root uses kind \"root\")\n"
    "  - summary: 1-2 sentence plain-language explanation of that node, faithful to "
    "the paper (concrete: name the technique/number/finding). This is shown when the "
    "user clicks the node.\n"
    "Aim for 5-8 top branches, 2-5 children each, depth 2-3. Do not invent facts; "
    "only use the paper."
)

SHAPE = (
    '{"title": "paper topic", "kind": "root", "summary": "one-sentence overview", '
    '"children": [{"title": "branch", "kind": "method", "summary": "1-2 sentences", '
    '"children": [{"title": "leaf", "kind": "concept", "summary": "1-2 sentences"}]}]}'
)


async def build_mindmap(
    paper_id: str, *, language: str | None = None, refresh: bool = False,
    provider: str | None = None, model: str | None = None,
) -> dict:
    lang = language or output_language()
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    text = truncate_to_tokens(parsed["full_text"], 55000)
    resolution = resolve_paper_lens(paper_id, parsed["full_text"])
    cache_key = lens_cache_key(f"mindmap2:{lang}", resolution)
    if not refresh:
        cached = store.cache_get(paper_id, cache_key)
        if cached:
            return cached

    user = (
        f"Build a mind map of this paper. Labels in {lang}.\n\n"
        f"=== READING LENS ===\n{guidance_for(resolution)}\n\n"
        f"=== PAPER ===\n{text}"
    )
    result = await json_complete(SYSTEM, user, SHAPE, provider=provider, model=model)

    # tolerate a {"tree": {...}} wrapper or a missing root title
    if isinstance(result, dict) and "title" not in result and isinstance(result.get("tree"), dict):
        result = result["tree"]
    if not isinstance(result, dict) or "title" not in result:
        title = (parsed.get("title") or "").strip() or "Mind Map"
        children = result.get("children", []) if isinstance(result, dict) else []
        result = {"title": title, "children": children}

    store.cache_set(paper_id, cache_key, result)
    return result
