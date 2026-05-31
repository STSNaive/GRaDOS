from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


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


def test_create_github_release_requires_remote_tag(monkeypatch) -> None:
    module = _load_release_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append(cmd)
        if cmd == ["gh", "release", "view", "v0.6.10"]:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._create_or_update_github_release("v0.6.10", "notes")

    assert calls[-1] == [
        "gh",
        "release",
        "create",
        "v0.6.10",
        "--title",
        "v0.6.10",
        "--notes",
        "notes",
        "--verify-tag",
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
