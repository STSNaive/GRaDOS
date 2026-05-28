"""Shared checks for whether a retrieved text fragment can act as evidence."""

from __future__ import annotations

import re

__all__ = [
    "classify_evidence_rejection",
    "is_backmatter_section",
    "is_citation_fragment",
    "is_evidence_eligible",
    "is_non_evidence_section",
    "is_title_only_or_empty",
]

_BACKMATTER_MARKERS = (
    "references",
    "bibliography",
    "works cited",
    "literature cited",
    "cited references",
    "reference list",
    "supplementary",
    "appendix",
    "参考文献",
)

_ADMIN_SECTION_MARKERS = (
    "credit",
    "author contribution",
    "acknowledg",
    "funding",
    "declaration",
    "conflict",
    "competing interest",
    "data availability",
    "ethics",
)

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]


def _strip_heading_marker(line: str) -> str:
    match = _HEADING_RE.match(line)
    return match.group(1).strip() if match else line.strip()


def _plain_text(text: str) -> str:
    lines = [_strip_heading_marker(line) for line in _nonempty_lines(text)]
    plain = " ".join(lines)
    plain = re.sub(r"\[[^\]]+\]", " ", plain)
    plain = re.sub(r"【[^】]+】", " ", plain)
    plain = re.sub(r"[\(（][^\)）]*\d{4}[^\)）]*[\)）]", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def is_backmatter_section(section_name: str) -> bool:
    normalized = _normalize(section_name)
    return any(marker in normalized for marker in _BACKMATTER_MARKERS)


def _is_administrative_section(section_name: str) -> bool:
    normalized = _normalize(section_name)
    return any(marker in normalized for marker in _ADMIN_SECTION_MARKERS)


def is_non_evidence_section(section_name: str) -> bool:
    return is_backmatter_section(section_name) or _is_administrative_section(section_name)


def is_title_only_or_empty(
    text: str,
    section_name: str | None = None,
    *,
    known_title: str | None = None,
) -> bool:
    lines = _nonempty_lines(text)
    if not lines:
        return True

    if all(_HEADING_RE.match(line) for line in lines):
        return True

    cleaned = " ".join(_strip_heading_marker(line) for line in lines).strip()
    if not cleaned:
        return True

    if section_name and _normalize(cleaned) == _normalize(section_name):
        return True

    if known_title and _normalize(cleaned) == _normalize(known_title):
        return True

    return len(lines) == 1 and bool(_HEADING_RE.match(lines[0]))


def is_citation_fragment(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text.strip())
    if not stripped:
        return False

    bare = stripped.strip(" .;,:")
    if re.fullmatch(r"(?:https?://(?:dx\.)?doi\.org/)?10\.\S+", bare, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[\[【]?\d+(?:\s*[-,]\s*\d+)*[\]】]?", bare):
        return True
    if re.fullmatch(r"[\(（\[][^()\[\]（）]{1,120}\d{4}[a-z]?[\)）\]]", bare, flags=re.IGNORECASE):
        return True

    plain = _plain_text(stripped)
    return bool(plain) and not re.search(r"[A-Za-z\u3400-\u9fff]{2,}", plain)


def classify_evidence_rejection(
    section_name: str | None,
    text: str,
    *,
    allow_backmatter: bool = False,
    known_title: str | None = None,
) -> str | None:
    section = section_name or ""
    if section and not allow_backmatter and is_backmatter_section(section):
        return "backmatter_section"
    if section and _is_administrative_section(section):
        return "administrative_section"
    if is_title_only_or_empty(text, section, known_title=known_title):
        return "title_only"
    if is_citation_fragment(text):
        return "citation_fragment"

    plain = _plain_text(text)
    substantive_chars = re.findall(r"[A-Za-z0-9\u3400-\u9fff]", plain)
    if len(substantive_chars) < 12:
        return "too_short"
    return None


def is_evidence_eligible(
    section_name: str | None,
    text: str,
    *,
    allow_backmatter: bool = False,
    known_title: str | None = None,
) -> bool:
    return (
        classify_evidence_rejection(
            section_name,
            text,
            allow_backmatter=allow_backmatter,
            known_title=known_title,
        )
        is None
    )
