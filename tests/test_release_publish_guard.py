from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_guard_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "check_release_publish_guard.py"
    spec = importlib.util.spec_from_file_location("release_publish_guard", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_guard_allows_next_tag_when_previous_is_published() -> None:
    module = _load_guard_module()

    errors = module.check_release_sequence(
        current=module.ReleaseVersion.from_tag("v0.6.26"),
        git_tags=["v0.6.24", "v0.6.25", "v0.6.26"],
        published_versions={"0.6.24", "0.6.25"},
        package="grados",
    )

    assert errors == []


def test_release_guard_blocks_skipping_unpublished_previous_tag() -> None:
    module = _load_guard_module()

    errors = module.check_release_sequence(
        current=module.ReleaseVersion.from_tag("v0.6.22"),
        git_tags=["v0.6.20", "v0.6.21", "v0.6.22"],
        published_versions={"0.6.20"},
        package="grados",
    )

    assert errors == [
        "previous release tag v0.6.21 exists, but grados 0.6.21 is missing from PyPI; "
        "recover or publish v0.6.21 before publishing v0.6.22"
    ]


def test_release_guard_blocks_republishing_existing_pypi_version() -> None:
    module = _load_guard_module()

    errors = module.check_release_sequence(
        current=module.ReleaseVersion.from_tag("refs/tags/v0.6.25"),
        git_tags=["v0.6.24", "v0.6.25"],
        published_versions={"0.6.24", "0.6.25"},
        package="grados",
    )

    assert errors == [
        "grados 0.6.25 already exists on PyPI; package indexes are immutable, so retry verification "
        "instead of publishing a new artifact"
    ]


def test_release_guard_requires_current_tag_to_be_fetched() -> None:
    module = _load_guard_module()

    errors = module.check_release_sequence(
        current=module.ReleaseVersion.from_tag("v0.6.25"),
        git_tags=["v0.6.24"],
        published_versions={"0.6.24"},
        package="grados",
    )

    assert errors == ["current tag v0.6.25 is not present in fetched git tags"]


def test_release_guard_can_check_not_yet_created_current_tag() -> None:
    module = _load_guard_module()

    errors = module.check_release_sequence(
        current=module.ReleaseVersion.from_tag("v0.6.26"),
        git_tags=["v0.6.24", "v0.6.25"],
        published_versions={"0.6.24", "0.6.25"},
        package="grados",
        require_current_tag=False,
    )

    assert errors == []


def test_git_release_tags_fetches_remote_tags_before_listing(monkeypatch, tmp_path) -> None:
    module = _load_guard_module()
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append((cmd, kwargs))
        if cmd == ["git", "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*"]:
            return SimpleNamespace(stdout="v0.6.27\nv0.6.28\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.git_release_tags(tmp_path) == ["v0.6.27", "v0.6.28"]
    assert calls[0][0] == ["git", "fetch", "--tags", "--prune", "origin"]
    assert calls[0][1]["check"] is True
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[1][0] == ["git", "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*"]
