from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from grados.config import SearchConfig
from grados.search.academic import CrossrefState, PaperMetadata, PubMedState, SearchPageResult
from grados.search.resumable import ContinuationData, decode_token, encode_token, run_resumable_search
from grados.storage.vector import search_papers


def test_continuation_token_round_trip() -> None:
    original = ContinuationData(
        query="composite vibration",
        normalized_query="composite vibration",
        active_sources=["Crossref"],
        exhausted_sources=["PubMed"],
        seen_dois=["10.1234/demo"],
        issued_at="2026-04-02T00:00:00+00:00",
    )

    decoded = decode_token(encode_token(original))

    assert decoded == original


def test_search_config_defaults_match_supported_sources() -> None:
    config = SearchConfig()

    assert config.order == ["Elsevier", "Springer", "WebOfScience", "Crossref", "PubMed"]
    assert config.enabled == {
        "Elsevier": True,
        "Springer": True,
        "WebOfScience": True,
        "Crossref": True,
        "PubMed": True,
    }


def test_run_resumable_search_handles_dedup_and_continuation(monkeypatch) -> None:
    async def fake_crossref(query, limit, state, client):
        assert query == "composite vibration"
        if state.pages_fetched == 0:
            return (
                SearchPageResult(
                    papers=[
                        PaperMetadata(title="Paper A", doi="10.1000/a", source="Crossref"),
                        PaperMetadata(title="Paper B", doi="10.1000/b", source="Crossref"),
                    ],
                    exhausted=False,
                ),
                CrossrefState(cursor="next", rows=state.rows, pages_fetched=1, cursor_issued_at=state.cursor_issued_at),
            )
        return (
            SearchPageResult(
                papers=[PaperMetadata(title="Paper B duplicate", doi="10.1000/b", source="Crossref")],
                exhausted=True,
            ),
            CrossrefState(cursor="done", rows=state.rows, pages_fetched=2, cursor_issued_at=state.cursor_issued_at),
        )

    async def fake_pubmed(query, limit, state, client):
        return (
            SearchPageResult(
                papers=[
                    PaperMetadata(title="Paper B duplicate", doi="10.1000/b", source="PubMed"),
                    PaperMetadata(title="Paper C", doi="10.1000/c", source="PubMed"),
                ],
                exhausted=True,
            ),
            PubMedState(retstart=state.retstart + state.page_size, page_size=state.page_size, total_count=2),
        )

    def fake_build_search_adapters(api_keys, etiquette_email, limit):
        issued_at = datetime.now(UTC).isoformat()
        return {
            "Crossref": {
                "init": lambda: CrossrefState(cursor="*", rows=2, pages_fetched=0, cursor_issued_at=issued_at),
                "fetch": fake_crossref,
                "max_page_size": 100,
            },
            "PubMed": {
                "init": lambda: PubMedState(retstart=0, page_size=2, total_count=2),
                "fetch": fake_pubmed,
                "max_page_size": 100,
            },
        }

    monkeypatch.setattr("grados.search.resumable.build_search_adapters", fake_build_search_adapters)

    first = asyncio.run(
        run_resumable_search(
            query="composite vibration",
            limit=2,
            continuation_token=None,
            search_order=["Crossref", "PubMed"],
            api_keys={},
            etiquette_email="research@example.edu",
        )
    )

    assert [paper.doi for paper in first.results] == ["10.1000/a", "10.1000/b"]
    assert first.has_more is True
    assert first.next_continuation_token is not None

    second = asyncio.run(
        run_resumable_search(
            query="composite vibration",
            limit=2,
            continuation_token=first.next_continuation_token,
            search_order=["Crossref", "PubMed"],
            api_keys={},
            etiquette_email="research@example.edu",
        )
    )

    assert [paper.doi for paper in second.results] == ["10.1000/c"]
    assert second.continuation_applied is True
    assert second.next_continuation_token is None


def test_search_papers_applies_metadata_filters_and_hybrid_reranking(monkeypatch) -> None:
    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: type("Docs", (), {"count": lambda self: 2})())
    monkeypatch.setattr(
        vector,
        "list_paper_documents",
        lambda chroma_dir: [
            {
                "doi": "10.1234/demo-a",
                "safe_doi": "10_1234_demo_a",
                "title": "Composite Damping Study",
                "source": "Crossref",
                "fetch_outcome": "native_full_text",
                "authors": ["Alice Smith", "Bob Lee"],
                "year": "2025",
                "journal": "Composite Structures",
                "section_headings": ["Abstract", "Methods"],
                "word_count": 100,
                "char_count": 800,
                "uri": "grados://papers/10_1234_demo_a",
                "content_markdown": "Composite vibration damping is discussed in detail.",
            },
            {
                "doi": "10.5678/demo-b",
                "safe_doi": "10_5678_demo_b",
                "title": "Unrelated Study",
                "source": "PubMed",
                "fetch_outcome": "native_full_text",
                "authors": ["Carol Jones"],
                "year": "2021",
                "journal": "Medical Journal",
                "section_headings": ["Abstract"],
                "word_count": 100,
                "char_count": 500,
                "uri": "grados://papers/10_5678_demo_b",
                "content_markdown": "Cell biology content.",
            },
        ],
    )

    class FakeChunks:
        def count(self) -> int:
            return 2

        def query(self, **kwargs):
            return {
                "distances": [[0.1, 0.4]],
                "documents": [[
                    "Composite vibration damping is discussed in detail.",
                    "Cell biology content.",
                ]],
                "metadatas": [[
                    {
                        "doi": "10.1234/demo-a",
                        "safe_doi": "10_1234_demo_a",
                        "title": "Composite Damping Study",
                    },
                    {
                        "doi": "10.5678/demo-b",
                        "safe_doi": "10_5678_demo_b",
                        "title": "Unrelated Study",
                    },
                ]],
            }

    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: FakeChunks())

    results = search_papers(
        chroma_dir=None,  # type: ignore[arg-type]
        query="composite vibration damping",
        limit=5,
        authors="alice",
        year_from=2024,
        journal="Composite",
        source="Crossref",
    )

    assert len(results) == 1
    assert results[0]["doi"] == "10.1234/demo-a"
    assert results[0]["authors"] == ["Alice Smith", "Bob Lee"]
    assert "Composite vibration damping" in results[0]["snippet"]


def test_search_papers_can_fall_back_to_document_level_lexical_matching(monkeypatch) -> None:
    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: type("Docs", (), {"count": lambda self: 1})())
    monkeypatch.setattr(
        vector,
        "list_paper_documents",
        lambda chroma_dir: [
            {
                "doi": "10.9999/local",
                "safe_doi": "10_9999_local",
                "title": "Local Composite Notes",
                "source": "Local PDF Library",
                "fetch_outcome": "local_import",
                "authors": [],
                "year": "2026",
                "journal": "",
                "section_headings": ["Abstract"],
                "word_count": 100,
                "char_count": 700,
                "uri": "grados://papers/10_9999_local",
                "content_markdown": "This local paper discusses composite vibration damping in detail.",
            }
        ],
    )

    class EmptyChunks:
        def count(self) -> int:
            return 0

    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: EmptyChunks())

    results = search_papers(
        chroma_dir=None,  # type: ignore[arg-type]
        query="composite vibration damping",
        limit=5,
    )

    assert len(results) == 1
    assert results[0]["doi"] == "10.9999/local"
    assert results[0]["dense_score"] == 0.0
