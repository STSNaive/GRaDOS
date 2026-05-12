from __future__ import annotations

import asyncio

import httpx
import pytest
from defusedxml.common import EntitiesForbidden

from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.extract.fetch import (
    FetchResult,
    UnpaywallResolution,
    _fetch_tdm,
    _resolve_unpaywall_locations,
    build_fetch_strategies,
    build_tdm_providers,
)
from grados.publisher.common import PublisherMetadata
from grados.publisher.elsevier import ElsevierFetchResult, ElsevierMetadataSignal, _extract_elsevier_markdown_from_xml
from grados.publisher.springer import SpringerFetchResult


def test_fetch_tdm_respects_order_and_enabled_publishers(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_elsevier(doi, key, client, **kwargs):
        _ = kwargs
        calls.append("Elsevier")
        return ElsevierFetchResult(
            text="ignored",
            outcome="native_full_text",
            asset_hints=[{"kind": "object_api_meta", "url": "https://example.com/object"}],
        )

    async def fake_springer(doi, meta_key, oa_key, client, **kwargs):
        _ = kwargs
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
    strategies = build_fetch_strategies(["OA", "Unknown", "codex", "Headless"])
    providers = build_tdm_providers(["Springer", "Missing", "Elsevier"])

    assert [strategy.name for strategy in strategies] == ["codex", "browser"]
    assert [provider.name for provider in providers] == ["Springer", "Elsevier"]


def test_fetch_strategy_builders_default_to_config_order() -> None:
    strategies = build_fetch_strategies()

    assert [strategy.name for strategy in strategies] == ["api", "browser", "codex", "scihub"]


def test_fetch_tdm_returns_metadata_only_when_no_full_text_available(monkeypatch) -> None:
    async def fake_elsevier(doi, key, client, **kwargs):
        _ = kwargs
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

    async def fake_springer(doi, meta_key, oa_key, client, **kwargs):
        _ = kwargs
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

    async def fake_fetch_scihub(*args, **kwargs):
        return FetchResult(outcome="failed", via="scihub", state="error", warnings=["Sci-Hub lookup failed"])

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_scihub", fake_fetch_scihub)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={"ELSEVIER_API_KEY": "elsevier-key"},
            etiquette_email="test@example.com",
            fetch_order=["api", "scihub"],
        )
    )

    assert result.outcome == "metadata_only"
    assert result.source == "Elsevier TDM"
    assert result.via == "api"
    assert result.state == "partial"
    assert result.metadata is not None
    assert result.metadata.title == "Metadata Only Paper"
    assert result.warnings == ["Sci-Hub lookup failed"]


def test_fetch_paper_preserves_browser_challenge_in_final_result(monkeypatch, tmp_path) -> None:
    import grados.extract.fetch as fetch_module
    from grados.browser.generic import BrowserFetchResult

    async def fake_fetch_tdm(*args, **kwargs):
        return FetchResult(outcome="failed", via="api", state="error", warnings=["api miss"])

    async def fake_fetch_with_browser(doi, config, paths, resume=None, target_url="", **kwargs):  # noqa: ANN001
        _ = (doi, config, paths, resume, target_url)
        return BrowserFetchResult(
            source="Browser",
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
    monkeypatch.setattr("grados.browser.generic.fetch_with_browser", fake_fetch_with_browser)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            fetch_order=["api", "browser"],
            headless_config=HeadlessBrowserConfig(),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
            unpaywall_enabled=False,
        )
    )

    assert result.outcome == "publisher_challenge"
    assert result.via == "browser"
    assert result.state == "challenge"
    assert result.manual is True
    assert result.host == "www.sciencedirect.com"
    assert result.resume["kind"] == "browser_profile"
    assert result.resume["profile_dir"].endswith("browser/profile")
    assert result.warnings == ["api miss", "Browser automation: publisher_challenge"]


