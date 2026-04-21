from __future__ import annotations

from pathlib import Path

from grados.research.full_context import get_papers_full_context
from grados.storage.papers import save_paper_markdown


def _write_long_paper(papers_dir: Path) -> None:
    repeated_text = "Composite damping improves vibration attenuation. " * 40
    save_paper_markdown(
        "10.1000/a",
        (
            "## Abstract\n\n"
            f"{repeated_text}\n\n"
            "## Methods\n\n"
            f"{repeated_text}\n\n"
            "## Results\n\n"
            "The attenuation gain reaches 18%."
        ),
        papers_dir,
        title="Paper A",
        year="2025",
        journal="Composite Structures",
    )


def test_get_papers_full_context_returns_requested_sections(tmp_path: Path) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"
    _write_long_paper(papers_dir)

    result = get_papers_full_context(
        chroma_dir,
        dois=["10.1000/a"],
        section_filter=["abstract", "methods"],
        mode="full",
        max_total_tokens=5000,
    )

    assert result.found == 1
    assert result.missing_dois == []
    assert [section.name for section in result.papers[0].sections] == ["Abstract", "Methods"]
    assert result.papers[0].sections[0].content.startswith("## Abstract")


def test_get_papers_full_context_marks_truncation_when_budget_is_small(tmp_path: Path) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"
    _write_long_paper(papers_dir)

    result = get_papers_full_context(
        chroma_dir,
        dois=["10.1000/a"],
        mode="full",
        max_total_tokens=40,
    )

    assert result.returned_total_tokens == 40
    assert result.estimated_total_tokens > result.returned_total_tokens
    assert result.papers[0].truncated is True
    assert any(section.truncated for section in result.papers[0].sections)
