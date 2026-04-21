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
        lambda record, focus="methods": [{"name": "Methods", "text": "Method paragraph"}],
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
