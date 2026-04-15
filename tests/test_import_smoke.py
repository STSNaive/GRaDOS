from __future__ import annotations

import asyncio
import json
from pathlib import Path

from grados.config import GRaDOSPaths, generate_default_config
from grados.extract.parse import ParsePipelineResult
from grados.importing import import_local_pdf_library


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
    assert (paths.papers / "10_1234_demo_a.md").is_file()
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

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
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
    assert (paths.papers / "10_1234_demo_a.md").is_file()
