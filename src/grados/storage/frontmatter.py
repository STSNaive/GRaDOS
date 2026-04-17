"""Canonical YAML frontmatter helpers for paper markdown mirrors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter as frontmatter_lib  # type: ignore[import-untyped]

__all__ = [
    "build_front_matter",
    "parse_authors_metadata",
    "read_frontmatter_metadata",
    "read_frontmatter_metadata_from_file",
    "strip_front_matter",
]

_FRONTMATTER_SCAN_LIMIT = 4096


def build_front_matter(
    doi: str,
    title: str = "",
    source: str = "",
    publisher: str = "",
    fetch_outcome: str = "",
    authors: list[str] | None = None,
    year: str = "",
    journal: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    """Build canonical YAML frontmatter block."""
    metadata: dict[str, Any] = {
        "doi": doi,
        "fetched_at": datetime.now(UTC).isoformat(),
        "extraction_status": "OK",
    }
    if title:
        metadata["title"] = title
    if source:
        metadata["source"] = source
    if publisher:
        metadata["publisher"] = publisher
    if fetch_outcome:
        metadata["fetch_outcome"] = fetch_outcome
    if year:
        metadata["year"] = year
    if journal:
        metadata["journal"] = journal
    if authors:
        metadata["authors_json"] = json.dumps([author for author in authors if author], ensure_ascii=False)
    if extra:
        for key, value in extra.items():
            metadata[key] = value

    post = frontmatter_lib.Post("", **metadata)
    return str(frontmatter_lib.dumps(post)).rstrip()


def _normalize_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized[key] = value
        else:
            normalized[key] = str(value)
    return normalized


def read_frontmatter_metadata(content: str) -> dict[str, str]:
    """Parse YAML frontmatter into plain string metadata."""
    if not content.startswith("---"):
        return {}
    try:
        post = frontmatter_lib.loads(content)
    except Exception:
        return {}
    return _normalize_metadata(post.metadata)


def strip_front_matter(content: str) -> str:
    """Return markdown body without YAML frontmatter."""
    if not content.startswith("---"):
        return content.strip()
    try:
        post = frontmatter_lib.loads(content)
    except Exception:
        return content.strip()
    return str(post.content).strip()


def read_frontmatter_metadata_from_file(path: Path, *, max_chars: int = _FRONTMATTER_SCAN_LIMIT) -> dict[str, str]:
    """Read only enough file content to parse complete frontmatter header."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
            if first_line.strip() != "---":
                return {}

            lines = [first_line]
            seen_chars = len(first_line)
            closed = False

            for line in handle:
                if seen_chars + len(line) > max_chars and line.strip() != "---":
                    break
                lines.append(line)
                seen_chars += len(line)
                if line.strip() == "---":
                    closed = True
                    break
                if seen_chars >= max_chars:
                    break
    except OSError:
        return {}

    if not closed:
        return {}
    return read_frontmatter_metadata("".join(lines))


def parse_authors_metadata(metadata: Mapping[str, str]) -> list[str]:
    raw = metadata.get("authors_json", "")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(author) for author in payload if str(author)]
