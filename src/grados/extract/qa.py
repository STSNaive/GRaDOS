"""Quality assurance for extracted paper content."""

from __future__ import annotations

import re

_PAYWALL_PATTERNS = [
    "purchase full access",
    "log in to view",
    "access provided by",
    "institution access required",
    "403 forbidden",
]

_STRUCTURE_RE = re.compile(
    r"abstract|introduction|methods|results|discussion|conclusion|references",
    re.IGNORECASE,
)


def is_valid_paper_content(
    text: str,
    min_characters: int = 1500,
    expected_title: str | None = None,
) -> bool:
    """Validate extracted paper text for quality.

    Checks: minimum length, no paywall markers, academic structure, optional title match.
    """
    if not text or len(text) < min_characters:
        return False

    text_lower = text.lower()
    for pattern in _PAYWALL_PATTERNS:
        if pattern in text_lower:
            return False

    matches = _STRUCTURE_RE.findall(text)
    if len(matches) < 2:
        return False

    if expected_title and len(expected_title) > 10:
        norm_title = _normalize(expected_title)
        norm_text = _normalize(text[:5000])
        if norm_title not in norm_text:
            short = norm_title[:50]
            if short not in norm_text:
                return False

    return True


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", s.lower())).strip()
