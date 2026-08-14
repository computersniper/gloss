"""Auto-highlight: LLM picks key sentences by category; anchor them to PDF rects."""
from __future__ import annotations

from ..config import output_language
from ..library import store
from ..pdf import ingest
from .common import json_complete, truncate_to_tokens
from .reading_lens import cache_revision, guidance_for, resolve_paper_lens

CATEGORY_COLORS = {
    "contribution": "#a5d6a7",   # green
    "method": "#90caf9",         # blue
    "result": "#fff59d",         # yellow
    "limitation": "#ef9a9a",     # red
    "definition": "#ce93d8",     # purple
    "background": "#ffcc80",     # orange
}

SYSTEM = (
    "You are Gloss's auto-highlighter. From the paper, select the MOST important "
    "sentences a researcher should notice, each tagged with a category. Copy each "
    "sentence VERBATIM from the text (so it can be located in the PDF). Prefer "
    "contributions, key methods, headline results, definitions, and limitations."
)

SHAPE = (
    '{"items": [{"text": "verbatim sentence from the paper", '
    '"category": "contribution|method|result|limitation|definition|background", '
    '"why": "short reason"}]}'
)


async def auto_highlight(
    paper_id: str, *, max_items: int = 24, provider: str | None = None, model: str | None = None
) -> list[dict]:
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    text = truncate_to_tokens(parsed["full_text"], 60000)
    resolution = resolve_paper_lens(paper_id, parsed["full_text"])
    user = (
        f"Select up to {max_items} key sentences (verbatim, in the paper's original "
        f"language so they can be located) with categories. Write each 'why' note in "
        f"{output_language()}.\n\n=== READING LENS ===\n"
        f"{guidance_for(resolution)} For humanities work, prioritize thesis statements, "
        f"source or textual evidence, interpretive turns, counter-readings, and limits; "
        f"map interpretive approach to 'method' and supported interpretations to 'result'."
        f"\n\n=== PAPER ===\n{text}"
    )
    result = await json_complete(SYSTEM, user, SHAPE, provider=provider, model=model)
    items = result.get("items", [])[:max_items]

    pdf = store.pdf_path(paper_id)
    store.clear_auto_highlights(paper_id)
    saved = []
    for it in items:
        sent = (it.get("text") or "").strip()
        if len(sent) < 8:
            continue
        loc = ingest.locate_text(pdf, sent)
        if not loc:
            continue
        cat = (it.get("category") or "background").lower()
        h = store.add_highlight(paper_id, {
            "page": loc["page"],
            "rects": loc["rects"],
            "color": CATEGORY_COLORS.get(cat, "#ffe082"),
            "category": cat,
            "text": sent,
            "note": it.get("why", ""),
            "kind": "auto",
        })
        saved.append(h)
    store.cache_set(paper_id, "autohighlight_done", cache_revision(resolution))
    return saved
