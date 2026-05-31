#!/usr/bin/env python3
"""Guard PyPI publishing against skipped failed release tags."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SEMVER_TAG_RE = re.compile(r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")


@dataclass(frozen=True, order=True)
class ReleaseVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def from_tag(cls, tag: str) -> ReleaseVersion:
        normalized = normalize_tag(tag)
        match = SEMVER_TAG_RE.fullmatch(normalized)
        if match is None:
            raise ValueError(f"{tag!r} is not a vX.Y.Z release tag")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
        )

    @property
    def tag(self) -> str:
        return f"v{self}"

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def normalize_tag(value: str) -> str:
    return value.removeprefix("refs/tags/").strip()


def parse_release_tags(tags: Iterable[str]) -> set[ReleaseVersion]:
    versions: set[ReleaseVersion] = set()
    for tag in tags:
        normalized = normalize_tag(tag)
        if SEMVER_TAG_RE.fullmatch(normalized) is None:
            continue
        versions.add(ReleaseVersion.from_tag(normalized))
    return versions


def previous_release_tag(current: ReleaseVersion, tags: Iterable[str]) -> ReleaseVersion | None:
    lower_versions = [version for version in parse_release_tags(tags) if version < current]
    if not lower_versions:
        return None
    return max(lower_versions)


def check_release_sequence(
    *,
    current: ReleaseVersion,
    git_tags: Iterable[str],
    published_versions: Iterable[str],
    package: str,
    require_current_tag: bool = True,
) -> list[str]:
    tag_versions = parse_release_tags(git_tags)
    published = set(published_versions)
    errors: list[str] = []

    if require_current_tag and current not in tag_versions:
        errors.append(f"current tag {current.tag} is not present in fetched git tags")

    if str(current) in published:
        errors.append(
            f"{package} {current} already exists on PyPI; package indexes are immutable, so retry verification "
            "instead of publishing a new artifact"
        )

    previous = previous_release_tag(current, (version.tag for version in tag_versions))
    if previous is not None and str(previous) not in published:
        errors.append(
            f"previous release tag {previous.tag} exists, but {package} {previous} is missing from PyPI; "
            f"recover or publish {previous.tag} before publishing {current.tag}"
        )

    return errors


def git_release_tags(repo: Path) -> list[str]:
    subprocess.run(
        ["git", "fetch", "--tags", "--prune", "origin"],
        check=True,
        cwd=repo,
    )
    result = subprocess.run(
        ["git", "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*"],
        check=True,
        capture_output=True,
        cwd=repo,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def read_pypi_releases(package: str, pypi_json: Path | None = None) -> set[str]:
    if pypi_json is not None:
        payload = json.loads(pypi_json.read_text(encoding="utf-8"))
    else:
        url = f"https://pypi.org/pypi/{package}/json"
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    releases = payload.get("releases", {})
    if not isinstance(releases, dict):
        raise ValueError("PyPI JSON does not contain a releases object")
    return {str(version) for version in releases}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag to publish, for example v0.6.25")
    parser.add_argument("--package", default="grados", help="PyPI package name")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    parser.add_argument("--pypi-json", type=Path, help="Optional PyPI JSON fixture path")
    parser.add_argument(
        "--allow-missing-current-tag",
        action="store_true",
        help="Allow checking a not-yet-created release tag before scripts/release.py creates it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    current = ReleaseVersion.from_tag(args.tag)
    tags = git_release_tags(args.repo)
    published_versions = read_pypi_releases(args.package, args.pypi_json)
    errors = check_release_sequence(
        current=current,
        git_tags=tags,
        published_versions=published_versions,
        package=args.package,
        require_current_tag=not args.allow_missing_current_tag,
    )
    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1

    previous = previous_release_tag(current, tags)
    previous_text = previous.tag if previous is not None else "none"
    print(f"Release publish guard passed for {args.package} {current} (previous tag: {previous_text}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
