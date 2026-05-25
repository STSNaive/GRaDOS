"""Shared ingest workflow helpers for library-facing entry points."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
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
    qa_passed: bool | None = None


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
    pdf_materialization: LibraryPdfMaterializationResult | None = None


@dataclass(frozen=True)
class LibraryPdfMaterializationResult:
    doi: str
    safe_doi: str
    input_pdf_path: str = ""
    input_pdf_name: str = ""
    input_pdf_hash: str = ""
    canonical_pdf_path: str = ""
    canonical_pdf_hash: str = ""
    action: str = "not_requested"
    outcome: str = "not_requested"
    warnings: list[str] = field(default_factory=list)
    conflict_existing_path: str = ""
    conflict_existing_hash: str = ""
    conflict_candidate_path: str = ""
    conflict_candidate_hash: str = ""

    @property
    def copied_pdf_path(self) -> str:
        return self.canonical_pdf_path if self.outcome == "success" and self.canonical_pdf_path else ""

    def as_sidecar(self, *, parse_outcome: str = "") -> dict[str, Any]:
        return {
            "input_pdf_path": self.input_pdf_path,
            "input_pdf_name": self.input_pdf_name,
            "input_pdf_hash": self.input_pdf_hash,
            "canonical_pdf_path": self.canonical_pdf_path,
            "canonical_pdf_hash": self.canonical_pdf_hash,
            "materialization_action": self.action,
            "materialization_outcome": self.outcome,
            "parse_outcome": parse_outcome,
            "warnings": list(self.warnings),
            "conflict_existing_path": self.conflict_existing_path,
            "conflict_existing_hash": self.conflict_existing_hash,
            "conflict_candidate_path": self.conflict_candidate_path,
            "conflict_candidate_hash": self.conflict_candidate_hash,
        }


_PDF_PROVENANCE_FRONTMATTER_KEYS = {
    "fetch_outcome",
    "original_pdf_path",
    "copied_pdf_path",
    "source_pdf_hash",
    "acquisition_via",
}


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
        qa_passed=result.qa_passed,
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
    result = materialize_library_pdf(
        doi=doi,
        paths=paths,
        input_path=None,
        pdf_bytes=pdf_bytes,
        copy_to_library=copy_to_library,
    )
    return result.copied_pdf_path


def materialize_library_pdf(
    *,
    doi: str,
    paths: GRaDOSPaths,
    input_path: Path | None = None,
    pdf_bytes: bytes | None = None,
    copy_to_library: bool,
) -> LibraryPdfMaterializationResult:
    """Materialize one DOI's managed PDF artifact without overwriting conflicts."""
    from grados.publisher.common import safe_doi_filename

    safe_doi = safe_doi_filename(doi)
    input_pdf_path = str(input_path) if input_path is not None else ""
    input_pdf_name = input_path.name if input_path is not None else ""
    input_pdf_hash = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes is not None else _sha256_file(input_path)
    canonical_path = paths.downloads / f"{safe_doi}.pdf"
    canonical_pdf_path = str(canonical_path)

    if pdf_bytes is None and input_path is None:
        return LibraryPdfMaterializationResult(doi=doi, safe_doi=safe_doi)
    if not copy_to_library:
        return LibraryPdfMaterializationResult(
            doi=doi,
            safe_doi=safe_doi,
            input_pdf_path=input_pdf_path,
            input_pdf_name=input_pdf_name,
            input_pdf_hash=input_pdf_hash,
            action="not_requested",
            outcome="not_requested",
        )

    paths.downloads.mkdir(parents=True, exist_ok=True)
    if canonical_path.exists():
        canonical_hash = _sha256_file(canonical_path)
        if canonical_hash and input_pdf_hash and canonical_hash == input_pdf_hash:
            return LibraryPdfMaterializationResult(
                doi=doi,
                safe_doi=safe_doi,
                input_pdf_path=input_pdf_path,
                input_pdf_name=input_pdf_name,
                input_pdf_hash=input_pdf_hash,
                canonical_pdf_path=canonical_pdf_path,
                canonical_pdf_hash=canonical_hash,
                action="reused",
                outcome="success",
            )
        conflict_candidate_path = input_pdf_path
        warnings: list[str] = []
        if pdf_bytes is not None and not conflict_candidate_path:
            conflict_dir = paths.downloads / "_conflicts"
            conflict_dir.mkdir(parents=True, exist_ok=True)
            conflict_path = conflict_dir / f"{safe_doi}.{input_pdf_hash[:12]}.pdf"
            if not conflict_path.exists():
                conflict_path.write_bytes(pdf_bytes)
            conflict_candidate_path = str(conflict_path)
            warnings.append("Candidate PDF bytes were preserved under downloads/_conflicts for manual review.")
        return LibraryPdfMaterializationResult(
            doi=doi,
            safe_doi=safe_doi,
            input_pdf_path=input_pdf_path,
            input_pdf_name=input_pdf_name,
            input_pdf_hash=input_pdf_hash,
            canonical_pdf_path=canonical_pdf_path,
            canonical_pdf_hash=canonical_hash,
            action="conflict",
            outcome="conflict",
            warnings=warnings,
            conflict_existing_path=canonical_pdf_path,
            conflict_existing_hash=canonical_hash,
            conflict_candidate_path=conflict_candidate_path,
            conflict_candidate_hash=input_pdf_hash,
        )

    action = "written"
    if (
        input_path is not None
        and _is_relative_to(input_path, paths.downloads)
        and not _is_saved_canonical_download_pdf(input_path, paths, target_safe_doi=safe_doi)
    ):
        input_path.rename(canonical_path)
        action = "renamed"
    elif input_path is not None:
        if pdf_bytes is not None:
            canonical_path.write_bytes(pdf_bytes)
        else:
            shutil.copy2(input_path, canonical_path)
        action = "copied"
    elif pdf_bytes is not None:
        canonical_path.write_bytes(pdf_bytes)

    canonical_hash = _sha256_file(canonical_path)
    return LibraryPdfMaterializationResult(
        doi=doi,
        safe_doi=safe_doi,
        input_pdf_path=input_pdf_path,
        input_pdf_name=input_pdf_name,
        input_pdf_hash=input_pdf_hash,
        canonical_pdf_path=canonical_pdf_path,
        canonical_pdf_hash=canonical_hash,
        action=action,
        outcome="success",
    )


