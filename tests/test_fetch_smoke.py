from __future__ import annotations

import asyncio

from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.extract.fetch import FetchResult, _fetch_tdm, build_fetch_strategies, build_tdm_providers
from grados.publisher.common import PublisherMetadata
from grados.publisher.elsevier import ElsevierFetchResult, ElsevierMetadataSignal, _extract_elsevier_markdown_from_xml
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
    assert result.via == "api"
    assert result.state == "ok"
    assert result.asset_hints == [{"kind": "article_pdf", "url": "https://example.com/paper.pdf"}]


def test_fetch_result_defaults_include_asset_hints() -> None:
    result = FetchResult()

    assert result.asset_hints == []
    assert result.via == ""
    assert result.state == ""


def test_fetch_strategy_builders_preserve_order_and_filter_unknown_names() -> None:
    strategies = build_fetch_strategies(["OA", "Unknown", "Headless"])
    providers = build_tdm_providers(["Springer", "Missing", "Elsevier"])

    assert [strategy.name for strategy in strategies] == ["oa", "browser"]
    assert [provider.name for provider in providers] == ["Springer", "Elsevier"]


def test_fetch_strategy_builders_default_to_browser_first_order() -> None:
    strategies = build_fetch_strategies()

    assert [strategy.name for strategy in strategies] == ["api", "browser", "oa", "scihub"]


def test_fetch_tdm_returns_metadata_only_when_no_full_text_available(monkeypatch) -> None:
    async def fake_elsevier(doi, key, client):
        return ElsevierFetchResult(
            metadata=ElsevierMetadataSignal(
                doi=doi,
                title="Metadata Only Paper",
                authors=["Alice Smith"],
                year="2026",
                journal="Fallback Journal",
                pii="S123456789000001",
                scidir_url="https://www.sciencedirect.com/science/article/pii/S123456789000001",
            ),
            outcome="metadata_only",
            asset_hints=[{"kind": "article_landing", "url": "https://example.com/article"}],
        )

    async def fake_springer(doi, meta_key, oa_key, client):
        return SpringerFetchResult(outcome="failed")

    monkeypatch.setattr("grados.extract.fetch.fetch_elsevier_article", fake_elsevier)
    monkeypatch.setattr("grados.extract.fetch.fetch_springer_article", fake_springer)

    result = asyncio.run(
        _fetch_tdm(
            doi="10.1234/demo",
            api_keys={
                "ELSEVIER_API_KEY": "elsevier-key",
                "SPRINGER_meta_API_KEY": "springer-key",
            },
            client=object(),  # type: ignore[arg-type]
            tdm_order=["Elsevier", "Springer"],
        )
    )

    assert result.outcome == "metadata_only"
    assert result.source == "Elsevier TDM"
    assert result.via == "api"
    assert result.state == "partial"
    assert result.metadata is not None
    assert result.metadata.title == "Metadata Only Paper"
    assert result.metadata.pii == "S123456789000001"
    assert result.asset_hints == [{"kind": "article_landing", "url": "https://example.com/article"}]


def test_fetch_paper_preserves_metadata_only_after_later_failures(monkeypatch) -> None:
    import grados.extract.fetch as fetch_module

    async def fake_fetch_tdm(*args, **kwargs):
        return FetchResult(
            outcome="metadata_only",
            source="Elsevier TDM",
            via="api",
            state="partial",
            metadata=PublisherMetadata(
                doi="10.1234/demo",
                title="Metadata Only Paper",
                authors=["Alice Smith"],
                year="2026",
                journal="Fallback Journal",
            ),
            asset_hints=[{"kind": "article_landing", "url": "https://example.com/article"}],
        )

    async def fake_fetch_oa(*args, **kwargs):
        return FetchResult(outcome="failed", via="oa", state="error", warnings=["OA lookup failed"])

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_oa", fake_fetch_oa)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={"ELSEVIER_API_KEY": "elsevier-key"},
            etiquette_email="test@example.com",
            fetch_order=["api", "oa"],
        )
    )

    assert result.outcome == "metadata_only"
    assert result.source == "Elsevier TDM"
    assert result.via == "api"
    assert result.state == "partial"
    assert result.metadata is not None
    assert result.metadata.title == "Metadata Only Paper"
    assert result.warnings == ["OA lookup failed"]


def test_fetch_paper_preserves_browser_challenge_in_final_result(monkeypatch, tmp_path) -> None:
    import grados.extract.fetch as fetch_module
    from grados.browser.generic import BrowserFetchResult

    async def fake_fetch_tdm(*args, **kwargs):
        return FetchResult(outcome="failed", via="api", state="error", warnings=["api miss"])

    async def fake_fetch_oa(*args, **kwargs):
        return FetchResult(outcome="failed", via="oa", state="error", warnings=["oa miss"])

    async def fake_fetch_with_browser(doi, config, paths, resume=None):  # noqa: ANN001
        _ = (doi, config, paths, resume)
        return BrowserFetchResult(
            source="Headless Browser",
            outcome="publisher_challenge",
            state="challenge",
            manual=True,
            host="www.sciencedirect.com",
            resume={
                "kind": "browser_profile",
                "doi": "10.1234/demo",
                "host": "www.sciencedirect.com",
                "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
                "profile_dir": str(paths.browser_profile),
                "action": "complete_publisher_verification_then_retry",
            },
            warnings=["Browser automation: publisher_challenge"],
        )

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_oa", fake_fetch_oa)
    monkeypatch.setattr("grados.browser.generic.fetch_with_browser", fake_fetch_with_browser)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            fetch_order=["api", "browser", "oa"],
            headless_config=HeadlessBrowserConfig(),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
        )
    )

    assert result.outcome == "publisher_challenge"
    assert result.via == "browser"
    assert result.state == "challenge"
    assert result.manual is True
    assert result.host == "www.sciencedirect.com"
    assert result.resume["kind"] == "browser_profile"
    assert result.resume["profile_dir"].endswith("browser/profile")
    assert result.warnings == ["api miss", "Browser automation: publisher_challenge", "oa miss"]


