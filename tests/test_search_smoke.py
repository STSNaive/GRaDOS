from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from grados.config import GRaDOSPaths, IndexingConfig, SearchConfig
from grados.search.academic import CrossrefState, PaperMetadata, PubMedState, SearchPageResult
from grados.search.resumable import ContinuationData, decode_token, encode_token, run_resumable_search
from grados.storage.chroma_client import collection_get, delete_paper_chunks, query_collection
from grados.storage.embedding import clear_embedding_backend_cache, load_embedding_backend
from grados.storage.papers import save_paper_markdown
from grados.storage.vector import PaperSearchResult, _chunk_text, get_index_stats, index_paper, search_papers


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

    class FakeBackend:
        def embed_query(self, query: str) -> list[float]:
            assert query == "composite vibration damping"
            return [0.2, 0.8]

    class FakeDocs:
        def count(self) -> int:
            return 2

        def query(self, **kwargs):
            assert "query_embeddings" in kwargs
            return {
                "distances": [[0.05]],
                "documents": [["Composite vibration damping is discussed in detail."]],
                "metadatas": [[
                    {
                        "doi": "10.1234/demo-a",
                        "safe_doi": "10_1234_demo_a",
                        "title": "Composite Damping Study",
                    }
                ]],
            }

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: FakeDocs())
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
            assert "query_embeddings" in kwargs
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
                            "section_name": "Abstract",
                            "section_level": 2,
                            "paragraph_start": 1,
                            "paragraph_count": 2,
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
    assert isinstance(results[0], PaperSearchResult)
    assert results[0].doi == "10.1234/demo-a"
    assert results[0].authors == ["Alice Smith", "Bob Lee"]
    assert results[0].section_name == "Abstract"
    assert results[0].paragraph_start == 1
    assert results[0].paragraph_count == 2
    assert "Composite vibration damping" in results[0].snippet


def test_search_papers_can_fall_back_to_document_level_lexical_matching(monkeypatch) -> None:
    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())

    class FakeBackend:
        def embed_query(self, query: str) -> list[float]:
            return [0.1, 0.9]

    class FakeDocs:
        def count(self) -> int:
            return 1

        def query(self, **kwargs):
            return {"distances": [[]], "documents": [[]], "metadatas": [[]]}

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: FakeDocs())
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
    assert isinstance(results[0], PaperSearchResult)
    assert results[0].doi == "10.9999/local"
    assert results[0].dense_score == 0.0


def test_search_papers_prefers_canonical_markdown_for_lexical_fallback(monkeypatch, tmp_path: Path) -> None:
    import grados.storage.vector as vector

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    (papers_dir / "10_9999_local.md").write_text(
        "---\n"
        'doi: "10.9999/local"\n'
        'title: "Local Composite Notes"\n'
        "---\n\n"
        "This canonical paper discusses composite vibration damping in detail.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())

    class FakeBackend:
        def embed_query(self, query: str) -> list[float]:
            return [0.1, 0.9]

    class FakeDocs:
        def count(self) -> int:
            return 1

        def query(self, **kwargs):
            return {"distances": [[]], "documents": [[]], "metadatas": [[]]}

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: FakeDocs())
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
                "content_markdown": "Stale Chroma copy without the relevant lexical terms.",
            }
        ],
    )

    class EmptyChunks:
        def count(self) -> int:
            return 0

    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: EmptyChunks())

    results = search_papers(
        chroma_dir=tmp_path / "database" / "chroma",
        papers_dir=papers_dir,
        query="composite vibration damping",
        limit=5,
    )

    assert len(results) == 1
    assert isinstance(results[0], PaperSearchResult)
    assert results[0].doi == "10.9999/local"
    assert "canonical paper discusses composite vibration damping" in results[0].snippet.lower()


