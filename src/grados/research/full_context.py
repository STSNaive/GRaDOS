"""Full-context deep reading helpers."""

from __future__ import annotations

from pathlib import Path

from grados.research.common import _estimate_tokens, _resolve_documents, _select_sections
from grados.research.models import FullContextPaper, FullContextResult, FullContextSection


def get_papers_full_context(
    chroma_dir: Path,
    *,
    dois: list[str],
    section_filter: list[str] | None = None,
    mode: str = "estimate",
    max_total_tokens: int = 32000,
) -> FullContextResult:
    """Return structured full-context material for a context-budgeted paper batch."""
    resolved, missing = _resolve_documents(chroma_dir, dois)
    papers: list[FullContextPaper] = []
    total_estimated = 0
    returned_tokens = 0

    for record in resolved:
        selected_sections = _select_sections(record, section_filter=section_filter)
        section_payloads: list[FullContextSection] = []
        paper_estimated = 0
        paper_returned = 0
        truncated = False

        for section in selected_sections:
            content = str(section["text"]).strip()
            token_estimate = _estimate_tokens(content)
            paper_estimated += token_estimate
            total_estimated += token_estimate

            content_value = ""
            section_truncated = False
            if mode == "full":
                remaining_budget = max_total_tokens - returned_tokens
                if remaining_budget <= 0:
                    truncated = True
                    continue
                if token_estimate <= remaining_budget:
                    content_value = content
                    paper_returned += token_estimate
                    returned_tokens += token_estimate
                else:
                    max_chars = max(0, remaining_budget * 4)
                    content_value = content[:max_chars].rstrip()
                    section_truncated = True
                    paper_returned += remaining_budget
                    returned_tokens += remaining_budget
                    truncated = True
                if returned_tokens >= max_total_tokens:
                    truncated = True
            section_payloads.append(
                FullContextSection(
                    name=str(section["name"]),
                    level=int(section["level"]),
                    token_estimate=token_estimate,
                    content=content_value,
                    truncated=section_truncated,
                )
            )

        papers.append(
            FullContextPaper(
                doi=record.doi,
                safe_doi=record.safe_doi,
                title=record.title,
                year=record.year,
                journal=record.journal,
                available_sections=list(record.section_headings),
                estimated_tokens=paper_estimated,
                returned_tokens=paper_returned,
                truncated=truncated,
                sections=section_payloads,
            )
        )

    return FullContextResult(
        mode=mode,
        requested_dois=dois,
        found=len(papers),
        missing_dois=missing,
        section_filter=section_filter or [],
        estimated_total_tokens=total_estimated,
        returned_total_tokens=returned_tokens,
        papers=papers,
    )
