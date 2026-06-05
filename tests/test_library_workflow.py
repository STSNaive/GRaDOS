from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from grados.config import GRaDOSPaths, IndexingConfig
from grados.extract.parse import ParsePipelineResult
from grados.publisher.common import safe_doi_filename
from grados.server_tools.library_tools import _pdf_materialization_conflict_receipt
from grados.storage.frontmatter import read_frontmatter_metadata
from grados.storage.remote_metadata import RemoteMetadataRecord
from grados.workflows.library import (
    LibraryDocumentArtifact,
    build_library_document_artifact,
    materialize_library_pdf,
    maybe_save_library_pdf,
    persist_reviewed_library_document,
    plan_duplicate_library_pdf_cleanup,
    review_library_document,
)


def test_build_library_document_artifact_and_save_pdf(tmp_path: Path) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()

    async def fake_producer() -> ParsePipelineResult:
        return ParsePipelineResult(
            markdown="# Demo\n\n## Abstract\n\nShared workflow artifact.",
            parser_used="Docling",
            warnings=["parser emitted partial text"],
            debug=["docling:ok"],
        )

    artifact = asyncio.run(build_library_document_artifact(fake_producer))
    saved_pdf = maybe_save_library_pdf(
        doi="10.1234/demo",
        pdf_bytes=b"%PDF-1.4\n%workflow",
        paths=paths,
        copy_to_library=True,
    )

    assert artifact == LibraryDocumentArtifact(
        markdown="# Demo\n\n## Abstract\n\nShared workflow artifact.",
        parser_used="Docling",
        warnings=["parser emitted partial text"],
        debug=["docling:ok"],
    )
    assert Path(saved_pdf).is_file()
    assert Path(saved_pdf).name == f"{safe_doi_filename('10.1234/demo')}.pdf"


def test_materialize_library_pdf_reuses_renames_copies_and_conflicts(tmp_path: Path) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    doi = "10.1234/materialize"
    safe = safe_doi_filename(doi)

    external = tmp_path / "publisher.pdf"
    external.write_bytes(b"%PDF-1.4\n%same")
    copied = materialize_library_pdf(
        doi=doi,
        paths=paths,
        input_path=external,
        pdf_bytes=external.read_bytes(),
        copy_to_library=True,
    )
    assert copied.action == "copied"
    assert copied.outcome == "success"
    assert Path(copied.canonical_pdf_path).name == f"{safe}.pdf"

    reused = materialize_library_pdf(
        doi=doi,
        paths=paths,
        input_path=external,
        pdf_bytes=external.read_bytes(),
        copy_to_library=True,
    )
    assert reused.action == "reused"
    assert reused.canonical_pdf_hash == copied.canonical_pdf_hash

    conflict_input = tmp_path / "different.pdf"
    conflict_input.write_bytes(b"%PDF-1.4\n%different")
    conflict = materialize_library_pdf(
        doi=doi,
        paths=paths,
        input_path=conflict_input,
        pdf_bytes=conflict_input.read_bytes(),
        copy_to_library=True,
    )
    assert conflict.action == "conflict"
    assert conflict.outcome == "conflict"
    assert Path(conflict.conflict_existing_path).is_file()
    assert Path(conflict.conflict_candidate_path).is_file()
    assert Path(conflict.conflict_existing_path).read_bytes() == b"%PDF-1.4\n%same"
    assert conflict_input.read_bytes() == b"%PDF-1.4\n%different"
    receipt = _pdf_materialization_conflict_receipt(
        doi,
        conflict,
        fetch_source=SimpleNamespace(
            session_id="pdf-session-1",
            capture={"source": "download"},
            via="browser",
            source="Browser",
        ),
    )
    assert "- **Existing Canonical PDF Size:** " in receipt
    assert "- **Existing Canonical PDF Mtime:** " in receipt
    assert "- **Candidate PDF Size:** " in receipt
    assert "- **Candidate PDF Mtime:** " in receipt
    assert "- **Source Session:** pdf-session-1" in receipt
    assert "- **Capture Source:** download" in receipt
    trace_receipt = _pdf_materialization_conflict_receipt(
        doi,
        conflict,
        fetch_source=SimpleNamespace(
            trace=[
                {
                    "browser_session_id": "pdf-session-2",
                    "capture": {"source": "cdp_response_body", "session_id": "pdf-session-2"},
                }
            ],
            capture={},
            via="browser",
            source="Browser",
        ),
    )
    assert "- **Source Session:** pdf-session-2" in trace_receipt
    assert "- **Capture Source:** cdp_response_body" in trace_receipt

    paths2 = GRaDOSPaths(tmp_path / "grados-home-2")
    paths2.ensure_directories()
    managed_candidate = paths2.downloads / "publisher-name.pdf"
    managed_candidate.write_bytes(b"%PDF-1.4\n%rename")
    renamed = materialize_library_pdf(
        doi=doi,
        paths=paths2,
        input_path=managed_candidate,
        pdf_bytes=managed_candidate.read_bytes(),
        copy_to_library=True,
    )
    assert renamed.action == "renamed"
    assert Path(renamed.canonical_pdf_path).is_file()
    assert not managed_candidate.exists()


