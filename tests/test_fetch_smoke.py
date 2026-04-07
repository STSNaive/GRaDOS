from __future__ import annotations

import asyncio

from grados.extract.fetch import FetchResult, _fetch_tdm
from grados.publisher.elsevier import ElsevierFetchResult
from grados.publisher.springer import SpringerFetchResult


def test_fetch_tdm_respects_order_and_enabled_publishers(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_elsevier(doi, key, client):
        calls.append("Elsevier")
        return ElsevierFetchResult(
            text="ignored",
            outcome="native_full_text",
            asset_hints=[{"kind": "object_api_meta", "url": "https://example.com/object"}],
        )

    async def fake_springer(doi, meta_key, oa_key, client):
        calls.append("Springer")
        return SpringerFetchResult(
            text="springer full text",
            outcome="native_full_text",
            asset_hints=[{"kind": "article_pdf", "url": "https://example.com/paper.pdf"}],
        )

    monkeypatch.setattr("grados.extract.fetch.fetch_elsevier_article", fake_elsevier)
    monkeypatch.setattr("grados.extract.fetch.fetch_springer_article", fake_springer)

    result = asyncio.run(
        _fetch_tdm(
            doi="10.1234/demo",
            api_keys={
                "ELSEVIER_API_KEY": "elsevier-key",
                "SPRINGER_meta_API_KEY": "springer-key",
                "SPRINGER_OA_API_KEY": "springer-oa-key",
            },
            client=object(),  # type: ignore[arg-type]
            tdm_order=["Elsevier", "Springer"],
            tdm_enabled={"Elsevier": False, "Springer": True},
        )
    )

    assert calls == ["Springer"]
    assert result.outcome == "native_full_text"
    assert result.source == "Springer TDM"
    assert result.asset_hints == [{"kind": "article_pdf", "url": "https://example.com/paper.pdf"}]


def test_fetch_result_defaults_include_asset_hints() -> None:
    result = FetchResult()

    assert result.asset_hints == []
