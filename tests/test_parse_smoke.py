from __future__ import annotations

import asyncio

import pymupdf

from grados.extract.parse import parse_pdf
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