def test_search_papers_merges_overlapping_chunk_hits_into_one_canonical_window(
    monkeypatch, tmp_path: Path
) -> None:
    import grados.storage.vector as vector

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    (papers_dir / "10_1234_demo_a.md").write_text(
        "---\n"
        'doi: "10.1234/demo-a"\n'
        'title: "Composite Damping Study"\n'
        "---\n\n"
        "## Results\n\n"
        "Composite damping improved vibration attenuation by 18%.\n\n"
        "The resonance peak was also reduced in the same experiment.\n\n"
        "Residual vibrations became negligible after treatment.\n\n"
        "An unrelated appendix note.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())

    class FakeBackend:
        def embed_query(self, query: str) -> list[float]:
            return [0.2, 0.8]

    class FakeDocs:
        def count(self) -> int:
            return 1

        def query(self, **kwargs):
            return {
                "distances": [[0.05]],
                "documents": [["Composite damping improved vibration attenuation by 18%."]],
                "metadatas": [[
                    {
                        "doi": "10.1234/demo-a",
                        "safe_doi": "10_1234_demo_a",
                        "title": "Composite Damping Study",
                    }
                ]],
            }

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: FakeDocs())
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
                "authors": ["Alice Smith"],
                "year": "2025",
                "journal": "Composite Structures",
                "section_headings": ["Results"],
                "word_count": 100,
                "char_count": 800,
                "uri": "grados://papers/10_1234_demo_a",
                "content_markdown": "Stale Chroma copy.",
            }
        ],
    )

    class FakeChunks:
        def count(self) -> int:
            return 2

        def query(self, **kwargs):
            return {
                "distances": [[0.08, 0.12]],
                "documents": [[
                    "Composite damping improved vibration attenuation by 18%. The resonance peak was reduced.",
                    "The resonance peak was reduced. Residual vibrations became negligible after treatment.",
                ]],
                "metadatas": [[
                    {
                        "doi": "10.1234/demo-a",
                        "safe_doi": "10_1234_demo_a",
                        "title": "Composite Damping Study",
                        "section_name": "Results",
                        "section_level": 2,
                        "paragraph_start": 0,
                        "paragraph_count": 3,
                    },
                    {
                        "doi": "10.1234/demo-a",
                        "safe_doi": "10_1234_demo_a",
                        "title": "Composite Damping Study",
                        "section_name": "Results",
                        "section_level": 2,
                        "paragraph_start": 2,
                        "paragraph_count": 2,
                    },
                ]],
            }

    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: FakeChunks())

    results = search_papers(
        chroma_dir=tmp_path / "database" / "chroma",
        papers_dir=papers_dir,
        query="composite damping attenuation resonance",
        limit=5,
    )

    assert len(results) == 1
    assert isinstance(results[0], PaperSearchResult)
    assert results[0].paragraph_start == 0
    assert results[0].paragraph_count == 4
    assert "residual vibrations became negligible" in results[0].snippet.lower()
    assert "unrelated appendix note" not in results[0].snippet.lower()