def test_fetch_paper_stops_after_browser_success(monkeypatch, tmp_path) -> None:
    import grados.extract.fetch as fetch_module
    from grados.browser.generic import BrowserFetchResult

    calls: list[str] = []

    async def fake_fetch_tdm(*args, **kwargs):
        calls.append("api")
        return FetchResult(outcome="metadata_only", via="api", state="partial")

    async def fake_fetch_scihub(*args, **kwargs):
        calls.append("scihub")
        return FetchResult(outcome="failed", via="scihub", state="error")

    async def fake_fetch_with_browser(doi, config, paths, resume=None, target_url="", **kwargs):  # noqa: ANN001
        _ = (doi, config, paths, resume, target_url)
        calls.append("browser")
        return BrowserFetchResult(
            pdf_buffer=b"%PDF-1.4\n%stub",
            source="Browser",
            outcome="pdf_obtained",
            state="ok",
        )

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_scihub", fake_fetch_scihub)
    monkeypatch.setattr("grados.browser.generic.fetch_with_browser", fake_fetch_with_browser)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            headless_config=HeadlessBrowserConfig(),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
            unpaywall_enabled=False,
        )
    )

    assert calls == ["api", "browser"]
    assert result.outcome == "pdf_obtained"
    assert result.via == "browser"
    assert result.state == "ok"


def test_fetch_paper_ignores_removed_oa_strategy_after_scihub_not_found(monkeypatch) -> None:
    import grados.extract.fetch as fetch_module

    calls: list[str] = []

    async def fake_fetch_scihub(*args, **kwargs):
        calls.append("scihub")
        return FetchResult(
            outcome="failed",
            source="Sci-Hub",
            via="scihub",
            state="not_found",
            warnings=["Sci-Hub primary endpoint sci-hub.se reports no paper for DOI"],
        )

    monkeypatch.setattr(fetch_module, "_fetch_scihub", fake_fetch_scihub)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            fetch_order=["scihub", "oa"],
            fetch_enabled={"scihub": True, "oa": True},
        )
    )

    assert calls == ["scihub"]
    assert result.outcome == "failed"
    assert result.via == "scihub"
    assert result.warnings == ["Sci-Hub primary endpoint sci-hub.se reports no paper for DOI"]


def test_fetch_paper_defaults_keep_codex_disabled(monkeypatch) -> None:
    import grados.extract.fetch as fetch_module

    calls: list[str] = []

    async def fake_fetch_tdm(*args, **kwargs):
        calls.append("api")
        return FetchResult(outcome="failed", via="api", state="error", warnings=["api miss"])

    async def fake_fetch_scihub(*args, **kwargs):
        calls.append("scihub")
        return FetchResult(outcome="failed", via="scihub", state="not_found", warnings=["scihub miss"])

    async def fake_resolve(*args, **kwargs):
        raise AssertionError("unconfigured browser and disabled codex should not trigger Unpaywall")

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_scihub", fake_fetch_scihub)
    monkeypatch.setattr(fetch_module, "_resolve_unpaywall_locations", fake_resolve)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
        )
    )

    assert calls == ["api", "scihub"]
    assert result.via == "scihub"
    assert not any(trace["via"] == "codex" for trace in result.trace)


