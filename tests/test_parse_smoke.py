from __future__ import annotations

import asyncio
import io
import subprocess
import zipfile
from typing import Any

import pymupdf

from grados.extract.parse import (
    ParsePipelineResult,
    build_pdf_parser_strategies,
    parse_pdf,
    parse_pdf_with_diagnostics,
    resolve_document_normalizer,
)
from grados.extract.qa import is_valid_paper_content


def _make_sample_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        "Demo Paper Title\n\n"
        "Abstract\n"
        + ("This study investigates composite vibration behavior in layered materials. " * 30)
        + "\n\nIntroduction\n"
        + ("More academic text describing methods, results, discussion, and conclusions. " * 25)
    )
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    pdf_buffer = doc.tobytes()
    doc.close()
    return pdf_buffer


def test_parse_pdf_with_pymupdf_pipeline() -> None:
    parsed = asyncio.run(
        parse_pdf(
            _make_sample_pdf_bytes(),
            parse_order=["PyMuPDF"],
            parse_enabled={"PyMuPDF": True, "Marker": False, "Docling": False},
        )
    )

    assert parsed is not None
    assert "Demo Paper Title" in parsed
    assert len(parsed) > 100


def test_parse_pdf_prefers_docling_before_pymupdf(monkeypatch) -> None:
    import grados.extract.parse as parse_module

    monkeypatch.setattr(
        parse_module,
        "_parse_docling_attempt",
        lambda pdf_buffer, filename: parse_module._ParserAttemptResult(markdown="# Docling\n\nConverted by Docling."),
    )
    monkeypatch.setattr(
        parse_module,
        "_parse_marker_attempt",
        lambda pdf_buffer, filename, marker_timeout: parse_module._ParserAttemptResult(markdown=None),
    )
    monkeypatch.setattr(
        parse_module,
        "_parse_pymupdf_attempt",
        lambda pdf_buffer: parse_module._ParserAttemptResult(markdown="# PyMuPDF\n\nFallback."),
    )

    parsed = asyncio.run(parse_module.parse_pdf(_make_sample_pdf_bytes()))

    assert parsed == "# Docling\n\nConverted by Docling."


def test_parse_strategy_registry_preserves_order_and_filters_unknown_names() -> None:
    strategies = build_pdf_parser_strategies(["PyMuPDF", "Unknown", "MinerU", "Docling"])

    assert [strategy.name for strategy in strategies] == ["PyMuPDF", "MinerU", "Docling"]


def test_default_parse_strategy_order_includes_mineru_after_docling() -> None:
    strategies = build_pdf_parser_strategies()

    assert [strategy.name for strategy in strategies] == ["Docling", "MinerU", "Marker", "PyMuPDF"]


def test_document_normalizer_registry_resolves_known_and_fallback_formats() -> None:
    assert resolve_document_normalizer("html").parser_used == "Docling"
    assert resolve_document_normalizer("unknown").parser_used == "PlainText"


