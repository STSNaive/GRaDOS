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
_METADATA_FIELD_RE = re.compile(
    r"^\s*(?:[-*]\s*)?"
    r"(?:title|authors?|by|doi|journal|source|publisher|year|published|volume|issue|pages?|"
    r"keywords?|received|accepted|available\s+online|correspondence|affiliations?|orcid|"
    r"article\s+history|copyright|license|funding|conflicts?\s+of\s+interest)"
    r"\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_DOI_VALUE_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?10\.\S+$", re.IGNORECASE)
_JOURNAL_SECTION_RE = re.compile(r"^(?:journal|source|publisher|publication)\b", re.IGNORECASE)
_AUTHOR_NAME_TOKEN_RE = re.compile(
    r"^[A-Z][A-Za-z'`.-]+(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'`.-]+)){0,3}$"
)
_INITIAL_NAME_TOKEN_RE = re.compile(r"^[A-Z](?:\.\s*)+[A-Z][A-Za-z'`.-]+$")


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


def _metadata_field_lines(text: str) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for line in _nonempty_lines(text):
        stripped = _strip_heading_marker(line).strip()
        match = _METADATA_FIELD_RE.match(stripped)
        if match:
            label = stripped.split(":", 1)[0].split("：", 1)[0].strip().lower()
            fields.append((label, match.group(1).strip()))
    return fields


def _is_doi_only(text: str) -> bool:
    lines = [_strip_heading_marker(line).strip().strip(" .;") for line in _nonempty_lines(text)]
    if not lines:
        return False
    if len(lines) == 1 and _DOI_VALUE_RE.fullmatch(lines[0]):
        return True
    fields = _metadata_field_lines(text)
    return bool(fields) and all(
        label == "doi" and _DOI_VALUE_RE.fullmatch(value.strip(" .;"))
        for label, value in fields
    )


def _looks_like_author_name(value: str) -> bool:
    normalized = value.strip().strip(" .;")
    if not normalized:
        return False
    if any(char.isdigit() for char in normalized):
        return False
    lowered = normalized.lower()
    if any(
        marker in lowered
        for marker in (
            "study",
            "method",
            "result",
            "analysis",
            "experiment",
            "data",
            "model",
            "paper",
            "show",
            "demonstrat",
            "investigat",
            "propose",
        )
    ):
        return False
    return bool(_AUTHOR_NAME_TOKEN_RE.fullmatch(normalized) or _INITIAL_NAME_TOKEN_RE.fullmatch(normalized))


def _is_author_line(text: str) -> bool:
    lines = [_strip_heading_marker(line).strip() for line in _nonempty_lines(text)]
    if not lines:
        return False
    if len(lines) > 3:
        return False

    fields = _metadata_field_lines(text)
    if fields and all(label in {"author", "authors", "by"} for label, _ in fields):
        return True

    joined = " ".join(lines)
    if joined.lower().startswith("by "):
        joined = joined[3:].strip()
    chunks = [
        chunk.strip()
        for chunk in re.split(r"\s*(?:,|;|\band\b|&|\|)\s*", joined)
        if chunk.strip()
    ]
    return bool(chunks) and len(chunks) <= 20 and all(_looks_like_author_name(chunk) for chunk in chunks)


def _is_metadata_only(text: str) -> bool:
    lines = [_strip_heading_marker(line).strip() for line in _nonempty_lines(text)]
    if not lines:
        return False
    fields = _metadata_field_lines(text)
    return bool(fields) and len(fields) == len(lines)


def _is_journal_only(section_name: str, text: str) -> bool:
    plain = _plain_text(text)
    if not plain:
        return False
    if _metadata_field_lines(text) and all(
        label in {"journal", "source", "publisher", "publication"} for label, _ in _metadata_field_lines(text)
    ):
        return True
    if _JOURNAL_SECTION_RE.match(section_name.strip()):
        return len(re.findall(r"[.!?。！？]", plain)) == 0 and len(plain.split()) <= 12
    return False


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
    if _is_doi_only(text):
        return "doi_only"
    if _is_journal_only(section, text):
        return "journal_only"
    if _is_author_line(text):
        return "author_line"
    if _is_metadata_only(text):
        return "metadata_only"
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
