"""Verified-network evidence collection for Deep Research."""
from __future__ import annotations

import asyncio

import pytest

from app.search import deep_research, scholar


def _paper() -> dict:
    return {
        "id": "paper-1",
        "title": "Interpretive Communities and Literary Meaning",
        "abstract": "This article studies interpretation, archives, and reader communities.",
        "authors": ["Ada Scholar"],
        "tags": ["literature", "hermeneutics"],
    }


def _source(*, source: str = "openalex", title: str = "A Related Study") -> dict:
    return {
        "paper_id": "external-1",
        "title": title,
        "abstract": "Retrieved abstract.",
        "year": 2024,
        "authors": ["Researcher One"],
        "url": "https://example.org/work/1",
        "doi": "10.1000/example",
        "citations": 12,
        "source": source,
        "relation": "search",
    }


def test_build_queries_uses_title_topics_and_author_for_exhaustive_depth():
    standard = deep_research.build_queries(_paper(), depth="standard")
    exhaustive = deep_research.build_queries(_paper(), depth="exhaustive")

    assert standard[0] == _paper()["title"]
    assert "literature" in standard[1]
    assert len(standard) == 2
    assert len(exhaustive) == 3
    assert "Ada Scholar" in exhaustive[2]
    assert exhaustive[2].endswith("related work")


def test_openalex_abstract_reconstructs_inverted_index():
    assert scholar._openalex_abstract({"world": [1], "Hello": [0], "again": [2]}) == (
        "Hello world again"
    )
    assert scholar._openalex_abstract(None) == ""


def test_effective_paper_recovers_title_and_abstract_from_pdf_text():
    paper = {
        **_paper(),
        "title": "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023",
        "abstract": "",
    }
    parsed = {"full_text": """Provided proper attribution is provided, Google hereby grants permission to
reproduce the tables and figures in this paper solely for scholarly works.

Attention Is All You Need

Ashish Vaswani
avaswani@example.com

Abstract

The dominant sequence transduction models use recurrent or convolutional networks.
We propose the Transformer based solely on attention mechanisms for translation.

1 Introduction
Prior work used recurrent models.
"""}

    effective = deep_research._effective_paper(paper, parsed)

    assert effective["title"] == "Attention Is All You Need"
    assert effective["abstract"].startswith("The dominant sequence")


def test_deduplicate_assigns_stable_ids_and_preserves_provenance():
    rows = [
        _source(),
        {**_source(source="semanticscholar"), "url": "https://duplicate.example"},
        {**_source(title="Another Source"), "doi": "", "url": "https://example.org/2"},
    ]

    sources = deep_research._deduplicate(rows, _paper()["title"])

    assert [item["source_id"] for item in sources] == ["S1", "S2"]
    assert sources[0]["database"] == "openalex"
    assert sources[0]["url"] == "https://example.org/work/1"
    assert sources[1]["title"] == "Another Source"


def test_collect_external_evidence_allows_partial_provider_results(monkeypatch):
    monkeypatch.setattr(deep_research.store, "get_paper", lambda _paper_id: _paper())
    monkeypatch.setattr(
        deep_research.store, "load_parsed", lambda _paper_id: {"full_text": "Paper text"}
    )
    monkeypatch.setattr(deep_research, "_paper_reference_excerpts", lambda _parsed: [])

    async def semantic_failure(*_args, **_kwargs):
        raise RuntimeError("Semantic Scholar unavailable")

    async def openalex_success(*_args, **_kwargs):
        return [_source()]

    monkeypatch.setattr(scholar, "search_semantic_scholar", semantic_failure)
    monkeypatch.setattr(scholar, "search_openalex", openalex_success)

    evidence = asyncio.run(
        deep_research.collect_external_evidence("paper-1", depth="standard")
    )
    public = deep_research.public_metadata(evidence)

    assert evidence["source_count"] == 1
    assert evidence["sources"][0]["source_id"] == "S1"
    assert evidence["providers"] == ["OpenAlex"]
    assert public["partial"] is True
    assert public["failed_providers"] == ["Semantic Scholar"]
    assert '"source_id": "S1"' in deep_research.prompt_bundle(evidence)
    assert "https://example.org/work/1" in deep_research.prompt_bundle(evidence)


def test_collect_external_evidence_fails_when_every_provider_fails(monkeypatch):
    monkeypatch.setattr(deep_research.store, "get_paper", lambda _paper_id: _paper())
    monkeypatch.setattr(
        deep_research.store, "load_parsed", lambda _paper_id: {"full_text": "Paper text"}
    )

    async def failure(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(scholar, "search_semantic_scholar", failure)
    monkeypatch.setattr(scholar, "search_openalex", failure)

    with pytest.raises(deep_research.DeepResearchNetworkError, match="could not reach"):
        asyncio.run(deep_research.collect_external_evidence("paper-1", depth="standard"))


@pytest.mark.network
def test_live_scholarly_indexes_return_traceable_sources():
    async def search():
        return await asyncio.gather(
            scholar.search_semantic_scholar("Attention Is All You Need", 3),
            scholar.search_openalex("Attention Is All You Need", 3),
            return_exceptions=True,
        )

    semantic, openalex = asyncio.run(search())
    successful = [result for result in (semantic, openalex) if isinstance(result, list)]
    assert successful, f"all scholarly indexes failed: {semantic!r}; {openalex!r}"
    results = [item for result in successful for item in result]
    assert results
    for item in results:
        assert item["title"]
        assert item["url"] or item["doi"] or item["arxiv_id"]
