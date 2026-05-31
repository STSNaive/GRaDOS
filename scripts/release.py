#!/usr/bin/env python3
"""Bump plugin version fields, commit, tag, and (optionally) publish.

Usage:
    python scripts/release.py 0.7.0          # bump + commit + tag
    python scripts/release.py 0.7.0 --push   # ... push and create/update GitHub Release
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
GITHUB_REPO_FALLBACK = "https://github.com/STSNaive/GRaDOS"
VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
RELEASE_PUBLISH_GUARD = REPO / "scripts" / "check_release_publish_guard.py"

# Every JSON file whose "version" fields must track the release version.
PLUGIN_FILES = [
    REPO / ".claude-plugin" / "plugin.json",
    REPO / ".claude-plugin" / "marketplace.json",
    REPO / "plugins" / "grados" / ".codex-plugin" / "plugin.json",
]


def _update_json(path: Path, version: str) -> bool:
    """Replace all top-level and nested "version" values in a JSON file."""
    text = path.read_text(encoding="utf-8")
    updated = re.sub(
        r'("version"\s*:\s*)"[^"]*"',
        rf'\1"{version}"',
        text,
    )
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _run(cmd: list[str], **kw: Any) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kw)  # noqa: S603


def _tag_exists(tag: str) -> bool:
    result = subprocess.run(  # noqa: S603
        ["git", "tag", "-l", tag],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    return tag in result.stdout.splitlines()


def _version_tag_key(tag: str) -> tuple[int, int, int]:
    major, minor, patch = tag.removeprefix("v").split(".")
    return int(major), int(minor), int(patch)


def _previous_release_tag(version: str) -> str | None:
    target_key = _version_tag_key(f"v{version}")
    result = subprocess.run(  # noqa: S603
        ["git", "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    candidates = [
        tag
        for tag in result.stdout.splitlines()
        if VERSION_TAG_RE.fullmatch(tag) and _version_tag_key(tag) < target_key
    ]
    if not candidates:
        return None
    return max(candidates, key=_version_tag_key)


def _collect_release_commits(
    target_ref: str,
    previous_tag: str | None,
) -> list[tuple[str, str]]:
    range_spec = f"{previous_tag}..{target_ref}" if previous_tag else target_ref
    result = subprocess.run(  # noqa: S603
        ["git", "log", "--reverse", "--format=%h%x09%s", range_spec],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    commits: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        short_sha, _, subject = line.partition("\t")
        if short_sha and subject:
            commits.append((short_sha, subject))
    return commits


def _normalize_github_remote_url(url: str) -> str:
    if url.startswith("git@github.com:"):
        owner_repo = url.removeprefix("git@github.com:").removesuffix(".git")
        return f"https://github.com/{owner_repo}"
    if url.startswith("https://github.com/") or url.startswith("http://github.com/"):
        return url.removesuffix(".git").removesuffix("/")
    return url.removesuffix(".git").removesuffix("/")


def _github_repo_url() -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        )
    except subprocess.CalledProcessError:
        return GITHUB_REPO_FALLBACK
    return _normalize_github_remote_url(result.stdout.strip()) or GITHUB_REPO_FALLBACK


def build_release_commit_message(version: str, changed: list[Path]) -> str:
    """Build a conventional commit message for the release manifest bump."""
    bullets = [
        f"- Update {path.relative_to(REPO).as_posix()} version metadata to {version}"
        for path in changed
    ]
    return "\n".join([f"chore: release v{version}", "", *bullets])


def build_github_release_notes(
    version: str,
    commits: list[tuple[str, str]],
    *,
    previous_tag: str | None,
    repo_url: str,
) -> str:
    """Build stable GitHub Release notes from the tagged commit range."""
    tag = f"v{version}"
    lines = ["## Changes", ""]
    if commits:
        lines.extend(f"- `{short_sha}` {subject}" for short_sha, subject in commits)
    else:
        lines.append("- No commit messages found for this release range.")

    if previous_tag:
        compare_label = f"{previous_tag}...{tag}"
        compare_url = f"{repo_url}/compare/{previous_tag}...{tag}"
        lines.extend(["", "## Compare", "", f"[{compare_label}]({compare_url})"])

    return "\n".join(lines) + "\n"


def _commit(message: str) -> None:
    print("  $ git commit -F -")
    subprocess.run(
        ["git", "commit", "-F", "-"],
        input=message,
        text=True,
        check=True,
        cwd=REPO,
    )  # noqa: S603


def _create_or_update_github_release(tag: str, notes: str) -> None:
    exists = subprocess.run(  # noqa: S603
        ["gh", "release", "view", tag],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    action = "edit" if exists else "create"
    cmd = ["gh", "release", action, tag, "--title", tag, "--notes", notes]
    if action == "create":
        cmd.append("--verify-tag")
    printable = [part if part != notes else "<generated>" for part in cmd]
    print(f"  $ {' '.join(printable)}")
    subprocess.run(cmd, check=True, cwd=REPO)  # noqa: S603


def _check_release_publish_guard(tag: str) -> None:
    _run(
        [
            sys.executable,
            str(RELEASE_PUBLISH_GUARD),
            "--tag",
            tag,
            "--package",
            "grados",
            "--repo",
            str(REPO),
            "--allow-missing-current-tag",
        ],
        cwd=REPO,
    )


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        raise SystemExit(0)

    version = sys.argv[1].removeprefix("v")
    push = "--push" in sys.argv

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"Error: '{version}' is not a valid X.Y.Z version")
        raise SystemExit(1)

    tag = f"v{version}"
    previous_tag = _previous_release_tag(version)

    # Check for existing tag
    if _tag_exists(tag):
        if push:
            print(f"Tag {tag} already exists; retrying push/release for the same version.")
            commits = _collect_release_commits(tag, previous_tag)
            notes = build_github_release_notes(
                version,
                commits,
                previous_tag=previous_tag,
                repo_url=_github_repo_url(),
            )
            _run(["git", "push", "origin", "main", tag], cwd=REPO)
            _create_or_update_github_release(tag, notes)
            print(f"GitHub Release {tag} synced.")
            return
        print(f"Error: tag {tag} already exists")
        raise SystemExit(1)

    _check_release_publish_guard(tag)

    # Update plugin JSONs
    changed: list[Path] = []
    for path in PLUGIN_FILES:
        if not path.exists():
            print(f"  skip (not found): {path.relative_to(REPO)}")
            continue
        if _update_json(path, version):
            changed.append(path)
            print(f"  updated: {path.relative_to(REPO)}")
        else:
            print(f"  already {version}: {path.relative_to(REPO)}")

    # Commit if anything changed
    if changed:
        _run(["git", "add", *(str(p) for p in changed)], cwd=REPO)
        _commit(build_release_commit_message(version, changed))

    commits = _collect_release_commits("HEAD", previous_tag)
    notes = build_github_release_notes(
        version,
        commits,
        previous_tag=previous_tag,
        repo_url=_github_repo_url(),
    )

    # Create an annotated tag so GitHub tag details stay version-only.
    _run(["git", "tag", "-a", tag, "-m", tag], cwd=REPO)
    print(f"\nTag {tag} created.")

    if push:
        _run(["git", "push", "origin", "main", tag], cwd=REPO)
        print(f"Pushed main + {tag} to origin.")
        _create_or_update_github_release(tag, notes)
        print(f"GitHub Release {tag} synced.")
    else:
        print(f"Run to publish:  uv run python scripts/release.py {version} --push")


if __name__ == "__main__":
    main()
