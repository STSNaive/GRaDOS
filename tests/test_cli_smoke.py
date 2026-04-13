from __future__ import annotations

import tomllib
from pathlib import Path
from subprocess import CompletedProcess

from click.testing import CliRunner

from grados import __version__
from grados.cli import _EXTRAS, main
from grados.config import GRaDOSPaths
from grados.importing import ImportItemResult, ImportLibraryResult


def test_setup_version_paths_and_status_commands(tmp_path: Path) -> None:
    home = tmp_path / "grados-home"
    runner = CliRunner()
    env = {"GRADOS_HOME": str(home)}

    import grados.cli as cli

    cli._setup_browser = lambda paths: None  # type: ignore[assignment]
    cli._setup_models = lambda paths: None  # type: ignore[assignment]

    setup_result = runner.invoke(main, ["setup"], env=env)
    assert setup_result.exit_code == 0
    assert (home / "config.json").is_file()

    version_result = runner.invoke(main, ["version"], env=env)
    assert version_result.exit_code == 0
    assert f"GRaDOS {__version__}" in version_result.output

    paths_result = runner.invoke(main, ["paths"], env=env, terminal_width=200)
    assert paths_result.exit_code == 0
    # The Rich table layout is terminal-dependent, so assert on stable labels instead
    # of exact rendered paths.
    assert "数据根目录" in paths_result.output
    assert "配置文件" in paths_result.output
    assert "ChromaDB" in paths_result.output
    assert "模式: GRADOS_HOME" in paths_result.output

    status_result = runner.invoke(main, ["status"], env=env, terminal_width=200)
    assert status_result.exit_code == 0
    assert "GRaDOS Status" in status_result.output
    assert "配置文件" in status_result.output
    assert "数据根目录" in status_result.output
    assert "已加载" in status_result.output
    assert "harrier-oss-v1-0.6b" in status_result.output


def test_update_db_command_reports_index_summary(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    (paths.papers / "10_1234_demo.md").write_text(
        '---\ndoi: "10.1234/demo"\ntitle: "Demo"\n---\n\n# Abstract\n\nDemo content.',
        encoding="utf-8",
    )

    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "index_all_papers", lambda chroma_dir, papers_dir, **kwargs: (1, 3))
    monkeypatch.setattr(
        vector,
        "get_index_stats",
        lambda chroma_dir, **kwargs: {"unique_papers": 1, "total_chunks": 3, "reindex_required": False},
    )

    runner = CliRunner()
    result = runner.invoke(main, ["update-db"], env={"GRADOS_HOME": str(home)})

    assert result.exit_code == 0
    assert "已索引" in result.output
    assert "1 篇论文" in result.output
    assert "3" in result.output


def test_import_pdfs_command_reports_summary(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "library"
    source.mkdir()
    (source / "paper.pdf").write_bytes(b"%PDF-1.4\nstub")

    import grados.importing as importing

    async def fake_import(**kwargs):
        assert kwargs["source_path"] == source
        return ImportLibraryResult(
            source_path=str(source),
            scanned=1,
            imported=1,
            skipped=0,
            failed=0,
            items=[
                ImportItemResult(
                    source_path=str(source / "paper.pdf"),
                    status="imported",
                    doi="10.1234/demo",
                    safe_doi="10_1234_demo",
                    title="Demo Paper",
                )
            ],
        )

    monkeypatch.setattr(importing, "import_local_pdf_library", fake_import)

    runner = CliRunner()
    result = runner.invoke(main, ["import-pdfs", "--from", str(source)])

    assert result.exit_code == 0
    assert "GRaDOS Import PDFs" in result.output
    assert "导入成功" in result.output
    assert "Demo Paper" in result.output


def test_optional_install_metadata_matches_runtime_backends() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert set(extras) == {"marker", "docling", "full"}
    assert extras["full"] == ["grados[marker,docling]"]
    assert {extra for _, _, extra in _EXTRAS} == {"marker", "docling"}


def test_client_install_and_remove_commands_manage_claude_and_codex(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    data_root = tmp_path / "data-root"
    runner = CliRunner()
    commands: list[list[str]] = []

    import grados.integrations.manager as manager

    def fake_which(name: str) -> str | None:
        if name in {"claude", "codex", "grados"}:
            return f"/usr/bin/{name}"
        return None

    def fake_run(command: list[str], *, check: bool) -> CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["/usr/bin/claude", "mcp", "list"]:
            return CompletedProcess(command, 0, "grados: /usr/bin/grados - ✓ Connected\n", "")
        if command[:4] == ["/usr/bin/codex", "mcp", "get", "grados"]:
            return CompletedProcess(command, 0, "grados\n", "")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(manager.shutil, "which", fake_which)
    monkeypatch.setattr(manager, "_run_command", fake_run)

    install_result = runner.invoke(
        main,
        ["client", "install", "all"],
        env={"HOME": str(home), "GRADOS_HOME": str(data_root)},
    )
    assert install_result.exit_code == 0
    assert (home / ".claude" / "skills" / "grados" / "SKILL.md").is_file()
    assert any(
        command
        == [
            "/usr/bin/claude",
            "mcp",
            "add",
            "-s",
            "user",
            "-e",
            f"GRADOS_HOME={data_root}",
            "grados",
            "--",
            "/usr/bin/grados",
        ]
        for command in commands
    )
    assert any(
        command
        == [
            "/usr/bin/codex",
            "mcp",
            "add",
            "grados",
            "--env",
            f"GRADOS_HOME={data_root}",
            "--",
            "/usr/bin/grados",
        ]
        for command in commands
    )

    remove_result = runner.invoke(main, ["client", "remove", "all"], env={"HOME": str(home)})
    assert remove_result.exit_code == 0
    assert not (home / ".claude" / "skills" / "grados").exists()
