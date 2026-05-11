"""Shared ingest workflow helpers for library-facing entry points."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from grados.config import GRaDOSPaths
from grados.extract.parse import ParsePipelineResult

if TYPE_CHECKING:
    from grados.config import IndexingConfig
    from grados.storage.assets import AssetLimits, PendingAsset
    from grados.storage.papers import PaperSavedSummary


@dataclass(frozen=True)
class LibraryDocumentArtifact:
    markdown: str | None
    parser_used: str = ""
    warnings: list[str] = field(default_factory=list)
    debug: list[str] = field(default_factory=list)
    assets: list[PendingAsset] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewedLibraryDocument:
    artifact: LibraryDocumentArtifact
    qa_passed: bool
    qa_warning_added: bool
    warnings: list[str] = field(default_factory=list)
    debug: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PersistedLibraryDocument:
    artifact: LibraryDocumentArtifact
    summary: PaperSavedSummary
    qa_passed: bool
    qa_warning_added: bool
    index_warning_added: bool
    warnings: list[str] = field(default_factory=list)
    debug: list[str] = field(default_factory=list)
    copied_pdf_path: str = ""
    asset_manifest_path: str = ""


async def build_library_document_artifact(
    producer: Callable[[], Awaitable[ParsePipelineResult]],
) -> LibraryDocumentArtifact:
    """Normalize parse/normalize pipeline output into one shared artifact."""
    result = await producer()
    return LibraryDocumentArtifact(
        markdown=result.markdown,
        parser_used=result.parser_used,
        warnings=list(result.warnings),
        debug=list(result.debug),
        assets=list(result.assets),
    )


def merge_library_diagnostics(
    artifact: LibraryDocumentArtifact,
    *,
    base_warnings: list[str] | None = None,
    base_debug: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    warnings = list(base_warnings or [])
    warnings.extend(artifact.warnings)
    debug = list(base_debug or [])
    debug.extend(artifact.debug)
    return warnings, debug


def review_library_document(
    artifact: LibraryDocumentArtifact,
    *,
    qa_validator: Callable[[str, int, str | None], bool],
    qa_min_characters: int,
    qa_expected_title: str | None = None,
    qa_warning_message: str = "",
    base_warnings: list[str] | None = None,
    base_debug: list[str] | None = None,
) -> ReviewedLibraryDocument:
    """Apply QA and aggregate warnings/debug before persistence or preview."""
    if artifact.markdown is None:
        raise ValueError("review_library_document requires artifact.markdown to be present")

    warnings, debug = merge_library_diagnostics(
        artifact,
        base_warnings=base_warnings,
        base_debug=base_debug,
    )
    qa_passed = qa_validator(artifact.markdown, qa_min_characters, qa_expected_title)
    qa_warning_added = False
    if not qa_passed and qa_warning_message:
        warnings.append(qa_warning_message)
        qa_warning_added = True

    return ReviewedLibraryDocument(
        artifact=artifact,
        qa_passed=qa_passed,
        qa_warning_added=qa_warning_added,
        warnings=warnings,
        debug=debug,
    )


def maybe_save_library_pdf(
    *,
    doi: str,
    pdf_bytes: bytes | None,
    paths: GRaDOSPaths,
    copy_to_library: bool,
) -> str:
    """Persist a raw PDF to the managed downloads archive when requested."""
    if pdf_bytes is None or not copy_to_library:
        return ""

    from grados.storage.papers import save_pdf

    return str(save_pdf(doi, pdf_bytes, paths.downloads))


def persist_reviewed_library_document(
    review: ReviewedLibraryDocument,
    *,
    paths: GRaDOSPaths,
    doi: str,
    title: str = "",
    source: str = "",
    publisher: str = "",
    fetch_outcome: str = "",
    extra_frontmatter: dict[str, str] | None = None,
    authors: list[str] | None = None,
    year: str = "",
    journal: str = "",
    asset_hints: list[dict[str, Any]] | None = None,
    asset_mode: str = "all",
    asset_limits: AssetLimits | None = None,
    copied_pdf_path: str = "",
    index_warning_message: str = "",
    indexing_config: IndexingConfig | None = None,
) -> PersistedLibraryDocument:
    """Persist reviewed markdown into canonical storage and refresh the index."""
    markdown = review.artifact.markdown
    if markdown is None:
        raise ValueError("persist_reviewed_library_document requires artifact.markdown to be present")

    from grados.storage.papers import save_asset_bundle, save_asset_manifest, save_paper_markdown

    warnings = list(review.warnings)
    debug = list(review.debug)
    frontmatter = dict(extra_frontmatter or {})
    asset_manifest_path = ""
    markdown_to_save = markdown

    if review.artifact.assets and asset_mode != "none":
        bundle_result = save_asset_bundle(
            doi,
            paths.papers,
            source=source,
            assets=review.artifact.assets,
            mode=asset_mode,
            limits=asset_limits,
        )
        warnings.extend(bundle_result.warnings)
        if bundle_result.skipped_count:
            warnings.append(
                f"Parser asset bundle skipped {bundle_result.skipped_count} assets due to configured limits."
            )
        if bundle_result.manifest_path:
            asset_manifest_path = bundle_result.manifest_path
            frontmatter["assets_manifest_path"] = bundle_result.manifest_path
            markdown_to_save = _rewrite_markdown_asset_refs(markdown_to_save, bundle_result.markdown_rewrites)

    if not asset_manifest_path and asset_hints:
        manifest_path = save_asset_manifest(
            doi,
            paths.papers,
            source=source,
            asset_hints=asset_hints,
        )
        if manifest_path:
            asset_manifest_path = manifest_path
            frontmatter["assets_manifest_path"] = manifest_path

    summary = save_paper_markdown(
        doi=doi,
        markdown=markdown_to_save,
        papers_dir=paths.papers,
        title=title,
        source=source,
        publisher=publisher,
        fetch_outcome=fetch_outcome,
        extra_frontmatter=frontmatter or None,
        chroma_dir=paths.database_chroma,
        authors=authors,
        year=year,
        journal=journal,
        indexing_config=indexing_config,
    )

    index_warning_added = False
    if summary.index_status == "failed" and index_warning_message:
        warnings.append(index_warning_message.format(index_error=summary.index_error))
        index_warning_added = True

    return PersistedLibraryDocument(
        artifact=review.artifact,
        summary=summary,
        qa_passed=review.qa_passed,
        qa_warning_added=review.qa_warning_added,
        index_warning_added=index_warning_added,
        warnings=warnings,
        debug=debug,
        copied_pdf_path=copied_pdf_path,
        asset_manifest_path=asset_manifest_path,
    )


def _rewrite_markdown_asset_refs(markdown: str, rewrites: dict[str, str]) -> str:
    result = markdown
    for original, replacement in sorted(rewrites.items(), key=lambda item: len(item[0]), reverse=True):
        if not original or not replacement:
            continue
        result = result.replace(f"]({original})", f"]({replacement})")
        result = result.replace(f"](<{original}>)", f"](<{replacement}>)")
        result = result.replace(f'src="{original}"', f'src="{replacement}"')
        result = result.replace(f"src='{original}'", f"src='{replacement}'")
    return result
