"""Common publisher utilities: PDF validation, bot detection, redirect parsing."""

from __future__ import annotations

import re


def classify_pdf_content(data: bytes, content_type: str = "") -> dict:
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


_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def looks_like_doi(value: str) -> bool:
    return bool(_DOI_PATTERN.match(value.strip()))


def normalize_doi(doi: str) -> str:
    return doi.strip().lower()


def safe_doi_filename(doi: str) -> str:
    """Convert DOI to a filesystem-safe filename."""
    return re.sub(r"[^a-zA-Z0-9]", "_", doi)
