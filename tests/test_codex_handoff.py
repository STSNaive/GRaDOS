from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from grados.config import GRaDOSPaths, generate_default_config
from grados.publisher.common import safe_doi_filename
from grados.server_tools import library_tools
from grados.storage.frontmatter import read_frontmatter_metadata_from_file
from grados.storage.papers import save_paper_markdown


def _write_config(
    home: Path,
    watch_dir: Path,
    *,
    max_age_seconds: float = 900.0,
    settle_seconds: float = 0.0,
    settle_max_wait_seconds: float = 0.0,
    max_local_pdf_bytes: int | None = None,
) -> None:
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["codex_handoff"]["download_watch_dir"] = str(watch_dir)
    config["extract"]["codex_handoff"]["download_max_age_seconds"] = max_age_seconds
    config["extract"]["codex_handoff"]["download_settle_seconds"] = settle_seconds
    config["extract"]["codex_handoff"]["download_settle_max_wait_seconds"] = settle_max_wait_seconds
    if max_local_pdf_bytes is not None:
        config["extract"]["security"]["max_local_pdf_bytes"] = max_local_pdf_bytes
    paths.config_file.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def _pending_codex_record(doi: str, *, issued_at: str | None = None) -> SimpleNamespace:
    issued = issued_at or (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    return SimpleNamespace(
        fetch_via="codex",
        fetch_status="host_action_required",
        fetch_state="host_action_required",
        fetch_manual=True,
        fetch_resume=json.dumps(
            {
                "kind": "codex",
                "doi": doi,
                "issued_at": issued,
                "download_watch_dir": "/ignored-by-config",
                "download_max_age_seconds": "900",
            }
        ),
    )


def _patch_pending_record(monkeypatch: Any, record: SimpleNamespace | None) -> None:
    import grados.storage.remote_metadata as remote_metadata

    monkeypatch.setattr(remote_metadata, "get_remote_metadata_by_doi", lambda *args, **kwargs: record)


def test_default_codex_handoff_config_uses_downloads_watch_dir() -> None:
    from grados.config import GRaDOSConfig

    config = GRaDOSConfig()

    assert config.extract.codex_handoff.download_watch_dir == "~/Downloads"
    assert config.extract.codex_handoff.download_max_age_seconds == 900.0
    assert config.extract.codex_handoff.download_settle_seconds == 2.0
    assert config.extract.codex_handoff.download_settle_max_wait_seconds == 30.0
    assert config.extract.codex_handoff.download_scan_recursive is False


def test_ingest_codex_downloaded_pdf_saves_unique_candidate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/codex-ingest"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    pdf_path = watch_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%codex")

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_parse_pdf(*args: Any, **kwargs: Any) -> Any:
        return parse_module.ParsePipelineResult(
            markdown="# Codex Ingest\n\n## Abstract\n\n" + ("downloaded paper content. " * 80),
            parser_used="PyMuPDF",
        )

    captured: dict[str, object] = {}

    def fake_record_remote_fetch_result(metadata_dir: Path, **kwargs: Any) -> int:
        captured["metadata_dir"] = metadata_dir
        captured["remote_metadata"] = kwargs
        return 1

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", fake_record_remote_fetch_result)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi, expected_title="Codex Ingest"))

    assert result["status"] == "success"
    assert result["source_path"] == str(pdf_path)
    assert Path(str(result["archived_pdf_path"])).is_file()
    assert result["source_pdf_hash"]
    assert "PDF Parsed & Saved" in str(result["parse_receipt"])
    assert captured["remote_metadata"]["fetch_status"] == "fulltext"
    assert captured["remote_metadata"]["fetch_via"] == "codex"


def test_ingest_codex_downloaded_pdf_returns_already_saved_without_pending(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/already-saved"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    paths = GRaDOSPaths(home)
    save_paper_markdown(
        doi,
        "# Already Saved\n\n## Abstract\n\n" + ("saved content. " * 80),
        paths.papers,
        title="Already Saved",
    )
    _patch_pending_record(monkeypatch, None)

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))

    assert result["status"] == "success"
    assert result["outcome"] == "already_saved"
    assert result["next_action"] == "read_saved_paper_or_get_saved_paper_structure"


def test_ingest_codex_downloaded_pdf_accepts_known_downloaded_file_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/known-path"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "missing-watch-dir"
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    pdf_path = tmp_path / "Chrome Downloads" / "known.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(b"%PDF-1.4\n%known")

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_parse_pdf(*args: Any, **kwargs: Any) -> Any:
        return parse_module.ParsePipelineResult(
            markdown="# Known Path\n\n## Abstract\n\n" + ("downloaded path content. " * 80),
            parser_used="Docling",
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    _patch_pending_record(monkeypatch, None)

    result = asyncio.run(
        library_tools.ingest_codex_downloaded_pdf(
            doi=doi,
            expected_title="Known Path",
            downloaded_file_path=str(pdf_path),
        )
    )

    assert result["status"] == "success"
    assert result["source_path"] == str(pdf_path)
    paper_path = home / "papers" / f"{safe_doi_filename(doi)}.md"
    frontmatter = read_frontmatter_metadata_from_file(paper_path)
    assert "original_pdf_path" not in frontmatter
    assert "copied_pdf_path" not in frontmatter
    assert "source_pdf_hash" not in frontmatter
    assert "acquisition_via" not in frontmatter
    parsed_manifest = json.loads((paper_path.parent / frontmatter["parsed_manifest_path"]).read_text())
    assert parsed_manifest["input_pdf_path"] == str(pdf_path.resolve())
    assert parsed_manifest["materialization_action"] == "copied"


def test_ingest_codex_downloaded_pdf_requires_pending_codex_handoff(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/not-codex"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    (watch_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n%codex")
    _patch_pending_record(
        monkeypatch,
        SimpleNamespace(
            fetch_via="api",
            fetch_status="failed",
            fetch_state="error",
            fetch_manual=False,
            fetch_resume="",
        ),
    )

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))

    assert result["status"] == "failed"
    assert result["failure_reason"] == "no_pending_handoff"
    assert "downloaded_file_path" in result["next_action"]


