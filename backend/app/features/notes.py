"""Study notes: a polished, faithful structured Markdown note for a paper."""
from __future__ import annotations

from ..config import output_language
from ..library import store
from .common import text_complete, truncate_to_tokens
from .reading_lens import cache_key as lens_cache_key
from .reading_lens import guidance_for, resolve_paper_lens

SYSTEM = (
    "You are Gloss, an expert researcher writing polished, faithful study "
    "notes for an academic paper. Be concrete; never hallucinate; only use the "
    "paper. Cite section/figure names when useful, and render math with $...$. "
    "Write ENTIRELY in {lang}. Use Markdown with clear ## headings. Omit a "
    "requested subsection when the paper genuinely has no corresponding content; "
    "never invent an experiment, formula, source, or result to fill a template."
)

OUTLINES = {
    "humanities": (
        "一句话概述 / 历史与思想语境 / 核心论题与中心论点 / 材料与史料 / "
        "理论框架与阐释方法 / 论证链与关键细读 / 主要阐释与洞见 / "
        "反向解读与争议 / 创新点 / 局限与不足 / 对我的启发 / 待深入的问题"
    ),
    "social_sciences": (
        "一句话概述 / 背景与研究问题 / 理论框架 / 研究设计与田野语境 / "
        "样本或参与者 / 资料收集与分析 / 研究发现 / 效度、伦理与研究者位置 / "
        "创新点 / 适用范围与局限 / 对我的启发 / 待深入的问题"
    ),
    "stem": (
        "一句话概述 / 背景与动机 / 要解决的问题 / 核心方法(附直觉解释) / "
        "关键公式与符号 / 实验设置 / 主要结果(含关键数字) / 创新点 / "
        "局限与不足 / 对我的启发(可复用点) / 待深入的问题"
    ),
    "general": (
        "一句话概述 / 背景与语境 / 核心问题或论题 / 方法、路径或材料 / "
        "论证、证据或发现 / 创新点 / 局限与不足 / 对我的启发 / 待深入的问题"
    ),
}


async def build_notes(
    paper_id: str, *, language: str | None = None, refresh: bool = False,
    provider: str | None = None, model: str | None = None,
) -> str:
    lang = language or output_language()
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    text = truncate_to_tokens(parsed["full_text"], 60000)
    resolution = resolve_paper_lens(paper_id, parsed["full_text"])
    cache_key = lens_cache_key(f"notes:{lang}", resolution)
    if not refresh:
        cached = store.cache_get(paper_id, cache_key)
        if cached:
            return cached

    system = SYSTEM.format(lang=lang)
    user = (
        f"Write structured study notes for this paper, in {lang}.\n\n"
        f"=== READING LENS ===\n{guidance_for(resolution)}\n\n"
        f"Use these headings in this order where applicable: "
        f"{OUTLINES[resolution['resolved']]}. Translate the heading names into "
        f"{lang} when needed.\n\n"
        f"=== PAPER ===\n{text}"
    )
    md = await text_complete(system, user, provider=provider, model=model)
    store.cache_set(paper_id, cache_key, md)
    return md
