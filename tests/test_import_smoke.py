from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from grados.config import GRaDOSPaths, IndexingConfig, generate_default_config
from grados.extract.parse import ParsePipelineResult
from grados.importing import import_local_pdf_library
from grados.publisher.common import safe_doi_filename


def test_import_local_pdf_library_imports_and_skips_duplicates(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    paths.config_file.write_text(json.dumps(generate_default_config(paths)), encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir()
    (source / "paper-a.pdf").write_bytes(b"%PDF-1.4\nsame-a")
    (source / "paper-a-copy.pdf").write_bytes(b"%PDF-1.4\nsame-a")
    (source / "paper-b.pdf").write_bytes(b"%PDF-1.4\nsame-b")

    async def fake_parse_pdf(pdf_bytes, filename, **kwargs):
        if filename == "paper-a.pdf" or filename == "paper-a-copy.pdf":
            return ParsePipelineResult(
                markdown=(
                    "# Demo Paper A\n\n"
                    "DOI: 10.1234/demo-a\n\n"
                    "## Abstract\n\n"
                    + ("Composite vibration behavior is discussed in detail. " * 40)
                ),
                parser_used="Docling",
            )
        return ParsePipelineResult(
            markdown=(
                "# Local Library Paper\n\n"
                "## Abstract\n\n"
                + ("A local PDF without DOI but with enough content to pass QA. " * 40)
            ),
            parser_used="Docling",
        )

    import grados.importing as importing
    import grados.storage.vector as vector

    monkeypatch.setattr(importing, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    first = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            copy_to_library=True,
        )
    )

    assert first.scanned == 3
    assert first.imported == 2
    assert first.skipped == 1
    assert first.failed == 0
    assert any(item.doi == "10.1234/demo-a" for item in first.items)
    assert any(item.doi.startswith("local-pdf/") for item in first.items if item.status.startswith("imported"))
    assert (paths.papers / f"{safe_doi_filename('10.1234/demo-a')}.md").is_file()
    assert len(list(paths.downloads.glob("*.pdf"))) == 2

    second = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            copy_to_library=False,
        )
    )

    assert second.imported == 0
    assert second.skipped == 3
    assert second.failed == 0


def test_import_local_pdf_library_surfaces_index_warning(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    paths.config_file.write_text(json.dumps(generate_default_config(paths)), encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir()
    (source / "paper-a.pdf").write_bytes(b"%PDF-1.4\nsame-a")

    async def fake_parse_pdf(pdf_bytes, filename, **kwargs):
        return ParsePipelineResult(
            markdown=(
                "# Demo Paper A\n\n"
                "DOI: 10.1234/demo-a\n\n"
                "## Abstract\n\n"
                + ("Composite vibration behavior is discussed in detail. " * 40)
            ),
            parser_used="Docling",
        )

    import grados.importing as importing
    import grados.storage.vector as vector

    monkeypatch.setattr(importing, "parse_pdf_with_diagnostics", fake_parse_pdf)
    captured: dict[str, object] = {}

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        captured["indexing_config"] = kwargs.get("indexing_config")
        raise RuntimeError("index backend offline")

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    result = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            copy_to_library=False,
        )
    )

    assert result.imported == 1
    assert result.items[0].status == "imported_with_warnings"
    assert "index_warning" in result.items[0].detail
    assert any("index refresh failed" in warning.lower() for warning in result.warnings)
    assert (paths.papers / f"{safe_doi_filename('10.1234/demo-a')}.md").is_file()
    assert isinstance(captured["indexing_config"], IndexingConfig)


def test_import_local_pdf_library_rejects_oversized_pdf(tmp_path: Path) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["security"]["max_local_pdf_bytes"] = 8
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir()
    (source / "too-large.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 32)

    result = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            copy_to_library=False,
        )
    )

    assert result.scanned == 1
    assert result.imported == 0
    assert result.failed == 1
    assert result.items[0].status == "failed"
    assert result.items[0].detail == "file_too_large"
    assert "Local PDF" in result.items[0].warnings[0]


def test_import_local_pdf_library_rejects_pdf_that_exceeds_limit_during_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["security"]["max_local_pdf_bytes"] = 12
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir()
    pdf_path = source / "grows-during-read.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n" + b"x" * 32)
    resolved_pdf_path = pdf_path.resolve()
    original_stat = Path.stat

    def fake_stat(self: Path, *args, **kwargs) -> os.stat_result:  # noqa: ANN002, ANN003
        file_stat = original_stat(self, *args, **kwargs)
        if self == resolved_pdf_path:
            values = list(file_stat)
            values[6] = 8
            return os.stat_result(values)
        return file_stat

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError("import_local_pdf_library should use bounded stream reads")

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    result = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            copy_to_library=False,
        )
    )

    assert result.scanned == 1
    assert result.imported == 0
    assert result.failed == 1
    assert result.items[0].status == "failed"
    assert result.items[0].detail == "file_too_large"
    assert "Local PDF" in result.items[0].warnings[0]