def test_search_papers_uses_index_candidates_before_canonical_hydration(monkeypatch) -> None:
    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())
    monkeypatch.setattr(
        vector,
        "list_paper_documents",
        lambda chroma_dir: (_ for _ in ()).throw(AssertionError("full-library document listing should not run")),
    )

    class FakeBackend:
        def embed_query(self, query: str) -> list[float]:
            return [0.1, 0.9]

    summaries = [
        {
            "doi": f"10.1000/{index}",
            "safe_doi": f"10_1000_{index}",
            "title": f"Paper {index}",
            "source": "Crossref",
            "fetch_outcome": "native_full_text",
            "authors_json": '["Alice Smith"]',
            "year": "2025",
            "journal": "Composite Structures",
            "section_headings_json": '["Abstract"]',
            "word_count": 100,
            "char_count": 600,
        }
        for index in range(40)
    ]

    class FakeDocs:
        def count(self) -> int:
            return len(summaries)

        def query(self, **kwargs):
            return {
                "distances": [[0.01]],
                "documents": [["Composite vibration damping evidence."]],
                "metadatas": [[summaries[0]]],
            }

        def get(self, ids=None, limit=None, include=None):  # noqa: ANN001
            if ids is not None:
                docs = [
                    (
                        "Composite vibration damping evidence."
                        if safe_doi == "10_1000_0"
                        else f"Generic paper content for {safe_doi}."
                    )
                    for safe_doi in ids
                ]
                selected = [
                    next(item for item in summaries if item["safe_doi"] == safe_doi)
                    for safe_doi in ids
                ]
                return {"ids": ids, "metadatas": selected, "documents": docs}
            assert include == ["metadatas"]
            selected = summaries[:limit]
            return {
                "ids": [item["safe_doi"] for item in selected],
                "metadatas": selected,
            }

    class EmptyChunks:
        def count(self) -> int:
            return 0

    hydrated_counts: list[int] = []

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: FakeDocs())
    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: EmptyChunks())
    monkeypatch.setattr(
        vector,
        "_hydrate_canonical_documents",
        lambda documents, papers_dir: hydrated_counts.append(len(documents)) or documents,
    )

    results = search_papers(
        chroma_dir=Path("/tmp/chroma"),
        query="composite vibration damping",
        limit=1,
    )

    assert len(results) == 1
    assert results[0].doi == "10.1000/0"
    assert hydrated_counts == [30]


def test_search_papers_end_to_end_rereads_updated_canonical_mirror(monkeypatch, tmp_path: Path) -> None:
    import grados.storage.vector as vector

    class FakeBackend:
        provider = "test"
        model_id = "test-backend"
        query_prompt_mode = "none"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, query: str) -> list[float]:
            return [1.0, 0.0, 0.0, 0.0]

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_ensure_index_compatible", lambda *args, **kwargs: None)

    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"

    save_paper_markdown(
        doi="10.1234/demo-e2e",
        markdown=(
            "# Composite Damping Study\n\n"
            "## Abstract\n\n"
            "This study investigates laminate damping behaviour.\n\n"
            "## Results\n\n"
            "Indexed wording reports a generic improvement in vibration response.\n\n"
            "## Discussion\n\n"
            "Closing discussion paragraph.\n"
        ),
        papers_dir=papers_dir,
        title="Composite Damping Study",
        source="Crossref",
        chroma_dir=chroma_dir,
    )

    (papers_dir / "10_1234_demo_e2e.md").write_text(
        '---\n'
        'doi: "10.1234/demo-e2e"\n'
        'title: "Composite Damping Study"\n'
        'source: "Crossref"\n'
        'fetched_at: "2026-04-15T00:00:00+00:00"\n'
        'extraction_status: "OK"\n'
        "---\n\n"
        "# Composite Damping Study\n\n"
        "## Abstract\n\n"
        "This study investigates laminate damping behaviour.\n\n"
        "## Results\n\n"
        "Canonical mirror wording says attenuation rose by 18 percent after laminate treatment.\n\n"
        "## Discussion\n\n"
        "Closing discussion paragraph.\n",
        encoding="utf-8",
    )

    results = search_papers(
        chroma_dir=chroma_dir,
        papers_dir=papers_dir,
        query="laminate attenuation treatment",
        limit=5,
    )

    assert len(results) == 1
    assert isinstance(results[0], PaperSearchResult)
    assert results[0].doi == "10.1234/demo-e2e"
    assert "attenuation rose by 18 percent after laminate treatment" in results[0].snippet.lower()
    assert "indexed wording reports a generic improvement" not in results[0].snippet.lower()


