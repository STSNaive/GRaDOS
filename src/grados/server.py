"""GRaDOS MCP server: all tool definitions and handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

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


def _missing_paper_selector_message(doi: str | None, safe_doi: str | None, uri: str | None) -> str | None:
    """Return a user-facing error when no paper selector was provided."""
    if doi or safe_doi or uri:
        return None
    return "Provide at least one of doi, safe_doi, or uri."


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


@mcp.tool(
    description=(
        "Search remote academic databases for paper metadata only. "
        "Returns deduplicated titles, abstracts, DOIs, and a continuation token when more results are available; "
        "use `extract_paper_full_text` after screening relevant DOIs."
    )
)
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


@mcp.tool(
    description=(
        "Fetch, parse, and save one paper's canonical full text by DOI. "
        "Returns a compact save receipt with URI, file path, section headings, "
        "and warnings rather than the full paper text."
    )
)
async def extract_paper_full_text(
    doi: Annotated[str, Field(min_length=1, description="Paper DOI to fetch and save.")],
    publisher: Annotated[
        str | None,
        Field(description="Optional publisher label to persist in saved metadata; does not change fetch routing."),
    ] = None,
    expected_title: Annotated[
        str | None,
        Field(description="Optional title used for QA validation and saved metadata."),
    ] = None,
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


@mcp.tool(
    description=(
        "Read a paragraph window from a previously saved paper for canonical deep reading and citation verification. "
        "Provide one of `doi`, `safe_doi`, or `uri`; use `section_query` to jump near a heading."
    )
)
async def read_saved_paper(
    doi: Annotated[str | None, Field(description="Paper DOI. Provide this, safe_doi, or uri.")] = None,
    safe_doi: Annotated[
        str | None,
        Field(description="Sanitized DOI filename such as `10_1234_demo`. Provide this, doi, or uri."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo`."),
    ] = None,
    start_paragraph: Annotated[
        int,
        Field(ge=0, description="Zero-based paragraph offset for manual windowing."),
    ] = 0,
    max_paragraphs: Annotated[
        int,
        Field(ge=1, le=100, description="Paragraphs to return in this window."),
    ] = 20,
    section_query: Annotated[
        str | None,
        Field(description="Optional section name or substring to jump near before windowing."),
    ] = None,
    include_front_matter: Annotated[
        bool,
        Field(description="Include YAML front matter when reading from canonical markdown."),
    ] = False,
) -> str:
    """Read a previously saved paper with paragraph windowing.

    Supports DOI, safe_doi, or grados:// URI for identification.
    Use section_query to jump to a specific section, or start_paragraph for manual offset.
    """
    from grados.storage.papers import read_paper

    selector_error = _missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return selector_error

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


@mcp.tool(
    description=(
        "Return a low-token structure card for one saved paper. "
        "Use this to screen a paper before calling `read_saved_paper`; it is not the full citation source."
    )
)
async def get_saved_paper_structure(
    doi: Annotated[str | None, Field(description="Paper DOI. Provide this, safe_doi, or uri.")] = None,
    safe_doi: Annotated[
        str | None,
        Field(description="Sanitized DOI filename such as `10_1234_demo`. Provide this, doi, or uri."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo`."),
    ] = None,
) -> dict[str, object]:
    """Return a compact structure card for a saved paper.

    This is the low-token navigation path. Use read_saved_paper for canonical deep reading.
    """
    from grados.storage.papers import get_paper_structure

    selector_error = _missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return {"found": False, "message": selector_error}

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


@mcp.tool(
    description=(
        "Import a local PDF file or directory into GRaDOS canonical storage and the retrieval index. "
        "Returns a summary plus the first 25 item results."
    )
)
async def import_local_pdf_library(
    source_path: Annotated[
        str,
        Field(min_length=1, description="Local path to a PDF file or a directory containing PDFs."),
    ],
    recursive: Annotated[bool, Field(description="Recursively scan subdirectories for matching PDFs.")] = False,
    glob_pattern: Annotated[
        str,
        Field(min_length=1, description="Glob pattern used to discover PDFs inside the source directory."),
    ] = "*.pdf",
    copy_to_library: Annotated[
        bool,
        Field(description="Copy raw PDFs into the managed downloads archive before parsing."),
    ] = True,
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


@mcp.tool(
    description=(
        "Parse a local PDF into markdown. "
        "Without a DOI it returns a truncated preview; with a DOI it saves canonical markdown "
        "and returns a save receipt."
    )
)
async def parse_pdf_file(
    file_path: Annotated[str, Field(min_length=1, description="Local path to the PDF file to parse.")],
    expected_title: Annotated[
        str | None,
        Field(description="Optional title used for QA validation only."),
    ] = None,
    doi: Annotated[
        str | None,
        Field(description="Optional DOI to bind the parsed PDF to canonical storage and save it to the paper library."),
    ] = None,
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


@mcp.tool(
    description=(
        "Save one paper to Zotero via the Web API using the configured library settings. "
        "Best used for papers that actually support the final answer."
    )
)
async def save_paper_to_zotero(
    doi: Annotated[str, Field(min_length=1, description="Paper DOI.")],
    title: Annotated[str, Field(min_length=1, description="Paper title.")],
    authors: Annotated[list[str] | None, Field(description="Optional author display names.")] = None,
    abstract: Annotated[str | None, Field(description="Optional paper abstract.")] = None,
    journal: Annotated[str | None, Field(description="Optional journal name.")] = None,
    year: Annotated[str | None, Field(description="Optional publication year string.")] = None,
    url: Annotated[str | None, Field(description="Optional paper URL.")] = None,
    tags: Annotated[list[str] | None, Field(description="Optional Zotero tags.")] = None,
    collection_key: Annotated[
        str | None,
        Field(description="Optional Zotero collection key override."),
    ] = None,
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


@mcp.tool(
    description=(
        "Search the local saved-paper library with semantic retrieval, metadata filters, "
        "and optional lexical reranking. "
        "Returned snippets are screening hints, not citation evidence; use `read_saved_paper` before citing."
    )
)
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
    """Search previously saved papers by keyword or semantic similarity.

    Uses metadata prefiltering, ChromaDB retrieval, and paper-level reranking.
    """
    from grados.storage.embedding import IndexCompatibilityError
    from grados.storage.papers import list_saved_papers
    from grados.storage.vector import get_index_stats, search_papers

    if year_from is not None and year_to is not None and year_from > year_to:
        return "Invalid year range: year_from must be less than or equal to year_to."

    paths, config = _get_paths_and_config()
    papers = list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
    if not papers:
        return "No saved papers found. Use extract_paper_full_text to save papers first."

    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)

    try:
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
        if paper.get("section_name"):
            lines.append(f"   - Section: {paper.get('section_name')}")
        if paper.get("snippet"):
            lines.append(f"   - Snippet: {paper['snippet']}")

    return "\n".join(lines)


# ── Stage B research tools ───────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Save a structured research artifact produced during search, extraction, reading, or writing. "
        "Use this for reusable intermediate outputs such as search snapshots, extraction receipts, and evidence tables."
    )
)
async def save_research_artifact(
    kind: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Artifact kind such as `search_snapshot`, "
                "`extraction_receipt`, or `evidence_table`."
            ),
        ),
    ],
    content: Annotated[
        dict[str, object] | str,
        Field(description="Structured JSON-like content or markdown text for the artifact body."),
    ],
    title: Annotated[
        str | None,
        Field(description="Optional short label. If omitted, GRaDOS derives one from the artifact kind."),
    ] = None,
    source_doi: Annotated[
        str | None,
        Field(description="Optional DOI most directly associated with this artifact."),
    ] = None,
    metadata: Annotated[
        dict[str, object] | None,
        Field(description="Optional structured metadata such as query terms, filters, or audit settings."),
    ] = None,
) -> dict[str, object]:
    """Persist a reusable research artifact in the local state database."""
    from grados.research_state import save_research_artifact as persist_artifact

    paths, _ = _get_paths_and_config()
    return persist_artifact(
        paths.database_state,
        kind=kind,
        title=title or "",
        content=content,
        source_doi=source_doi or "",
        metadata=metadata,
    )


@mcp.tool(
    description=(
        "Query previously saved research artifacts by id, kind, or keyword. "
        "Set `detail=true` to load the full stored content."
    )
)
async def query_research_artifacts(
    artifact_id: Annotated[
        str | None,
        Field(description="Optional exact artifact id returned by `save_research_artifact`."),
    ] = None,
    kind: Annotated[
        str | None,
        Field(description="Optional artifact kind filter."),
    ] = None,
    query: Annotated[
        str | None,
        Field(description="Optional keyword query over artifact titles and stored content."),
    ] = None,
    detail: Annotated[
        bool,
        Field(description="Return full artifact content instead of previews."),
    ] = False,
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum artifacts to return."),
    ] = 20,
) -> dict[str, object]:
    """Query local research artifacts."""
    from grados.research_state import query_research_artifacts as run_query

    paths, _ = _get_paths_and_config()
    return run_query(
        paths.database_state,
        artifact_id=artifact_id or "",
        kind=kind or "",
        query=query or "",
        detail=detail,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Record, inspect, and summarize failed fetch/parse/search/citation attempts. "
        "Use `mode=suggest_retry` to get conservative next-step guidance from the local failure memory."
    )
)
async def manage_failure_cases(
    mode: Annotated[
        Literal["record", "query", "suggest_retry"],
        Field(description="Whether to record a failure, query history, or ask for retry suggestions."),
    ],
    failure_type: Annotated[
        str | None,
        Field(description="Optional failure category such as `fetch`, `parse`, `search`, or `citation`."),
    ] = None,
    doi: Annotated[
        str | None,
        Field(description="Optional DOI associated with the failure."),
    ] = None,
    query_text: Annotated[
        str | None,
        Field(description="Optional search query or draft fragment associated with the failure."),
    ] = None,
    source: Annotated[
        str | None,
        Field(description="Optional backend or publisher label associated with the failure."),
    ] = None,
    error_message: Annotated[
        str | None,
        Field(description="Optional raw error message. Especially useful with `mode=record` and `mode=suggest_retry`."),
    ] = None,
    context: Annotated[
        dict[str, object] | None,
        Field(description="Optional structured failure context such as filters, parser order, or citation style."),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum failure cases to return for query or retry analysis."),
    ] = 20,
) -> dict[str, object]:
    """Manage local failure memory."""
    from grados.research_state import manage_failure_cases as run_failure_memory

    paths, _ = _get_paths_and_config()
    return run_failure_memory(
        paths.database_state,
        mode=mode,
        failure_type=failure_type or "",
        doi=doi or "",
        query_text=query_text or "",
        source=source or "",
        error_message=error_message or "",
        context=context,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Return local citation relationships among saved papers. "
        "Supports paper neighborhoods, common references, and reverse "
        "citing-paper lookups without generating prose conclusions."
    )
)
async def get_citation_graph(
    mode: Annotated[
        Literal["neighbors", "common_references", "citing_papers"],
        Field(description="Which citation subquery to run."),
    ] = "neighbors",
    doi: Annotated[
        str | None,
        Field(description="Optional primary DOI. Use this for single-paper neighbor or citing-paper queries."),
    ] = None,
    dois: Annotated[
        list[str] | None,
        Field(description="Optional DOI list for multi-paper citation analysis such as common references."),
    ] = None,
    max_hops: Annotated[
        int,
        Field(ge=1, le=3, description="Only used by `neighbors`; expands local citation hops conservatively."),
    ] = 1,
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum relationship items to return."),
    ] = 20,
) -> dict[str, object]:
    """Return lightweight local citation graph data."""
    from grados.research_tools import get_citation_graph as run_citation_graph

    paths, _ = _get_paths_and_config()
    return run_citation_graph(
        paths.database_chroma,
        mode=mode,
        doi=doi or "",
        dois=dois,
        max_hops=max_hops,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Return structured full-context material for a small set of saved papers. "
        "Use `mode=estimate` to budget context first, then `mode=full` "
        "when you are ready to enter a CAG-style deep-reading pass."
    )
)
async def get_papers_full_context(
    dois: Annotated[
        list[str],
        Field(min_length=1, description="Saved-paper DOI list. Best for 1-8 papers you intend to read closely."),
    ],
    section_filter: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional section names to scope the returned context, "
                "such as `Abstract`, `Methods`, or `Results`."
            )
        ),
    ] = None,
    mode: Annotated[
        Literal["estimate", "full"],
        Field(description="Use `estimate` for token budgeting and `full` for actual section content."),
    ] = "estimate",
    max_total_tokens: Annotated[
        int,
        Field(ge=1000, le=128000, description="Approximate token budget across all returned papers when `mode=full`."),
    ] = 32000,
) -> dict[str, object]:
    """Return full-context material for a small saved-paper set."""
    from grados.research_tools import get_papers_full_context as run_full_context

    paths, _ = _get_paths_and_config()
    return run_full_context(
        paths.database_chroma,
        dois=dois,
        section_filter=section_filter,
        mode=mode,
        max_total_tokens=max_total_tokens,
    )


@mcp.tool(
    description=(
        "Build an evidence grid for a research topic or subquestions. "
        "Returns aligned paper-section-snippet rows so the agent can plan writing before drafting prose."
    )
)
async def build_evidence_grid(
    topic: Annotated[
        str,
        Field(min_length=1, description="Research topic or question that the evidence grid should organize."),
    ],
    subquestions: Annotated[
        list[str] | None,
        Field(description="Optional focused subquestions. If omitted, the topic itself is used as one query."),
    ] = None,
    dois: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional saved-paper DOI scope. When provided, GRaDOS "
                "only mines evidence from these papers."
            )
        ),
    ] = None,
    section_filter: Annotated[
        list[str] | None,
        Field(description="Optional section names to prefer while gathering evidence."),
    ] = None,
    max_papers: Annotated[
        int,
        Field(ge=1, le=12, description="Maximum paper hits to consider per subquestion."),
    ] = 8,
) -> dict[str, object]:
    """Construct an evidence grid for writing preparation."""
    from grados.research_tools import build_evidence_grid as run_evidence_grid

    paths, _ = _get_paths_and_config()
    return run_evidence_grid(
        paths.database_chroma,
        topic=topic,
        subquestions=subquestions,
        dois=dois,
        section_filter=section_filter,
        max_papers=max_papers,
    )


@mcp.tool(
    description=(
        "Extract parallel comparison material across saved papers. "
        "It aligns methods, results, or full-text excerpts into a table "
        "or bullet view, leaving higher-level comparison reasoning to "
        "the agent."
    )
)
async def compare_papers(
    dois: Annotated[
        list[str],
        Field(min_length=2, description="Saved-paper DOI list to compare side by side."),
    ],
    focus: Annotated[
        Literal["methods", "results", "full_text"],
        Field(description="Which paper aspect to align for comparison."),
    ] = "methods",
    comparison_axes: Annotated[
        list[str] | None,
        Field(description="Optional comparison axes such as dataset, metric, limitation, or objective."),
    ] = None,
    output_format: Annotated[
        Literal["table", "bullets"],
        Field(description="Preferred presentation for the aligned comparison payload."),
    ] = "table",
) -> dict[str, object]:
    """Compare saved papers without collapsing them into one narrative."""
    from grados.research_tools import compare_papers as run_compare_papers

    paths, _ = _get_paths_and_config()
    return run_compare_papers(
        paths.database_chroma,
        dois=dois,
        focus=focus,
        comparison_axes=comparison_axes,
        output_format=output_format,
    )


@mcp.tool(
    description=(
        "Audit draft claims against the local paper library. "
        "Returns claim-level `supported`, `weak`, `unsupported`, or "
        "`misattributed` statuses plus candidate evidence snippets."
    )
)
async def audit_draft_support(
    draft_text: Annotated[
        str,
        Field(min_length=1, description="Markdown or plain-text draft to audit claim by claim."),
    ],
    citation_style: Annotated[
        Literal["author_year", "numeric"],
        Field(description="Citation style used in the draft so GRaDOS can parse citation markers more accurately."),
    ] = "author_year",
    strictness: Annotated[
        Literal["strict", "balanced"],
        Field(
            description=(
                "Strict mode treats mismatched citations as "
                "`misattributed`; balanced mode softens that to `weak`."
            )
        ),
    ] = "strict",
    return_claim_map: Annotated[
        bool,
        Field(description="Include a compact claim-to-evidence map in addition to the full claim audit."),
    ] = True,
) -> dict[str, object]:
    """Audit whether a draft is supported by the local evidence store."""
    from grados.research_tools import audit_draft_support as run_audit

    paths, _ = _get_paths_and_config()
    return run_audit(
        paths.database_chroma,
        draft_text=draft_text,
        citation_style=citation_style,
        strictness=strictness,
        return_claim_map=return_claim_map,
    )


# ── Server runner ────────────────────────────────────────────────────────────


def run_server() -> None:
    """Start the MCP stdio server."""
    mcp.run()
