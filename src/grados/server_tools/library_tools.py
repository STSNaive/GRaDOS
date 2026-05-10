"""Library and paper-management MCP tools/resources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from grados.config import IndexingConfig
from grados.http_limits import SizeLimitError, ensure_byte_limit
from grados.publisher.common import PublisherMetadata, normalize_publisher_metadata, safe_doi_filename
from grados.server_tools.shared import (
    format_paper_index_resource,
    format_paper_overview_resource,
    get_paths_and_config,
    missing_paper_selector_message,
)
from grados.workflows.library import (
    build_library_document_artifact,
    maybe_save_library_pdf,
    merge_library_diagnostics,
    persist_reviewed_library_document,
    review_library_document,
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


def _format_asset_hint_lines(asset_hints: Sequence[Mapping[str, object]]) -> list[str]:
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
        f"- **Paper ID:** {safe_doi_filename(doi)}",
        f"- **Safe DOI:** {safe_doi_filename(doi)}",
        f"- **Source:** {source or 'Unknown'}",
        "- **Outcome:** metadata_only",
        "- **Fetch Status:** metadata_only",
        "- **Has Fulltext:** false",
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


def _append_remote_metadata_warning(result: str, warning: str | None) -> str:
    if not warning:
        return result
    return result + f"\n\n### Remote Metadata\n- {warning}"


def _append_manual_resume_receipt(result: str, fetch_result: object) -> str:
    manual = bool(getattr(fetch_result, "manual", False))
    resume = getattr(fetch_result, "resume", {}) or {}
    if not manual or not isinstance(resume, dict) or not resume:
        return result

    kind = str(resume.get("kind", "") or "")
    if kind == "codex":
        doi = str(resume.get("doi", "") or "")
        browser = str(resume.get("browser", "") or "Google Chrome")
        start_url = str(resume.get("start_url", "") or (f"https://doi.org/{doi}" if doi else ""))
        start_url_source = str(resume.get("start_url_source", "") or "")
        documentation_url = str(resume.get("documentation_url", "") or "")
        result += "\n\n### Codex Chrome Extension Download\n"
        result += f"- **Browser:** {browser}\n"
        if documentation_url:
            result += f"- **Setup:** {documentation_url}\n"
        if start_url:
            result += f"- **Start URL:** {start_url}\n"
        if start_url_source:
            result += f"- **Start URL Source:** {start_url_source}\n"
        result += (
            "- **Next:** use Chrome with the Codex extension to download the PDF, then call "
            "`parse_pdf_file(file_path=..., doi=..., copy_to_library=true, "
            "acquisition_via=\"codex\")` with the downloaded file path.\n"
        )
        return result.rstrip()

    host = str(getattr(fetch_result, "host", "") or resume.get("host", "") or "")
    url = str(resume.get("url", "") or "")
    profile_dir = str(resume.get("profile_dir", "") or "")
    action = str(resume.get("action", "complete_publisher_verification_then_retry") or "")
    result += "\n\n### Manual Browser Resume\n"
    if host:
        result += f"- **Host:** {host}\n"
    if url:
        result += f"- **URL:** {url}\n"
    if profile_dir:
        result += f"- **Profile:** {profile_dir}\n"
    if action:
        result += f"- **Action:** {action}\n"
    result += "- **Retry:** call `extract_paper_full_text` with `resume_browser=true` after verification.\n"
    return result.rstrip()


def _remote_metadata_dir(paths: object) -> Path:
    metadata_dir = getattr(paths, "database_remote_metadata", None)
    if isinstance(metadata_dir, Path):
        return metadata_dir
    chroma_dir = getattr(paths, "database_chroma")
    if isinstance(chroma_dir, Path):
        return chroma_dir
    return Path(chroma_dir)


def _load_browser_resume(paths: object, doi: str) -> dict[str, str] | None:
    from grados.storage.remote_metadata import get_remote_metadata_by_doi

    metadata_dirs = [_remote_metadata_dir(paths)]
    legacy_dir = getattr(paths, "database_chroma", None)
    if legacy_dir is not None and legacy_dir != metadata_dirs[0]:
        metadata_dirs.append(legacy_dir)

    record = None
    for metadata_dir in metadata_dirs:
        record = get_remote_metadata_by_doi(metadata_dir, doi)
        if record is not None:
            break
    if record is None or record.fetch_status != "challenge" or not record.fetch_manual:
        return None
    if not record.fetch_resume:
        return None
    try:
        loaded = json.loads(record.fetch_resume)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    resume = {str(key): str(value) for key, value in loaded.items() if str(value)}
    return resume or None


def _infer_remote_fetch_status(outcome: str, state: str, warnings: list[str]) -> str:
    if outcome == "metadata_only":
        return "metadata_only"
    if outcome == "host_action_required" or state == "host_action_required":
        return "host_action_required"
    if outcome in {"native_full_text", "pdf_obtained"}:
        return "fulltext"
    if state == "challenge":
        return "challenge"

    warning_text = " ".join(warnings).lower()
    challenge_markers = (
        "publisher_challenge",
        "challenge",
        "captcha",
        "are you a robot",
        "just a moment",
    )
    if any(marker in warning_text for marker in challenge_markers):
        return "challenge"
    return "failed"


def _record_remote_metadata_update(
    *,
    metadata_dir: Path,
    doi: str,
    fetch_status: str,
    has_fulltext: bool,
    source: str,
    title: str,
    metadata: PublisherMetadata | None,
    fetch_via: str = "",
    fetch_state: str = "",
    fetch_host: str = "",
    fetch_resume: dict[str, str] | None = None,
    fetch_manual: bool = False,
    indexing_config: IndexingConfig | None = None,
) -> str | None:
    from grados.storage.remote_metadata import record_remote_fetch_result

    try:
        record_remote_fetch_result(
            metadata_dir,
            doi=doi,
            fetch_status=fetch_status,
            has_fulltext=has_fulltext,
            source=source,
            title=title,
            metadata=metadata,
            fetch_via=fetch_via,
            fetch_state=fetch_state,
            fetch_host=fetch_host,
            fetch_resume=fetch_resume,
            fetch_manual=fetch_manual,
            indexing_config=indexing_config,
        )
    except Exception as exc:
        return f"Remote metadata cache update failed: {exc.__class__.__name__}: {exc}"
    return None


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
    resume_browser: Annotated[
        bool,
        Field(
            description=(
                "Resume a previous browser challenge for this DOI. When true, GRaDOS starts at the browser "
                "strategy and uses the saved publisher URL/profile instead of rerunning the full api-first chain."
            )
        ),
    ] = False,
) -> str:
    """Fetch, parse, and save one paper's canonical full text by DOI."""
    from grados.extract.fetch import fetch_paper
    from grados.extract.parse import normalize_document_text_with_diagnostics, parse_pdf_with_diagnostics
    from grados.extract.qa import is_valid_paper_content

    paths, config = get_paths_and_config()
    indexing_config = getattr(config, "indexing", None)
    api_keys = {k: v for k, v in config.api_keys.model_dump().items() if v}
    metadata_dir = _remote_metadata_dir(paths)
    browser_resume = _load_browser_resume(paths, doi) if resume_browser else None
    if resume_browser and browser_resume is None:
        browser_resume = {}

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
        browser_resume=browser_resume if resume_browser else None,
        unpaywall_enabled=bool(getattr(config.extract.unpaywall, "enabled", True)),
        max_remote_pdf_bytes=config.extract.security.max_remote_pdf_bytes,
        max_remote_text_bytes=config.extract.security.max_remote_text_bytes,
        max_browser_capture_bytes=config.extract.security.max_browser_capture_bytes,
    )

    metadata = normalize_publisher_metadata(fetch_result.metadata)
    if metadata is not None and expected_title and not metadata.title:
        metadata = metadata.model_copy(update={"title": expected_title})

    if fetch_result.outcome == "metadata_only":
        remote_warning = _record_remote_metadata_update(
            metadata_dir=metadata_dir,
            doi=doi,
            fetch_status="metadata_only",
            has_fulltext=False,
            source=fetch_result.source,
            title=expected_title or (metadata.title if metadata is not None else ""),
            metadata=metadata,
            fetch_via=fetch_result.via,
            fetch_state=fetch_result.state,
            fetch_host=fetch_result.host,
            fetch_resume=fetch_result.resume,
            fetch_manual=fetch_result.manual,
            indexing_config=indexing_config,
        )
        return _append_remote_metadata_warning(
            _metadata_only_receipt(
                doi,
                source=fetch_result.source,
                metadata=metadata,
                asset_hints=fetch_result.asset_hints,
                warnings=fetch_result.warnings,
            ),
            remote_warning,
        )

    if fetch_result.outcome not in ("native_full_text", "pdf_obtained"):
        remote_warning = _record_remote_metadata_update(
            metadata_dir=metadata_dir,
            doi=doi,
            fetch_status=_infer_remote_fetch_status(fetch_result.outcome, fetch_result.state, fetch_result.warnings),
            has_fulltext=False,
            source=fetch_result.source,
            title=expected_title or (metadata.title if metadata is not None else ""),
            metadata=metadata,
            fetch_via=fetch_result.via,
            fetch_state=fetch_result.state,
            fetch_host=fetch_result.host,
            fetch_resume=fetch_result.resume,
            fetch_manual=fetch_result.manual,
            indexing_config=indexing_config,
        )
        failed_result = _append_manual_resume_receipt(
            f"Failed to fetch paper: {doi}\nWarnings: " + "; ".join(fetch_result.warnings or []),
            fetch_result,
        )
        return _append_remote_metadata_warning(
            failed_result,
            remote_warning,
        )

    if fetch_result.outcome == "native_full_text":
        artifact = await build_library_document_artifact(
            lambda: normalize_document_text_with_diagnostics(
                fetch_result.text,
                content_format=fetch_result.text_format or "text",
                filename=f"{doi}.txt",
            )
        )
        if not artifact.markdown:
            remote_warning = _record_remote_metadata_update(
                metadata_dir=metadata_dir,
                doi=doi,
                fetch_status="failed",
                has_fulltext=True,
                source=fetch_result.source,
                title=expected_title or (metadata.title if metadata is not None else ""),
                metadata=metadata,
                fetch_via=fetch_result.via,
                fetch_state=fetch_result.state,
                fetch_host=fetch_result.host,
                fetch_resume=fetch_result.resume,
                fetch_manual=fetch_result.manual,
                indexing_config=indexing_config,
            )
            return _append_remote_metadata_warning(
                f"Failed to normalize native full text for {doi}",
                remote_warning,
            )
        copied_pdf_path = ""
    else:
        copied_pdf_path = maybe_save_library_pdf(
            doi=doi,
            pdf_bytes=fetch_result.pdf_buffer,
            paths=paths,
            copy_to_library=True,
        )
        artifact = await build_library_document_artifact(
            lambda: parse_pdf_with_diagnostics(
                fetch_result.pdf_buffer,
                filename=Path(copied_pdf_path).name,
                parse_order=config.extract.parsing.order,
                parse_enabled=config.extract.parsing.enabled,
                marker_timeout=config.extract.parsing.marker_timeout,
                mineru_api_key=config.api_keys.MINERU_API_KEY,
                mineru_model_version=config.extract.parsing.mineru_model_version,
                mineru_language=config.extract.parsing.mineru_language,
                mineru_timeout=config.extract.parsing.mineru_timeout,
                mineru_poll_interval=config.extract.parsing.mineru_poll_interval,
                mineru_enable_formula=config.extract.parsing.mineru_enable_formula,
                mineru_enable_table=config.extract.parsing.mineru_enable_table,
                mineru_is_ocr=config.extract.parsing.mineru_is_ocr,
                mineru_max_zip_bytes=config.extract.security.max_mineru_zip_bytes,
                mineru_max_full_md_bytes=config.extract.security.max_mineru_full_md_bytes,
            )
        )
        if not artifact.markdown:
            warnings, parser_debug = merge_library_diagnostics(
                artifact,
                base_warnings=fetch_result.warnings,
            )
            warning_block = "\n".join(f"- {warning}" for warning in warnings) if warnings else "- Unknown parse failure"
            debug_block = "\n".join(f"- {entry}" for entry in parser_debug)
            result = f"Failed to parse PDF for {doi}\n\nWarnings:\n{warning_block}"
            if debug_block:
                result += f"\n\nParser debug:\n{debug_block}"
            remote_warning = _record_remote_metadata_update(
                metadata_dir=metadata_dir,
                doi=doi,
                fetch_status="failed",
                has_fulltext=True,
                source=fetch_result.source,
                title=expected_title or (metadata.title if metadata is not None else ""),
                metadata=metadata,
                fetch_via=fetch_result.via,
                fetch_state=fetch_result.state,
                fetch_host=fetch_result.host,
                fetch_resume=fetch_result.resume,
                fetch_manual=fetch_result.manual,
                indexing_config=indexing_config,
            )
            return _append_remote_metadata_warning(result, remote_warning)

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

    review = review_library_document(
        artifact,
        qa_validator=is_valid_paper_content,
        qa_min_characters=config.extract.qa.min_characters,
        qa_expected_title=expected_title,
        qa_warning_message="QA validation failed — saved content may be incomplete.",
        base_warnings=list(fetch_result.warnings),
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi=doi,
        title=title,
        source=fetch_result.source,
        publisher=publisher or publisher_name,
        fetch_outcome=fetch_result.outcome,
        extra_frontmatter=extra_frontmatter or None,
        authors=authors,
        year=year,
        journal=journal,
        asset_hints=fetch_result.asset_hints,
        copied_pdf_path=copied_pdf_path,
        index_warning_message=(
            "Search index refresh failed — canonical markdown was saved to papers/ only. "
            "Error: {index_error}"
        ),
        indexing_config=indexing_config,
    )
    fetch_status = "partial_success" if persisted.index_warning_added else "fulltext"
    remote_warning = _record_remote_metadata_update(
        metadata_dir=metadata_dir,
        doi=doi,
        fetch_status=fetch_status,
        has_fulltext=True,
        source=fetch_result.source,
        title=title,
        metadata=metadata,
        fetch_via=fetch_result.via,
        fetch_state=fetch_result.state,
        fetch_host=fetch_result.host,
        fetch_resume=fetch_result.resume,
        fetch_manual=fetch_result.manual,
        indexing_config=indexing_config,
    )

    result = (
        "## Paper Extracted with Partial Success\n\n"
        if persisted.index_warning_added
        else "## Paper Extracted Successfully\n\n"
    )
    result += f"- **DOI:** {doi}\n"
    result += f"- **Paper ID:** {persisted.summary.safe_doi}\n"
    result += f"- **Safe DOI:** {persisted.summary.safe_doi}\n"
    result += f"- **URI:** {persisted.summary.uri}\n"
    result += f"- **File:** {persisted.summary.file_path}\n"
    result += f"- **Words:** {persisted.summary.word_count:,}\n"
    result += f"- **Characters:** {persisted.summary.char_count:,}\n"
    result += f"- **Source:** {fetch_result.source}\n"
    if fetch_result.via:
        result += f"- **Via:** {fetch_result.via}\n"
    result += f"- **Outcome:** {fetch_result.outcome}\n"
    if fetch_result.state:
        result += f"- **State:** {fetch_result.state}\n"
    result += f"- **Fetch Status:** {fetch_status}\n"
    result += "- **Has Fulltext:** true\n"
    result += f"- **Index Status:** {persisted.summary.index_status}\n"
    if persisted.artifact.parser_used:
        result += f"- **Parser Used:** {persisted.artifact.parser_used}\n"
    if persisted.summary.section_headings:
        result += "\n### Sections\n" + "\n".join(f"- {heading}" for heading in persisted.summary.section_headings)
    if persisted.warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {warning}" for warning in persisted.warnings)
    if persisted.debug:
        result += "\n\n### Parser Debug\n" + "\n".join(f"- {entry}" for entry in persisted.debug)
    return _append_remote_metadata_warning(result, remote_warning)