def test_chunk_text_uses_section_aware_chunking_with_overlap() -> None:
    config = IndexingConfig(chunk_min_chars=40, chunk_max_chars=130, chunk_overlap_paragraphs=1)
    markdown = (
        "# Title\n\n"
        "## Abstract\n\n"
        "First abstract paragraph with several relevant details.\n\n"
        "Second abstract paragraph that should overlap with the next chunk.\n\n"
        "Third abstract paragraph closes the section.\n\n"
        "## Methods\n\n"
        "Methods paragraph describing the experiment."
    )

    chunks = _chunk_text(markdown, config, fallback_title="Demo")

    assert len(chunks) >= 2
    assert chunks[0]["section_name"] == "Abstract"
    abstract_chunks = [chunk for chunk in chunks if chunk["section_name"] == "Abstract"]
    assert len(abstract_chunks) == 2
    assert "Second abstract paragraph" in abstract_chunks[0]["text"]
    assert "Second abstract paragraph" in abstract_chunks[1]["text"]
    assert abstract_chunks[0]["section_level"] == 2
    methods_chunk = next(chunk for chunk in chunks if chunk["section_name"] == "Methods")
    assert methods_chunk["paragraph_start"] == 5
    assert methods_chunk["paragraph_count"] == 2


def test_chunk_text_splits_overlong_single_paragraph_by_sentence() -> None:
    config = IndexingConfig(chunk_min_chars=40, chunk_max_chars=190, chunk_overlap_paragraphs=1)
    markdown = (
        "# Title\n\n"
        "## Abstract\n\n"
        "Sentence one carries the first half of the finding in a deliberately long style. "
        "Sentence two keeps the same paragraph long enough to exceed the chunk budget cleanly. "
        "Sentence three should appear in an overlapping follow-up chunk for retrieval stability. "
        "Sentence four closes the paragraph with one more long clause for safety."
    )

    chunks = _chunk_text(markdown, config, fallback_title="Demo")

    assert len(chunks) >= 3
    assert chunks[0]["paragraph_count"] == 2
    assert all(chunk["paragraph_count"] == 1 for chunk in chunks[1:])
    assert "Sentence two keeps the same paragraph" in chunks[0]["text"]
    assert "Sentence two keeps the same paragraph" in chunks[1]["text"]
    assert "Sentence three should appear" in chunks[1]["text"]
    assert "Sentence three should appear" in chunks[2]["text"]


def test_index_paper_writes_real_embeddings_and_section_metadata(tmp_path: Path, monkeypatch) -> None:
    import grados.storage.vector as vector

    class FakeBackend:
        provider = "harrier"
        model_id = "microsoft/harrier-oss-v1-270m"
        query_prompt_mode = "prompt_name:web_search_query"
        embedding_dim = 4

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[float(index + 1)] * 4 for index, _ in enumerate(texts)]

    class FakeDocs:
        def __init__(self) -> None:
            self.last_upsert: dict[str, object] | None = None

        def upsert(self, **kwargs) -> None:
            self.last_upsert = kwargs

        def count(self) -> int:
            return 1

    class FakeChunks:
        def __init__(self) -> None:
            self.last_upsert: dict[str, object] | None = None

        def get(self, where=None):  # noqa: ANN001
            return {"ids": []}

        def delete(self, ids):  # noqa: ANN001
            return None

        def upsert(self, **kwargs) -> None:
            self.last_upsert = kwargs

        def count(self) -> int:
            if self.last_upsert is None:
                return 0
            return len(self.last_upsert.get("ids", []))

    fake_docs = FakeDocs()
    fake_chunks = FakeChunks()

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: fake_docs)
    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: fake_chunks)
    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())

    chunk_count = index_paper(
        chroma_dir=tmp_path / "chroma",
        doi="10.1234/demo",
        safe_doi="10_1234_demo",
        title="Demo Paper",
        markdown=(
            "## Abstract\n\n"
            "A long abstract paragraph for retrieval.\n\n"
            "## Methods\n\n"
            "A methods paragraph that becomes its own chunk.\n\n"
            "## References\n\n"
            "Smith et al. Example study. doi:10.9999/example-ref."
        ),
        indexing_config=IndexingConfig(chunk_min_chars=20, chunk_max_chars=80, chunk_overlap_paragraphs=1),
    )

    assert chunk_count == 3
    assert fake_docs.last_upsert is not None
    assert fake_docs.last_upsert["embeddings"] == [[1.0, 1.0, 1.0, 1.0]]
    assert fake_chunks.last_upsert is not None
    chunk_metadatas = fake_chunks.last_upsert["metadatas"]
    assert chunk_metadatas[0]["section_name"] == "Abstract"
    assert chunk_metadatas[1]["section_name"] == "Methods"
    assert chunk_metadatas[2]["section_name"] == "References"
    doc_metadata = fake_docs.last_upsert["metadatas"][0]
    assert doc_metadata["cites_json"] == '["10.9999/example-ref"]'
    stats = get_index_stats(tmp_path / "chroma", indexing_config=IndexingConfig())
    assert stats.index_manifest_present is True
    assert stats.embedding_model == "microsoft/harrier-oss-v1-270m"