def test_fetch_paper_stops_after_browser_success(monkeypatch, tmp_path) -> None:
    import grados.extract.fetch as fetch_module
    from grados.browser.generic import BrowserFetchResult

    calls: list[str] = []

    async def fake_fetch_tdm(*args, **kwargs):
        calls.append("api")
        return FetchResult(outcome="metadata_only", via="api", state="partial")

    async def fake_fetch_oa(*args, **kwargs):
        calls.append("oa")
        return FetchResult(outcome="failed", via="oa", state="error", warnings=["oa miss"])

    async def fake_fetch_scihub(*args, **kwargs):
        calls.append("scihub")
        return FetchResult(outcome="failed", via="scihub", state="error")

    async def fake_fetch_with_browser(doi, config, paths, resume=None):  # noqa: ANN001
        _ = (doi, config, paths, resume)
        calls.append("browser")
        return BrowserFetchResult(
            pdf_buffer=b"%PDF-1.4\n%stub",
            source="Headless Browser",
            outcome="pdf_obtained",
            state="ok",
        )

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_oa", fake_fetch_oa)
    monkeypatch.setattr(fetch_module, "_fetch_scihub", fake_fetch_scihub)
    monkeypatch.setattr("grados.browser.generic.fetch_with_browser", fake_fetch_with_browser)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            headless_config=HeadlessBrowserConfig(),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
        )
    )

    assert calls == ["api", "browser"]
    assert result.outcome == "pdf_obtained"
    assert result.via == "browser"
    assert result.state == "ok"


def test_fetch_paper_resume_starts_at_browser_and_passes_resume(monkeypatch, tmp_path) -> None:
    import grados.extract.fetch as fetch_module
    from grados.browser.generic import BrowserFetchResult

    calls: list[str] = []
    resume = {
        "kind": "browser_profile",
        "doi": "10.1234/demo",
        "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
        "host": "www.sciencedirect.com",
    }

    async def fake_fetch_tdm(*args, **kwargs):
        _ = (args, kwargs)
        raise AssertionError("resume should not rerun api")

    async def fake_fetch_with_browser(doi, config, paths, resume=None):  # noqa: ANN001
        _ = (doi, config, paths)
        calls.append("browser")
        assert resume == {
            "kind": "browser_profile",
            "doi": "10.1234/demo",
            "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
            "host": "www.sciencedirect.com",
        }
        return BrowserFetchResult(
            pdf_buffer=b"%PDF-1.4\n%stub",
            source="Headless Browser",
            outcome="pdf_obtained",
            state="ok",
        )

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr("grados.browser.generic.fetch_with_browser", fake_fetch_with_browser)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            headless_config=HeadlessBrowserConfig(),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
            browser_resume=resume,
        )
    )

    assert calls == ["browser"]
    assert result.outcome == "pdf_obtained"
    assert result.via == "browser"
    assert result.trace[0]["via"] == "browser"
    assert result.trace[0]["state"] == "ok"


def test_fetch_oa_surfaces_warning_when_pdf_download_fails() -> None:
    from grados.extract.fetch import _fetch_oa

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "oa_locations": [
                    {
                        "host_type": "repository",
                        "url_for_pdf": "https://example.com/demo.pdf",
                    }
                ]
            }

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            if "unpaywall" in url:
                return FakeResponse()
            raise RuntimeError("network down")

    result = asyncio.run(_fetch_oa("10.1234/demo", "test@example.com", FakeClient()))  # type: ignore[arg-type]

    assert result.outcome == "failed"
    assert result.via == "oa"
    assert result.state == "error"
    assert result.warnings == ["OA PDF fetch failed: RuntimeError: network down"]


def test_fetch_scihub_surfaces_warning_when_pdf_link_missing() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "<html><body>no pdf here</body></html>"
            self.headers: dict[str, str] = {}
            self.content = self.text.encode("utf-8")

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            return FakeResponse()

    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            FakeClient(),  # type: ignore[arg-type]
            {"fallback_mirror": "https://sci-hub.se"},
        )
    )

    assert result.outcome == "failed"
    assert result.via == "scihub"
    assert result.state == "error"
    assert result.warnings == ["Sci-Hub lookup failed: no PDF link found"]


def test_fetch_scihub_ignores_legacy_fallback_mirror_key() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "<html><body>no pdf here</body></html>"
            self.headers: dict[str, str] = {}
            self.content = self.text.encode("utf-8")

    class FakeClient:
        def __init__(self) -> None:
            self.last_url = ""

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.last_url = url
            return FakeResponse()

    client = FakeClient()
    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            client,  # type: ignore[arg-type]
            {"fallbackMirror": "https://legacy.example"},
        )
    )

    assert result.outcome == "failed"
    assert result.via == "scihub"
    assert result.state == "error"
    assert client.last_url == "https://sci-hub.se/10.1234/demo"


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
