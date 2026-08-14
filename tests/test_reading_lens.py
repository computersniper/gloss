"""Discipline-aware paper reading behavior and cache isolation."""
from __future__ import annotations

import asyncio
import sqlite3

from app.features import notes, reading_lens, summarize


def test_init_db_migrates_legacy_papers_to_auto(tmp_path, monkeypatch) -> None:
    from app.library import store

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO papers (id, title) VALUES ('legacy', 'Old paper')")
    monkeypatch.setattr(store, "DB_PATH", database)

    store.init_db()

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT reading_lens FROM papers WHERE id='legacy'"
        ).fetchone()
    assert row == ("auto",)


def test_explicit_reading_lenses_are_stable() -> None:
    for lens in ("humanities", "social_sciences", "stem"):
        result = reading_lens.resolve_reading_lens(lens, "unrelated content")
        assert result["requested"] == lens
        assert result["resolved"] == lens
        assert result["confidence"] == 1.0


def test_auto_detects_humanities_social_sciences_and_stem() -> None:
    humanities = reading_lens.resolve_reading_lens(
        "auto",
        "This archival historiography uses close reading and primary sources.",
    )
    social = reading_lens.resolve_reading_lens(
        "auto",
        "Interviews and ethnographic fieldwork support a qualitative thematic analysis.",
    )
    stem = reading_lens.resolve_reading_lens(
        "auto",
        "The algorithm is trained on a benchmark dataset and reports accuracy and ablation results.",
    )
    assert humanities["resolved"] == "humanities"
    assert social["resolved"] == "social_sciences"
    assert stem["resolved"] == "stem"


def test_auto_uses_general_lens_for_ambiguous_text() -> None:
    result = reading_lens.resolve_reading_lens(
        "auto", "This paper discusses a question and offers several observations."
    )
    assert result == {
        "requested": "auto",
        "resolved": "general",
        "confidence": 0.0,
        "signals": [],
    }


def test_cache_key_changes_when_reading_lens_changes() -> None:
    text = "Archival historiography and close reading of primary sources."
    automatic = reading_lens.resolve_reading_lens("auto", text)
    explicit = reading_lens.resolve_reading_lens("humanities", text)
    stem = reading_lens.resolve_reading_lens("stem", text)
    assert reading_lens.cache_key("summary:English", automatic) != reading_lens.cache_key(
        "summary:English", explicit
    )
    assert reading_lens.cache_key("summary:English", explicit) != reading_lens.cache_key(
        "summary:English", stem
    )


def test_humanities_summary_prompt_centers_sources_and_argument(
    client, paper_id, monkeypatch
) -> None:
    response = client.patch(
        f"/api/papers/{paper_id}", json={"reading_lens": "humanities"}
    )
    assert response.status_code == 200
    captured: dict[str, str] = {}

    async def fake_complete(system, user, shape, **_kwargs):
        captured.update(system=system, user=user, shape=shape)
        return {"tldr": "ok"}

    monkeypatch.setattr(summarize, "json_complete", fake_complete)
    asyncio.run(summarize.summarize_paper(paper_id, language="English", refresh=True))

    prompt = captured["user"].lower()
    assert "primary and secondary sources" in prompt
    assert "argument's sequence" in prompt
    assert "do not demand experiments" in prompt


def test_humanities_notes_do_not_force_stem_sections(
    client, paper_id, monkeypatch
) -> None:
    client.patch(f"/api/papers/{paper_id}", json={"reading_lens": "humanities"})
    captured: dict[str, str] = {}

    async def fake_complete(system, user, **_kwargs):
        captured.update(system=system, user=user)
        return "# Notes"

    monkeypatch.setattr(notes, "text_complete", fake_complete)
    asyncio.run(notes.build_notes(paper_id, language="English", refresh=True))

    prompt = captured["user"]
    outline = prompt.split("=== PAPER ===", 1)[0]
    assert "材料与史料" in outline
    assert "论证链与关键细读" in outline
    assert "实验设置" not in outline
    assert "关键公式与符号" not in outline
