"""Stable operation aliases for long-running server tools."""

from __future__ import annotations

import re

_DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)


def normalize_operation_doi(value: str) -> str:
    doi = str(value or "").strip()
    if doi.lower().startswith("doi:"):
        doi = doi.split(":", 1)[1].strip()
    doi = _DOI_URL_RE.sub("", doi).strip()
    return doi.strip(" .;,").lower()


def extract_full_text_idempotency_key(doi: str) -> str:
    normalized = normalize_operation_doi(doi)
    return f"extract_paper_full_text:doi:{normalized}" if normalized else ""


def extract_full_text_doi_alias_key(value: str) -> str:
    normalized = normalize_operation_doi(value)
    if not normalized:
        return ""
    lowered = str(value or "").strip().lower()
    if lowered.startswith(
        ("doi:", "10.", "https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
    ):
        return extract_full_text_idempotency_key(normalized)
    return ""
