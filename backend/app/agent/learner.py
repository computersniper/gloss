"""Learner model: what the (single, local) user knows, for the tutoring workflow.

One JSON file ``<DATA_DIR>/learner.json`` maps concepts to evidence about the
learner: how often a concept was explained, and quiz answers that were right
or wrong. Mastery is the posterior mean of a Beta(1, 1) prior over correct
answers, so an untested concept sits at 0.5 and moves with every answer.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .. import config
from ..platform_support import read_utf8_text
from .capabilities import bm25_rank

OUTCOMES = ("correct", "wrong", "explained")
MAX_CONCEPTS = 2000
_lock = threading.RLock()


def _path() -> Path:
    return config.DATA_DIR / "learner.json"


def _key(concept: str) -> str:
    return re.sub(r"\s+", " ", str(concept or "").strip().lower())[:120]


def _load() -> dict[str, Any]:
    try:
        data = json.loads(read_utf8_text(_path()))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get("concepts"), dict):
        data = {"concepts": {}}
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def mastery(entry: dict[str, Any]) -> float:
    return (entry.get("correct", 0) + 1) / (entry.get("correct", 0) + entry.get("wrong", 0) + 2)


def _view(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "mastery": round(mastery(entry), 3)}


def record(concept: str, outcome: str, note: str = "") -> dict[str, Any]:
    key = _key(concept)
    if not key:
        raise ValueError("'concept' is required")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(OUTCOMES)}")
    with _lock:
        data = _load()
        concepts = data["concepts"]
        entry = concepts.get(key) or {"name": str(concept).strip()[:120], "correct": 0, "wrong": 0,
                                      "explained": 0, "notes": []}
        entry[outcome] = entry.get(outcome, 0) + 1
        entry["last"] = time.time()
        if note.strip():
            entry["notes"] = (entry.get("notes") or [])[-4:] + [note.strip()[:300]]
        concepts[key] = entry
        if len(concepts) > MAX_CONCEPTS:  # forget the least recently touched concepts
            for old in sorted(concepts, key=lambda k: concepts[k].get("last", 0))[: len(concepts) - MAX_CONCEPTS]:
                concepts.pop(old, None)
        _save(data)
        return _view(entry)


def profile(query: str = "", k: int = 8) -> list[dict[str, Any]]:
    """Concepts most related to ``query`` (all, most recent first, without one)."""
    with _lock:
        entries = list(_load()["concepts"].values())
    if query.strip():
        ranked = bm25_rank(query, [e.get("name", "") + " " + " ".join(e.get("notes") or []) for e in entries])
        entries = [entries[i] for i, score in ranked if score > 0]
    else:
        entries.sort(key=lambda e: e.get("last", 0), reverse=True)
    return [_view(e) for e in entries[:k]]
