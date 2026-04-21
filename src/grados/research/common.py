"""Shared helpers for research module workflows."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from grados.storage.chunking import extract_sections
from grados.storage.papers import PaperRecord, load_paper_record
from grados.storage.paths import resolve_papers_dir

_METHOD_SECTION_NAMES = {
    "methods",
    "materials and methods",
    "methodology",
    "experimental",
    "experiments",
    "materials",
}
_RESULT_SECTION_NAMES = {
    "results",
    "results and discussion",
    "findings",
    "evaluation",
    "experiments and results",
    "discussion",
}
_REFERENCE_SECTION_NAMES = {
    "references",
    "bibliography",
    "works cited",
    "literature cited",
    "参考文献",
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9]{3,}", query.lower()) if term]


def _section_matches(section_name: str, section_filter: list[str] | None) -> bool:
    if not section_filter:
        return True
    normalized = _normalize_text(section_name)
    candidates = {_normalize_text(value) for value in section_filter if value.strip()}
    return any(candidate in normalized or normalized in candidate for candidate in candidates)


def _resolve_documents(chroma_dir: Path, dois: list[str]) -> tuple[list[PaperRecord], list[str]]:
    papers_dir = resolve_papers_dir(chroma_dir)
    resolved: list[PaperRecord] = []
    missing: list[str] = []
    for doi in dois:
        record = load_paper_record(papers_dir, doi=doi)
        if not record:
            missing.append(doi)
            continue
        resolved.append(record)
    return resolved, missing


def _select_sections(
    record: PaperRecord,
    *,
    section_filter: list[str] | None = None,
    focus: str = "full_text",
) -> list[dict[str, Any]]:
    markdown = record.content_markdown
    all_sections = extract_sections(markdown, fallback_title=record.title)
    if not all_sections:
        return []

    sections = all_sections
    if focus == "methods":
        sections = [
            section
            for section in all_sections
            if _normalize_text(str(section["name"])) in _METHOD_SECTION_NAMES
        ]
    elif focus == "results":
        sections = [
            section
            for section in all_sections
            if _normalize_text(str(section["name"])) in _RESULT_SECTION_NAMES
        ]
    elif focus == "references":
        sections = [
            section
            for section in all_sections
            if _normalize_text(str(section["name"])) in _REFERENCE_SECTION_NAMES
        ]

    if not sections:
        sections = all_sections

    selected = [section for section in sections if _section_matches(str(section["name"]), section_filter)]
    return selected or sections


def _excerpt_for_axis(text: str, axis: str, max_chars: int = 260) -> str:
    axis_terms = _query_terms(axis)
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    if not paragraphs:
        return ""

    best_paragraph = ""
    best_score = -1
    for paragraph in paragraphs:
        score = sum(paragraph.lower().count(term) for term in axis_terms)
        if score > best_score:
            best_score = score
            best_paragraph = paragraph
    excerpt = re.sub(r"\s+", " ", best_paragraph).strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[: max_chars - 3].rstrip() + "..."
