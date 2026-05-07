"""Aligned paper comparison helpers."""

from __future__ import annotations

import re
from pathlib import Path

from grados.research.common import _excerpt_for_axis, _resolve_documents, _select_sections
from grados.research.models import ComparisonEvidenceItem, PaperComparisonResult, PaperComparisonRow


def _canonical_uri(safe_doi: str) -> str:
    return f"grados://papers/{safe_doi}" if safe_doi else ""


def _escape_markdown_table_cell(value: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    normalized = " <br> ".join(line for line in lines if line)
    return normalized.replace("|", r"\|")


def _coerce_nonnegative_int(value: object) -> int:
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return 0


def _axis_evidence(
    *,
    axis: str,
    sections: list[dict[str, object]],
    canonical_uri: str,
) -> ComparisonEvidenceItem:
    axis_terms = re.findall(r"[a-z0-9]{3,}", axis.lower())
    best_section: dict[str, object] | None = None
    best_excerpt = ""
    best_score = -1

    for section in sections:
        excerpt = _excerpt_for_axis(str(section.get("text", "")), axis)
        if not excerpt:
            continue
        score = sum(excerpt.lower().count(term) for term in axis_terms)
        if score > best_score:
            best_section = section
            best_excerpt = excerpt
            best_score = score

    if best_section is None:
        return ComparisonEvidenceItem(
            axis=axis,
            section_name="",
            excerpt="",
            canonical_uri=canonical_uri,
            warning="No comparable excerpt could be located.",
        )

    paragraph_count = _coerce_nonnegative_int(best_section.get("paragraph_count", 0))
    paragraph_start = _coerce_nonnegative_int(best_section.get("paragraph_start", 0))
    return ComparisonEvidenceItem(
        axis=axis,
        section_name=str(best_section.get("name", "")),
        excerpt=best_excerpt,
        canonical_uri=canonical_uri,
        paragraph_start=paragraph_start if paragraph_count > 0 else None,
        paragraph_count=paragraph_count if paragraph_count > 0 else None,
        warning="Section-level anchor; reread the canonical paragraph window before citing.",
    )


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
        canonical_uri = _canonical_uri(record.safe_doi)
        evidence = [_axis_evidence(axis=axis, sections=sections, canonical_uri=canonical_uri) for axis in axes]
        comparisons = {item.axis: item.excerpt for item in evidence}
        paper_rows.append(
            PaperComparisonRow(
                doi=record.doi,
                safe_doi=record.safe_doi,
                canonical_uri=canonical_uri,
                title=record.title,
                year=record.year,
                journal=record.journal,
                focus=focus,
                sections_used=[str(section["name"]) for section in sections],
                comparisons=comparisons,
                evidence=evidence,
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