def test_get_index_stats_reports_reindex_when_manifest_mismatches(tmp_path: Path) -> None:
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    (chroma_dir / "index-manifest.json").write_text(
        '{"schema_version": 3, "provider": "harrier", "model_id": "all-MiniLM-L6-v2", '
        '"max_length": 512, "retrieval_strategy": "two-stage-v1", '
        '"chunking_strategy": "section-aware-v2", "chunk_min_chars": 300, '
        '"chunk_max_chars": 2000, "chunk_overlap_paragraphs": 1}',
        encoding="utf-8",
    )

    stats = get_index_stats(chroma_dir, indexing_config=IndexingConfig())

    assert stats.reindex_required is True
    assert "model_id" in stats.reindex_reason


def test_query_collection_surfaces_degraded_filter_when_filters_are_unsupported() -> None:
    class FakeCollection:
        def query(self, **kwargs):  # noqa: ANN003
            if "where_document" in kwargs:
                raise TypeError("where_document unsupported")
            if "where" in kwargs:
                raise TypeError("where unsupported")
            return {
                "documents": [["demo chunk"]],
                "metadatas": [[{"safe_doi": "10_1234_demo"}]],
                "distances": [[0.1]],
            }

    result = query_collection(
        collection=FakeCollection(),
        query_embedding=[0.1, 0.2],
        n_results=3,
        where={"source": "Crossref"},
        where_document={"$contains": "demo"},
    )

    assert result["degraded_filter"] is True
    assert result["warnings"] == [
        "Chroma query() does not support where_document; retried without document filter.",
        "Chroma query() does not support where filter; retried without metadata filter.",
    ]
    assert result["documents"] == [["demo chunk"]]


def test_collection_get_surfaces_degraded_filter_when_projection_is_unsupported() -> None:
    class FakeCollection:
        def get(self, **kwargs):  # noqa: ANN003
            if "include" in kwargs:
                raise TypeError("include unsupported")
            return {"ids": ["10_1234_demo"], "documents": ["demo"]}

    result = collection_get(
        collection=FakeCollection(),
        ids=["10_1234_demo"],
        include=["documents"],
    )

    assert result["degraded_filter"] is True
    assert result["warnings"] == ["Chroma get() does not support include projection; retried without include."]
    assert result["ids"] == ["10_1234_demo"]


def test_delete_paper_chunks_logs_failures(caplog) -> None:
    class FakeCollection:
        def get(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("db unavailable")

    with caplog.at_level(logging.ERROR):
        delete_paper_chunks(FakeCollection(), "10_1234_demo")

    assert "Failed to delete Chroma chunks for 10_1234_demo" in caplog.text


def test_get_index_stats_logs_collection_failures(tmp_path: Path, monkeypatch, caplog) -> None:
    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: (_ for _ in ()).throw(RuntimeError("db unavailable")))

    with caplog.at_level(logging.ERROR):
        stats = get_index_stats(tmp_path / "chroma", indexing_config=IndexingConfig())

    assert stats.total_chunks == 0
    assert stats.unique_papers == 0
    assert "Failed to inspect Chroma index stats" in caplog.text


