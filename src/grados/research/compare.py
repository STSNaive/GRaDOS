"""Aligned paper comparison helpers."""

from __future__ import annotations

import re
from pathlib import Path

from grados.research.common import _excerpt_for_axis, _resolve_documents, _select_sections
from grados.research.models import PaperComparisonResult, PaperComparisonRow


def _escape_markdown_table_cell(value: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    normalized = " <br> ".join(line for line in lines if line)
    return normalized.replace("|", r"\|")


def compare_papers(
    chroma_dir: Path,
    *,
    dois: list[str],
    focus: str = "methods",
    comparison_axes: list[str] | None = None,
    output_format: str = "table",
) -> PaperComparisonResult:
    """Return aligned, parallel paper comparisons for agent consumption."""
    resolved, missing = _resolve_documents(chroma_dir, dois)
    axes = [axis.strip() for axis in (comparison_axes or []) if axis.strip()]
    if not axes:
        if focus == "results":
            axes = ["dataset", "metric", "main finding", "limitation"]
        elif focus == "full_text":
            axes = ["objective", "approach", "key finding", "limitation"]
        else:
            axes = ["objective", "dataset", "method", "limitation"]

    paper_rows: list[PaperComparisonRow] = []
    for record in resolved:
        sections = _select_sections(record, focus=focus)
        joined_text = "\n\n".join(str(section["text"]).strip() for section in sections)
        comparisons = {
            axis: _excerpt_for_axis(joined_text, axis)
            for axis in axes
        }
        paper_rows.append(
            PaperComparisonRow(
                doi=record.doi,
                safe_doi=record.safe_doi,
                title=record.title,
                year=record.year,
                journal=record.journal,
                focus=focus,
                sections_used=[str(section["name"]) for section in sections],
                comparisons=comparisons,
            )
        )

    rendered = ""
    if output_format == "table" and paper_rows:
        header = "| Paper | " + " | ".join(_escape_markdown_table_cell(axis) for axis in axes) + " |"
        divider = "| --- | " + " | ".join("---" for _ in axes) + " |"
        rows = []
        for paper in paper_rows:
            label = _escape_markdown_table_cell(f"{paper.title} ({paper.year})".strip())
            cells = [_escape_markdown_table_cell(paper.comparisons.get(axis, "")) for axis in axes]
            rows.append("| " + " | ".join([label, *cells]) + " |")
        rendered = "\n".join([header, divider, *rows])
    elif output_format == "bullets" and paper_rows:
        lines: list[str] = []
        for paper in paper_rows:
            lines.append(f"- {paper.title} ({paper.doi})")
            for axis in axes:
                lines.append(f"  - {axis}: {paper.comparisons.get(axis, '')}")
        rendered = "\n".join(lines)

    return PaperComparisonResult(
        focus=focus,
        axes=axes,
        missing_dois=missing,
        papers=paper_rows,
        output_format=output_format,
        rendered=rendered,
    )
