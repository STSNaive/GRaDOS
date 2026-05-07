"""Search-facing MCP tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from grados.config import GRaDOSConfig, GRaDOSPaths
from grados.server_tools.shared import get_api_keys, get_paths_and_config

__all__ = ["register_search_tools", "search_academic_papers", "search_saved_papers"]

INDEPTH_HARD_CANDIDATE_LIMIT = 8


def _resolved_indepth_enabled(config: object, override: bool | None) -> bool:
    if override is not None:
        return bool(override)
    research = getattr(config, "research", None)
    indepth = getattr(research, "indepth", None)
    return bool(getattr(indepth, "enabled", False))


def _indepth_auto_summarize(config: object) -> bool:
    research = getattr(config, "research", None)
    indepth = getattr(research, "indepth", None)
    return bool(getattr(indepth, "auto_summarize", True))


def _saved_paper_anchor_payload(query: str, paper: object) -> dict[str, object]:
    safe_doi = str(getattr(paper, "safe_doi", "") or "")
    paragraph_count = int(getattr(paper, "paragraph_count", 0) or 0)
    paragraph_start = int(getattr(paper, "paragraph_start", 0) or 0)
    return {
        "query_used": query,
        "canonical_uri": f"grados://papers/{safe_doi}" if safe_doi else "",
        "section_name": str(getattr(paper, "section_name", "") or ""),
        "paragraph_start": paragraph_start if paragraph_count > 0 else None,
        "paragraph_count": paragraph_count if paragraph_count > 0 else None,
        "score": round(float(getattr(paper, "score", 0.0) or 0.0), 6),
        "dense_score": round(float(getattr(paper, "dense_score", 0.0) or 0.0), 6),
        "lexical_score": round(float(getattr(paper, "lexical_score", 0.0) or 0.0), 6),
    }


def _local_state_for_paper(
    paths: GRaDOSPaths | None,
    paper: object,
    metadata_dir: Path | None,
    config: object,
) -> dict[str, object]:
    from grados.publisher.common import normalize_doi, safe_doi_filename
    from grados.research_checkpoint import paper_summary_status
    from grados.storage.papers import load_paper_record
    from grados.storage.remote_metadata import get_remote_metadata_by_doi

    doi = normalize_doi(str(getattr(paper, "doi", "") or ""))
    safe_doi = safe_doi_filename(doi) if doi else ""
    state: dict[str, object] = {
        "already_saved": False,
        "fetch_status": "metadata_only" if doi else "missing_doi",
        "has_fulltext": False,
        "paper_uri": "",
        "paper_summary_status": "not_applicable",
        "paper_id": safe_doi,
        "safe_doi": safe_doi,
    }
    if not doi or paths is None:
        return state

    record = None
    try:
        record = load_paper_record(getattr(paths, "papers"), doi=doi)
    except Exception:
        record = None
    if record is not None:
        state.update(
            {
                "already_saved": True,
                "fetch_status": "fulltext",
                "has_fulltext": True,
                "paper_uri": record.canonical_uri,
                "paper_id": record.safe_doi,
                "safe_doi": record.safe_doi,
            }
        )

    if metadata_dir is not None:
        try:
            remote = get_remote_metadata_by_doi(metadata_dir, doi)
        except Exception:
            remote = None
        if remote is not None:
            remote_status = remote.fetch_status or ""
            materialized_statuses = {"fulltext", "partial_success", "summary_failed", "challenge"}
            if not state.get("already_saved") or remote_status in materialized_statuses:
                state["fetch_status"] = remote_status or state["fetch_status"]
            state["has_fulltext"] = bool(state["has_fulltext"] or remote.has_fulltext)
            state["paper_id"] = remote.paper_id or state["paper_id"]
            state["safe_doi"] = remote.safe_doi or state["safe_doi"]
            if remote.has_fulltext and not state["paper_uri"] and remote.safe_doi:
                state["paper_uri"] = f"grados://papers/{remote.safe_doi}"

    summary_root = getattr(paths, "paper_summaries", None)
    papers_dir = getattr(paths, "papers", None)
    if summary_root is not None and papers_dir is not None:
        try:
            state["paper_summary_status"] = paper_summary_status(summary_root, papers_dir, doi=doi)
        except Exception:
            state["paper_summary_status"] = "stale"
    return state


def _format_local_state_line(state: dict[str, object]) -> str:
    parts = [
        f"already_saved={str(bool(state.get('already_saved'))).lower()}",
        f"fetch_status={state.get('fetch_status') or 'unknown'}",
        f"has_fulltext={str(bool(state.get('has_fulltext'))).lower()}",
        f"paper_summary_status={state.get('paper_summary_status') or 'unknown'}",
    ]
    if state.get("paper_uri"):
        parts.append(f"paper_uri={state['paper_uri']}")
    if state.get("paper_id"):
        parts.append(f"paper_id={state['paper_id']}")
    return "; ".join(parts)


def _receipt_fetch_status(receipt: str) -> str:
    lowered = receipt.lower()
    if "paper extracted with partial success" in lowered:
        return "partial_success"
    if "paper extracted successfully" in lowered:
        return "fulltext"
    if "metadata_only" in lowered:
        return "metadata_only"
    if "manual browser resume" in lowered or "publisher_challenge" in lowered or "captcha" in lowered:
        return "challenge"
    if "failed" in lowered:
        return "failed"
    return "metadata_only"


def _receipt_index_status(receipt: str) -> str:
    match = re.search(r"Index Status:\*\*\s*([^\n]+)", receipt)
    return match.group(1).strip() if match else ""


def _compact_failure_reason(receipt: str) -> str:
    for line in receipt.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return "No receipt returned."


async def _run_indepth_for_results(
    *,
    query: str,
    limit: int,
    papers: list[object],
    paths: GRaDOSPaths | None,
    config: GRaDOSConfig,
    metadata_dir: Path | None,
) -> tuple[list[str], str]:
    from grados.publisher.common import normalize_doi, safe_doi_filename
    from grados.research_checkpoint import (
        EvidenceAnchor,
        ResearchCheckpointPaper,
        generate_paper_summary,
        make_research_checkpoint,
        write_research_checkpoint,
    )
    from grados.server_tools import library_tools
    from grados.storage.remote_metadata import record_remote_fetch_result

    warnings: list[str] = []
    if paths is None:
        return ["indepth skipped because GRaDOS paths were unavailable."], ""

    candidates = [paper for paper in papers[:limit] if normalize_doi(str(getattr(paper, "doi", "") or ""))]
    if len(candidates) > INDEPTH_HARD_CANDIDATE_LIMIT:
        warnings.append(
            f"indepth processed the first {INDEPTH_HARD_CANDIDATE_LIMIT} returned candidates "
            f"from the same limit; {len(candidates) - INDEPTH_HARD_CANDIDATE_LIMIT} were left for later."
        )
        candidates = candidates[:INDEPTH_HARD_CANDIDATE_LIMIT]

    checkpoint_papers: list[ResearchCheckpointPaper] = []
    findings: list[str] = []
    evidence_anchors: list[EvidenceAnchor] = []
    open_questions: list[str] = []
    next_actions: list[str] = []
    summaries_written = 0
    fulltext_count = 0

    for paper in candidates:
        doi = normalize_doi(str(getattr(paper, "doi", "") or ""))
        safe_doi = safe_doi_filename(doi)
        title = str(getattr(paper, "title", "") or "")
        state = _local_state_for_paper(paths, paper, metadata_dir, config)
        receipt = ""
        fetch_status = str(state.get("fetch_status") or "metadata_only")
        failure_reason = ""

        if not state.get("already_saved"):
            try:
                receipt = await library_tools.extract_paper_full_text(
                    doi=doi,
                    publisher=str(getattr(paper, "publisher", "") or ""),
                    expected_title=title or None,
                )
                fetch_status = _receipt_fetch_status(receipt)
            except Exception as exc:
                fetch_status = "failed"
                failure_reason = f"{exc.__class__.__name__}: {exc}"
                warnings.append(f"indepth extraction failed for {doi}: {failure_reason}")
                if metadata_dir is not None:
                    try:
                        record_remote_fetch_result(
                            metadata_dir,
                            doi=doi,
                            fetch_status="failed",
                            has_fulltext=False,
                            source=str(getattr(paper, "source", "") or getattr(paper, "publisher", "") or ""),
                            title=title,
                            indexing_config=config.indexing,
                        )
                    except Exception as remote_exc:
                        warnings.append(
                            "Remote metadata failed update failed for "
                            f"{doi}: {remote_exc.__class__.__name__}: {remote_exc}"
                        )
        else:
            fetch_status = "fulltext"

        state = _local_state_for_paper(paths, paper, metadata_dir, config)
        if state.get("already_saved"):
            fulltext_count += 1
            paper_uri = str(state.get("paper_uri") or f"grados://papers/{safe_doi}")
            index_status = _receipt_index_status(receipt)
            paper_summary_id = ""
            if _indepth_auto_summarize(config):
                try:
                    paper_summary = generate_paper_summary(
                        getattr(paths, "paper_summaries"),
                        getattr(paths, "papers"),
                        doi=doi,
                    )
                    summaries_written += 1
                    paper_summary_id = paper_summary.summary_id
                    findings.extend(paper_summary.key_findings[:2])
                    evidence_anchors.extend(paper_summary.evidence_anchors[:4])
                except Exception as exc:
                    fetch_status = "summary_failed"
                    failure_reason = f"paper_summary failed: {exc.__class__.__name__}: {exc}"
                    warnings.append(f"{doi}: {failure_reason}")
                    if metadata_dir is not None:
                        try:
                            record_remote_fetch_result(
                                metadata_dir,
                                doi=doi,
                                fetch_status="summary_failed",
                                has_fulltext=True,
                                source=str(getattr(paper, "source", "") or getattr(paper, "publisher", "") or ""),
                                title=title,
                                indexing_config=config.indexing,
                            )
                        except Exception as remote_exc:
                            warnings.append(
                                "Remote metadata summary_failed update failed for "
                                f"{doi}: {remote_exc.__class__.__name__}: {remote_exc}"
                            )
            checkpoint_papers.append(
                ResearchCheckpointPaper(
                    doi=doi,
                    safe_doi=safe_doi,
                    paper_id=str(state.get("paper_id") or safe_doi),
                    title=title,
                    screening_status="candidate",
                    fetch_status=fetch_status,
                    paper_uri=paper_uri,
                    paper_summary_id=paper_summary_id,
                    index_status=index_status,
                    failure_reason=failure_reason,
                )
            )
        else:
            if not failure_reason:
                failure_reason = _compact_failure_reason(receipt)
            if fetch_status == "challenge":
                open_questions.append(f"Manual browser verification needed for {doi}.")
                next_actions.append(f"Retry extract_paper_full_text for {doi} with resume_browser=true.")
            elif fetch_status in {"failed", "metadata_only"}:
                open_questions.append(f"Full text not available yet for {doi}.")
                next_actions.append(f"Review metadata and retry another acquisition route for {doi}.")
            checkpoint_papers.append(
                ResearchCheckpointPaper(
                    doi=doi,
                    safe_doi=safe_doi,
                    paper_id=str(state.get("paper_id") or safe_doi),
                    title=title,
                    screening_status="candidate",
                    fetch_status=fetch_status,
                    paper_uri=str(state.get("paper_uri") or ""),
                    paper_summary_id="",
                    index_status=_receipt_index_status(receipt),
                    failure_reason=failure_reason,
                )
            )

    checkpoint = make_research_checkpoint(
        user_question=query,
        search_queries=[query],
        papers=checkpoint_papers,
        current_findings=_dedupe_keep_order(findings)[:8],
        evidence_anchors=evidence_anchors,
        open_questions=_dedupe_keep_order(open_questions),
        next_actions=_dedupe_keep_order(next_actions),
        warnings=warnings,
    )
    checkpoint_path = write_research_checkpoint(getattr(paths, "research_checkpoints"), checkpoint)
    summary = (
        "### Indepth Checkpoint\n"
        f"- Path: `{checkpoint_path}`\n"
        f"- Candidates processed: {len(candidates)}\n"
        f"- Full text available: {fulltext_count}\n"
        f"- Paper summaries written: {summaries_written}\n"
        "- Note: checkpoint and paper_summary content is navigation material; cite only after `read_saved_paper`."
    )
    return warnings, summary


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


async def search_academic_papers(
    query: Annotated[
        str,
        Field(min_length=1, description="Metadata search query. English keywords work best for source coverage."),
    ],
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum metadata results to return in this page."),
    ] = 15,
    continuation_token: Annotated[
        str | None,
        Field(description="Opaque token returned by a previous search_academic_papers call to continue that search."),
    ] = None,
    indepth: Annotated[
        bool | None,
        Field(
            description=(
                "Override research.indepth.enabled for this request. "
                "Default config is off; when enabled, GRaDOS attempts full-text materialization "
                "for the returned candidates using the same limit."
            )
        ),
    ] = None,
) -> str:
    """Search multiple academic databases sequentially and return deduplicated paper metadata."""
    from grados.search.resumable import run_resumable_search
    from grados.storage.remote_metadata import upsert_remote_metadata

    paths, config = get_paths_and_config()
    api_keys = get_api_keys(config)

    search_order = [source for source in config.search.order if config.search.enabled.get(source, True)]
    result = await run_resumable_search(
        query=query,
        limit=limit,
        continuation_token=continuation_token,
        search_order=search_order,
        api_keys=api_keys,
        etiquette_email=config.academic_etiquette_email,
    )
    warnings = list(result.warnings)
    if continuation_token and not result.continuation_applied:
        warnings.append(
            "Provided continuation_token was not applied; it was stale, invalid, or tied to a different query. "
            "Results restarted from page 1."
        )
    metadata_dir = paths.database_remote_metadata if paths is not None else None
    if metadata_dir is not None:
        try:
            upsert_remote_metadata(
                metadata_dir,
                list(result.results),
                indexing_config=config.indexing,
            )
        except Exception as exc:
            warnings.append(f"Remote metadata cache update failed: {exc.__class__.__name__}: {exc}")

    indepth_summary = ""
    if _resolved_indepth_enabled(config, indepth):
        indepth_warnings, indepth_summary = await _run_indepth_for_results(
            query=query,
            limit=limit,
            papers=list(result.results),
            paths=paths,
            config=config,
            metadata_dir=metadata_dir,
        )
        warnings.extend(indepth_warnings)

    papers_md = []
    for i, paper in enumerate(result.results, 1):
        local_state = _local_state_for_paper(paths, paper, metadata_dir, config)
        parts = [f"### {i}. {paper.title or '(No title)'}"]
        if paper.doi:
            parts.append(f"- DOI: `{paper.doi}`")
        parts.append(f"- Local State: {_format_local_state_line(local_state)}")
        if paper.publisher:
            parts.append(f"- Publisher: {paper.publisher}")
        if paper.year:
            parts.append(f"- Year: {paper.year}")
        if paper.url:
            parts.append(f"- URL: {paper.url}")
        if paper.authors:
            parts.append(f"- Authors: {', '.join(paper.authors[:6])}")
        if paper.abstract:
            parts.append(f"- Abstract: {paper.abstract[:800]}")
        papers_md.append("\n".join(parts))

    header = f"## Search Results for: {query}\n\nReturned {len(result.results)} papers"
    if result.has_more:
        header += " (more available)"
    header += "\n"
    if result.exhausted_sources:
        header += f"\nExhausted sources: {', '.join(result.exhausted_sources)}"
    if warnings:
        header += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)

    body = "\n\n".join(papers_md)
    footer = ""
    if result.next_continuation_token:
        footer = f"\n\n---\n**continuation_token:** `{result.next_continuation_token}`\n"
        footer += "Pass this token to get more results."

    if indepth_summary:
        footer = f"{footer}\n\n---\n{indepth_summary}"

    return header + "\n\n" + body + footer


async def search_saved_papers(
    query: Annotated[
        str,
        Field(min_length=1, description="Keyword or semantic search query over the local saved-paper library."),
    ],
    limit: Annotated[
        int,
        Field(ge=1, le=25, description="Maximum paper-level matches to return."),
    ] = 10,
    doi: Annotated[str | None, Field(description="Optional exact DOI filter.")] = None,
    authors: Annotated[str | None, Field(description="Optional author substring filter.")] = None,
    year_from: Annotated[
        int | None,
        Field(description="Optional inclusive lower bound for publication year."),
    ] = None,
    year_to: Annotated[
        int | None,
        Field(description="Optional inclusive upper bound for publication year."),
    ] = None,
    journal: Annotated[str | None, Field(description="Optional journal substring filter.")] = None,
    source: Annotated[
        str | None,
        Field(description="Optional source substring filter such as Crossref or Elsevier TDM."),
    ] = None,
    use_reranking: Annotated[
        bool,
        Field(description="Keep true to blend semantic retrieval with lightweight lexical reranking."),
    ] = True,
) -> str:
    """Search previously saved papers by keyword or semantic similarity."""
    from grados.storage.embedding import IndexCompatibilityError
    from grados.storage.papers import list_saved_papers, read_paper
    from grados.storage.vector import get_index_stats, search_papers

    if year_from is not None and year_to is not None and year_from > year_to:
        return "Invalid year range: year_from must be less than or equal to year_to."

    paths, config = get_paths_and_config()
    papers = list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
    if not papers:
        return "No saved papers found. Use extract_paper_full_text to save papers first."

    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)

    try:
        results = search_papers(
            paths.database_chroma,
            query,
            limit,
            papers_dir=paths.papers,
            doi=doi or "",
            authors=authors or "",
            year_from=year_from,
            year_to=year_to,
            journal=journal or "",
            source=source or "",
            use_reranking=use_reranking,
            indexing_config=config.indexing,
        )
    except IndexCompatibilityError as exc:
        return (
            "Semantic index requires a full rebuild before search can continue.\n\n"
            f"- Reason: {exc}\n"
            "- Action: run `grados reindex` from the CLI, then retry `search_saved_papers`."
        )
    except RuntimeError as exc:
        return (
            "Semantic retrieval runtime is not ready.\n\n"
            f"- Reason: {exc}\n"
            "- Action: install the embedding runtime and run `grados setup`."
        )

    filter_parts = []
    if doi:
        filter_parts.append(f"doi={doi}")
    if authors:
        filter_parts.append(f"authors~{authors}")
    if year_from is not None or year_to is not None:
        filter_parts.append(f"year={year_from or '-'}..{year_to or '-'}")
    if journal:
        filter_parts.append(f"journal~{journal}")
    if source:
        filter_parts.append(f"source~{source}")
    filters_suffix = f" | filters: {', '.join(filter_parts)}" if filter_parts else ""

    if not results:
        hint = " Run `grados update-db` to build retrieval chunks." if stats.total_chunks == 0 else ""
        return f"No papers matching '{query}' found among {len(papers)} saved papers.{hint}"

    mode = "hybrid reranked" if use_reranking else "dense"
    lines = [f"## Saved Paper Search: {query}{filters_suffix}\n"]
    lines.append(
        f"Found **{len(results)}** matches "
        f"({mode}, {stats.unique_papers} papers / {stats.total_chunks} chunks indexed):\n"
    )
    for i, paper in enumerate(results, 1):
        canonical_excerpt = ""
        paragraph_start = paper.paragraph_start
        paragraph_count = paper.paragraph_count
        if paper.safe_doi and paragraph_count > 0:
            canonical_window = read_paper(
                papers_dir=paths.papers,
                safe_doi=paper.safe_doi,
                start_paragraph=paragraph_start,
                max_paragraphs=paragraph_count,
            )
            if canonical_window:
                canonical_excerpt = " ".join(canonical_window.text.split())

        lines.append(f"{i}. **{paper.title or '(untitled)'}**  (score: {paper.score:.2f})")
        lines.append(f"   - DOI: {paper.doi}")
        lines.append(f"   - URI: grados://papers/{paper.safe_doi}")
        if paper.authors:
            lines.append(f"   - Authors: {', '.join(paper.authors[:4])}")
        if paper.year:
            lines.append(f"   - Year: {paper.year}")
        if paper.journal:
            lines.append(f"   - Journal: {paper.journal}")
        if paper.source:
            lines.append(f"   - Source: {paper.source}")
        if paper.section_name:
            lines.append(f"   - Section: {paper.section_name}")
        if paragraph_count > 0:
            start_label = paragraph_start + 1
            end_label = paragraph_start + paragraph_count
            lines.append(f"   - Paragraphs: {start_label}–{end_label}")
        anchor_payload = _saved_paper_anchor_payload(query, paper)
        lines.append(
            "   - Evidence Anchor: "
            f"`{json.dumps(anchor_payload, ensure_ascii=False, sort_keys=True)}`"
        )
        if canonical_excerpt:
            excerpt = canonical_excerpt[:280]
            if len(canonical_excerpt) > 280:
                excerpt += "..."
            lines.append(f"   - Canonical Excerpt: {excerpt}")
        elif paper.snippet:
            lines.append(f"   - Snippet: {paper.snippet}")

    return "\n".join(lines)


def register_search_tools(mcp: FastMCP) -> None:
    mcp.tool(
        description=(
            "Search remote academic databases for paper metadata only. "
            "Returns deduplicated titles, abstracts, DOIs, and a continuation token when more results are available; "
            "also exposes local saved/full-text/summary state. "
            "Use `indepth=true` only when you want GRaDOS to materialize returned candidates immediately."
        )
    )(search_academic_papers)

    mcp.tool(
        description=(
            "Search the local saved-paper library with semantic retrieval, metadata filters, "
            "and optional lexical reranking. "
            "Returned snippets and evidence anchors are screening/reranking material, not citation evidence; "
            "use `read_saved_paper` before citing."
        )
    )(search_saved_papers)
