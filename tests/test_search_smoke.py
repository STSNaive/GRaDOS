from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from grados.config import SearchConfig
from grados.search.academic import CrossrefState, PaperMetadata, PubMedState, SearchPageResult
from grados.search.resumable import ContinuationData, decode_token, encode_token, run_resumable_search


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
