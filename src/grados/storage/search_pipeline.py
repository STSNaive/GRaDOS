"""Multi-retriever saved-paper search pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from grados.config import IndexingConfig
from grados.storage.fts import (
    FTSBlockResult,
    ensure_fts_index,
    fts_index_path,
    search_exact_blocks,
    search_fts_blocks,
)
from grados.storage.retrieval import PaperSearchResult, build_search_result, make_snippet, query_terms

RRF_K = 60


@dataclass(frozen=True)
class SearchPipelineResult:
    results: list[PaperSearchResult]
    mode: str
    retrievers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dense_error: str = ""
    fts_paper_count: int = 0
    fts_block_count: int = 0


@dataclass(frozen=True)
class _RankedCandidate:
    result: PaperSearchResult
    retriever: str
    rank: int
    raw_score: float


def search_saved_library(
    *,
    chroma_dir: Path,
    papers_dir: Path,
    query: str,
    limit: int = 10,
    doi: str = "",
    authors: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    journal: str = "",
    source: str = "",
    use_reranking: bool = True,
    indexing_config: IndexingConfig | None = None,
) -> SearchPipelineResult:
    """Search saved papers with dense, FTS/BM25, and exact candidates."""
    warnings: list[str] = []
    dense_candidates: list[PaperSearchResult] = []
    dense_error = ""

    try:
        dense_candidates = _dense_search(
            chroma_dir=chroma_dir,
            papers_dir=papers_dir,
            query=query,
            limit=limit,
            doi=doi,
            authors=authors,
            year_from=year_from,
            year_to=year_to,
            journal=journal,
            source=source,
            use_reranking=False,
            indexing_config=indexing_config,
        )
    except Exception as exc:  # noqa: BLE001 - fallback must capture runtime/index failures.
        dense_error = f"{exc.__class__.__name__}: {exc}" if str(exc).strip() else exc.__class__.__name__
        if use_reranking:
            warnings.append(f"Dense retriever unavailable; using lexical fallback. Reason: {dense_error}")
        else:
            warnings.append(f"Dense retriever unavailable; dense_only returned no results. Reason: {dense_error}")

    if not use_reranking:
        return SearchPipelineResult(
            results=_stamp_dense_only(dense_candidates[:limit], query=query),
            mode="dense_only",
            retrievers=["dense"] if dense_candidates else [],
            warnings=warnings,
            dense_error=dense_error,
        )

    fts_stats = ensure_fts_index(papers_dir=papers_dir, chroma_dir=chroma_dir)
    db_path = fts_index_path(chroma_dir)
    fts_limit = max(limit * 4, 20)
    fts_candidates = [
        _paper_result_from_fts(block)
        for block in search_fts_blocks(
            db_path=db_path,
            query=query,
            limit=fts_limit,
            doi=doi,
            authors=authors,
            year_from=year_from,
            year_to=year_to,
            journal=journal,
            source=source,
        )
    ]
    exact_candidates = [
        _paper_result_from_fts(block)
        for block in search_exact_blocks(
            db_path=db_path,
            query=query,
            limit=fts_limit,
            doi=doi,
            authors=authors,
            year_from=year_from,
            year_to=year_to,
            journal=journal,
            source=source,
        )
    ]

    if not dense_candidates and not fts_candidates and not exact_candidates:
        mode = "fts" if dense_error else "hybrid_rrf"
        return SearchPipelineResult(
            results=[],
            mode=mode,
            retrievers=[],
            warnings=warnings,
            dense_error=dense_error,
            fts_paper_count=fts_stats.paper_count,
            fts_block_count=fts_stats.block_count,
        )

    if not dense_candidates and fts_candidates and not exact_candidates:
        return SearchPipelineResult(
            results=_stamp_single_retriever(fts_candidates[:limit], mode="fts", retriever="fts_bm25", query=query),
            mode="fts",
            retrievers=["fts_bm25"],
            warnings=warnings,
            dense_error=dense_error,
            fts_paper_count=fts_stats.paper_count,
            fts_block_count=fts_stats.block_count,
        )

    if not dense_candidates and exact_candidates and not fts_candidates:
        return SearchPipelineResult(
            results=_stamp_single_retriever(exact_candidates[:limit], mode="fts", retriever="exact", query=query),
            mode="fts",
            retrievers=["exact"],
            warnings=warnings,
            dense_error=dense_error,
            fts_paper_count=fts_stats.paper_count,
            fts_block_count=fts_stats.block_count,
        )

    fused = _rrf_fuse(
        [
            ("dense", dense_candidates),
            ("fts_bm25", fts_candidates),
            ("exact", exact_candidates),
        ],
        query=query,
        limit=limit,
    )
    retrievers = [
        name
        for name, candidates in (
            ("dense", dense_candidates),
            ("fts_bm25", fts_candidates),
            ("exact", exact_candidates),
        )
        if candidates
    ]
    mode = "hybrid_rrf" if use_reranking and dense_candidates else "fts"
    if dense_error and mode == "fts_bm25":
        mode = "fts"
    fused = _stamp_mode(fused, mode=mode, query=query)

    return SearchPipelineResult(
        results=fused,
        mode=mode,
        retrievers=retrievers,
        warnings=warnings,
        dense_error=dense_error,
        fts_paper_count=fts_stats.paper_count,
        fts_block_count=fts_stats.block_count,
    )


def _dense_search(
    *,
    chroma_dir: Path,
    papers_dir: Path,
    query: str,
    limit: int,
    doi: str,
    authors: str,
    year_from: int | None,
    year_to: int | None,
    journal: str,
    source: str,
    use_reranking: bool,
    indexing_config: IndexingConfig | None,
) -> list[PaperSearchResult]:
    from grados.storage import vector

    return vector.search_papers(
        chroma_dir,
        query,
        max(limit * 3, limit),
        papers_dir=papers_dir,
        doi=doi,
        authors=authors,
        year_from=year_from,
        year_to=year_to,
        journal=journal,
        source=source,
        use_reranking=use_reranking,
        indexing_config=indexing_config or IndexingConfig(),
    )


def _stamp_dense_only(results: list[PaperSearchResult], *, query: str) -> list[PaperSearchResult]:
    stamped: list[PaperSearchResult] = []
    for index, result in enumerate(results, 1):
        stamped.append(
            replace(
                result,
                mode="dense_only",
                retriever="dense",
                rank=index,
                retrieval_score=result.score,
                query=query,
                trace={
                    "mode": "dense_only",
                    "retrievers": {
                        "dense": {
                            "rank": index,
                            "score": round(float(result.score or 0.0), 6),
                        }
                    },
                },
            )
        )
    return stamped


def _stamp_single_retriever(
    results: list[PaperSearchResult],
    *,
    mode: str,
    retriever: str,
    query: str,
) -> list[PaperSearchResult]:
    stamped: list[PaperSearchResult] = []
    for index, result in enumerate(results, 1):
        trace = dict(result.trace)
        trace.setdefault("mode", mode)
        trace.setdefault("retriever", retriever)
        trace.setdefault("rank", index)
        stamped.append(
            replace(
                result,
                mode=mode,
                retriever=retriever,
                rank=index,
                retrieval_score=result.score,
                query=query,
                trace=trace,
            )
        )
    return stamped


def _stamp_mode(results: list[PaperSearchResult], *, mode: str, query: str) -> list[PaperSearchResult]:
    stamped: list[PaperSearchResult] = []
    for result in results:
        trace = dict(result.trace)
        trace["mode"] = mode
        stamped.append(replace(result, mode=mode, query=query, trace=trace))
    return stamped


def _paper_result_from_fts(block: FTSBlockResult) -> PaperSearchResult:
    return build_search_result(
        record={
            "doi": block.doi,
            "title": block.title,
            "authors": block.authors,
            "year": block.year,
            "journal": block.journal,
            "source": block.source,
        },
        safe_doi=block.safe_doi,
        score=block.score,
        dense_score=0.0,
        doc_dense_score=0.0,
        chunk_dense_score=0.0,
        lexical_score=block.score,
        section_name=block.section_name,
        section_level=block.section_level,
        paragraph_start=block.paragraph_start,
        paragraph_count=block.paragraph_count,
        snippet=make_snippet(block.text, query_terms(block.query), "", max_chars=320),
        block_id=block.block_id,
        block_type=block.block_type,
        heading_path=block.heading_path,
        mode=block.retriever,
        retriever=block.retriever,
        rank=block.rank,
        retrieval_score=block.score,
        query=block.query,
        trace={
            "retriever": block.retriever,
            "rank": block.rank,
            "score": round(float(block.score or 0.0), 6),
            "raw_score": round(float(block.raw_score or 0.0), 6),
            "block_id": block.block_id,
            "block_type": block.block_type,
            "heading_path": block.heading_path,
        },
    )


def _rrf_fuse(
    ranked_lists: list[tuple[str, list[PaperSearchResult]]],
    *,
    query: str,
    limit: int,
) -> list[PaperSearchResult]:
    grouped: dict[str, list[_RankedCandidate]] = {}
    for retriever, results in ranked_lists:
        seen_in_retriever: set[str] = set()
        for rank, result in enumerate(results, 1):
            key = result.safe_doi
            if not key or key in seen_in_retriever:
                continue
            seen_in_retriever.add(key)
            grouped.setdefault(key, []).append(
                _RankedCandidate(
                    result=result,
                    retriever=retriever,
                    rank=int(getattr(result, "rank", 0) or rank),
                    raw_score=float(result.score or 0.0),
                )
            )

    fused_rows: list[tuple[float, str, PaperSearchResult, list[_RankedCandidate]]] = []
    for safe_doi, candidates in grouped.items():
        rrf_score = sum(1.0 / (RRF_K + candidate.rank) for candidate in candidates)
        primary_candidate = _select_primary_candidate(candidates)
        fused_rows.append((rrf_score, safe_doi, primary_candidate.result, candidates))

    sorted_rows = sorted(fused_rows, key=lambda item: (-item[0], item[1]))[:limit]
    output: list[PaperSearchResult] = []
    for final_rank, (rrf_score, _safe_doi, primary_result, candidates) in enumerate(sorted_rows, 1):
        retriever_trace = {
            candidate.retriever: {
                "rank": candidate.rank,
                "score": round(candidate.raw_score, 6),
                "rrf": round(1.0 / (RRF_K + candidate.rank), 8),
            }
            for candidate in candidates
        }
        dense_candidate = next((candidate.result for candidate in candidates if candidate.retriever == "dense"), None)
        lexical_candidate = next(
            (candidate.result for candidate in candidates if candidate.retriever in {"fts_bm25", "exact"}),
            None,
        )
        output.append(
            replace(
                primary_result,
                score=rrf_score,
                dense_score=float(getattr(dense_candidate, "dense_score", 0.0) or 0.0),
                doc_dense_score=float(getattr(dense_candidate, "doc_dense_score", 0.0) or 0.0),
                chunk_dense_score=float(getattr(dense_candidate, "chunk_dense_score", 0.0) or 0.0),
                lexical_score=float(getattr(lexical_candidate or primary_result, "lexical_score", 0.0) or 0.0),
                mode="hybrid_rrf",
                retriever="rrf",
                rank=final_rank,
                retrieval_score=rrf_score,
                query=query,
                trace={
                    "mode": "hybrid_rrf",
                    "rank": final_rank,
                    "score": round(rrf_score, 8),
                    "primary_retriever": primary_result.retriever or "dense",
                    "retrievers": retriever_trace,
                },
            )
        )
    return output


def _select_primary_candidate(candidates: list[_RankedCandidate]) -> _RankedCandidate:
    priority = {"exact": 3, "fts_bm25": 2, "dense": 1}
    return max(
        candidates,
        key=lambda candidate: (
            priority.get(candidate.retriever, 0),
            -candidate.rank,
            candidate.raw_score,
        ),
    )
