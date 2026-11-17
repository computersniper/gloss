"""Scoring: answer quality, claim labels, calibration and citation grounding.

Citation checks are the same for every system: each citation is
``{paper, page, quote}`` and is located in the paper's own text. A citation is
*fabricated* when its paper is not one of the task's papers or its quote cannot
be found there; it is *located* otherwise. Whether a located quote actually
supports the sentence is judged separately (NLI or LLM judge, see ``judge.py``).
"""
from __future__ import annotations

import math
import random
import re
import string
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable

from .tasks import CLAIM_LABELS

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)


def normalize(text: str) -> str:
    text = str(text or "").lower().translate(_PUNCT)
    return " ".join(_ARTICLES.sub(" ", text).split())


def token_f1(prediction: str, gold: str) -> float:
    pred, ref = normalize(prediction).split(), normalize(gold).split()
    if not pred or not ref:
        return float(pred == ref)
    common = Counter(pred) & Counter(ref)
    same = sum(common.values())
    if same == 0:
        return 0.0
    precision, recall = same / len(pred), same / len(ref)
    return 2 * precision * recall / (precision + recall)


def best_f1(prediction: str, golds: Iterable[str]) -> float:
    """QASPER-style: max token F1 over the annotators' answers."""
    return max((token_f1(prediction, g) for g in golds), default=0.0)


def exact_match(prediction: str, golds: Iterable[str]) -> float:
    p = normalize(prediction)
    return float(any(p == normalize(g) for g in golds))


VERDICT_LINE = re.compile(r"verdict\s*[:：]\s*(supported|refuted|mixed|not enough evidence)", re.I)


def parse_verdict(answer: str) -> str | None:
    """The ``Verdict: X`` line the claim-verification prompt asks every system for."""
    match = VERDICT_LINE.search(answer or "")
    return match.group(1).upper() if match else None


def label_scores(pairs: list[tuple[str | None, str]]) -> dict[str, float]:
    """Accuracy and macro-F1 over (predicted, gold) claim labels."""
    if not pairs:
        return {"accuracy": 0.0, "macro_f1": 0.0, "n": 0}
    acc = sum(p == g for p, g in pairs) / len(pairs)
    f1s = []
    for label in CLAIM_LABELS:
        tp = sum(p == label and g == label for p, g in pairs)
        fp = sum(p == label and g != label for p, g in pairs)
        fn = sum(p != label and g == label for p, g in pairs)
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return {"accuracy": acc, "macro_f1": sum(f1s) / len(f1s), "n": len(pairs)}


def ece(confidences: list[float], correct: list[bool], bins: int = 10) -> float:
    """Expected calibration error with equal-width bins."""
    if not confidences:
        return 0.0
    total = len(confidences)
    err = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confidences) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if not idx:
            continue
        conf = sum(confidences[i] for i in idx) / len(idx)
        acc = sum(correct[i] for i in idx) / len(idx)
        err += len(idx) / total * abs(conf - acc)
    return err


def locate_quote(quote: str, text: str, threshold: float = 0.85) -> float:
    """Best fuzzy match ratio of ``quote`` inside ``text`` (0 when below threshold)."""
    q, t = normalize(quote), normalize(text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    words, n = t.split(), len(q.split())
    best = 0.0
    for size in {max(1, n - 2), n, n + 2}:
        for i in range(0, max(1, len(words) - size + 1)):
            ratio = SequenceMatcher(None, q, " ".join(words[i:i + size])).ratio()
            if ratio > best:
                best = ratio
                if best >= 0.99:
                    return best
    return best if best >= threshold else 0.0


def citation_report(
    citations: list[dict[str, Any]],
    task_papers: list[str],
    paper_text: Callable[[str, int | None], str],
) -> dict[str, Any]:
    """Locate every citation; returns counts and per-citation detail.

    ``paper_text(paper, page)`` returns the text of that page (or the whole
    paper when ``page`` is None); pages are 1-based as the systems print them.
    """
    known = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0]: p for p in task_papers}
    rows = []
    for c in citations:
        paper = str(c.get("paper") or "")
        key = paper.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        quote = str(c.get("quote") or "")
        page = c.get("page") if isinstance(c.get("page"), int) else None
        if key not in known or not quote.strip():
            rows.append({**c, "located": False, "fabricated": True, "match": 0.0})
            continue
        match = locate_quote(quote, paper_text(known[key], page)) if page is not None else 0.0
        page_ok = bool(match)
        if not match:  # off-by-one pages are common: fall back to the whole paper
            match = locate_quote(quote, paper_text(known[key], None))
        rows.append({**c, "located": bool(match), "fabricated": not match, "match": match,
                     "page_ok": page_ok})
    n = len(rows)
    return {
        "n": n,
        "located": sum(r["located"] for r in rows),
        "fabricated": sum(r["fabricated"] for r in rows),
        "fabricated_rate": (sum(r["fabricated"] for r in rows) / n) if n else 0.0,
        "rows": rows,
    }


def cohen_kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's kappa for two raters' nominal labels."""
    if not pairs:
        return math.nan
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in left) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def bootstrap_ci(values: list[float], iters: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> tuple[float, float, float]:
    """Mean and a percentile bootstrap confidence interval over tasks."""
    if not values:
        return (math.nan, math.nan, math.nan)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(iters))
    lo = means[int(alpha / 2 * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (sum(values) / n, lo, hi)