def plan_duplicate_library_pdf_cleanup(paths: GRaDOSPaths) -> dict[str, Any]:
    """Return a dry-run report for noncanonical downloads that duplicate canonical PDFs."""
    from grados.storage.papers import list_saved_papers

    paths.ensure_directories()
    canonical_by_hash: dict[str, dict[str, str]] = {}
    for paper in list_saved_papers(paths.papers, chroma_dir=paths.database_chroma):
        if not paper.safe_doi:
            continue
        canonical_path = paths.downloads / f"{paper.safe_doi}.pdf"
        if not canonical_path.is_file():
            continue
        canonical_hash = _sha256_file(canonical_path)
        if canonical_hash:
            canonical_by_hash[canonical_hash] = {
                "doi": paper.doi,
                "safe_doi": paper.safe_doi,
                "canonical_pdf_path": str(canonical_path),
            }

    duplicates: list[dict[str, str]] = []
    for candidate in sorted(paths.downloads.glob("*.pdf")):
        candidate_hash = _sha256_file(candidate)
        canonical = canonical_by_hash.get(candidate_hash)
        if not canonical:
            continue
        if candidate.name == f"{canonical['safe_doi']}.pdf":
            continue
        duplicates.append({
            **canonical,
            "duplicate_pdf_path": str(candidate),
            "duplicate_pdf_hash": candidate_hash,
            "recommended_action": "move_duplicate_to_trash_after_user_confirmation",
        })

    return {
        "status": "dry_run",
        "scanned_dir": str(paths.downloads),
        "duplicate_count": len(duplicates),
        "duplicates": duplicates,
        "next_action": "review_report_then_confirm_trash_move",
    }


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
    pdf_materialization: LibraryPdfMaterializationResult | None = None,
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
    for key in _PDF_PROVENANCE_FRONTMATTER_KEYS:
        frontmatter.pop(key, None)
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

    safe_doi = ""
    try:
        from grados.storage.papers import resolve_safe_doi_for_write
        from grados.storage.parsed_sidecar import save_parsed_manifest

        safe_doi = resolve_safe_doi_for_write(paths.papers, doi)
        materialization_payload = pdf_materialization.as_sidecar(parse_outcome="success") if pdf_materialization else {}
        sidecar = save_parsed_manifest(
            paths.papers,
            doi=doi,
            safe_doi=safe_doi,
            markdown=markdown_to_save,
            parser=review.artifact.parser_used,
            canonical_markdown=f"{safe_doi}.md",
            assets_manifest_path=asset_manifest_path,
            materialization=materialization_payload,
            parse_outcome="success",
        )
        warnings.extend(sidecar.warnings)
        if sidecar.manifest_path:
            frontmatter["parsed_manifest_path"] = sidecar.manifest_path
    except Exception as exc:
        warnings.append(f"Parsed sidecar write failed: {exc.__class__.__name__}: {exc}")

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
        copied_pdf_path=pdf_materialization.copied_pdf_path if pdf_materialization else copied_pdf_path,
        asset_manifest_path=asset_manifest_path,
        pdf_materialization=pdf_materialization,
    )


def _sha256_file(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_saved_canonical_download_pdf(path: Path, paths: GRaDOSPaths, *, target_safe_doi: str) -> bool:
    if path.suffix.lower() != ".pdf" or path.stem == target_safe_doi:
        return False
    if (paths.papers / f"{path.stem}.md").is_file():
        return True
    try:
        from grados.storage.papers import list_saved_papers

        return any(paper.safe_doi == path.stem for paper in list_saved_papers(paths.papers))
    except Exception:
        return False


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