def test_load_embedding_backend_reuses_process_cache_for_same_key(tmp_path: Path, monkeypatch) -> None:
    class FakeSentenceTransformer:
        init_count = 0
        encode_calls: list[dict[str, object]] = []

        def __init__(self, model_id: str, **kwargs) -> None:
            type(self).init_count += 1
            self.model_id = model_id
            self.max_seq_length = 0
            self.device = SimpleNamespace(type="mps")

        def encode(self, texts: list[str], **kwargs) -> list[list[float]]:
            type(self).encode_calls.append(dict(kwargs))
            return [[1.0, 2.0, 3.0] for _ in texts]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    clear_embedding_backend_cache()
    paths = GRaDOSPaths(tmp_path / "grados-home")
    config = IndexingConfig()

    first = load_embedding_backend(config=config, paths=paths)
    second = load_embedding_backend(config=config.model_copy(deep=True), paths=paths)

    first.embed_documents(["doc"])
    second.embed_query("query")

    assert first is second
    assert FakeSentenceTransformer.init_count == 1
    assert FakeSentenceTransformer.encode_calls[0]["batch_size"] == 2
    assert FakeSentenceTransformer.encode_calls[1]["batch_size"] == 1
    assert first._load_model().max_seq_length == 4096

    clear_embedding_backend_cache()


def test_load_embedding_backend_uses_batch_size_1_for_harrier_0_6b_on_mps(tmp_path: Path, monkeypatch) -> None:
    class FakeSentenceTransformer:
        encode_calls: list[dict[str, object]] = []

        def __init__(self, model_id: str, **kwargs) -> None:
            self.model_id = model_id
            self.max_seq_length = 0
            self.device = SimpleNamespace(type="mps")

        def encode(self, texts: list[str], **kwargs) -> list[list[float]]:
            type(self).encode_calls.append(dict(kwargs))
            return [[1.0, 2.0, 3.0] for _ in texts]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    clear_embedding_backend_cache()
    try:
        backend = load_embedding_backend(
            config=IndexingConfig(model_id="microsoft/harrier-oss-v1-0.6b"),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
        )

        backend.embed_documents(["doc one", "doc two"])

        assert FakeSentenceTransformer.encode_calls[0]["batch_size"] == 1
    finally:
        clear_embedding_backend_cache()


def test_embed_documents_surfaces_oom_diagnostics(tmp_path: Path, monkeypatch) -> None:
    class FakeSentenceTransformer:
        def __init__(self, model_id: str, **kwargs) -> None:
            self.model_id = model_id
            self.max_seq_length = 0
            self.device = SimpleNamespace(type="mps")

        def encode(self, texts: list[str], **kwargs) -> list[list[float]]:
            raise MemoryError("Unable to allocate 52.38 GiB")

        def tokenize(self, texts: list[str]) -> dict[str, list[list[int]]]:
            token_count = min(len(texts[0].split()) + 2, 128)
            return {"input_ids": [[0] * token_count]}

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    clear_embedding_backend_cache()
    try:
        backend = load_embedding_backend(
            config=IndexingConfig(model_id="microsoft/harrier-oss-v1-0.6b"),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
        )

        with pytest.raises(RuntimeError, match="Embedding encode ran out of memory") as excinfo:
            backend.embed_documents(
                [
                    "This is a deliberately long text batch entry for diagnostics.",
                    "Another text makes sure the batch summary reports the count.",
                ]
            )
        assert "batch_size=1" in str(excinfo.value)
        assert "max_length=4096" in str(excinfo.value)
        assert "texts=2" in str(excinfo.value)
    finally:
        clear_embedding_backend_cache()


def test_load_embedding_backend_invalidates_when_backend_key_changes(tmp_path: Path) -> None:
    clear_embedding_backend_cache()
    try:
        paths = GRaDOSPaths(tmp_path / "grados-home")
        base = load_embedding_backend(
            config=IndexingConfig(),
            paths=paths,
        )
        different_model = load_embedding_backend(
            config=IndexingConfig(model_id="demo/model"),
            paths=paths,
        )
        different_cache_dir = load_embedding_backend(
            config=IndexingConfig(cache_dir=str(tmp_path / "alt-embedding-cache")),
            paths=paths,
        )

        assert base is not different_model
        assert base is not different_cache_dir
    finally:
        clear_embedding_backend_cache()