def test_parse_marker_returns_none_on_timeout(monkeypatch) -> None:
    import grados.extract.parse as parse_module

    observed: dict[str, float | None] = {}

    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        observed["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd="marker", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(parse_module.subprocess, "run", fake_run)

    parsed = parse_module._parse_marker(_make_sample_pdf_bytes(), "paper.pdf", marker_timeout=50)

    assert parsed is None
    assert observed["timeout"] == 0.05


def test_parse_mineru_cloud_parser_uses_authenticated_signed_upload(monkeypatch) -> None:
    import grados.extract.parse as parse_module

    class FakeResponse:
        def __init__(
            self,
            json_payload: dict[str, Any] | None = None,
            content: bytes = b"",
        ) -> None:
            self._json_payload = json_payload or {}
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._json_payload

    class FakeClient:
        def __init__(self) -> None:
            self.auth_headers: list[str] = []
            self.uploaded_pdf = b""
            self.upload_headers: list[dict[str, str]] = []
            self.polls = 0

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            assert url == "https://mineru.net/api/v4/file-urls/batch"
            self.auth_headers.append(headers["Authorization"])
            assert json["model_version"] == "vlm"
            assert json["files"][0]["name"] == "paper.pdf"
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.example/paper.pdf"],
                    },
                    "msg": "ok",
                }
            )

        def put(self, url: str, *, content: bytes, headers: dict[str, str]) -> FakeResponse:
            assert url == "https://upload.example/paper.pdf"
            assert headers == {}
            self.upload_headers.append(headers)
            self.uploaded_pdf = content
            return FakeResponse()

        def get(self, url: str, *, headers: dict[str, str] | None = None) -> FakeResponse:
            if url == "https://mineru.net/api/v4/extract-results/batch/batch-1":
                assert headers is not None
                self.auth_headers.append(headers["Authorization"])
                self.polls += 1
                if self.polls == 1:
                    return FakeResponse({"code": -60012, "msg": "task not found or expire"})
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "batch_id": "batch-1",
                            "extract_result": [
                                {
                                    "data_id": "ignored-by-fallback",
                                    "state": "done",
                                    "full_zip_url": "https://cdn.example/result.zip",
                                }
                            ],
                        },
                        "msg": "ok",
                    }
                )
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as archive:
                archive.writestr("paper/full.md", "# MinerU\n\n" + ("Parsed by MinerU cloud. " * 12))
            return FakeResponse(content=zip_buffer.getvalue())

    fake_client = FakeClient()
    monkeypatch.setattr(parse_module.httpx, "Client", lambda **kwargs: fake_client)
    monkeypatch.setattr(parse_module.time, "sleep", lambda seconds: None)

    result = parse_module._parse_mineru_attempt(
        _make_sample_pdf_bytes(),
        "paper.pdf",
        api_key="Bearer mineru-secret",
        model_version="vlm",
        language="en",
        timeout_ms=1000,
        poll_interval=0.5,
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
    )

    assert result.markdown is not None
    assert "Parsed by MinerU cloud" in result.markdown
    assert result.warning == ""
    assert fake_client.uploaded_pdf.startswith(b"%PDF")
    assert fake_client.upload_headers == [{}]
    assert fake_client.auth_headers == ["Bearer mineru-secret", "Bearer mineru-secret", "Bearer mineru-secret"]


def test_parse_pdf_falls_back_after_marker_timeout(monkeypatch) -> None:
    import grados.extract.parse as parse_module

    monkeypatch.setattr(
        parse_module,
        "_parse_docling_attempt",
        lambda pdf_buffer, filename: parse_module._ParserAttemptResult(markdown=None),
    )
    monkeypatch.setattr(
        parse_module,
        "_parse_pymupdf_attempt",
        lambda pdf_buffer: parse_module._ParserAttemptResult(markdown="# PyMuPDF\n\nFallback content." * 10),
    )

    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        raise subprocess.TimeoutExpired(cmd="marker", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(parse_module.subprocess, "run", fake_run)

    parsed = asyncio.run(
        parse_module.parse_pdf(
            _make_sample_pdf_bytes(),
            parse_order=["Marker", "PyMuPDF"],
            parse_enabled={"Docling": False, "Marker": True, "PyMuPDF": True},
            marker_timeout=25,
        )
    )

    assert parsed is not None
    assert "PyMuPDF" in parsed


def test_parse_pdf_with_diagnostics_preserves_standardized_parser_messages(monkeypatch) -> None:
    import grados.extract.parse as parse_module

    monkeypatch.setattr(
        parse_module,
        "_parse_docling_attempt",
        lambda pdf_buffer, filename: parse_module._ParserAttemptResult(
            markdown=None,
            warning="Docling: parse failed; falling back to next parser.",
            debug="Docling debug: RuntimeError: cold start failed",
        ),
    )
    monkeypatch.setattr(
        parse_module,
        "_parse_marker_attempt",
        lambda pdf_buffer, filename, marker_timeout: parse_module._ParserAttemptResult(
            markdown="# Marker\n\nRecovered parser output." * 10,
        ),
    )

    result = asyncio.run(
        parse_pdf_with_diagnostics(
            _make_sample_pdf_bytes(),
            parse_order=["Docling", "Marker"],
            parse_enabled={"Docling": True, "Marker": True, "PyMuPDF": False},
        )
    )

    assert isinstance(result, ParsePipelineResult)
    assert result.parser_used == "Marker"
    assert result.markdown is not None
    assert result.warnings == ["Docling: parse failed; falling back to next parser."]
    assert result.debug == ["Docling debug: RuntimeError: cold start failed"]


def test_quality_assurance_accepts_structured_paper_text() -> None:
    text = (
        "# Demo Paper Title\n\n"
        "## Abstract\n\n"
        + ("This study investigates composite vibration behavior in layered materials. " * 25)
        + "\n\n## Introduction\n\n"
        + ("Methods, results, discussion, and conclusion are described in this article. " * 20)
    )

    assert is_valid_paper_content(text, min_characters=400, expected_title="Demo Paper Title") is True
    assert is_valid_paper_content("short", min_characters=10) is False