def test_fetch_paper_returns_chrome_extension_action_at_configured_position(monkeypatch) -> None:
    import grados.extract.fetch as fetch_module

    calls: list[str] = []

    async def fake_fetch_tdm(*args, **kwargs):
        calls.append("api")
        return FetchResult(outcome="failed", via="api", state="error", warnings=["api miss"])

    async def fake_fetch_scihub(*args, **kwargs):
        calls.append("scihub")
        raise AssertionError("codex should stop before later strategies")

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_fetch_scihub", fake_fetch_scihub)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            fetch_order=["api", "codex", "scihub"],
            fetch_enabled={"api": True, "codex": True, "scihub": True},
            unpaywall_enabled=False,
        )
    )

    assert calls == ["api"]
    assert result.outcome == "host_action_required"
    assert result.via == "codex"
    assert result.state == "host_action_required"
    assert result.manual is True
    assert result.source == "Codex Chrome Extension"
    assert result.host == "Google Chrome"
    assert result.resume["kind"] == "codex"
    assert result.resume["browser"] == "Google Chrome"
    assert result.resume["start_url"] == "https://doi.org/10.1234/demo"
    assert result.resume["start_url_source"] == "doi"
    assert result.resume["issued_at"]
    assert result.resume["download_watch_dir"].endswith("/Downloads")
    assert result.resume["download_max_age_seconds"] == "900"
    assert result.resume["action"] == "download_pdf_with_chrome_extension_then_call_ingest_codex_downloaded_pdf"
    assert result.resume["next_action"] == "download_with_chrome_extension_then_call_ingest_codex_downloaded_pdf"
    assert result.resume["fallback_action"] == "call_parse_pdf_file_with_known_pdf_path"
    assert result.resume["documentation_url"] == "https://developers.openai.com/codex/app/chrome-extension"
    assert result.warnings[0] == "api miss"
    assert any(trace["via"] == "codex" for trace in result.trace)


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

    async def fake_fetch_with_browser(doi, config, paths, resume=None, target_url="", **kwargs):  # noqa: ANN001
        _ = (doi, config, paths, target_url)
        calls.append("browser")
        assert resume == {
            "kind": "browser_profile",
            "doi": "10.1234/demo",
            "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
            "host": "www.sciencedirect.com",
        }
        return BrowserFetchResult(
            pdf_buffer=b"%PDF-1.4\n%stub",
            source="Browser",
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


def test_unpaywall_resolver_prefers_best_pdf_url() -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "best_oa_location": {
                    "host_type": "repository",
                    "url_for_pdf": "https://example.com/best.pdf",
                    "url_for_landing_page": "https://example.com/best",
                },
                "oa_locations": [
                    {
                        "host_type": "publisher",
                        "url_for_pdf": "https://example.com/other.pdf",
                    }
                ]
            }

    class FakeClient:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.calls.append(url)
            if "unpaywall" in url:
                return FakeResponse()
            raise RuntimeError("network down")

    client = FakeClient()
    result = asyncio.run(
        _resolve_unpaywall_locations("10.1234/demo", "test@example.com", client)  # type: ignore[arg-type]
    )

    assert client.calls == ["https://api.unpaywall.org/v2/10.1234/demo"]
    assert result.selected_url == "https://example.com/best.pdf"
    assert result.selected_url_source == "unpaywall.best_oa_location.url_for_pdf"
    assert result.best_oa_location["url_for_pdf"] == "https://example.com/best.pdf"
    assert result.oa_locations[0]["url_for_pdf"] == "https://example.com/other.pdf"


def test_unpaywall_resolver_falls_back_to_landing_page() -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "best_oa_location": {
                    "host_type": "repository",
                    "url_for_landing_page": "https://example.com/article",
                },
                "oa_locations": [],
            }

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            _ = (url, kwargs)
            return FakeResponse()

    result = asyncio.run(
        _resolve_unpaywall_locations("10.1234/demo", "test@example.com", FakeClient())  # type: ignore[arg-type]
    )

    assert result.selected_url == "https://example.com/article"
    assert result.selected_url_source == "unpaywall.best_oa_location.url_for_landing_page"


def test_unpaywall_resolver_rejects_oversized_response() -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-length": "2048"}
        content = b"{}"

        @staticmethod
        def json() -> dict[str, object]:
            raise AssertionError("oversized Unpaywall payload should not be parsed")

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            _ = (url, kwargs)
            return FakeResponse()

    result = asyncio.run(
        _resolve_unpaywall_locations(
            "10.1234/demo",
            "test@example.com",
            FakeClient(),  # type: ignore[arg-type]
            max_remote_text_bytes=8,
        )
    )

    assert result.selected_url == ""
    assert any("Unpaywall response exceeds configured size limit" in warning for warning in result.warnings)


