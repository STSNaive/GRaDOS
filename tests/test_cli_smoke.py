from __future__ import annotations

import json
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
    assert "grados auth set" in setup_result.output

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
    assert "harrier-oss-v1-270m" in status_result.output
    assert "4096" in status_result.output

    browser_result = runner.invoke(main, ["browser", "status", "--json"], env=env)
    assert browser_result.exit_code == 0
    browser_payload = json.loads(browser_result.output)
    assert browser_payload["protocol"] == "pdf-browser-v1"
    assert browser_payload["browser_profile"] == str(home / "browser" / "profile")
    assert browser_payload["browser_pdf_sessions"] == str(home / "browser" / "pdf-sessions")
    assert isinstance(browser_payload["browser_executable"]["found"], bool)
    assert browser_payload["browser_executable"]["profile_directory"] == str(home / "browser" / "profile")

    external_result = runner.invoke(main, ["external-synthesis", "status", "--json"], env=env)
    assert external_result.exit_code == 0
    external_payload = json.loads(external_result.output)
    assert external_payload["enabled"] is False
    assert external_payload["status"] == "disabled"
    assert external_payload["config_file"] == str(home / "config.json")
    assert external_payload["config_exists"] is True
    assert external_payload["protocol"] == "external_synthesis_browser_v1"
    assert external_payload["browser_profile"] == str(home / "browser" / "chatgpt-profile")
    assert external_payload["browser_sessions"] == str(home / "browser" / "chatgpt-sessions")
    assert external_payload["browser_profile_initialized"] is False
    assert external_payload["browser_profile_initialized_meaning"] == "chrome_profile_markers_only_not_login_readiness"
    assert external_payload["setup_command"] == "grados external-synthesis setup-browser"

    external_predicate_result = runner.invoke(main, ["external-synthesis", "is-enabled"], env=env)
    assert external_predicate_result.exit_code == 1
    assert external_predicate_result.output == "false\n"

    external_predicate_quiet_result = runner.invoke(
        main, ["external-synthesis", "is-enabled", "--quiet"], env=env
    )
    assert external_predicate_quiet_result.exit_code == 1
    assert external_predicate_quiet_result.output == ""

    config_data = json.loads((home / "config.json").read_text(encoding="utf-8"))
    config_data["research"]["external_synthesis"]["enabled"] = True
    (home / "config.json").write_text(json.dumps(config_data), encoding="utf-8")

    external_enabled_result = runner.invoke(main, ["external-synthesis", "status", "--json"], env=env)
    assert external_enabled_result.exit_code == 0
    external_enabled_payload = json.loads(external_enabled_result.output)
    assert external_enabled_payload["enabled"] is True
    assert external_enabled_payload["status"] == "enabled"

    external_enabled_predicate_result = runner.invoke(main, ["external-synthesis", "is-enabled"], env=env)
    assert external_enabled_predicate_result.exit_code == 0
    assert external_enabled_predicate_result.output == "true\n"

    external_enabled_predicate_quiet_result = runner.invoke(
        main, ["external-synthesis", "is-enabled", "--quiet"], env=env
    )
    assert external_enabled_predicate_quiet_result.exit_code == 0
    assert external_enabled_predicate_quiet_result.output == ""


def test_external_synthesis_doctor_live_formats_logged_out_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    runner = CliRunner()
    env = {"GRADOS_HOME": str(home)}

    import grados.browser.chatgpt.runtime as runtime

    async def fake_check_chatgpt_login(*args, **kwargs) -> dict[str, object]:
        return {
            "ok": False,
            "status": 200,
            "dom_login_cta": True,
            "on_auth_page": False,
            "error": None,
        }

    monkeypatch.setattr(runtime, "check_chatgpt_login", fake_check_chatgpt_login)

    result = runner.invoke(main, ["external-synthesis", "doctor", "--live"], env=env, terminal_width=200)

    assert result.exit_code == 0
    assert "Live ChatGPT login: not signed in" in result.output
    assert "status=200" in result.output
    assert "dom_login_cta=true" in result.output
    assert "on_auth_page=false" in result.output
    assert "Live ChatGPT login: None" not in result.output


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
        lambda chroma_dir, **kwargs: vector.IndexStats(unique_papers=1, total_chunks=3, reindex_required=False),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["update-db"], env={"GRADOS_HOME": str(home)})

    assert result.exit_code == 0
    assert "已索引" in result.output
    assert "1 篇论文" in result.output
    assert "3" in result.output


def test_reindex_migrates_remote_metadata_before_clearing_chroma(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    paths.database_chroma.mkdir(parents=True)
    (paths.database_chroma / "legacy.sqlite3").write_text("legacy", encoding="utf-8")
    (paths.papers / "10_1234_demo.md").write_text(
        '---\ndoi: "10.1234/demo"\ntitle: "Demo"\n---\n\n# Abstract\n\nDemo content.',
        encoding="utf-8",
    )

    import grados.storage.embedding as embedding
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    migrated_calls: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        embedding,
        "inspect_embedding_runtime",
        lambda paths, indexing: {
            "max_length": 4096,
            "batch_size_hint": 8,
            "warnings": [],
            "dependencies": {},
            "runtime": "test",
            "provider": "test",
            "model_id": "test-model",
            "query_prompt_mode": "query_document",
            "cache_dir": str(tmp_path / "cache"),
        },
    )
    monkeypatch.setattr(
        remote_metadata,
        "migrate_remote_metadata_store",
        lambda legacy, target, **kwargs: migrated_calls.append((legacy, target)) or 1,
    )
    monkeypatch.setattr(vector, "index_all_papers", lambda chroma_dir, papers_dir, **kwargs: (1, 3))
    monkeypatch.setattr(
        vector,
        "get_index_stats",
        lambda chroma_dir, **kwargs: vector.IndexStats(unique_papers=1, total_chunks=3, reindex_required=False),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["reindex"], env={"GRADOS_HOME": str(home)})

    assert result.exit_code == 0
    assert migrated_calls == [(paths.database_chroma, paths.database_remote_metadata)]
    assert "已迁移" in result.output
    assert "已清空旧索引目录" in result.output
    assert "已重建" in result.output


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


def test_search_command_passes_indepth_override(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_search_academic_papers(query, **kwargs):  # noqa: ANN001, ANN003
        calls.append({"query": query, **kwargs})
        return "search result"

    monkeypatch.setattr(
        "grados.server_tools.search_tools.search_academic_papers",
        fake_search_academic_papers,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["search", "composite", "damping", "--limit", "3", "--indepth"])

    assert result.exit_code == 0
    assert "search result" in result.output
    assert calls == [
        {
            "query": "composite damping",
            "limit": 3,
            "continuation_token": None,
            "indepth": True,
        }
    ]


def test_optional_install_metadata_matches_runtime_backends() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    extras = data["project"]["optional-dependencies"]

    assert set(extras) == {"marker", "docling", "full"}
    assert "docling" in dependencies
    assert extras["docling"] == []
    assert extras["marker"] == []
    assert extras["full"] == []
    assert _EXTRAS == []


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
