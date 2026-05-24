from __future__ import annotations

import asyncio
from pathlib import Path

from grados.config import GRaDOSPaths, IndexingConfig
from grados.extract.parse import ParsePipelineResult
from grados.publisher.common import safe_doi_filename
from grados.storage.frontmatter import read_frontmatter_metadata
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
