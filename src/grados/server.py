"""GRaDOS MCP server: all tool definitions and handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP

from grados import __version__
from grados.config import GRaDOSPaths, load_config

mcp = FastMCP(
    "GRaDOS",
    version=__version__,
    instructions="Academic research MCP server — search, extract, and manage papers",
)


def _get_paths_and_config():
    paths = GRaDOSPaths()
    config = load_config(paths)
    return paths, config


def _get_api_keys(config) -> dict[str, str]:
    keys = config.api_keys
    return {k: v for k, v in keys.model_dump().items() if v}


# ── search_academic_papers ───────────────────────────────────────────────────


@mcp.tool()
async def search_academic_papers(
    query: Annotated[str, "The search query"],
    limit: Annotated[int, "Maximum results to return"] = 15,
    continuation_token: Annotated[str | None, "Opaque token for resumable searches"] = None,
) -> str:
    """Search multiple academic databases sequentially and return deduplicated paper metadata.

    Supports resumable continuation via continuation_token for fetching more results.
    Sources: Crossref, PubMed, Web of Science, Elsevier (Scopus), Springer Nature.
    """
    from grados.search.resumable import run_resumable_search

    _, config = _get_paths_and_config()
    api_keys = _get_api_keys(config)

    search_order = config.search.order
    # Filter to enabled sources
    search_order = [s for s in search_order if config.search.enabled.get(s, True)]

    result = await run_resumable_search(
        query=query,
        limit=limit,
        continuation_token=continuation_token,
        search_order=search_order,
        api_keys=api_keys,
        etiquette_email=config.academic_etiquette_email,
    )

    # Format as structured response
    papers_md = []
    for i, p in enumerate(result.results, 1):
        parts = [f"### {i}. {p.title or '(No title)'}"]
        if p.doi:
            parts.append(f"- **DOI:** {p.doi}")
        if p.authors:
            parts.append(f"- **Authors:** {', '.join(p.authors[:5])}")
        if p.year:
            parts.append(f"- **Year:** {p.year}")
        if p.publisher:
            parts.append(f"- **Publisher:** {p.publisher}")
        if p.source:
            parts.append(f"- **Source:** {p.source}")
        if p.abstract:
            abstract = p.abstract[:300] + "..." if len(p.abstract) > 300 else p.abstract
            parts.append(f"- **Abstract:** {abstract}")
        if p.url:
            parts.append(f"- **URL:** {p.url}")
        papers_md.append("\n".join(parts))

    header = f"## Search Results for: {query}\n\n"
    header += f"Found **{len(result.results)}** papers"
    if result.has_more:
        header += " (more available)"
    header += "\n"
    if result.exhausted_sources:
        header += f"\nExhausted sources: {', '.join(result.exhausted_sources)}"
    if result.warnings:
        header += f"\n\nWarnings:\n" + "\n".join(f"- {w}" for w in result.warnings)

    body = "\n\n".join(papers_md)

    footer = ""
    if result.next_continuation_token:
        footer = f"\n\n---\n**continuation_token:** `{result.next_continuation_token}`\n"
        footer += "Pass this token to get more results."

    return header + "\n\n" + body + footer


# ── extract_paper_full_text ──────────────────────────────────────────────────


@mcp.tool()
async def extract_paper_full_text(
    doi: Annotated[str, "The paper DOI"],
    publisher: Annotated[str | None, "Publisher hint (e.g. 'Elsevier')"] = None,
    expected_title: Annotated[str | None, "Expected title for QA validation"] = None,
) -> str:
    """Extract full text from an academic paper by DOI.

    Tries multiple strategies: publisher APIs (TDM), Open Access, Sci-Hub, browser automation.
    Parses PDFs into markdown and saves to the papers directory with YAML frontmatter.
    """
    from grados.extract.fetch import fetch_paper
    from grados.extract.parse import parse_pdf
    from grados.extract.qa import is_valid_paper_content
    from grados.storage.papers import save_paper_markdown, save_pdf

    paths, config = _get_paths_and_config()
    api_keys = _get_api_keys(config)

    extract_cfg = config.extract
    fetch_result = await fetch_paper(
        doi=doi,
        api_keys=api_keys,
        etiquette_email=config.academic_etiquette_email,
        fetch_order=extract_cfg.fetch_strategy.order,
        fetch_enabled=extract_cfg.fetch_strategy.enabled,
        sci_hub_config=extract_cfg.sci_hub.model_dump(),
        headless_config=extract_cfg.headless_browser,
        paths=paths,
    )

    warnings = fetch_result.warnings.copy()
    markdown = ""

    if fetch_result.outcome == "native_full_text" and fetch_result.text:
        markdown = fetch_result.text
    elif fetch_result.outcome == "pdf_obtained" and fetch_result.pdf_buffer:
        # Save raw PDF
        save_pdf(doi, fetch_result.pdf_buffer, paths.downloads)

        # Parse PDF
        parsed = await parse_pdf(
            fetch_result.pdf_buffer,
            filename=f"{doi}.pdf",
            parse_order=extract_cfg.parsing.order,
            parse_enabled=extract_cfg.parsing.enabled,
            marker_timeout=extract_cfg.parsing.marker_timeout,
        )
        if parsed:
            markdown = parsed
        else:
            return f"Failed to parse PDF for DOI {doi}. PDF saved to downloads."
    else:
        return f"Failed to fetch paper for DOI {doi}. Outcome: {fetch_result.outcome}. {'; '.join(warnings)}"

    # QA validation
    if not is_valid_paper_content(markdown, extract_cfg.qa.min_characters, expected_title):
        warnings.append("QA validation failed — content may be incomplete or paywalled.")

    # Save
    summary = save_paper_markdown(
        doi=doi,
        markdown=markdown,
        papers_dir=paths.papers,
        title=expected_title or "",
        source=fetch_result.source,
        publisher=publisher or "",
        fetch_outcome=fetch_result.outcome,
        chroma_dir=paths.database_chroma,
    )

    result = f"## Paper Extracted Successfully\n\n"
    result += f"- **DOI:** {doi}\n"
    result += f"- **URI:** {summary.uri}\n"
    result += f"- **File:** {summary.file_path}\n"
    result += f"- **Words:** {summary.word_count:,}\n"
    result += f"- **Characters:** {summary.char_count:,}\n"
    result += f"- **Source:** {fetch_result.source}\n"
    result += f"- **Outcome:** {fetch_result.outcome}\n"
    if summary.section_headings:
        result += f"\n### Sections\n" + "\n".join(f"- {h}" for h in summary.section_headings)
    if warnings:
        result += f"\n\n### Warnings\n" + "\n".join(f"- {w}" for w in warnings)

    return result


# ── read_saved_paper ─────────────────────────────────────────────────────────


@mcp.tool()
async def read_saved_paper(
    doi: Annotated[str | None, "Paper DOI"] = None,
    safe_doi: Annotated[str | None, "Sanitized DOI filename"] = None,
    uri: Annotated[str | None, "Paper URI (grados://papers/...)"] = None,
    start_paragraph: Annotated[int, "Starting paragraph offset"] = 0,
    max_paragraphs: Annotated[int, "Number of paragraphs to return"] = 20,
    section_query: Annotated[str | None, "Jump to section matching this query"] = None,
    include_front_matter: Annotated[bool, "Include YAML front-matter"] = False,
) -> str:
    """Read a previously saved paper with paragraph windowing.

    Supports DOI, safe_doi, or grados:// URI for identification.
    Use section_query to jump to a specific section, or start_paragraph for manual offset.
    """
    from grados.storage.papers import read_paper

    paths, _ = _get_paths_and_config()
    result = read_paper(
        papers_dir=paths.papers,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
        start_paragraph=start_paragraph,
        max_paragraphs=max_paragraphs,
        section_query=section_query,
        include_front_matter=include_front_matter,
    )

    if not result:
        return f"Paper not found. doi={doi}, safe_doi={safe_doi}, uri={uri}"

    header = f"## Reading: {result.doi}\n\n"
    header += f"Paragraphs {result.start_paragraph + 1}–{result.start_paragraph + result.paragraph_count}"
    header += f" of {result.total_paragraphs}"
    if result.truncated:
        header += " (truncated — increase max_paragraphs or use start_paragraph to continue)"
    header += "\n\n---\n\n"

    footer = ""
    if result.section_headings:
        footer = "\n\n---\n### Available Sections\n" + "\n".join(f"- {h}" for h in result.section_headings)

    return header + result.text + footer


# ── parse_pdf_file ───────────────────────────────────────────────────────────


@mcp.tool()
async def parse_pdf_file(
    file_path: Annotated[str, "Path to the PDF file"],
    expected_title: Annotated[str | None, "Expected title for QA"] = None,
    doi: Annotated[str | None, "If provided, saves to papers directory with frontmatter"] = None,
) -> str:
    """Parse a local PDF file into markdown.

    If DOI is provided, saves the result to the papers directory with YAML frontmatter.
    """
    from grados.extract.parse import parse_pdf
    from grados.extract.qa import is_valid_paper_content
    from grados.storage.papers import save_paper_markdown

    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return f"File not found: {file_path}"

    pdf_buffer = path.read_bytes()
    if pdf_buffer[:5] != b"%PDF-":
        return f"Not a valid PDF file: {file_path}"

    paths, config = _get_paths_and_config()
    parsed = await parse_pdf(
        pdf_buffer,
        filename=path.name,
        parse_order=config.extract.parsing.order,
        parse_enabled=config.extract.parsing.enabled,
    )

    if not parsed:
        return f"All parsers failed for: {file_path}"

    warnings = []
    if not is_valid_paper_content(parsed, config.extract.qa.min_characters, expected_title):
        warnings.append("QA validation failed — content may be incomplete.")

    if doi:
        summary = save_paper_markdown(
            doi=doi,
            markdown=parsed,
            papers_dir=paths.papers,
            title=expected_title or "",
            source="Local PDF",
            fetch_outcome="local_parse",
            chroma_dir=paths.database_chroma,
        )
        result = f"## PDF Parsed & Saved\n\n"
        result += f"- **URI:** {summary.uri}\n"
        result += f"- **File:** {summary.file_path}\n"
        result += f"- **Words:** {summary.word_count:,}\n"
    else:
        result = f"## PDF Parsed\n\n"
        result += f"- **Words:** {len(parsed.split()):,}\n"
        result += f"- **Characters:** {len(parsed):,}\n"
        result += f"\n---\n\n{parsed[:3000]}"
        if len(parsed) > 3000:
            result += f"\n\n... (truncated, {len(parsed):,} total chars)"

    if warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {w}" for w in warnings)

    return result


# ── save_paper_to_zotero ─────────────────────────────────────────────────────


@mcp.tool()
async def save_paper_to_zotero(
    doi: Annotated[str, "Paper DOI"],
    title: Annotated[str, "Paper title"],
    authors: Annotated[list[str] | None, "Author names"] = None,
    abstract: Annotated[str | None, "Paper abstract"] = None,
    journal: Annotated[str | None, "Journal name"] = None,
    year: Annotated[str | None, "Publication year"] = None,
    url: Annotated[str | None, "Paper URL"] = None,
    tags: Annotated[list[str] | None, "Tags for the Zotero item"] = None,
    collection_key: Annotated[str | None, "Zotero collection key override"] = None,
) -> str:
    """Save a paper to your Zotero library via the Web API."""
    from grados.zotero import save_to_zotero

    _, config = _get_paths_and_config()
    zotero_cfg = config.zotero
    api_key = config.api_keys.ZOTERO_API_KEY

    result = await save_to_zotero(
        doi=doi,
        title=title,
        library_id=zotero_cfg.library_id,
        library_type=zotero_cfg.library_type,
        api_key=api_key,
        authors=authors,
        abstract=abstract or "",
        journal=journal or "",
        year=year or "",
        url=url or "",
        tags=tags,
        collection_key=collection_key or zotero_cfg.default_collection_key,
    )

    if result.success:
        return f"## Saved to Zotero\n\n- **Item key:** {result.item_key}\n- **Title:** {title}\n- **DOI:** {doi}"
    else:
        return f"## Zotero Save Failed\n\n- **Error:** {result.message}"


# ── search_saved_papers (placeholder for Phase 3) ────────────────────────────


@mcp.tool()
async def search_saved_papers(
    query: Annotated[str, "Search query"],
    limit: Annotated[int, "Maximum results"] = 10,
) -> str:
    """Search previously saved papers by keyword or semantic similarity.

    Uses ChromaDB for semantic search when available, falls back to keyword matching.
    """
    from grados.storage.papers import list_saved_papers
    from grados.storage.vector import get_index_stats, search_papers

    paths, _ = _get_paths_and_config()

    # Try ChromaDB semantic search first
    stats = get_index_stats(paths.database_chroma)
    if stats["total_chunks"] > 0:
        results = search_papers(paths.database_chroma, query, limit)
        if results:
            lines = [f"## Saved Paper Search: {query}\n"]
            lines.append(f"Found **{len(results)}** matches (semantic search, {stats['unique_papers']} papers indexed):\n")
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. **{r['title'] or '(untitled)'}**  (score: {r['score']:.2f})")
                lines.append(f"   - DOI: {r['doi']}")
                lines.append(f"   - URI: grados://papers/{r['safe_doi']}")
                if r.get("snippet"):
                    snippet = r["snippet"][:150].replace("\n", " ")
                    lines.append(f"   - Snippet: {snippet}...")
            return "\n".join(lines)

    # Keyword fallback
    papers = list_saved_papers(paths.papers)

    if not papers:
        return "No saved papers found. Use extract_paper_full_text to save papers first."

    query_lower = query.lower()
    matches = []
    for p in papers:
        score = 0
        for word in query_lower.split():
            if word in p.get("title", "").lower():
                score += 2
            if word in p.get("doi", "").lower():
                score += 1
        if score > 0:
            matches.append((score, p))

    matches.sort(key=lambda x: x[0], reverse=True)
    results_kw = matches[:limit]

    if not results_kw:
        hint = " Run `grados update-db` to enable semantic search." if stats["total_chunks"] == 0 else ""
        return f"No papers matching '{query}' found among {len(papers)} saved papers.{hint}"

    lines = [f"## Saved Paper Search: {query}\n\nFound **{len(results_kw)}** matches (keyword):\n"]
    for i, (score, p) in enumerate(results_kw, 1):
        lines.append(f"{i}. **{p.get('title', '(untitled)')}**")
        lines.append(f"   - DOI: {p.get('doi', '')}")
        lines.append(f"   - URI: grados://papers/{p.get('safe_doi', '')}")

    return "\n".join(lines)


# ── Server runner ────────────────────────────────────────────────────────────


def run_server() -> None:
    """Start the MCP stdio server."""
    mcp.run()