def test_fetch_paper_does_not_resolve_unpaywall_for_api_only(monkeypatch) -> None:
    import grados.extract.fetch as fetch_module

    async def fake_fetch_tdm(*args, **kwargs):
        return FetchResult(
            text="full text",
            outcome="native_full_text",
            source="Elsevier TDM",
            via="api",
            state="ok",
        )

    async def fake_resolve(*args, **kwargs):
        raise AssertionError("api path should not trigger Unpaywall resolution")

    monkeypatch.setattr(fetch_module, "_fetch_tdm", fake_fetch_tdm)
    monkeypatch.setattr(fetch_module, "_resolve_unpaywall_locations", fake_resolve)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={"ELSEVIER_API_KEY": "elsevier-key"},
            etiquette_email="test@example.com",
            fetch_order=["api"],
        )
    )

    assert result.outcome == "native_full_text"
    assert result.via == "api"


def test_fetch_paper_codex_uses_unpaywall_start_url(monkeypatch) -> None:
    import grados.extract.fetch as fetch_module

    async def fake_resolve(*args, **kwargs):
        _ = (args, kwargs)
        return UnpaywallResolution(
            best_oa_location={"url_for_pdf": "https://example.com/best.pdf"},
            selected_url="https://example.com/best.pdf",
            selected_url_source="unpaywall.best_oa_location.url_for_pdf",
        )

    monkeypatch.setattr(fetch_module, "_resolve_unpaywall_locations", fake_resolve)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            fetch_order=["codex"],
            fetch_enabled={"codex": True},
        )
    )

    assert result.via == "codex"
    assert result.resume["start_url"] == "https://example.com/best.pdf"
    assert result.resume["start_url_source"] == "unpaywall.best_oa_location.url_for_pdf"


def test_fetch_paper_browser_uses_unpaywall_target_url(monkeypatch, tmp_path) -> None:
    import grados.extract.fetch as fetch_module
    from grados.browser.generic import BrowserFetchResult

    captured: dict[str, str] = {}

    async def fake_resolve(*args, **kwargs):
        _ = (args, kwargs)
        return UnpaywallResolution(
            best_oa_location={"url_for_pdf": "https://example.com/best.pdf"},
            selected_url="https://example.com/best.pdf",
            selected_url_source="unpaywall.best_oa_location.url_for_pdf",
        )

    async def fake_fetch_with_browser(doi, config, paths, resume=None, target_url="", **kwargs):  # noqa: ANN001
        _ = (doi, config, paths, resume)
        captured["target_url"] = target_url
        return BrowserFetchResult(
            pdf_buffer=b"%PDF-1.4\n%stub",
            source="Browser",
            outcome="pdf_obtained",
            state="ok",
        )

    monkeypatch.setattr(fetch_module, "_resolve_unpaywall_locations", fake_resolve)
    monkeypatch.setattr("grados.browser.generic.fetch_with_browser", fake_fetch_with_browser)

    result = asyncio.run(
        fetch_module.fetch_paper(
            doi="10.1234/demo",
            api_keys={},
            etiquette_email="test@example.com",
            fetch_order=["browser"],
            headless_config=HeadlessBrowserConfig(),
            paths=GRaDOSPaths(tmp_path / "grados-home"),
        )
    )

    assert result.via == "browser"
    assert captured["target_url"] == "https://example.com/best.pdf"


def _http_response(
    url: str,
    *,
    status_code: int = 200,
    text: str = "",
    content: bytes | None = None,
    content_type: str = "text/html",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    response_headers = {"content-type": content_type, **(headers or {})}
    return httpx.Response(
        status_code,
        content=content if content is not None else text.encode("utf-8"),
        headers=response_headers,
        request=httpx.Request("GET", url),
    )


def test_fetch_scihub_surfaces_warning_when_pdf_link_missing() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            return _http_response(url, text="<html><body>no pdf here</body></html>")

    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            FakeClient(),  # type: ignore[arg-type]
            {"endpoints": ["https://sci-hub.se"]},
        )
    )

    assert result.outcome == "failed"
    assert result.via == "scihub"
    assert result.state == "parse_error"
    assert result.warnings == ["Sci-Hub primary endpoint sci-hub.se page did not expose a PDF link"]
    assert result.trace[0]["endpoint_role"] == "primary"
    assert result.trace[0]["reason"] == "pdf_link_missing"


