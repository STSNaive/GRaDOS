"""Local PDF library import helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from grados.config import GRaDOSPaths, load_config
from grados.extract.parse import parse_pdf_with_diagnostics
from grados.extract.qa import is_valid_paper_content
from grados.publisher.common import normalize_doi, safe_doi_filename
from grados.storage.papers import list_saved_papers, save_paper_markdown, save_pdf

_DOI_SEARCH_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)


@dataclass
class ImportItemResult:
    source_path: str
    status: str
    doi: str = ""
    safe_doi: str = ""
    title: str = ""
    detail: str = ""
    copied_pdf_path: str = ""
    warnings: list[str] = field(default_factory=list)
    debug: list[str] = field(default_factory=list)


@dataclass
class ImportLibraryResult:
    source_path: str
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)
    items: list[ImportItemResult] = field(default_factory=list)


async def import_local_pdf_library(
    source_path: Path,
    paths: GRaDOSPaths,
    *,
    recursive: bool = False,
    glob_pattern: str = "*.pdf",
    copy_to_library: bool = True,
) -> ImportLibraryResult:
    """Import a local PDF library into the canonical paper store."""
    source_path = source_path.expanduser().resolve()
    config = load_config(paths)
    result = ImportLibraryResult(source_path=str(source_path))

    pdf_files = _discover_pdf_files(source_path, recursive=recursive, glob_pattern=glob_pattern)
    existing_safe_dois = {
        item.safe_doi
        for item in list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
        if item.safe_doi
    }
    seen_hashes: set[str] = set()

    for pdf_file in pdf_files:
        result.scanned += 1
        pdf_bytes = pdf_file.read_bytes()

        if pdf_bytes[:5] != b"%PDF-":
            result.failed += 1
            result.items.append(
                ImportItemResult(
                    source_path=str(pdf_file),
                    status="failed",
                    detail="not_a_pdf",
                )
            )
            continue

        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        if pdf_hash in seen_hashes:
            result.skipped += 1
            result.items.append(
                ImportItemResult(
                    source_path=str(pdf_file),
                    status="skipped",
                    detail="duplicate_file_in_batch",
                )
            )
            continue
        seen_hashes.add(pdf_hash)

        parse_result = await parse_pdf_with_diagnostics(
            pdf_bytes,
            filename=pdf_file.name,
            parse_order=config.extract.parsing.order,
            parse_enabled=config.extract.parsing.enabled,
            marker_timeout=config.extract.parsing.marker_timeout,
        )
        parser_warnings = list(parse_result.warnings)
        parser_debug = list(parse_result.debug)
        if not parse_result.markdown:
            result.failed += 1
            result.warnings.extend(f"{pdf_file.name}: {warning}" for warning in parser_warnings)
            result.items.append(
                ImportItemResult(
                    source_path=str(pdf_file),
                    status="failed",
                    detail="parse_failed",
                    warnings=parser_warnings,
                    debug=parser_debug,
                )
            )
            continue
        parsed = parse_result.markdown

        doi = _infer_doi(parsed, pdf_file) or f"local-pdf/{pdf_hash[:16]}"
        safe_doi = safe_doi_filename(doi)
        if safe_doi in existing_safe_dois:
            result.skipped += 1
            result.items.append(
                ImportItemResult(
                    source_path=str(pdf_file),
                    status="skipped",
                    doi=doi,
                    safe_doi=safe_doi,
                    detail="duplicate_identifier",
                )
            )
            continue

        title = _infer_title(parsed, pdf_file)
        qa_ok = is_valid_paper_content(parsed, config.extract.qa.min_characters, title or None)
        item_warnings: list[str] = []
        item_debug: list[str] = parser_debug.copy()
        detail_tokens: list[str] = []
        if parser_warnings:
            result.warnings.extend(f"{pdf_file.name}: {warning}" for warning in parser_warnings)
            item_warnings.extend(parser_warnings)
            detail_tokens.append("parser_warning")
        if not qa_ok:
            warning = f"{pdf_file.name}: QA validation failed, imported with warning."
            result.warnings.append(warning)
            item_warnings.append("QA validation failed — imported anyway.")
            detail_tokens.append("qa_warning")

        copied_pdf_path = ""
        if copy_to_library:
            copied_pdf_path = str(save_pdf(doi, pdf_bytes, paths.downloads))

        summary = save_paper_markdown(
            doi=doi,
            markdown=parsed,
            papers_dir=paths.papers,
            title=title,
            source="Local PDF Library",
            fetch_outcome="local_import",
            extra_frontmatter={
                "original_pdf_path": str(pdf_file),
                "source_pdf_hash": pdf_hash,
            },
            chroma_dir=paths.database_chroma,
        )
        existing_safe_dois.add(summary.safe_doi)
        if summary.index_status == "failed":
            warning = (
                f"{pdf_file.name}: search index refresh failed after canonical mirror save. "
                f"Error: {summary.index_error}"
            )
            result.warnings.append(warning)
            item_warnings.append(
                f"Search index refresh failed — paper saved to papers/ only. Error: {summary.index_error}"
            )
            detail_tokens.append("index_warning")

        result.imported += 1
        result.items.append(
            ImportItemResult(
                source_path=str(pdf_file),
                status="imported_with_warnings" if item_warnings else "imported",
                doi=doi,
                safe_doi=summary.safe_doi,
                title=title,
                detail=",".join(detail_tokens),
                copied_pdf_path=copied_pdf_path,
                warnings=item_warnings,
                debug=item_debug,
            )
        )

    return result


def _discover_pdf_files(source_path: Path, *, recursive: bool, glob_pattern: str) -> list[Path]:
    if source_path.is_file():
        return [source_path] if source_path.suffix.lower() == ".pdf" else []
    if not source_path.is_dir():
        return []

    iterator = source_path.rglob(glob_pattern) if recursive else source_path.glob(glob_pattern)
    return sorted(path.resolve() for path in iterator if path.is_file() and path.suffix.lower() == ".pdf")


def _infer_doi(markdown: str, pdf_file: Path) -> str:
    search_space = "\n".join([pdf_file.name, pdf_file.stem, markdown[:12000]])
    matches = _DOI_SEARCH_PATTERN.findall(search_space)
    for match in matches:
        candidate = normalize_doi(match.rstrip(").,;]"))
        if candidate:
            return candidate
    return ""


def _infer_title(markdown: str, pdf_file: Path) -> str:
    heading_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip()

    for line in markdown.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped

    return pdf_file.stem.replace("_", " ").replace("-", " ").strip()
