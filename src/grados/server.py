"""GRaDOS MCP server: all tool definitions and handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP

from grados import __version__
from grados.config import GRaDOSConfig, GRaDOSPaths, load_config

__all__ = ["mcp"]

mcp = FastMCP(
    "GRaDOS",
    version=__version__,
    instructions="Academic research MCP server — search, extract, and manage papers",
)


def _get_paths_and_config() -> tuple[GRaDOSPaths, GRaDOSConfig]:
    paths = GRaDOSPaths()
    config = load_config(paths)
    return paths, config


def _get_api_keys(config: GRaDOSConfig) -> dict[str, str]:
    keys = config.api_keys
    return {k: v for k, v in keys.model_dump().items() if v}


def _format_paper_index_resource(papers: list[dict[str, str]]) -> str:
    lines = ["# GRaDOS Saved Papers Index", ""]
    if not papers:
        lines.append("No saved papers found.")
        return "\n".join(lines)

    lines.append(f"Total papers: {len(papers)}")
    lines.append("")
    for item in papers:
        title = item.get("title") or "(untitled)"
        lines.append(f"## {title}")
        lines.append(f"- DOI: {item.get('doi', '')}")
        lines.append(f"- URI: grados://papers/{item.get('safe_doi', '')}")
        lines.append("")

    return "\n".join(lines).strip()


def _format_paper_overview_resource(structure: dict[str, object]) -> str:
    lines = [f"# {structure.get('title') or structure.get('safe_doi')}", ""]
    lines.append(f"- DOI: {structure.get('doi', '')}")
    lines.append(f"- URI: {structure.get('canonical_uri', '')}")
    if structure.get("year"):
        lines.append(f"- Year: {structure.get('year')}")
    if structure.get("journal"):
        lines.append(f"- Journal: {structure.get('journal')}")
    if structure.get("source"):
        lines.append(f"- Source: {structure.get('source')}")
    if structure.get("word_count"):
        lines.append(f"- Word count: {structure.get('word_count')}")
    if structure.get("paragraph_count"):
        lines.append(f"- Paragraph count: {structure.get('paragraph_count')}")

    preview = str(structure.get("preview_excerpt", "") or "")
    if preview:
        lines.extend(["", "## Preview", "", preview])

    raw_headings = structure.get("section_headings")
    headings: list[object] = list(raw_headings) if isinstance(raw_headings, (list, tuple)) else []
    if headings:
        lines.extend(["", "## Sections", ""])
        lines.extend(f"- {heading}" for heading in headings)

    assets_summary = structure.get("assets_summary") or {}
    if isinstance(assets_summary, dict) and assets_summary.get("has_assets"):
        lines.extend(
            [
                "",
                "## Assets",
                "",
                f"- Manifest: {assets_summary.get('manifest_path', '')}",
                f"- Figures: {assets_summary.get('figures', 0)}",
                f"- Tables: {assets_summary.get('tables', 0)}",
                f"- Objects: {assets_summary.get('objects', 0)}",
            ]
        )

    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "Use `read_saved_paper` for canonical deep reading and citation verification.",
        ]
    )
    return "\n".join(lines).strip()


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
        header += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in result.warnings)

    body = "\n\n".join(papers_md)

    footer = ""
    if result.next_continuation_token:
        footer = f"\n\n---\n**continuation_token:** `{result.next_continuation_token}`\n"
        footer += "Pass this token to get more results."

    return header + "\n\n" + body + footer


# ── paper resources ──────────────────────────────────────────────────────────


@mcp.resource("grados://papers/index", mime_type="text/markdown")
def papers_index_resource() -> str:
    """Low-token index of saved papers."""
    from grados.storage.papers import list_saved_papers

    paths, _ = _get_paths_and_config()
    papers = list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
    return _format_paper_index_resource(papers)


@mcp.resource("grados://papers/{safe_doi}", mime_type="text/markdown")
def paper_overview_resource(safe_doi: str) -> str:
    """Overview card for a saved paper resource."""
    from grados.storage.papers import get_paper_structure

    paths, _ = _get_paths_and_config()
    structure = get_paper_structure(
        papers_dir=paths.papers,
        safe_doi=safe_doi,
        chroma_dir=paths.database_chroma,
    )
    if not structure:
        return f"# Paper Not Found\n\nCould not resolve grados://papers/{safe_doi}"

    return _format_paper_overview_resource(structure.__dict__)


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
    from grados.storage.papers import save_asset_manifest, save_paper_markdown, save_pdf

    paths, config = _get_paths_and_config()
    api_keys = _get_api_keys(config)

    extract_cfg = config.extract
    fetch_result = await fetch_paper(
        doi=doi,
        api_keys=api_keys,
        etiquette_email=config.academic_etiquette_email,
        fetch_order=extract_cfg.fetch_strategy.order,
        fetch_enabled=extract_cfg.fetch_strategy.enabled,
        tdm_order=extract_cfg.tdm.order,
        tdm_enabled=extract_cfg.tdm.enabled,
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

    assets_manifest_path = save_asset_manifest(
        doi=doi,
        papers_dir=paths.papers,
        source=fetch_result.source,
        asset_hints=fetch_result.asset_hints,
    )

    # Save
    summary = save_paper_markdown(
        doi=doi,
        markdown=markdown,
        papers_dir=paths.papers,
        title=expected_title or "",
        source=fetch_result.source,
        publisher=publisher or "",
        fetch_outcome=fetch_result.outcome,
        extra_frontmatter={"assets_manifest_path": assets_manifest_path} if assets_manifest_path else None,
        chroma_dir=paths.database_chroma,
    )

    result = "## Paper Extracted Successfully\n\n"
    result += f"- **DOI:** {doi}\n"
    result += f"- **URI:** {summary.uri}\n"
    result += f"- **File:** {summary.file_path}\n"
    result += f"- **Words:** {summary.word_count:,}\n"
    result += f"- **Characters:** {summary.char_count:,}\n"
    result += f"- **Source:** {fetch_result.source}\n"
    result += f"- **Outcome:** {fetch_result.outcome}\n"
    if summary.section_headings:
        result += "\n### Sections\n" + "\n".join(f"- {h}" for h in summary.section_headings)
    if warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {w}" for w in warnings)

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
        chroma_dir=paths.database_chroma,
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


# ── get_saved_paper_structure ────────────────────────────────────────────────


@mcp.tool()
async def get_saved_paper_structure(
    doi: Annotated[str | None, "Paper DOI"] = None,
    safe_doi: Annotated[str | None, "Sanitized DOI filename"] = None,
    uri: Annotated[str | None, "Paper URI (grados://papers/...)"] = None,
) -> dict[str, object]:
    """Return a compact structure card for a saved paper.

    This is the low-token navigation path. Use read_saved_paper for canonical deep reading.
    """
    from grados.storage.papers import get_paper_structure

    paths, _ = _get_paths_and_config()
    structure = get_paper_structure(
        papers_dir=paths.papers,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
        chroma_dir=paths.database_chroma,
    )
    if not structure:
        return {
            "found": False,
            "message": f"Paper not found. doi={doi}, safe_doi={safe_doi}, uri={uri}",
        }

    payload = structure.__dict__.copy()
    payload["found"] = True
    return payload


# ── import_local_pdf_library ─────────────────────────────────────────────────


@mcp.tool()
async def import_local_pdf_library(
    source_path: Annotated[str, "Path to a PDF file or a directory containing PDFs"],
    recursive: Annotated[bool, "Recursively scan subdirectories"] = False,
    glob_pattern: Annotated[str, "Glob pattern for PDF discovery"] = "*.pdf",
    copy_to_library: Annotated[bool, "Copy raw PDFs into the managed downloads archive"] = True,
) -> dict[str, object]:
    """Import a local PDF library into GRaDOS canonical storage."""
    from grados.importing import import_local_pdf_library as run_import

    paths, _ = _get_paths_and_config()
    result = await run_import(
        source_path=Path(source_path),
        paths=paths,
        recursive=recursive,
        glob_pattern=glob_pattern,
        copy_to_library=copy_to_library,
    )

    item_limit = 25
    items = [
        {
            "source_path": item.source_path,
            "status": item.status,
            "doi": item.doi,
            "safe_doi": item.safe_doi,
            "title": item.title,
            "detail": item.detail,
            "copied_pdf_path": item.copied_pdf_path,
        }
        for item in result.items[:item_limit]
    ]
    return {
        "source_path": result.source_path,
        "scanned": result.scanned,
        "imported": result.imported,
        "skipped": result.skipped,
        "failed": result.failed,
        "warnings": result.warnings,
        "items": items,
        "truncated_items": max(0, len(result.items) - item_limit),
    }


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
        result = "## PDF Parsed & Saved\n\n"
        result += f"- **URI:** {summary.uri}\n"
        result += f"- **File:** {summary.file_path}\n"
        result += f"- **Words:** {summary.word_count:,}\n"
    else:
        result = "## PDF Parsed\n\n"
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


# ── search_saved_papers ──────────────────────────────────────────────────────


@mcp.tool()
async def search_saved_papers(
    query: Annotated[str, "Search query"],
    limit: Annotated[int, "Maximum results"] = 10,
    doi: Annotated[str | None, "Exact DOI filter"] = None,
    authors: Annotated[str | None, "Author substring filter"] = None,
    year_from: Annotated[int | None, "Inclusive lower bound for publication year"] = None,
    year_to: Annotated[int | None, "Inclusive upper bound for publication year"] = None,
    journal: Annotated[str | None, "Journal substring filter"] = None,
    source: Annotated[str | None, "Source substring filter"] = None,
    use_reranking: Annotated[bool, "Apply lightweight lexical reranking after dense retrieval"] = True,
) -> str:
    """Search previously saved papers by keyword or semantic similarity.

    Uses metadata prefiltering, ChromaDB retrieval, and paper-level reranking.
    """
    from grados.storage.papers import list_saved_papers
    from grados.storage.vector import get_index_stats, search_papers

    paths, _ = _get_paths_and_config()
    stats = get_index_stats(paths.database_chroma)
    results = search_papers(
        paths.database_chroma,
        query,
        limit,
        doi=doi or "",
        authors=authors or "",
        year_from=year_from,
        year_to=year_to,
        journal=journal or "",
        source=source or "",
        use_reranking=use_reranking,
    )

    papers = list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
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

    if not papers:
        return "No saved papers found. Use extract_paper_full_text to save papers first."

    if not results:
        hint = " Run `grados update-db` to build retrieval chunks." if stats["total_chunks"] == 0 else ""
        return f"No papers matching '{query}' found among {len(papers)} saved papers.{hint}"

    mode = "hybrid reranked" if use_reranking else "dense"
    lines = [f"## Saved Paper Search: {query}{filters_suffix}\n"]
    lines.append(
        f"Found **{len(results)}** matches "
        f"({mode}, {stats['unique_papers']} papers / {stats['total_chunks']} chunks indexed):\n"
    )
    for i, paper in enumerate(results, 1):
        lines.append(f"{i}. **{paper.get('title') or '(untitled)'}**  (score: {paper.get('score', 0.0):.2f})")
        lines.append(f"   - DOI: {paper.get('doi', '')}")
        lines.append(f"   - URI: grados://papers/{paper.get('safe_doi', '')}")
        if paper.get("authors"):
            lines.append(f"   - Authors: {', '.join(paper['authors'][:4])}")
        if paper.get("year"):
            lines.append(f"   - Year: {paper.get('year')}")
        if paper.get("journal"):
            lines.append(f"   - Journal: {paper.get('journal')}")
        if paper.get("source"):
            lines.append(f"   - Source: {paper.get('source')}")
        if paper.get("snippet"):
            lines.append(f"   - Snippet: {paper['snippet']}")

    return "\n".join(lines)


# ── Server runner ────────────────────────────────────────────────────────────


def run_server() -> None:
    """Start the MCP stdio server."""
    mcp.run()