async def read_saved_paper(
    doi: Annotated[str | None, Field(description="Paper DOI. Provide this, safe_doi, or uri.")] = None,
    safe_doi: Annotated[
        str | None,
        Field(description="Opaque GRaDOS paper ID such as `10_1234_demo__51facb5bc98d`. Provide this, doi, or uri."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo__51facb5bc98d`."),
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
        Field(description="Opaque GRaDOS paper ID such as `10_1234_demo__51facb5bc98d`. Provide this, doi, or uri."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo__51facb5bc98d`."),
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
    copy_to_library: Annotated[
        bool,
        Field(description="Copy the raw PDF into the managed downloads archive when a DOI is provided."),
    ] = False,
    acquisition_via: Annotated[
        str | None,
        Field(description="Optional acquisition route label, such as `codex` or `local_pdf`."),
    ] = None,
) -> str:
    """Parse a local PDF file into markdown."""
    from grados.extract.parse import parse_pdf_with_diagnostics
    from grados.extract.qa import is_valid_paper_content

    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return f"File not found: {file_path}"

    paths, config = get_paths_and_config()
    try:
        ensure_byte_limit(
            path.stat().st_size,
            max_bytes=config.extract.security.max_local_pdf_bytes,
            label=f"Local PDF {path}",
        )
    except SizeLimitError as exc:
        return f"PDF file is too large: {exc}"

    pdf_buffer = path.read_bytes()
    if pdf_buffer[:5] != b"%PDF-":
        return f"Not a valid PDF file: {file_path}"
    pdf_hash = hashlib.sha256(pdf_buffer).hexdigest()

    artifact = await build_library_document_artifact(
        lambda: parse_pdf_with_diagnostics(
            pdf_buffer,
            filename=path.name,
            parse_order=config.extract.parsing.order,
            parse_enabled=config.extract.parsing.enabled,
            marker_timeout=config.extract.parsing.marker_timeout,
            mineru_api_key=config.api_keys.MINERU_API_KEY,
            mineru_model_version=config.extract.parsing.mineru_model_version,
            mineru_language=config.extract.parsing.mineru_language,
            mineru_timeout=config.extract.parsing.mineru_timeout,
            mineru_poll_interval=config.extract.parsing.mineru_poll_interval,
            mineru_enable_formula=config.extract.parsing.mineru_enable_formula,
            mineru_enable_table=config.extract.parsing.mineru_enable_table,
            mineru_is_ocr=config.extract.parsing.mineru_is_ocr,
            mineru_max_zip_bytes=config.extract.security.max_mineru_zip_bytes,
            mineru_max_full_md_bytes=config.extract.security.max_mineru_full_md_bytes,
        )
    )
    if not artifact.markdown:
        warnings, parser_debug = merge_library_diagnostics(artifact)
        result = f"All parsers failed for: {file_path}"
        if warnings:
            result += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)
        if parser_debug:
            result += "\n\nParser debug:\n" + "\n".join(f"- {entry}" for entry in parser_debug)
        return result

    review = review_library_document(
        artifact,
        qa_validator=is_valid_paper_content,
        qa_min_characters=config.extract.qa.min_characters,
        qa_expected_title=expected_title,
        qa_warning_message="QA validation failed — content may be incomplete.",
    )
    markdown = artifact.markdown

    if doi:
        normalized_acquisition = (acquisition_via or "").strip()
        source = "Codex Chrome Extension" if normalized_acquisition == "codex" else "Local PDF"
        copied_pdf_path = maybe_save_library_pdf(
            doi=doi,
            pdf_bytes=pdf_buffer,
            paths=paths,
            copy_to_library=copy_to_library,
        )
        extra_frontmatter = {
            "original_pdf_path": str(path),
            "source_pdf_hash": pdf_hash,
        }
        if copied_pdf_path:
            extra_frontmatter["copied_pdf_path"] = copied_pdf_path
        if normalized_acquisition:
            extra_frontmatter["acquisition_via"] = normalized_acquisition
        persisted = persist_reviewed_library_document(
            review,
            paths=paths,
            doi=doi,
            title=expected_title or "",
            source=source,
            fetch_outcome="local_parse",
            extra_frontmatter=extra_frontmatter,
            copied_pdf_path=copied_pdf_path,
            index_warning_message=(
                "Search index refresh failed — canonical markdown was saved to papers/ only. "
                "Error: {index_error}"
            ),
            indexing_config=config.indexing,
        )
        fetch_status = "partial_success" if persisted.index_warning_added else "fulltext"
        remote_warning = _record_remote_metadata_update(
            metadata_dir=_remote_metadata_dir(paths),
            doi=doi,
            fetch_status=fetch_status,
            has_fulltext=True,
            source=source,
            title=expected_title or "",
            metadata=None,
            fetch_via=normalized_acquisition or "local_pdf",
            fetch_state="ok",
            indexing_config=config.indexing,
        )
        result = (
            "## PDF Parsed & Saved with Partial Success\n\n"
            if persisted.index_warning_added
            else "## PDF Parsed & Saved\n\n"
        )
        result += f"- **DOI:** {doi}\n"
        result += f"- **URI:** {persisted.summary.uri}\n"
        result += f"- **File:** {persisted.summary.file_path}\n"
        if persisted.copied_pdf_path:
            result += f"- **Copied PDF:** {persisted.copied_pdf_path}\n"
        result += f"- **Source PDF Hash:** {pdf_hash}\n"
        if normalized_acquisition:
            result += f"- **Acquisition Via:** {normalized_acquisition}\n"
        result += f"- **Fetch Status:** {fetch_status}\n"
        result += f"- **Words:** {persisted.summary.word_count:,}\n"
        result += f"- **Index Status:** {persisted.summary.index_status}\n"
        if persisted.artifact.parser_used:
            result += f"- **Parser Used:** {persisted.artifact.parser_used}\n"
        warnings = persisted.warnings
        parser_debug = persisted.debug
        if remote_warning:
            warnings = [*warnings, remote_warning]
    else:
        result = "## PDF Parsed\n\n"
        if review.artifact.parser_used:
            result += f"- **Parser Used:** {review.artifact.parser_used}\n"
        result += f"- **Words:** {len(markdown.split()):,}\n"
        result += f"- **Characters:** {len(markdown):,}\n"
        result += f"\n---\n\n{markdown[:3000]}"
        if len(markdown) > 3000:
            result += f"\n\n... (truncated, {len(markdown):,} total chars)"
        warnings = review.warnings
        parser_debug = review.debug

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
            "and returns a save receipt. Use copy_to_library=true for host-agent downloaded PDFs "
            "that should be archived under downloads/."
        )
    )(parse_pdf_file)
