from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_release_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("release_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_release_commit_message_describes_manifest_updates() -> None:
    module = _load_release_module()

    changed = [
        module.REPO / ".claude-plugin" / "plugin.json",
        module.REPO / "plugins" / "grados" / ".codex-plugin" / "plugin.json",
    ]

    assert module.build_release_commit_message("0.6.10", changed) == (
        "chore: release v0.6.10\n"
        "\n"
        "- Update .claude-plugin/plugin.json version metadata to 0.6.10\n"
        "- Update plugins/grados/.codex-plugin/plugin.json version metadata to 0.6.10"
    )


def test_build_github_release_notes_uses_version_title_and_commit_list() -> None:
    module = _load_release_module()

    notes = module.build_github_release_notes(
        "0.6.10",
        [
            ("abc1234", "fix: stabilize PDF handoff"),
            ("def5678", "chore: release v0.6.10"),
        ],
        previous_tag="v0.6.9",
        repo_url="https://github.com/STSNaive/GRaDOS",
    )

    assert notes == (
        "## Changes\n"
        "\n"
        "- `abc1234` fix: stabilize PDF handoff\n"
        "- `def5678` chore: release v0.6.10\n"
        "\n"
        "## Compare\n"
        "\n"
        "[v0.6.9...v0.6.10](https://github.com/STSNaive/GRaDOS/compare/v0.6.9...v0.6.10)\n"
    )


def test_build_github_release_notes_handles_empty_range() -> None:
    module = _load_release_module()

    notes = module.build_github_release_notes(
        "0.1.0",
        [],
        previous_tag=None,
        repo_url="https://github.com/STSNaive/GRaDOS",
    )

    assert notes == (
        "## Changes\n"
        "\n"
        "- No commit messages found for this release range.\n"
    )


def test_normalize_github_remote_url() -> None:
    module = _load_release_module()

    assert (
        module._normalize_github_remote_url("git@github.com:STSNaive/GRaDOS.git")
        == "https://github.com/STSNaive/GRaDOS"
    )
    assert (
        module._normalize_github_remote_url("https://github.com/STSNaive/GRaDOS.git")
        == "https://github.com/STSNaive/GRaDOS"
    )


def test_pypi_version_exists_requires_release_files(monkeypatch) -> None:
    module = _load_release_module()

    class FakeResponse:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def read(self) -> bytes:
            return b'{"releases": {"0.6.10": [{"filename": "grados-0.6.10.tar.gz"}], "0.6.11": []}}'

    def fake_urlopen(url, timeout):  # noqa: ANN001, ANN202
        assert url == "https://pypi.org/pypi/grados/json"
        assert timeout == 30
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    assert module._pypi_version_exists("0.6.10") is True
    assert module._pypi_version_exists("0.6.11") is False


def test_existing_remote_tag_with_pypi_version_dispatches_recovery_workflow(monkeypatch) -> None:
    module = _load_release_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append(cmd)

    monkeypatch.setattr(module.sys, "argv", ["release.py", "0.6.10", "--push"])
    monkeypatch.setattr(module, "_previous_release_tag", lambda version: "v0.6.9")
    monkeypatch.setattr(module, "_tag_exists", lambda tag: True)
    monkeypatch.setattr(module, "_remote_tag_exists", lambda tag: True)
    monkeypatch.setattr(module, "_pypi_version_exists", lambda version: True)
    monkeypatch.setattr(module, "_run", fake_run)

    module.main()

    assert calls == [
        ["git", "push", "origin", "main"],
        [
            "gh",
            "workflow",
            "run",
            "publish.yml",
            "--ref",
            "main",
            "-f",
            "publish_ref=v0.6.10",
            "-f",
            "recover_existing=true",
        ],
    ]


def test_existing_remote_tag_without_pypi_version_dispatches_publish_workflow(monkeypatch) -> None:
    module = _load_release_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append(cmd)

    monkeypatch.setattr(module.sys, "argv", ["release.py", "0.6.10", "--push"])
    monkeypatch.setattr(module, "_previous_release_tag", lambda version: "v0.6.9")
    monkeypatch.setattr(module, "_tag_exists", lambda tag: True)
    monkeypatch.setattr(module, "_remote_tag_exists", lambda tag: True)
    monkeypatch.setattr(module, "_pypi_version_exists", lambda version: False)
    monkeypatch.setattr(module, "_run", fake_run)

    module.main()

    assert calls == [
        ["git", "push", "origin", "main"],
        [
            "gh",
            "workflow",
            "run",
            "publish.yml",
            "--ref",
            "main",
            "-f",
            "publish_ref=v0.6.10",
        ],
    ]


def test_new_release_push_only_pushes_tag_for_workflow_publish(monkeypatch) -> None:
    module = _load_release_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append(cmd)

    monkeypatch.setattr(module.sys, "argv", ["release.py", "0.6.10", "--push"])
    monkeypatch.setattr(module, "PLUGIN_FILES", [])
    monkeypatch.setattr(module, "_previous_release_tag", lambda version: "v0.6.9")
    monkeypatch.setattr(module, "_tag_exists", lambda tag: False)
    monkeypatch.setattr(module, "_check_release_publish_guard", lambda tag: None)
    monkeypatch.setattr(module, "_run", fake_run)

    module.main()

    assert calls == [
        ["git", "tag", "-a", "v0.6.10", "-m", "v0.6.10"],
        ["git", "push", "origin", "main", "v0.6.10"],
    ]
    assert all(cmd[:2] != ["gh", "release"] for cmd in calls)


def test_print_release_notes_uses_existing_notes_format(monkeypatch, capsys) -> None:
    module = _load_release_module()

    monkeypatch.setattr(module.sys, "argv", ["release.py", "0.6.10", "--print-release-notes"])
    monkeypatch.setattr(module, "_previous_release_tag", lambda version: "v0.6.9")
    monkeypatch.setattr(
        module,
        "_collect_release_commits",
        lambda target_ref, previous_tag: [("abc1234", "fix: stabilize PDF handoff")],
    )
    monkeypatch.setattr(module, "_github_repo_url", lambda: "https://github.com/STSNaive/GRaDOS")

    module.main()

    assert capsys.readouterr().out == (
        "## Changes\n"
        "\n"
        "- `abc1234` fix: stabilize PDF handoff\n"
        "\n"
        "## Compare\n"
        "\n"
        "[v0.6.9...v0.6.10](https://github.com/STSNaive/GRaDOS/compare/v0.6.9...v0.6.10)\n"
    )


def test_trigger_publish_workflow_targets_existing_tag(monkeypatch) -> None:
    module = _load_release_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append(cmd)

    monkeypatch.setattr(module, "_run", fake_run)

    module._trigger_publish_workflow("v0.6.10", recover_existing=True)

    assert calls == [
        [
            "gh",
            "workflow",
            "run",
            "publish.yml",
            "--ref",
            "main",
            "-f",
            "publish_ref=v0.6.10",
            "-f",
            "recover_existing=true",
        ]
    ]


def test_release_publish_guard_allows_missing_current_tag(monkeypatch) -> None:
    module = _load_release_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append(cmd)

    monkeypatch.setattr(module, "_run", fake_run)

    module._check_release_publish_guard("v0.6.10")

    assert calls == [
        [
            module.sys.executable,
            str(module.RELEASE_PUBLISH_GUARD),
            "--tag",
            "v0.6.10",
            "--package",
            "grados",
            "--repo",
            str(module.REPO),
            "--allow-missing-current-tag",
        ]
    ]