def test_ingest_codex_downloaded_pdf_rejects_missing_watch_dir(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/missing-watch"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "missing"
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))

    assert result["status"] == "failed"
    assert result["failure_reason"] == "watch_dir_missing"
    assert "downloaded_file_path" in result["next_action"]


def test_ingest_codex_downloaded_pdf_watch_dir_mismatch_uses_exact_path_next_action(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/watch-mismatch"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))

    assert result["status"] == "failed"
    assert result["failure_reason"] == "no_candidate"
    assert "downloaded_file_path" in result["next_action"]
    assert "download_with_chrome_extension" not in result["next_action"]
    assert result["download_watch_dir_semantics"] == "scan_only"


def test_ingest_codex_downloaded_pdf_recovers_project_downloads_candidate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/project-download"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    publisher_pdf = paths.downloads / "publisher-name.pdf"
    publisher_pdf.write_bytes(b"%PDF-1.4\n%project")

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_parse_pdf(*args: Any, **kwargs: Any) -> Any:
        return parse_module.ParsePipelineResult(
            markdown="# Project Download\n\n## Abstract\n\n" + ("project downloads content. " * 80),
            parser_used="Docling",
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))

    result = asyncio.run(
        library_tools.ingest_codex_downloaded_pdf(doi=doi, file_name_hint="publisher-name.pdf")
    )

    assert result["status"] == "success"
    assert result["source_path"] == str(publisher_pdf)
    assert Path(str(result["archived_pdf_path"])).is_file()
    assert not publisher_pdf.exists()


def test_ingest_codex_downloaded_pdf_rejects_unsafe_candidates(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/unsafe"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir, max_local_pdf_bytes=12)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))

    (watch_dir / "partial.pdf.crdownload").write_bytes(b"%PDF-1.4\n")
    (watch_dir / "not-pdf.pdf").write_bytes(b"not a pdf")
    (watch_dir / "too-large.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 32)
    target = watch_dir / "target.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    (watch_dir / "linked.pdf").symlink_to(target)

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))
    reasons = {str(candidate["reason"]) for candidate in result["rejected_candidates"]}

    assert result["status"] == "failed"
    assert {"temporary_file", "not_pdf", "too_large", "symlink_rejected"} <= reasons


def test_read_candidate_pdf_hash_rejects_oversize_before_read_bytes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    pdf_path = tmp_path / "too-large.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n" + b"x" * 32)

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError("oversize candidate should be rejected by stat before read_bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    source_hash, data, file_stat, error = library_tools._read_candidate_pdf_hash(
        pdf_path,
        max_bytes=12,
    )

    assert source_hash == ""
    assert data == b""
    assert file_stat is not None
    assert error.startswith("too_large:")


def test_ingest_codex_downloaded_pdf_returns_disambiguation_for_multiple_candidates(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/multiple"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))
    (watch_dir / "a.pdf").write_bytes(b"%PDF-1.4\n%a")
    (watch_dir / "b.pdf").write_bytes(b"%PDF-1.4\n%b")

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))

    assert result["status"] == "needs_disambiguation"
    assert result["failure_reason"] == "multiple_candidates"
    assert result["disambiguation_token"]
    assert len(result["candidates"]) == 2


def test_file_name_hint_narrows_candidates_without_bypassing_pdf_validation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/hint"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))
    (watch_dir / "good.pdf").write_bytes(b"%PDF-1.4\n%good")
    (watch_dir / "bad.pdf").write_bytes(b"not a pdf")

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi, file_name_hint="bad.pdf"))
    reasons = {str(candidate["reason"]) for candidate in result["rejected_candidates"]}

    assert result["status"] == "failed"
    assert result["failure_reason"] == "not_pdf"
    assert "file_name_hint_mismatch" in reasons


def test_ingest_codex_downloaded_pdf_detects_hash_change_before_parse(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doi = "10.1234/hash-change"
    home = tmp_path / "grados-home"
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir()
    _write_config(home, watch_dir)
    monkeypatch.setenv("GRADOS_HOME", str(home))
    _patch_pending_record(monkeypatch, _pending_codex_record(doi))
    pdf_path = watch_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%first")
    real_read_hash = library_tools._read_candidate_pdf_hash
    calls = {"n": 0}

    def changing_hash(path: Path, *, max_bytes: int) -> tuple[str, bytes, object | None, str]:
        calls["n"] += 1
        source_hash, data, file_stat, error = real_read_hash(path, max_bytes=max_bytes)
        if calls["n"] == 1:
            return source_hash, data, file_stat, error
        return "changed", data, file_stat, error

    monkeypatch.setattr(library_tools, "_read_candidate_pdf_hash", changing_hash)

    result = asyncio.run(library_tools.ingest_codex_downloaded_pdf(doi=doi))

    assert result["status"] == "failed"
    assert result["failure_reason"] == "hash_changed"
