"""Discipline-aware reading lenses shared by core features and plugins."""
from __future__ import annotations

import re
from typing import Literal, TypedDict

from ..library import store

ReadingLens = Literal["auto", "humanities", "social_sciences", "stem"]
ResolvedReadingLens = Literal[
    "general", "humanities", "social_sciences", "stem"
]

VALID_READING_LENSES: tuple[ReadingLens, ...] = (
    "auto",
    "humanities",
    "social_sciences",
    "stem",
)
CACHE_REVISION = "reading-lens-v1"


class LensResolution(TypedDict):
    requested: ReadingLens
    resolved: ResolvedReadingLens
    confidence: float
    signals: list[str]


_SIGNALS: dict[ResolvedReadingLens, tuple[str, ...]] = {
    "humanities": (
        "archive", "archival", "historiography", "historical context",
        "literary", "literature", "narrative", "discourse", "rhetoric",
        "philosophy", "philosophical", "interpretation", "hermeneutic",
        "textual analysis", "primary source", "cultural history",
        "close reading", "intellectual history", "史料", "档案", "叙事",
        "阐释", "文本分析", "文学", "哲学", "思想史",
    ),
    "social_sciences": (
        "interview", "survey", "participant", "respondent", "ethnography",
        "ethnographic", "qualitative", "thematic analysis", "fieldwork",
        "case study", "focus group", "mixed methods", "social theory",
        "positionality", "sampling", "问卷", "访谈", "参与者", "田野",
        "民族志", "质性研究", "主题分析", "社会科学",
    ),
    "stem": (
        "algorithm", "dataset", "accuracy", "benchmark", "training",
        "neural network", "model architecture", "loss function", "f1 score",
        "ablation", "simulation", "experiment", "experimental results",
        "statistical significance", "implementation", "算法", "数据集",
        "准确率", "训练", "模型", "实验结果", "消融", "仿真",
    ),
    "general": (),
}

_GUIDANCE: dict[ResolvedReadingLens, str] = {
    "humanities": (
        "Use a humanities reading lens. Center the research question and central "
        "thesis; historical, cultural, or intellectual context; primary and "
        "secondary sources; theoretical framework; interpretive or textual method; "
        "the argument's sequence; counter-readings; and limits of the interpretation. "
        "Do not demand experiments, datasets, metrics, formulas, or quantitative "
        "results when the paper does not use them. Treat a supported interpretation "
        "or argument as a result in compatibility fields."
    ),
    "social_sciences": (
        "Use a social-science reading lens. Identify whether the study is qualitative, "
        "quantitative, or mixed-methods; describe participants or sample and field "
        "context; evidence collection and analysis; positionality, validity, ethics, "
        "findings, scope, and transferability. Do not invent metrics or experiments "
        "for qualitative work."
    ),
    "stem": (
        "Use a STEM reading lens. Emphasize the technical problem, assumptions, "
        "method or system, datasets or experimental setup when present, evaluation "
        "metrics, quantitative and qualitative results, reproducibility, and limits."
    ),
    "general": (
        "Use a discipline-neutral academic reading lens. Follow the paper's own "
        "epistemic style and evidence. Describe its question, approach or materials, "
        "argument or findings, contributions, and limits without assuming that it "
        "must contain experiments, datasets, metrics, or formulas."
    ),
}


def validate_reading_lens(value: str | None) -> ReadingLens:
    normalized = (value or "auto").strip().lower()
    if normalized not in VALID_READING_LENSES:
        raise ValueError(
            "reading_lens must be one of: " + ", ".join(VALID_READING_LENSES)
        )
    return normalized  # type: ignore[return-value]


def _present(text: str, signal: str) -> bool:
    if re.fullmatch(r"[a-z0-9 -]+", signal):
        return re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", text) is not None
    return signal in text


def resolve_reading_lens(requested: str | None, text: str) -> LensResolution:
    lens = validate_reading_lens(requested)
    if lens != "auto":
        return {
            "requested": lens,
            "resolved": lens,
            "confidence": 1.0,
            "signals": [],
        }

    lowered = (text or "").lower()
    matches = {
        name: [signal for signal in signals if _present(lowered, signal.lower())]
        for name, signals in _SIGNALS.items()
        if name != "general"
    }
    ranked = sorted(matches, key=lambda name: len(matches[name]), reverse=True)
    winner = ranked[0]
    top = len(matches[winner])
    runner_up = len(matches[ranked[1]])
    total = sum(len(items) for items in matches.values())
    if top < 2 or top == runner_up:
        return {
            "requested": "auto",
            "resolved": "general",
            "confidence": 0.0,
            "signals": [],
        }
    return {
        "requested": "auto",
        "resolved": winner,  # type: ignore[typeddict-item]
        "confidence": round(top / max(total, 1), 2),
        "signals": matches[winner][:6],
    }


def resolve_paper_lens(paper_id: str, text: str) -> LensResolution:
    paper = store.get_paper(paper_id)
    if not paper:
        raise ValueError("paper not found")
    return resolve_reading_lens(paper.get("reading_lens"), text)


def guidance_for(resolution: LensResolution) -> str:
    return _GUIDANCE[resolution["resolved"]]


def cache_revision(resolution: LensResolution) -> str:
    return (
        f"{CACHE_REVISION}:{resolution['requested']}:{resolution['resolved']}"
    )


def cache_key(base: str, resolution: LensResolution) -> str:
    return f"{base}:{cache_revision(resolution)}"


def cache_key_for_paper(paper_id: str, base: str) -> str:
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    resolution = resolve_paper_lens(paper_id, parsed.get("full_text", ""))
    return cache_key(base, resolution)
