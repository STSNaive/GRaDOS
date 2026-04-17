"""Library and paper-management MCP tools/resources."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from grados.publisher.common import PublisherMetadata, normalize_publisher_metadata
from grados.server_tools.shared import (
    format_paper_index_resource,
    format_paper_overview_resource,
    get_paths_and_config,
    missing_paper_selector_message,
)

__all__ = [
    "extract_paper_full_text",
    "get_saved_paper_structure",
    "import_local_pdf_library",
    "paper_overview_resource",
    "papers_index_resource",
    "parse_pdf_file",
    "read_saved_paper",
    "register_library_tools",
]


def _format_asset_hint_lines(asset_hints: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    for hint in asset_hints:
        label = str(hint.get("label", "")).strip() or str(hint.get("kind", "")).strip() or "asset_hint"
        target = str(hint.get("url", "")).strip() or str(hint.get("value", "")).strip()
        if target:
            lines.append(f"- {label}: {target}")
        else:
            lines.append(f"- {label}")
    return lines


def _metadata_only_receipt(
    doi: str,
    *,
    source: str,
    metadata: PublisherMetadata | None,
    asset_hints: list[dict[str, str]],
    warnings: list[str],
) -> str:
    lines = [
        "## Paper Located but Full Text Unavailable",
        "",
        f"- **DOI:** {doi}",
        f"- **Source:** {source or 'Unknown'}",
        "- **Outcome:** metadata_only",
        "- **Canonical Save:** not_written",
        "- **Index Status:** not_requested",
    ]

    if metadata is not None:
        if metadata.title:
            lines.append(f"- **Title:** {metadata.title}")
        if metadata.authors:
            lines.append(f"- **Authors:** {', '.join(metadata.authors[:8])}")
        if metadata.year:
            lines.append(f"- **Year:** {metadata.year}")
        if metadata.journal:
            lines.append(f"- **Journal:** {metadata.journal}")
        if metadata.publisher:
            lines.append(f"- **Publisher:** {metadata.publisher}")

    lines.extend(
        [
            "",
            "### Next Step",
            "- No canonical markdown was saved because no full text was obtained.",
            (
                "- Use the metadata and asset hints to decide whether to retry with another route "
                "or save the citation only."
            ),
        ]
    )

    if asset_hints:
        lines.extend(["", "### Asset Hints", *_format_asset_hint_lines(asset_hints)])
    if warnings:
        lines.extend(["", "### Warnings", *[f"- {warning}" for warning in warnings]])

    return "\n".join(lines)


def papers_index_resource() -> str:
    """Low-token index of saved papers."""
    from grados.storage.papers import list_saved_papers

    paths, _ = get_paths_and_config()
    papers = list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
    return format_paper_index_resource(papers)


def paper_overview_resource(safe_doi: str) -> str:
    """Overview card for a saved paper resource."""
    from grados.storage.papers import get_paper_structure

    paths, _ = get_paths_and_config()
    structure = get_paper_structure(
        papers_dir=paths.papers,
        safe_doi=safe_doi,
    )
    if not structure:
        return f"# Paper Not Found\n\nCould not resolve grados://papers/{safe_doi}"

    return format_paper_overview_resource(structure)


async def extract_paper_full_text(
    doi: Annotated[str, Field(min_length=1, description="Paper DOI to fetch and save.")],
    publisher: Annotated[
        str | None,
        Field(
            description=(
                "Optional publisher hint for reporting only. "
                "This does not change fetch routing; GRaDOS still follows the configured strategy order."
            )
        ),
    ] = None,
    expected_title: Annotated[
        str | None,
        Field(description="Optional title hint used for QA validation and save metadata only."),
    ] = None,
) -> str:
    """Fetch, parse, and save one paper's canonical full text by DOI."""
    from grados.extract.fetch import fetch_paper
    from grados.extract.parse import normalize_document_text_with_diagnostics, parse_pdf_with_diagnostics
    from grados.extract.qa import is_valid_paper_content
    from grados.storage.papers import save_asset_manifest, save_paper_markdown, save_pdf

    paths, config = get_paths_and_config()
    api_keys = {k: v for k, v in config.api_keys.model_dump().items() if v}

    fetch_result = await fetch_paper(
        doi=doi,
        api_keys=api_keys,
        etiquette_email=config.academic_etiquette_email,
        fetch_order=config.extract.fetch_strategy.order,
        fetch_enabled=config.extract.fetch_strategy.enabled,
        tdm_order=config.extract.tdm.order,
        tdm_enabled=config.extract.tdm.enabled,
        sci_hub_config=config.extract.sci_hub.model_dump(),
        headless_config=config.extract.headless_browser,
        paths=paths,
    )

    metadata = normalize_publisher_metadata(fetch_result.metadata)
    if metadata is not None and expected_title and not metadata.title:
        metadata = metadata.model_copy(update={"title": expected_title})

    if fetch_result.outcome == "metadata_only":
        return _metadata_only_receipt(
            doi,
            source=fetch_result.source,
            metadata=metadata,
            asset_hints=fetch_result.asset_hints,
            warnings=fetch_result.warnings,
        )

    if fetch_result.outcome not in ("native_full_text", "pdf_obtained"):
        return f"Failed to fetch paper: {doi}\nWarnings: " + "; ".join(fetch_result.warnings or [])

    warnings = list(fetch_result.warnings)
    parser_debug: list[str] = []
    parser_used = ""

    if fetch_result.outcome == "native_full_text":
        normalized = await normalize_document_text_with_diagnostics(
            fetch_result.text,
            content_format=fetch_result.text_format or "text",
            filename=f"{doi}.txt",
        )
        markdown = normalized.markdown
        warnings.extend(normalized.warnings)
        parser_debug.extend(normalized.debug)
        parser_used = normalized.parser_used
        if not markdown:
            return f"Failed to normalize native full text for {doi}"
    else:
        pdf_path = save_pdf(doi, fetch_result.pdf_buffer, paths.downloads)
        parsed = await parse_pdf_with_diagnostics(
            fetch_result.pdf_buffer,
            filename=pdf_path.name,
            parse_order=config.extract.parsing.order,
            parse_enabled=config.extract.parsing.enabled,
            marker_timeout=config.extract.parsing.marker_timeout,
        )
        warnings.extend(parsed.warnings)
        parser_debug.extend(parsed.debug)
        parser_used = parsed.parser_used
        if not parsed.markdown:
            warning_block = "\n".join(f"- {warning}" for warning in warnings) if warnings else "- Unknown parse failure"
            debug_block = "\n".join(f"- {entry}" for entry in parser_debug)
            result = f"Failed to parse PDF for {doi}\n\nWarnings:\n{warning_block}"
            if debug_block:
                result += f"\n\nParser debug:\n{debug_block}"
            return result
        markdown = parsed.markdown

    if not is_valid_paper_content(markdown, config.extract.qa.min_characters, expected_title):
        warnings.append("QA validation failed — saved content may be incomplete.")

    title = ""
    authors: list[str] = []
    year = ""
    journal = ""
    publisher_name = ""
    extra_frontmatter: dict[str, str] = {}

    if metadata is not None:
        dump = metadata.model_dump()
        title = str(dump.get("title", ""))
        authors = [str(value) for value in dump.get("authors", []) if str(value)]
        year = str(dump.get("year", ""))
        journal = str(dump.get("journal", ""))
        publisher_name = str(dump.get("publisher", ""))

    if expected_title and not title:
        title = expected_title

    if fetch_result.asset_hints:
        manifest_path = save_asset_manifest(
            doi,
            paths.papers,
            source=fetch_result.source,
            asset_hints=fetch_result.asset_hints,
        )
        if manifest_path:
            extra_frontmatter["assets_manifest_path"] = manifest_path

    summary = save_paper_markdown(
        doi=doi,
        markdown=markdown,
        papers_dir=paths.papers,
        title=title,
        source=fetch_result.source,
        publisher=publisher or publisher_name,
        fetch_outcome=fetch_result.outcome,
        extra_frontmatter=extra_frontmatter or None,
        chroma_dir=paths.database_chroma,
        authors=authors,
        year=year,
        journal=journal,
    )

    partial_success = False
    if summary.index_status == "failed":
        partial_success = True
        warnings.append(
            "Search index refresh failed — canonical markdown was saved to papers/ only. "
            f"Error: {summary.index_error}"
        )

    result = "## Paper Extracted with Partial Success\n\n" if partial_success else "## Paper Extracted Successfully\n\n"
    result += f"- **DOI:** {doi}\n"
    result += f"- **URI:** {summary.uri}\n"
    result += f"- **File:** {summary.file_path}\n"
    result += f"- **Words:** {summary.word_count:,}\n"
    result += f"- **Characters:** {summary.char_count:,}\n"
    result += f"- **Source:** {fetch_result.source}\n"
    result += f"- **Outcome:** {fetch_result.outcome}\n"
    result += f"- **Index Status:** {summary.index_status}\n"
    if parser_used:
        result += f"- **Parser Used:** {parser_used}\n"
    if summary.section_headings:
        result += "\n### Sections\n" + "\n".join(f"- {heading}" for heading in summary.section_headings)
    if warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {warning}" for warning in warnings)
    if parser_debug:
        result += "\n\n### Parser Debug\n" + "\n".join(f"- {entry}" for entry in parser_debug)

    return result


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
    """Read a previously saved paper with paragraph windowing."""
    from grados.storage.papers import read_paper

    selector_error = missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return selector_error

    paths, _ = get_paths_and_config()
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
        footer = "\n\n---\n### Available Sections\n" + "\n".join(f"- {heading}" for heading in result.section_headings)

    return header + result.text + footer


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
    """Return a compact structure card for a saved paper."""
    from grados.storage.papers import get_paper_structure

    selector_error = missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return {"found": False, "message": selector_error}

    paths, _ = get_paths_and_config()
    structure = get_paper_structure(
        papers_dir=paths.papers,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
    )
    if not structure:
        return {
            "found": False,
            "message": f"Paper not found. doi={doi}, safe_doi={safe_doi}, uri={uri}",
        }

    payload = asdict(structure)
    payload["found"] = True
    return payload


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

    paths, _ = get_paths_and_config()
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
            "warnings": item.warnings,
            "debug": item.debug,
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
    """Parse a local PDF file into markdown."""
    from grados.extract.parse import parse_pdf_with_diagnostics
    from grados.extract.qa import is_valid_paper_content
    from grados.storage.papers import save_paper_markdown

    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return f"File not found: {file_path}"

    pdf_buffer = path.read_bytes()
    if pdf_buffer[:5] != b"%PDF-":
        return f"Not a valid PDF file: {file_path}"

    paths, config = get_paths_and_config()
    parsed = await parse_pdf_with_diagnostics(
        pdf_buffer,
        filename=path.name,
        parse_order=config.extract.parsing.order,
        parse_enabled=config.extract.parsing.enabled,
        marker_timeout=config.extract.parsing.marker_timeout,
    )

    warnings = list(parsed.warnings)
    parser_debug = list(parsed.debug)
    parser_used = parsed.parser_used

    if not parsed.markdown:
        result = f"All parsers failed for: {file_path}"
        if warnings:
            result += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)
        if parser_debug:
            result += "\n\nParser debug:\n" + "\n".join(f"- {entry}" for entry in parser_debug)
        return result

    markdown = parsed.markdown
    if not is_valid_paper_content(markdown, config.extract.qa.min_characters, expected_title):
        warnings.append("QA validation failed — content may be incomplete.")

    if doi:
        summary = save_paper_markdown(
            doi=doi,
            markdown=markdown,
            papers_dir=paths.papers,
            title=expected_title or "",
            source="Local PDF",
            fetch_outcome="local_parse",
            chroma_dir=paths.database_chroma,
        )
        partial_success = False
        if summary.index_status == "failed":
            partial_success = True
            warnings.append(
                "Search index refresh failed — canonical markdown was saved to papers/ only. "
                f"Error: {summary.index_error}"
            )
        result = "## PDF Parsed & Saved with Partial Success\n\n" if partial_success else "## PDF Parsed & Saved\n\n"
        result += f"- **URI:** {summary.uri}\n"
        result += f"- **File:** {summary.file_path}\n"
        result += f"- **Words:** {summary.word_count:,}\n"
        result += f"- **Index Status:** {summary.index_status}\n"
        if parser_used:
            result += f"- **Parser Used:** {parser_used}\n"
    else:
        result = "## PDF Parsed\n\n"
        if parser_used:
            result += f"- **Parser Used:** {parser_used}\n"
        result += f"- **Words:** {len(markdown.split()):,}\n"
        result += f"- **Characters:** {len(markdown):,}\n"
        result += f"\n---\n\n{markdown[:3000]}"
        if len(markdown) > 3000:
            result += f"\n\n... (truncated, {len(markdown):,} total chars)"

    if warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {warning}" for warning in warnings)
    if parser_debug:
        result += "\n\n### Parser Debug\n" + "\n".join(f"- {entry}" for entry in parser_debug)

    return result


