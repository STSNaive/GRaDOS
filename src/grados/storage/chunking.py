"""Section-aware chunking and canonical markdown primitives.

This module owns the paragraph/section/chunk vocabulary used by the retrieval
index. Kept intentionally narrow: no Chroma dependency, no embedding backend,
no hydration — just markdown → structured sections/chunks with absolute
paragraph coordinates.
"""

from __future__ import annotations

import re
from typing import Any

from grados.config import GRaDOSPaths, IndexingConfig, load_config
from grados.storage.frontmatter import strip_front_matter as strip_markdown_front_matter

__all__ = [
    "DOI_PATTERN",
    "DOC_SUMMARY_MAX_CHARS",
    "build_doc_summary",
    "chunk_text",
    "extract_headings",
    "extract_reference_dois",
    "extract_sections",
    "find_section_content",
    "normalize_doi",
    "resolve_indexing_config",
    "split_paragraphs",
    "strip_frontmatter",
]

DOC_SUMMARY_MAX_CHARS = 4000
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)


def resolve_indexing_config(indexing_config: IndexingConfig | None) -> IndexingConfig:
    if indexing_config is not None:
        return indexing_config
    return load_config(GRaDOSPaths()).indexing


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown."""
    return strip_markdown_front_matter(text)


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n{2,}", text.strip()) if part.strip()]


def extract_headings(markdown: str) -> list[str]:
    return re.findall(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)[:20]


def extract_sections(markdown: str, *, fallback_title: str = "") -> list[dict[str, Any]]:
    """Split markdown into section objects while preserving absolute paragraph metadata."""
    sections: list[dict[str, Any]] = []
    paragraphs = split_paragraphs(markdown)
    current_heading = ""
    current_name = fallback_title.strip() or "Preamble"
    current_level = 0
    current_heading_index: int | None = None
    current_paragraphs: list[str] = []
    current_body_start: int | None = None

    def flush() -> None:
        if not current_paragraphs:
            return

        body = "\n\n".join(current_paragraphs).strip()
        text_parts = [current_heading] if current_heading else []
        text_parts.extend(current_paragraphs)
        section_start = current_heading_index
        if section_start is None:
            section_start = current_body_start if current_body_start is not None else 0

        sections.append(
            {
                "name": current_name,
                "level": current_level,
                "heading": current_heading,
                "content": body,
                "text": "\n\n".join(part for part in text_parts if part).strip(),
                "heading_paragraph_index": current_heading_index,
                "body_paragraph_start": current_body_start if current_body_start is not None else section_start,
                "paragraph_start": section_start,
                "paragraph_count": len(current_paragraphs) + (1 if current_heading else 0),
            }
        )

    for index, paragraph in enumerate(paragraphs):
        match = re.match(r"^(#{1,6})\s+(.+)$", paragraph)
        if match:
            flush()
            current_heading = paragraph
            current_name = match.group(2).strip()
            current_level = len(match.group(1))
            current_heading_index = index
            current_paragraphs = []
            current_body_start = None
            continue

        if current_body_start is None:
            current_body_start = index
        current_paragraphs.append(paragraph)

    flush()
    return sections


def chunk_text(
    text: str,
    indexing_config: IndexingConfig | None = None,
    *,
    fallback_title: str = "",
) -> list[dict[str, Any]]:
    """Split paper markdown into section-aware retrieval chunks."""
    config = resolve_indexing_config(indexing_config)
    sections = extract_sections(text, fallback_title=fallback_title)
    chunks: list[dict[str, Any]] = []

    for section_index, section in enumerate(sections):
        body_paragraphs = split_paragraphs(str(section["content"]))
        if not body_paragraphs:
            continue
        body_start = int(section.get("body_paragraph_start", 0) or 0)
        heading_index = section.get("heading_paragraph_index")
        has_heading = isinstance(heading_index, int)

        start = 0
        while start < len(body_paragraphs):
            current: list[str] = []
            current_length = 0
            index = start
            while index < len(body_paragraphs):
                paragraph = body_paragraphs[index]
                proposed = current_length + len(paragraph) + (2 if current else 0)
                if current and current_length >= config.chunk_min_chars and proposed > config.chunk_max_chars:
                    break
                current.append(paragraph)
                current_length = proposed
                index += 1
                if current_length >= config.chunk_max_chars:
                    break

            if not current:
                break

            parts = [str(section["heading"]).strip()] if section["heading"] else []
            parts.extend(current)
            paragraph_start = body_start + start
            paragraph_count = len(current)
            if start == 0 and has_heading:
                paragraph_start = int(heading_index)
                paragraph_count += 1
            chunks.append(
                {
                    "text": "\n\n".join(part for part in parts if part).strip(),
                    "section_name": str(section["name"]),
                    "section_level": int(section["level"]),
                    "section_index": section_index,
                    "paragraph_start": paragraph_start,
                    "paragraph_count": paragraph_count,
                }
            )

            if index >= len(body_paragraphs):
                break

            next_start = max(start + 1, index - max(0, config.chunk_overlap_paragraphs))
            if next_start == start:
                next_start = index
            start = next_start

    return chunks


def find_section_content(sections: list[dict[str, Any]], candidates: set[str]) -> str:
    for section in sections:
        name = str(section["name"]).strip().lower()
        if name in candidates:
            return str(section["content"]).strip()
    return ""


def build_doc_summary(title: str, body: str, sections: list[dict[str, Any]]) -> tuple[str, str]:
    """Prefer abstract for doc-level retrieval; fall back to title + intro lead."""
    abstract = find_section_content(sections, {"abstract", "摘要", "summary"})
    if abstract:
        return abstract[:DOC_SUMMARY_MAX_CHARS], "abstract"

    intro = find_section_content(
        sections,
        {"introduction", "intro", "background", "overview", "引言", "研究背景"},
    )
    if intro:
        prefix = f"{title.strip()}\n\n" if title.strip() else ""
        return (prefix + intro)[:DOC_SUMMARY_MAX_CHARS], "title_plus_intro"

    lead = "\n\n".join(split_paragraphs(body)[:3]) or body
    prefix = f"{title.strip()}\n\n" if title.strip() else ""
    return (prefix + lead.strip())[:DOC_SUMMARY_MAX_CHARS], "title_plus_lead"


def normalize_doi(value: str) -> str:
    return re.sub(r"[)\].,;:]+$", "", value.strip().lower())


def extract_reference_dois(markdown: str) -> list[str]:
    """Extract DOI references from bibliography-like sections."""
    sections = extract_sections(markdown)
    reference_sections = [
        section
        for section in sections
        if str(section["name"]).strip().lower()
        in {"references", "bibliography", "works cited", "literature cited", "参考文献"}
    ]
    if reference_sections:
        search_space = "\n\n".join(str(section["text"]) for section in reference_sections)
    else:
        search_space = markdown

    seen: set[str] = set()
    citations: list[str] = []
    for match in DOI_PATTERN.findall(search_space):
        normalized = normalize_doi(match)
        if normalized in seen:
            continue
        seen.add(normalized)
        citations.append(normalized)
    return citations
