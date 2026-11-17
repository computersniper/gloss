"""Dependency-light lexical retrieval (BM25) over a paper's text blocks.

Used to ground the chat feature without downloading any embedding model, so it
works offline on the data disk.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def _tok(text: str) -> list[str]:
    # NFKC expands PDF ligatures ("\ufb01rst" -> "first"); CJK characters count as one token each
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())


def build_chunks(parsed: dict, max_chars: int = 900) -> list[dict]:
    """Flatten blocks into retrieval chunks with page + bbox provenance."""
    chunks: list[dict] = []
    for page in parsed.get("pages", []):
        for b in page.get("blocks", []):
            text = b.get("text", "").strip()
            if len(text) < 30:
                continue
            # split overly long blocks
            if len(text) <= max_chars:
                pieces = [text]
            else:
                pieces = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
            for pc in pieces:
                chunks.append({
                    "page": page["index"],
                    "bbox": b.get("bbox"),
                    "block_id": b.get("id"),
                    "text": pc,
                    "tokens": _tok(pc),
                })
    return chunks


def retrieve(query: str, chunks: list[dict], k: int = 6) -> list[dict]:
    """Return the top-k chunks by BM25 score."""
    q_terms = _tok(query)
    if not q_terms or not chunks:
        return chunks[:k]
    N = len(chunks)
    df: Counter = Counter()
    for c in chunks:
        for t in set(c["tokens"]):
            df[t] += 1
    avgdl = sum(len(c["tokens"]) for c in chunks) / N
    k1, b = 1.5, 0.75

    scored = []
    for c in chunks:
        tf = Counter(c["tokens"])
        dl = len(c["tokens"]) or 1
        score = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            denom = tf[t] + k1 * (1 - b + b * dl / avgdl)
            score += idf * (tf[t] * (k1 + 1)) / denom
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:k]]
