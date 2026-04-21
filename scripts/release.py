#!/usr/bin/env python3
"""Bump plugin version fields, commit, tag, and (optionally) push.

Usage:
    python scripts/release.py 0.7.0          # bump + commit + tag
    python scripts/release.py 0.7.0 --push   # … and push to origin
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

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


def _run(cmd: list[str], **kw: object) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kw)  # noqa: S603


def build_release_commit_message(version: str, changed: list[Path]) -> str:
    """Build a conventional commit message for the release manifest bump."""
    return f"chore: release v{version}"


def _commit(message: str) -> None:
    print("  $ git commit -F -")
    subprocess.run(
        ["git", "commit", "-F", "-"],
        input=message,
        text=True,
        check=True,
        cwd=REPO,
    )  # noqa: S603


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

    # Check for existing tag
    result = subprocess.run(
        ["git", "tag", "-l", tag],
        capture_output=True, text=True, cwd=REPO,
    )
    if tag in result.stdout.splitlines():
        print(f"Error: tag {tag} already exists")
        raise SystemExit(1)

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

    # Create tag
    _run(["git", "tag", tag], cwd=REPO)
    print(f"\nTag {tag} created.")

    # Push
    if push:
        _run(["git", "push", "origin", "main", tag], cwd=REPO)
        print(f"Pushed main + {tag} to origin.")
    else:
        print(f"Run to publish:  git push origin main {tag}")


if __name__ == "__main__":
    main()
