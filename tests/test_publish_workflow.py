from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step_run(job: dict[str, Any], name: str) -> str:
    for step in job["steps"]:
        if step.get("name") == name:
            return str(step.get("run", ""))
    raise AssertionError(f"Missing workflow step: {name}")


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing workflow step: {name}")


def test_publish_workflow_exposes_recovery_mode() -> None:
    workflow = _load_workflow()

    recovery = workflow[True]["workflow_dispatch"]["inputs"]["recover_existing"]
    assert recovery["type"] == "boolean"
    assert recovery["default"] is False


def test_publish_workflow_generates_release_notes_with_current_release_tooling() -> None:
    workflow = _load_workflow()
    prepare_notes = workflow["jobs"]["prepare-release-notes"]

    tooling_checkout = _step(prepare_notes, "Check out release tooling")
    assert tooling_checkout["uses"] == "actions/checkout@v6"
    assert tooling_checkout["with"] == {
        "ref": "${{ github.ref }}",
        "fetch-depth": 0,
    }

    setup_python = _step(prepare_notes, "Set up Python")
    assert setup_python["uses"] == "actions/setup-python@v6"
    assert setup_python["with"] == {"python-version": "3.12"}

    run = _step_run(prepare_notes, "Generate GitHub Release notes")
    assert 'python scripts/release.py "$VERSION" --print-release-notes > release-notes.md' in run
    assert "inputs.publish_ref || github.ref_name" in run

    upload_steps = [
        step
        for step in prepare_notes["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    ]
    assert any(step.get("with", {}).get("name") == "release-notes" for step in upload_steps)


def test_publish_workflow_runs_release_guard_from_current_tooling() -> None:
    workflow = _load_workflow()
    prepare_notes = workflow["jobs"]["prepare-release-notes"]
    verify_release = workflow["jobs"]["verify-release"]

    guard_step = _step(prepare_notes, "Guard release sequence")
    assert guard_step["if"] == "${{ !(github.event_name == 'workflow_dispatch' && inputs.recover_existing) }}"
    assert "python scripts/check_release_publish_guard.py" in guard_step["run"]
    assert "Guard release sequence" not in [step.get("name") for step in verify_release["steps"]]


def test_publish_workflow_skips_source_verification_during_recovery() -> None:
    workflow = _load_workflow()
    verify_release = workflow["jobs"]["verify-release"]

    assert verify_release["if"] == "${{ !(github.event_name == 'workflow_dispatch' && inputs.recover_existing) }}"


def test_publish_workflow_skips_pypi_upload_during_recovery() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    publish = jobs["publish"]
    assert publish["needs"] == ["prepare-release-notes", "verify-release"]
    assert publish["if"] == "${{ !(github.event_name == 'workflow_dispatch' && inputs.recover_existing) }}"

    verify_pypi = jobs["verify-pypi"]
    assert verify_pypi["needs"] == ["prepare-release-notes", "verify-release", "publish"]
    assert "needs.prepare-release-notes.result == 'success'" in verify_pypi["if"]
    assert "needs.verify-release.result == 'success'" in verify_pypi["if"]
    assert "needs.verify-release.result == 'skipped'" in verify_pypi["if"]
    assert "needs.publish.result == 'skipped'" in verify_pypi["if"]
    assert "inputs.recover_existing" in verify_pypi["if"]


def test_publish_workflow_retries_real_pypi_installs_instead_of_json_gate() -> None:
    workflow = _load_workflow()
    verify_pypi = workflow["jobs"]["verify-pypi"]

    step_names = [step.get("name") for step in verify_pypi["steps"]]
    assert "Wait for package to appear on PyPI" not in step_names

    run = _step_run(verify_pypi, "Verify uv tool install and uvx from PyPI with retries")
    assert "for attempt in $(seq 1 30)" in run
    assert 'uv tool install --refresh-package grados "grados==${VERSION}" --force' in run
    assert 'uvx --no-cache --refresh-package grados --from "grados==${VERSION}" grados version' in run
    assert "diagnose_pypi" in run
    assert "use the normal publish path" not in str(workflow)


def test_publish_workflow_creates_github_release_after_pypi_verification() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    release_job = jobs["publish-github-release"]
    assert release_job["needs"] == ["prepare-release-notes", "verify-pypi"]
    assert release_job["permissions"] == {"contents": "write"}

    run = _step_run(release_job, "Create or update GitHub Release")
    assert 'gh release edit "$TAG" --repo "$GITHUB_REPOSITORY" --title "$TAG" --notes-file release-notes.md' in run
    assert (
        'gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --title "$TAG" '
        "--notes-file release-notes.md --verify-tag"
    ) in run