def register_library_tools(mcp: FastMCP) -> None:
    mcp.resource("grados://papers/index", mime_type="text/markdown")(papers_index_resource)
    mcp.resource("grados://papers/{safe_doi}", mime_type="text/markdown")(paper_overview_resource)

    mcp.tool(
        description=(
            "Fetch, parse, and save one paper's canonical full text by DOI. "
            "Returns a compact save receipt with URI, file path, section headings, "
            "and warnings rather than the full paper text."
        )
    )(extract_paper_full_text)

    mcp.tool(
        description=(
            "Read a paragraph window from a previously saved paper for canonical deep reading "
            "and citation verification. "
            "Provide one of `doi`, `safe_doi`, or `uri`; use `section_query` to jump near a heading."
        )
    )(read_saved_paper)

    mcp.tool(
        description=(
            "Return a low-token structure card for one saved paper. "
            "Use this to screen a paper before calling `read_saved_paper`; it is not the full citation source."
        )
    )(get_saved_paper_structure)

    mcp.tool(
        description=(
            "Import a local PDF file or directory into GRaDOS canonical storage and the retrieval index. "
            "Returns a summary plus the first 25 item results."
        )
    )(import_local_pdf_library)

    mcp.tool(
        description=(
            "Parse a local PDF into markdown. "
            "Without a DOI it returns a truncated preview; with a DOI it saves canonical markdown "
            "and returns a save receipt."
        )
    )(parse_pdf_file)
