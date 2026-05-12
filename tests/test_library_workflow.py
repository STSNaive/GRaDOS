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
    maybe_save_library_pdf,
    persist_reviewed_library_document,
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