def test_materialize_library_pdf_copies_existing_canonical_download_for_other_doi(tmp_path: Path) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    source_doi = "10.1234/source"
    target_doi = "10.1234/target"
    source_safe = safe_doi_filename(source_doi)
    target_safe = safe_doi_filename(target_doi)
    (paths.papers / f"{source_safe}.md").write_text(
        f"---\ndoi: {source_doi}\ntitle: Source\n---\n\n# Source\n\nBody",
        encoding="utf-8",
    )
    source_pdf = paths.downloads / f"{source_safe}.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n%source")

    copied = materialize_library_pdf(
        doi=target_doi,
        paths=paths,
        input_path=source_pdf,
        pdf_bytes=source_pdf.read_bytes(),
        copy_to_library=True,
    )

    assert copied.action == "copied"
    assert source_pdf.is_file()
    target_pdf = paths.downloads / f"{target_safe}.pdf"
    assert target_pdf.is_file()
    assert target_pdf.read_bytes() == source_pdf.read_bytes()


def test_plan_library_pdf_cleanup_reports_same_hash_duplicates(tmp_path: Path) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    doi = "10.1234/cleanup"
    save_paper = paths.papers / f"{safe_doi_filename(doi)}.md"
    save_paper.write_text(
        f"---\ndoi: {doi}\ntitle: Cleanup\n---\n\n# Cleanup\n\nBody",
        encoding="utf-8",
    )
    canonical = paths.downloads / f"{safe_doi_filename(doi)}.pdf"
    duplicate = paths.downloads / "publisher-copy.pdf"
    canonical.write_bytes(b"%PDF-1.4\n%same")
    duplicate.write_bytes(b"%PDF-1.4\n%same")

    report = plan_duplicate_library_pdf_cleanup(paths)

    assert report["status"] == "dry_run"
    assert report["duplicate_count"] == 1
    assert report["duplicates"][0]["duplicate_pdf_path"] == str(duplicate)
    assert duplicate.is_file()


