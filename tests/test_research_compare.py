from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import grados.research.compare as compare_module
from grados.research.compare import compare_papers
from grados.storage.papers import save_paper_markdown


def _write_saved_papers(papers_dir: Path) -> None:
    save_paper_markdown(
        "10.1000/a",
        (
            "## Abstract\n\n"
            "Paper A studies composite damping.\n\n"
            "## Methods\n\n"
            "Paper A uses modal analysis on laminate plates.\n\n"
            "## Results\n\n"
            "Composite damping improves vibration attenuation by 18%."
        ),
        papers_dir,
        title="Paper A",
        year="2025",
        journal="Composite Structures",
    )
    save_paper_markdown(
        "10.1000/b",
        (
            "## Abstract\n\n"
            "Paper B studies vibration control.\n\n"
            "## Methods\n\n"
            "Paper B uses finite-element evaluation on sandwich panels.\n\n"
            "## Results\n\n"
            "The baseline condition remains stable."
        ),
        papers_dir,
        title="Paper B",
        year="2024",
        journal="Engineering Reports",
    )


def test_compare_papers_aligns_saved_methods_sections(tmp_path: Path) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"
    _write_saved_papers(papers_dir)

    comparison = compare_papers(
        chroma_dir,
        dois=["10.1000/a", "10.1000/b"],
        focus="methods",
        comparison_axes=["method"],
    )

    assert comparison.missing_dois == []
    assert comparison.axes == ["method"]
    assert "| Paper | method |" in comparison.rendered
    assert "Paper A (2025)" in comparison.rendered
    assert "Paper B (2024)" in comparison.rendered
    assert comparison.papers[0].canonical_uri.startswith("grados://papers/")
    assert comparison.papers[0].evidence[0].axis == "method"
    assert comparison.papers[0].evidence[0].section_name == "Methods"
    assert comparison.papers[0].evidence[0].paragraph_start is not None
    assert comparison.papers[0].evidence[0].paragraph_count is not None
    assert "reread" in comparison.papers[0].evidence[0].warning


def test_compare_papers_excludes_backmatter_sections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        compare_module,
        "_resolve_documents",
        lambda chroma_dir, dois: (
            [
                SimpleNamespace(
                    doi="10.1234/demo",
                    safe_doi="10_1234_demo",
                    title="Paper A",
                    year="2025",
                    journal="Composite Structures",
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        compare_module,
        "_select_sections",
        lambda record, focus="methods": [
            {
                "name": "References",
                "text": "Method term appears in a cited reference title.",
                "paragraph_start": 12,
                "paragraph_count": 2,
            },
            {
                "name": "Funding",
                "text": "The method was funded by a program.",
                "paragraph_start": 10,
                "paragraph_count": 1,
            },
            {
                "name": "Methods",
                "text": "The method uses modal analysis.",
                "paragraph_start": 3,
                "paragraph_count": 2,
            },
        ],
    )

    comparison = compare_papers(
        tmp_path / "chroma",
        dois=["10.1234/demo"],
        focus="methods",
        comparison_axes=["method"],
    )

    assert comparison.papers[0].sections_used == ["Methods"]
    assert comparison.papers[0].excluded_sections == ["References", "Funding"]
    assert comparison.papers[0].evidence[0].section_name == "Methods"


def test_compare_papers_returns_empty_axis_when_only_title_placeholder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        compare_module,
        "_resolve_documents",
        lambda chroma_dir, dois: (
            [
                SimpleNamespace(
                    doi="10.1234/demo",
                    safe_doi="10_1234_demo",
                    title="Paper A",
                    year="2025",
                    journal="Composite Structures",
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        compare_module,
        "_select_sections",
        lambda record, focus="methods": [
            {
                "name": "Methods",
                "text": "Paper A",
                "paragraph_start": 3,
                "paragraph_count": 1,
            }
        ],
    )

    comparison = compare_papers(
        tmp_path / "chroma",
        dois=["10.1234/demo"],
        focus="methods",
        comparison_axes=["method"],
    )

    assert comparison.papers[0].comparisons["method"] == ""
    assert comparison.papers[0].evidence[0].warning == "no_evidence_for_axis"
    assert "| Paper A (2025) |  |" in comparison.rendered


def test_compare_papers_returns_empty_axis_when_only_author_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        compare_module,
        "_resolve_documents",
        lambda chroma_dir, dois: (
            [
                SimpleNamespace(
                    doi="10.1234/demo",
                    safe_doi="10_1234_demo",
                    title="Paper A",
                    year="2025",
                    journal="Composite Structures",
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        compare_module,
        "_select_sections",
        lambda record, focus="methods": [
            {
                "name": "Methods",
                "text": "Authors: Alice Smith, Bob Lee",
                "paragraph_start": 3,
                "paragraph_count": 1,
            }
        ],
    )

    comparison = compare_papers(
        tmp_path / "chroma",
        dois=["10.1234/demo"],
        focus="methods",
        comparison_axes=["method"],
    )

    assert comparison.papers[0].comparisons["method"] == ""
    assert comparison.papers[0].evidence[0].warning == "no_evidence_for_axis"


def test_compare_papers_escapes_markdown_table_cells(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        compare_module,
        "_resolve_documents",
        lambda chroma_dir, dois: (
            [
                SimpleNamespace(
                    doi="10.1234/demo",
                    safe_doi="10_1234_demo",
                    title="Paper | A",
                    year="2025",
                    journal="Composite Structures",
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        compare_module,
        "_select_sections",
        lambda record, focus="methods": [
            {
                "name": "Methods",
                "text": "Method paragraph",
                "paragraph_start": 3,
                "paragraph_count": 2,
            }
        ],
    )
    monkeypatch.setattr(
        compare_module,
        "_excerpt_for_axis",
        lambda text, axis, max_chars=260: "Line one |\nLine two",
    )

    comparison = compare_papers(
        tmp_path / "chroma",
        dois=["10.1234/demo"],
        focus="methods",
        comparison_axes=["method"],
    )

    assert comparison.rendered == (
        "| Paper | method |\n"
        "| --- | --- |\n"
        "| Paper \\| A (2025) | Line one \\| <br> Line two |"
    )
    assert comparison.papers[0].canonical_uri == "grados://papers/10_1234_demo"
    assert comparison.papers[0].evidence[0].paragraph_start == 3
    assert comparison.papers[0].evidence[0].paragraph_count == 2
