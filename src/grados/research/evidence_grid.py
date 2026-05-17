"""Evidence-grid construction helpers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from grados.research.common import _normalize_text, _section_matches
from grados.research.models import EvidenceGridBlock, EvidenceGridResult, EvidenceGridRow
from grados.storage.paths import resolve_papers_dir
from grados.storage.retrieval import PaperSearchResult
from grados.storage.vector import search_papers


def _canonical_uri(safe_doi: str) -> str:
    return f"grados://papers/{safe_doi}" if safe_doi else ""


def _row_from_match(
    *,
    subquestion: str,
    query_text: str,
    match: PaperSearchResult,
) -> EvidenceGridRow:
    return EvidenceGridRow(
        subquestion=subquestion,
        query_used=query_text,
        doi=match.doi,
        safe_doi=match.safe_doi,
        canonical_uri=_canonical_uri(match.safe_doi),
        title=match.title,
        year=match.year,
        journal=match.journal,
        section_name=match.section_name,
        paragraph_start=match.paragraph_start if match.paragraph_count > 0 else None,
        paragraph_count=match.paragraph_count if match.paragraph_count > 0 else None,
        snippet=match.snippet,
        score=match.score,
        support_strength=_support_strength(match.score),
        dense_score=match.dense_score,
        lexical_score=match.lexical_score,
    )


def build_evidence_grid(
    chroma_dir: Path,
    *,
    topic: str,
    subquestions: list[str] | None = None,
    dois: list[str] | None = None,
    section_filter: list[str] | None = None,
    max_papers: int = 8,
) -> EvidenceGridResult:
    """Construct a compact evidence grid for a topic and subquestions."""
    papers_dir = resolve_papers_dir(chroma_dir)
    resolved_subquestions = [question.strip() for question in (subquestions or []) if question.strip()] or [topic]
    scoped_dois = [value.strip() for value in (dois or []) if value.strip()]
    grids: list[EvidenceGridBlock] = []
    paper_counter: Counter[str] = Counter()

    for subquestion in resolved_subquestions:
        rows: list[EvidenceGridRow] = []
        query_candidates = [subquestion]
        if topic.strip() and _normalize_text(topic) != _normalize_text(subquestion):
            query_candidates.append(topic)

        if scoped_dois:
            scoped_query_dois = scoped_dois[:max_papers]
            for query_text in query_candidates:
                matches = search_papers(
                    chroma_dir,
                    query_text,
                    limit=max(1, len(scoped_query_dois)),
                    papers_dir=papers_dir,
                    dois=scoped_query_dois,
                    use_reranking=True,
                )
                matches_by_doi: dict[str, PaperSearchResult] = {}
                for match in matches:
                    match_doi = match.doi.strip().lower()
                    if match_doi and match_doi not in matches_by_doi:
                        matches_by_doi[match_doi] = match

                for scoped_doi in scoped_query_dois:
                    selected = matches_by_doi.get(scoped_doi.strip().lower())
                    if selected is None:
                        continue
                    if section_filter and not _section_matches(selected.section_name, section_filter):
                        continue
                    rows.append(_row_from_match(subquestion=subquestion, query_text=query_text, match=selected))
                    paper_counter[selected.doi] += 1
                if rows:
                    break
        else:
            for query_text in query_candidates:
                matches = search_papers(
                    chroma_dir,
                    query_text,
                    limit=max_papers,
                    papers_dir=papers_dir,
                    use_reranking=True,
                )
                if not matches:
                    continue
                for match in matches:
                    if section_filter and not _section_matches(match.section_name, section_filter):
                        continue
                    rows.append(_row_from_match(subquestion=subquestion, query_text=query_text, match=match))
                    paper_counter[match.doi] += 1
                if rows:
                    break
        grids.append(EvidenceGridBlock(subquestion=subquestion, rows=rows))

    return EvidenceGridResult(
        topic=topic,
        subquestions=resolved_subquestions,
        scoped_dois=scoped_dois,
        section_filter=section_filter or [],
        paper_coverage=dict(paper_counter),
        grids=grids,
    )


def _support_strength(score: float) -> str:
    if score >= 1.1:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"