def test_fetch_scihub_rejects_oversized_landing_page() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            _ = kwargs
            return _http_response(
                url,
                text="<html></html>",
                headers={"content-length": "2048", "content-type": "text/html"},
            )

    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            FakeClient(),  # type: ignore[arg-type]
            {"endpoints": ["https://sci-hub.se"]},
            max_remote_text_bytes=8,
        )
    )

    assert result.outcome == "failed"
    assert result.state == "error"
    assert result.trace[0]["reason"] == "landing_size_limit"
    assert any("Sci-Hub landing response exceeds configured size limit" in warning for warning in result.warnings)


def test_extract_scihub_pdf_url_handles_common_mirror_markup() -> None:
    from grados.extract.fetch import _extract_scihub_pdf_url

    base_url = "https://sci-hub.se/10.1234/demo"

    assert (
        _extract_scihub_pdf_url('<iframe id="pdf" src="//cdn.sci-hub.se/paper.pdf"></iframe>', base_url)
        == "https://cdn.sci-hub.se/paper.pdf"
    )
    assert (
        _extract_scihub_pdf_url('<embed id="plugin" src="/downloads/paper.pdf#view">', base_url)
        == "https://sci-hub.se/downloads/paper.pdf#view"
    )
    assert (
        _extract_scihub_pdf_url('<embed original-url="https://moscow.sci-hub.se/paper.pdf">', base_url)
        == "https://moscow.sci-hub.se/paper.pdf"
    )
    assert (
        _extract_scihub_pdf_url('<object data="/pdfs/paper.pdf?download=true"></object>', base_url)
        == "https://sci-hub.se/pdfs/paper.pdf?download=true"
    )
    assert (
        _extract_scihub_pdf_url('<a href="/downloads/paper.pdf">Download</a>', base_url)
        == "https://sci-hub.se/downloads/paper.pdf"
    )
    assert (
        _extract_scihub_pdf_url('<button onclick="location.href=&quot;/button/paper.pdf&quot;"></button>', base_url)
        == "https://sci-hub.se/button/paper.pdf"
    )


def test_fetch_scihub_uses_fallback_endpoint_when_primary_unreachable() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.calls.append(url)
            if url == "https://primary.example/10.1234/demo":
                raise httpx.ConnectError("network down", request=httpx.Request("GET", url))
            if url == "https://fallback.example/10.1234/demo":
                return _http_response(url, text='<html><iframe src="/paper.pdf"></iframe></html>')
            if url == "https://fallback.example/paper.pdf":
                return _http_response(
                    url,
                    content=b"%PDF-1.4\n%demo\n",
                    content_type="application/pdf",
                )
            raise AssertionError(f"unexpected URL: {url}")

    client = FakeClient()
    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            client,  # type: ignore[arg-type]
            {"endpoints": ["https://primary.example", "https://fallback.example"]},
        )
    )

    assert result.outcome == "pdf_obtained"
    assert result.state == "ok"
    assert result.host == "fallback.example"
    assert client.calls[0] == "https://primary.example/10.1234/demo"
    assert client.calls[-2:] == [
        "https://fallback.example/10.1234/demo",
        "https://fallback.example/paper.pdf",
    ]
    assert result.trace[0]["endpoint_role"] == "primary"
    assert result.trace[0]["state"] == "site_unreachable"
    assert result.trace[1]["endpoint_role"] == "fallback"
    assert result.trace[1]["state"] == "ok"


