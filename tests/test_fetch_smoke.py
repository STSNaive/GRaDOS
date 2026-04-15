from __future__ import annotations

import asyncio

from grados.extract.fetch import FetchResult, _fetch_tdm, build_fetch_strategies, build_tdm_providers
from grados.publisher.elsevier import ElsevierFetchResult, _extract_elsevier_markdown_from_xml
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


def test_fetch_strategy_builders_preserve_order_and_filter_unknown_names() -> None:
    strategies = build_fetch_strategies(["OA", "Unknown", "Headless"])
    providers = build_tdm_providers(["Springer", "Missing", "Elsevier"])

    assert [strategy.name for strategy in strategies] == ["OA", "Headless"]
    assert [provider.name for provider in providers] == ["Springer", "Elsevier"]


def test_elsevier_xml_is_parsed_deterministically_into_markdown() -> None:
    xml_payload = """<?xml version="1.0" encoding="UTF-8"?>
<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd"
    xmlns:ce="http://www.elsevier.com/xml/common/dtd"
    xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <coredata>
    <prism:doi>10.1016/j.demo.2026.01.001</prism:doi>
    <pii>S000000000000001</pii>
    <eid>1-s2.0-demo</eid>
    <dc:title>Deterministic Elsevier Parsing Demo</dc:title>
    <dc:description>Short abstract for deterministic parsing.</dc:description>
    <prism:publicationName>Demo Journal</prism:publicationName>
    <prism:coverDate>2026-01-01</prism:coverDate>
    <openaccess>1</openaccess>
  </coredata>
  <originalText>flattened text that should not be used</originalText>
  <doc>
    <ce:author-group>
      <ce:author><ce:given-name>Alice</ce:given-name><ce:surname>Smith</ce:surname></ce:author>
      <ce:author><ce:given-name>Bob</ce:given-name><ce:surname>Lee</ce:surname></ce:author>
    </ce:author-group>
    <ce:keywords>
      <ce:keyword>Machine learning</ce:keyword>
      <ce:keyword>Policy</ce:keyword>
    </ce:keywords>
    <ce:sections>
      <ce:section>
        <ce:section-title>Introduction</ce:section-title>
        <ce:para>Intro paragraph.</ce:para>
      </ce:section>
      <ce:section>
        <ce:section-title>Methods</ce:section-title>
        <ce:para>Methods paragraph.</ce:para>
      </ce:section>
    </ce:sections>
    <ce:bibliography-sec>
      <ce:bib-reference>Smith et al. Example reference.</ce:bib-reference>
    </ce:bibliography-sec>
  </doc>
</full-text-retrieval-response>
"""

    markdown, metadata = _extract_elsevier_markdown_from_xml(
        xml_payload,
        fallback_doi="10.1016/j.demo.2026.01.001",
    )

    assert metadata is not None
    assert metadata.doi == "10.1016/j.demo.2026.01.001"
    assert metadata.title == "Deterministic Elsevier Parsing Demo"
    assert metadata.authors == ["Alice Smith", "Bob Lee"]
    assert markdown.startswith("# Deterministic Elsevier Parsing Demo")
    assert "## Abstract" in markdown
    assert "## Keywords" in markdown
    assert "## Introduction" in markdown
    assert "## Methods" in markdown
    assert "## References" in markdown
    assert "Intro paragraph." in markdown
    assert "Smith et al. Example reference." in markdown
