"""Common publisher utilities: PDF validation, bot detection, redirect parsing."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PublisherMetadata(BaseModel):
    """Shared publisher metadata shape that can cross fetch / save boundaries safely."""

    model_config = ConfigDict(extra="ignore")

    doi: str = ""
    title: str = ""
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    year: str = ""
    journal: str = ""
    publisher: str = ""
    openaccess: bool = False
    pii: str = ""
    eid: str = ""
    scidir_url: str = ""
    html_url: str = ""
    pdf_url: str = ""


def normalize_publisher_metadata(value: Any) -> PublisherMetadata | None:
    """Normalize dicts, dataclasses, or model instances into one shared metadata model."""
    if value is None:
        return None
    if isinstance(value, PublisherMetadata):
        return value

    try:
        metadata = PublisherMetadata.model_validate(value, from_attributes=True)
    except ValidationError:
        return None

    if not (
        metadata.doi.strip()
        or metadata.title.strip()
        or metadata.abstract.strip()
        or metadata.authors
        or metadata.year.strip()
        or metadata.journal.strip()
        or metadata.publisher.strip()
        or metadata.openaccess
        or metadata.pii.strip()
        or metadata.eid.strip()
        or metadata.scidir_url.strip()
        or metadata.html_url.strip()
        or metadata.pdf_url.strip()
    ):
        return None
    return metadata


def classify_pdf_content(data: bytes, content_type: str = "") -> dict[str, object]:
    """Validate whether a response body is a real PDF.

    Returns dict with keys: is_pdf (bool), reason (str).
    """
    if not data:
        return {"is_pdf": False, "reason": "empty_body"}

    has_magic = data[:5] == b"%PDF-"
    preview = data[:512].decode("latin-1", errors="replace").lower()

    # HTML / challenge page detection
    html_markers = ["<html", "<!doctype", "<body", "cf-browser", "captcha", "are you a robot"]
    if any(m in preview for m in html_markers):
        return {"is_pdf": False, "reason": "html_or_challenge_page"}

    if "text/html" in content_type.lower():
        return {"is_pdf": False, "reason": "html_or_challenge_page"}

    if has_magic:
        return {"is_pdf": True, "reason": "ok"}

    if "application/pdf" in content_type.lower():
        return {"is_pdf": False, "reason": "pdf_content_type_without_magic_bytes"}

    return {"is_pdf": False, "reason": "missing_pdf_magic_bytes"}


def detect_bot_challenge(title: str, html: str, url: str = "") -> bool:
    """Detect Cloudflare or publisher bot challenges."""
    title_lower = title.lower()
    challenge_titles = ["just a moment", "attention required", "are you a robot", "请稍候"]
    if any(t in title_lower for t in challenge_titles):
        return True

    combined = (html + url).lower()
    challenge_markers = ["cf-browser", "challenges.cloudflare.com", "captcha", "recaptcha"]
    return any(m in combined for m in challenge_markers)


DOI_STRICT_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def looks_like_doi(value: str) -> bool:
    return bool(DOI_STRICT_PATTERN.match(value.strip()))


def normalize_doi(doi: str) -> str:
    return doi.strip().lower()


SAFE_DOI_SELECTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")


def legacy_safe_doi_filename(doi: str) -> str:
    """Return the pre-v2 readable DOI slug used by older GRaDOS libraries."""
    return re.sub(r"[^a-zA-Z0-9]", "_", doi)


def safe_doi_filename(doi: str) -> str:
    """Convert DOI to a collision-resistant filesystem-safe paper id."""
    normalized = normalize_doi(doi)
    legacy_slug = legacy_safe_doi_filename(normalized).strip("_") or "doi"
    if len(legacy_slug) > 180:
        legacy_slug = legacy_slug[:180].rstrip("_") or "doi"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{legacy_slug}__{digest}"


def safe_doi_filename_candidates(doi: str) -> list[str]:
    """Return current and legacy safe DOI ids for backward-compatible lookups."""
    normalized = normalize_doi(doi)
    current = safe_doi_filename(normalized)
    legacy = legacy_safe_doi_filename(normalized)
    return [current] if current == legacy else [current, legacy]


def is_safe_doi_filename(value: str) -> bool:
    """Return true if a selector is a filename token, not a path fragment."""
    return bool(SAFE_DOI_SELECTOR_PATTERN.fullmatch(value.strip()))