def test_fetch_scihub_not_found_tries_fallback_endpoint() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.calls.append(url)
            if url == "https://primary.example/10.1234/demo":
                return _http_response(url, status_code=404, text="not found")
            if url == "https://fallback.example/10.1234/demo":
                return _http_response(url, text='<html><iframe src="/paper.pdf"></iframe></html>')
            if url == "https://fallback.example/paper.pdf":
                return _http_response(
                    url,
                    content=b"%PDF-1.4\n%demo\n",
                    content_type="application/pdf",
                )
            raise AssertionError(f"unexpected URL: {url}")

    client = FakeClient()
    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            client,  # type: ignore[arg-type]
            {"endpoints": ["https://primary.example", "https://fallback.example"]},
        )
    )

    assert result.outcome == "pdf_obtained"
    assert result.state == "ok"
    assert client.calls == [
        "https://primary.example/10.1234/demo",
        "https://fallback.example/10.1234/demo",
        "https://fallback.example/paper.pdf",
    ]
    assert result.trace[0]["reason"] == "not_found_status"
    assert result.trace[1]["state"] == "ok"


def test_fetch_scihub_reports_unreachable_after_all_endpoints_fail() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.calls.append(url)
            raise httpx.ConnectError("network down", request=httpx.Request("GET", url))

    client = FakeClient()
    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            client,  # type: ignore[arg-type]
            {"endpoints": ["https://primary.example", "https://fallback.example"]},
        )
    )

    assert result.outcome == "failed"
    assert result.state == "site_unreachable"
    assert result.trace[0]["endpoint_role"] == "primary"
    assert result.trace[-1]["endpoint_role"] == "fallback"
    assert len(result.warnings) == 2


def test_fetch_scihub_uses_legacy_fallback_mirror_when_endpoints_missing() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        def __init__(self) -> None:
            self.last_url = ""

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.last_url = url
            return _http_response(url, text="<html><body>no pdf here</body></html>")

    client = FakeClient()
    result = asyncio.run(
        _fetch_scihub(
            "10.1234/demo",
            client,  # type: ignore[arg-type]
            {"fallback_mirror": "https://legacy.example"},
        )
    )

    assert result.outcome == "failed"
    assert result.via == "scihub"
    assert result.state == "parse_error"
    assert client.last_url == "https://legacy.example/10.1234/demo"


def test_fetch_scihub_ignores_legacy_fallback_mirror_key() -> None:
    from grados.extract.fetch import _fetch_scihub

    class FakeClient:
        def __init__(self) -> None:
            self.last_url = ""

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            self.last_url = url
            return _http_response(url, text="<html><body>no pdf here</body></html>")

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
    assert result.state == "parse_error"
    assert client.last_url == "https://sci-hub.se/10.1234/demo"


def test_download_pdf_rejects_oversized_content_length() -> None:
    from grados.extract.fetch import _download_pdf
    from grados.http_limits import SizeLimitError

    class FakeClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            _ = kwargs
            return httpx.Response(
                200,
                content=b"%PDF-1.4\n",
                headers={"content-length": "2048", "content-type": "application/pdf"},
                request=httpx.Request("GET", url),
            )

    with pytest.raises(SizeLimitError, match="Remote PDF response exceeds configured size limit"):
        asyncio.run(_download_pdf(FakeClient(), "https://example.com/paper.pdf", max_bytes=1024))  # type: ignore[arg-type]


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


def test_elsevier_xml_rejects_entity_expansion() -> None:
    xml_payload = """<?xml version="1.0"?>
<!DOCTYPE demo [
  <!ENTITY expand "unsafe">
]>
<full-text-retrieval-response>
  <coredata>
    <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">&expand;</dc:title>
  </coredata>
</full-text-retrieval-response>
"""

    with pytest.raises(EntitiesForbidden):
        _extract_elsevier_markdown_from_xml(xml_payload, fallback_doi="10.1016/j.demo.2026.01.001")