def test_review_and_persist_library_document_applies_shared_contracts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()

    import grados.storage.vector as vector

    indexing_config = IndexingConfig(chunk_min_chars=25, chunk_max_chars=90)
    captured: dict[str, object] = {}

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        captured["indexing_config"] = kwargs.get("indexing_config")
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    artifact = LibraryDocumentArtifact(
        markdown="# Demo\n\n## Abstract\n\n" + ("content " * 80),
        parser_used="Docling",
        warnings=["parser emitted partial text"],
        debug=["docling:ok"],
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: False,
        qa_min_characters=50,
        qa_expected_title="Demo",
        qa_warning_message="QA validation failed — content may be incomplete.",
        base_warnings=["fetch warning"],
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/demo",
        title="Demo",
        source="Local PDF Library",
        fetch_outcome="local_import",
        extra_frontmatter={"source_pdf_hash": "abc123"},
        asset_hints=[{"kind": "figure_image", "url": "https://example.com/fig1.png"}],
        index_warning_message="Search index refresh failed — paper saved to papers/ only. Error: {index_error}",
        indexing_config=indexing_config,
    )

    assert persisted.qa_passed is False
    assert persisted.qa_warning_added is True
    assert persisted.index_warning_added is True
    assert Path(persisted.summary.file_path).is_file()
    assert persisted.asset_manifest_path == f"_assets/{safe_doi_filename('10.1234/demo')}.json"
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))
    parsed_manifest_path = saved_metadata["parsed_manifest_path"]
    parsed_manifest = paths.papers / parsed_manifest_path
    assert parsed_manifest.is_file()
    assert parsed_manifest_path == f"_parsed/{safe_doi_filename('10.1234/demo')}.json"
    assert persisted.warnings == [
        "fetch warning",
        "parser emitted partial text",
        "QA validation failed — content may be incomplete.",
        "Search index refresh failed — paper saved to papers/ only. Error: RuntimeError: embedding backend unavailable",
    ]
    assert persisted.debug == ["docling:ok"]
    assert captured["indexing_config"] is indexing_config


def test_persist_reviewed_library_document_completes_blank_metadata_from_exact_remote_doi(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "get_remote_metadata_by_doi",
        lambda metadata_dir, doi: RemoteMetadataRecord(
            doi=doi,
            title="Springer Completion",
            year="2026",
            journal="Meccanica",
            publisher="Springer Nature",
            source="Springer Nature",
        ),
    )

    artifact = LibraryDocumentArtifact(
        markdown="# Springer Completion\n\n## Abstract\n\n" + ("canonical content " * 40),
        parser_used="Docling",
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: True,
        qa_min_characters=50,
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/springer",
        title="Springer Completion",
        source="Springer TDM",
        fetch_outcome="native_full_text",
        year="",
        journal="",
        publisher="",
    )
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))
    completion_sources = json.loads(saved_metadata["metadata_completion_sources"])

    assert saved_metadata["year"] == "2026"
    assert saved_metadata["journal"] == "Meccanica"
    assert saved_metadata["publisher"] == "Springer Nature"
    assert completion_sources == {
        "journal": "Springer Nature",
        "publisher": "Springer Nature",
        "year": "Springer Nature",
    }


def test_persist_reviewed_library_document_does_not_overwrite_existing_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "get_remote_metadata_by_doi",
        lambda metadata_dir, doi: RemoteMetadataRecord(
            doi=doi,
            title="Conflict Completion",
            year="2026",
            journal="Meccanica",
            publisher="Springer Nature",
            source="Springer Nature",
        ),
    )

    artifact = LibraryDocumentArtifact(
        markdown="# Conflict Completion\n\n## Abstract\n\n" + ("canonical content " * 40),
        parser_used="Docling",
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: True,
        qa_min_characters=50,
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/conflict",
        title="Conflict Completion",
        source="Parser",
        fetch_outcome="native_full_text",
        year="2025",
        journal="Other Journal",
        publisher="Existing Publisher",
    )
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))

    assert saved_metadata["year"] == "2025"
    assert saved_metadata["journal"] == "Other Journal"
    assert saved_metadata["publisher"] == "Existing Publisher"
    assert "metadata_completion_sources" not in saved_metadata
    assert any("Remote metadata year conflict ignored" in warning for warning in persisted.warnings)
    assert any("Remote metadata journal conflict ignored" in warning for warning in persisted.warnings)
    assert any("Remote metadata publisher conflict ignored" in warning for warning in persisted.warnings)


