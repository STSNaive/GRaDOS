"""Corpus-layer defaults for canonical and future working paper records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CANONICAL_CORPUS",
    "CANONICAL_TIER",
    "DEFAULT_CANONICAL_METADATA",
    "normalize_corpus_metadata",
]

CANONICAL_CORPUS = "canonical"
CANONICAL_TIER = "stable"

DEFAULT_CANONICAL_METADATA: dict[str, str] = {
    "corpus": CANONICAL_CORPUS,
    "tier": CANONICAL_TIER,
    "workset_id": "",
    "promoted_at": "",
    "promote_reason": "",
}


def normalize_corpus_metadata(extra: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Return corpus-layer metadata with canonical defaults applied."""
    normalized = dict(DEFAULT_CANONICAL_METADATA)
    if not extra:
        return normalized

    for key, value in extra.items():
        if value is None:
            continue
        normalized[str(key)] = str(value)

    if not normalized.get("corpus", "").strip():
        normalized["corpus"] = CANONICAL_CORPUS
    if not normalized.get("tier", "").strip():
        normalized["tier"] = CANONICAL_TIER
    for key in ("workset_id", "promoted_at", "promote_reason"):
        normalized[key] = normalized.get(key, "")
    return normalized
