"""Search-facing MCP tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from grados.server_tools.shared import get_api_keys, get_paths_and_config

__all__ = ["register_search_tools", "search_academic_papers", "search_saved_papers"]


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
) -> str:
    """Search multiple academic databases sequentially and return deduplicated paper metadata."""
    from grados.search.resumable import run_resumable_search

    _, config = get_paths_and_config()
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

    papers_md = []
    for i, paper in enumerate(result.results, 1):
        parts = [f"### {i}. {paper.title or '(No title)'}"]
        if paper.doi:
            parts.append(f"- DOI: `{paper.doi}`")
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
    if result.warnings:
        header += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in result.warnings)

    body = "\n\n".join(papers_md)
    footer = ""
    if result.next_continuation_token:
        footer = f"\n\n---\n**continuation_token:** `{result.next_continuation_token}`\n"
        footer += "Pass this token to get more results."

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
            "use `extract_paper_full_text` after screening relevant DOIs."
        )
    )(search_academic_papers)

    mcp.tool(
        description=(
            "Search the local saved-paper library with semantic retrieval, metadata filters, "
            "and optional lexical reranking. "
            "Returned snippets are screening hints, not citation evidence; use `read_saved_paper` before citing."
        )
    )(search_saved_papers)