def test_index_and_search_share_cached_embedding_backend(tmp_path: Path, monkeypatch) -> None:
    import grados.storage.vector as vector

    class FakeSentenceTransformer:
        init_count = 0

        def __init__(self, model_id: str, **kwargs) -> None:
            type(self).init_count += 1
            self.max_seq_length = 0

        def encode(self, texts: list[str], **kwargs) -> list[list[float]]:
            return [[float(index + 1)] * 4 for index, _ in enumerate(texts)]

        def get_sentence_embedding_dimension(self) -> int:
            return 4

    class FakeDocs:
        def __init__(self) -> None:
            self.last_upsert: dict[str, object] | None = None

        def upsert(self, **kwargs) -> None:
            self.last_upsert = kwargs

        def count(self) -> int:
            return 1

        def query(self, **kwargs) -> dict[str, object]:
            return {
                "distances": [[0.05]],
                "documents": [["Composite vibration damping evidence."]],
                "metadatas": [[{"safe_doi": "10_1234_demo", "doi": "10.1234/demo", "title": "Demo Paper"}]],
            }

    class FakeChunks:
        def __init__(self) -> None:
            self.last_upsert: dict[str, object] | None = None

        def get(self, where=None):  # noqa: ANN001
            return {"ids": []}

        def delete(self, ids):  # noqa: ANN001
            return None

        def upsert(self, **kwargs) -> None:
            self.last_upsert = kwargs

        def count(self) -> int:
            return 1 if self.last_upsert is not None else 0

        def query(self, **kwargs) -> dict[str, object]:
            return {
                "distances": [[0.1]],
                "documents": [["Composite vibration damping evidence."]],
                "metadatas": [[
                    {
                        "safe_doi": "10_1234_demo",
                        "paragraph_start": 0,
                        "paragraph_count": 2,
                        "section_name": "Abstract",
                        "section_level": 2,
                    }
                ]],
            }

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    fake_docs = FakeDocs()
    fake_chunks = FakeChunks()
    monkeypatch.setattr(vector, "_get_client", lambda chroma_dir: object())
    monkeypatch.setattr(vector, "_get_docs_collection", lambda client: fake_docs)
    monkeypatch.setattr(vector, "_get_chunks_collection", lambda client: fake_chunks)
    monkeypatch.setattr(vector, "_hydrate_canonical_documents", lambda documents, papers_dir: documents)
    monkeypatch.setattr(
        vector,
        "list_paper_documents",
        lambda chroma_dir: [
            {
                "doi": "10.1234/demo",
                "safe_doi": "10_1234_demo",
                "title": "Demo Paper",
                "source": "Crossref",
                "fetch_outcome": "native_full_text",
                "authors": ["Alice Smith"],
                "year": "2025",
                "journal": "Composite Structures",
                "section_headings": ["Abstract"],
                "word_count": 20,
                "char_count": 120,
                "uri": "grados://papers/10_1234_demo",
                "content_markdown": "Composite vibration damping evidence.",
            }
        ],
    )

    clear_embedding_backend_cache()
    try:
        index_paper(
            chroma_dir=tmp_path / "chroma",
            doi="10.1234/demo",
            safe_doi="10_1234_demo",
            title="Demo Paper",
            markdown="## Abstract\n\nComposite vibration damping evidence.",
            indexing_config=IndexingConfig(),
        )

        results = search_papers(
            chroma_dir=tmp_path / "chroma",
            papers_dir=tmp_path / "papers",
            query="composite vibration damping",
            limit=5,
            indexing_config=IndexingConfig(),
        )

        assert len(results) == 1
        assert FakeSentenceTransformer.init_count == 1
    finally:
        clear_embedding_backend_cache()