def test_persist_reviewed_library_document_does_not_use_provider_as_publisher(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "get_remote_metadata_by_doi",
        lambda metadata_dir, doi: RemoteMetadataRecord(
            doi=doi,
            title="Provider Separated",
            year="2026",
            journal="Journal of Metadata",
            publisher="Actual Publisher",
            source="Crossref",
        ),
    )

    artifact = LibraryDocumentArtifact(
        markdown="# Provider Separated\n\n## Abstract\n\n" + ("canonical content " * 40),
        parser_used="Docling",
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: True,
        qa_min_characters=50,
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/provider-separated",
        title="Provider Separated",
        source="Parser",
        fetch_outcome="native_full_text",
        year="",
        journal="",
        publisher="",
    )
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))
    completion_sources = json.loads(saved_metadata["metadata_completion_sources"])

    assert saved_metadata["publisher"] == "Actual Publisher"
    assert saved_metadata["publisher"] != "Crossref"
    assert completion_sources["publisher"] == "Crossref"


def test_persist_reviewed_library_document_ignores_nonmatching_remote_doi(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "get_remote_metadata_by_doi",
        lambda metadata_dir, doi: RemoteMetadataRecord(
            doi="10.9999/other",
            title="Similar Title",
            year="2026",
            journal="Meccanica",
            publisher="Springer Nature",
            source="Springer Nature",
        ),
    )

    artifact = LibraryDocumentArtifact(
        markdown="# Similar Title\n\n## Abstract\n\n" + ("canonical content " * 40),
        parser_used="Docling",
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: True,
        qa_min_characters=50,
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/requested",
        title="Similar Title",
        source="Parser",
        fetch_outcome="native_full_text",
        year="",
        journal="",
        publisher="",
    )
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))

    assert saved_metadata.get("year", "") == ""
    assert saved_metadata.get("journal", "") == ""
    assert saved_metadata.get("publisher", "") == ""
    assert "metadata_completion_sources" not in saved_metadata


def test_persist_reviewed_library_document_reports_remote_api_field_absent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "get_remote_metadata_by_doi",
        lambda metadata_dir, doi: RemoteMetadataRecord(
            doi=doi,
            title="Missing Remote Fields",
            year="",
            journal="",
            publisher="Springer Nature",
            source="Springer Nature",
        ),
    )

    artifact = LibraryDocumentArtifact(
        markdown="# Missing Remote Fields\n\n## Abstract\n\n" + ("canonical content " * 40),
        parser_used="Docling",
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: True,
        qa_min_characters=50,
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/missing-fields",
        title="Missing Remote Fields",
        source="Parser",
        fetch_outcome="native_full_text",
        year="",
        journal="",
        publisher="",
    )
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))

    assert saved_metadata.get("year", "") == ""
    assert saved_metadata.get("journal", "") == ""
    assert saved_metadata["publisher"] == "Springer Nature"
    assert "metadata_completion_sources" in saved_metadata
    assert any(
        "Remote metadata api_field_absent for 10.1234/missing-fields: year, journal" in warning
        for warning in persisted.warnings
    )


def test_persist_reviewed_library_document_ignores_non_metadata_handoff_record(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    paths = GRaDOSPaths(tmp_path / "grados-home")
    paths.ensure_directories()
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "get_remote_metadata_by_doi",
        lambda metadata_dir, doi: SimpleNamespace(
            fetch_status="host_action_required",
            fetch_via="codex",
            fetch_resume='{"kind":"codex"}',
        ),
    )

    artifact = LibraryDocumentArtifact(
        markdown="# Codex Pending\n\n## Abstract\n\n" + ("canonical content " * 40),
        parser_used="Docling",
    )
    review = review_library_document(
        artifact,
        qa_validator=lambda markdown, minimum, expected: True,
        qa_min_characters=50,
    )
    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi="10.1234/codex-pending",
        title="Codex Pending",
        source="Codex Chrome Extension",
        fetch_outcome="local_parse",
        year="",
        journal="",
        publisher="",
    )
    saved_metadata = read_frontmatter_metadata(Path(persisted.summary.file_path).read_text(encoding="utf-8"))

    assert saved_metadata.get("year", "") == ""
    assert saved_metadata.get("journal", "") == ""
    assert saved_metadata.get("publisher", "") == ""
    assert "metadata_completion_sources" not in saved_metadata
    assert persisted.warnings == []
