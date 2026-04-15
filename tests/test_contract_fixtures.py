from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from grados.config import GRaDOSPaths, generate_default_config
from grados.extract.parse import ParsePipelineResult
from grados.importing import import_local_pdf_library
from grados.publisher.common import classify_pdf_content, detect_bot_challenge
from grados.publisher.elsevier import fetch_elsevier_article
from grados.publisher.springer import fetch_springer_article


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        content: bytes = b"",
        json_payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.content = content
        self._json_payload = json_payload or {}

    def json(self) -> dict[str, Any]:
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    def __init__(self, responses: dict[tuple[str, str], FakeResponse]) -> None:
        self.responses = responses

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        params = kwargs.get("params") or {}
        key = (
            url,
            json.dumps(params, sort_keys=True, ensure_ascii=False),
        )
        response = self.responses.get(key)
        if response is None:
            raise AssertionError(f"Unexpected request: {url} params={params}")
        return response


def test_elsevier_contract_falls_back_to_metadata_and_asset_hints() -> None:
    doi = "10.1016/j.contract.2026.01.001"
    base = f"https://api.elsevier.com/content/article/doi/{doi}"
    client = FakeAsyncClient(
        {
            (base, json.dumps({"view": "FULL"}, sort_keys=True)): FakeResponse(
                status_code=200,
                text="<broken-xml",
            ),
            (base, json.dumps({}, sort_keys=True)): FakeResponse(
                status_code=200,
                json_payload={
                    "full-text-retrieval-response": {
                        "coredata": {
                            "prism:doi": doi,
                            "dc:title": "Metadata Only Elsevier Contract",
                            "dc:description": "Metadata fallback still carries useful signals.",
                            "pii": "S123456789000001",
                            "eid": "2-s2.0-contract",
                            "prism:publicationName": "Contract Journal",
                            "prism:coverDate": "2026-01-15",
                            "openaccess": "1",
                            "link": [
                                {
                                    "@href": "https://www.sciencedirect.com/science/article/pii/S123456789000001",
                                    "@rel": "scidir",
                                }
                            ],
                        }
                    }
                },
            ),
        }
    )

    result = asyncio.run(fetch_elsevier_article(doi, "elsevier-key", client))  # type: ignore[arg-type]

    assert result.outcome == "metadata_only"
    assert result.metadata is not None
    assert result.metadata.pii == "S123456789000001"
    assert result.metadata.scidir_url.endswith("S123456789000001")
    assert result.asset_hints == [
        {
            "kind": "article_landing",
            "label": "ScienceDirect landing page",
            "url": "https://www.sciencedirect.com/science/article/pii/S123456789000001",
        },
        {
            "kind": "object_api_meta",
            "label": "Elsevier object metadata",
            "url": "https://api.elsevier.com/content/object/pii/S123456789000001?view=META",
        },
        {
            "kind": "scopus_eid",
            "label": "Scopus EID",
            "value": "2-s2.0-contract",
        },
    ]


def test_springer_contract_falls_through_xml_html_to_pdf() -> None:
    doi = "10.1007/s-contract-2026-0001"
    client = FakeAsyncClient(
        {
            (
                "https://api.springernature.com/meta/v2/json",
                json.dumps({"api_key": "meta-key", "q": f"doi:{doi}"}, sort_keys=True),
            ): FakeResponse(
                status_code=200,
                json_payload={
                    "records": [
                        {
                            "doi": doi,
                            "title": "Springer Contract Fixture",
                            "abstract": "Fixture abstract",
                            "publisher": "Springer Nature",
                            "openaccess": "true",
                            "url": [
                                {"format": "html", "value": "https://springer.example/article"},
                                {"format": "pdf", "value": "https://springer.example/article.pdf"},
                            ],
                        }
                    ]
                },
            ),
            (
                "https://api.springernature.com/openaccess/jats",
                json.dumps({"api_key": "oa-key", "q": f"doi:{doi}"}, sort_keys=True),
            ): FakeResponse(status_code=200, text="<article>short xml</article>"),
            ("https://springer.example/article", json.dumps({}, sort_keys=True)): FakeResponse(
                status_code=200,
                text="<html><body>short html</body></html>",
            ),
            ("https://springer.example/article.pdf", json.dumps({}, sort_keys=True)): FakeResponse(
                status_code=200,
                content=b"%PDF-1.4\n%contract-pdf",
            ),
        }
    )

    result = asyncio.run(fetch_springer_article(doi, "meta-key", "oa-key", client))  # type: ignore[arg-type]

    assert result.outcome == "pdf_obtained"
    assert result.pdf_buffer.startswith(b"%PDF-")
    assert result.asset_hints == [
        {
            "kind": "article_html",
            "label": "Springer HTML landing page",
            "url": "https://springer.example/article",
        },
        {
            "kind": "article_pdf",
            "label": "Springer PDF",
            "url": "https://springer.example/article.pdf",
        },
    ]


def test_browser_contract_rejects_html_disguised_as_pdf_and_detects_challenge() -> None:
    disguised_html = (
        b"<html><head><title>Just a moment</title></head>"
        b"<body><iframe src=\"https://challenges.cloudflare.com\"></iframe></body></html>"
    )

    assert classify_pdf_content(disguised_html, "application/pdf") == {
        "is_pdf": False,
        "reason": "html_or_challenge_page",
    }
    assert detect_bot_challenge("请稍候", disguised_html.decode("latin-1"), "https://example.com/pdfft") is True


def test_local_import_contract_uses_recursive_fixture_and_surfaces_warning_mix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    paths.config_file.write_text(json.dumps(generate_default_config(paths)), encoding="utf-8")

    source = tmp_path / "library"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "fixture.pdf").write_bytes(b"%PDF-1.4\nfixture")

    async def fake_parse_pdf(pdf_bytes: bytes, filename: str, **kwargs: Any) -> ParsePipelineResult:
        return ParsePipelineResult(
            markdown=(
                "# Nested Contract Paper\n\n"
                "DOI: 10.1234/fixture-a).\n\n"
                "## Abstract\n\n"
                "Too short for QA."
            ),
            parser_used="PyMuPDF",
            warnings=["parser emitted partial text"],
            debug=["fallback:pymupdf"],
        )

    import grados.importing as importing
    import grados.storage.vector as vector

    monkeypatch.setattr(importing, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    non_recursive = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            recursive=False,
        )
    )
    recursive = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            recursive=True,
        )
    )

    assert non_recursive.scanned == 0
    assert recursive.scanned == 1
    assert recursive.imported == 1
    assert recursive.items[0].status == "imported_with_warnings"
    assert recursive.items[0].doi == "10.1234/fixture-a"
    assert recursive.items[0].detail == "parser_warning,qa_warning"
    assert recursive.items[0].warnings == [
        "parser emitted partial text",
        "QA validation failed — imported anyway.",
    ]
    assert recursive.items[0].debug == ["fallback:pymupdf"]
    assert (paths.papers / "10_1234_fixture_a.md").is_file()
