from __future__ import annotations

import asyncio
import subprocess

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
    strategies = build_pdf_parser_strategies(["PyMuPDF", "Unknown", "Docling"])

    assert [strategy.name for strategy in strategies] == ["PyMuPDF", "Docling"]


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
