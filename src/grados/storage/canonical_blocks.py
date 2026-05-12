"""Canonical block registry for saved paper Markdown."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from grados.storage.chunking import split_paragraphs
from grados.storage.papers import PaperRecord, load_paper_record

__all__ = [
    "CANONICAL_BLOCK_PARSER_VERSION",
    "CanonicalBlock",
    "CanonicalBlockManifest",
    "build_canonical_block_manifest",
    "canonical_block_to_dict",
    "find_block_for_paragraph_window",
    "parse_block_ordinal",
]

CANONICAL_BLOCK_PARSER_VERSION = "canonical-blocks-v1"
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
_BLOCK_ID_PATTERN = re.compile(r"^paragraph-(\d{6})-[0-9a-f]{12}$")


@dataclass(frozen=True)
class CanonicalBlock:
    paper_id: str
    safe_doi: str
    doi: str
    canonical_uri: str
    block_id: str
    block_type: str
    heading_path: list[str]
    ordinal: int
    source_paragraph_index: int
    text: str
    text_sha256: str
    prev_hash: str
    next_hash: str
    doc_sha256: str
    parser_version: str = CANONICAL_BLOCK_PARSER_VERSION


@dataclass(frozen=True)
class CanonicalBlockManifest:
    paper_id: str
    safe_doi: str
    doi: str
    canonical_uri: str
    doc_sha256: str
    parser_version: str
    block_count: int
    blocks: list[CanonicalBlock]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _block_id(ordinal: int, text_sha256: str) -> str:
    return f"paragraph-{ordinal:06d}-{text_sha256[:12]}"


def _update_heading_path(heading_path: list[str], paragraph: str) -> list[str] | None:
    match = _HEADING_PATTERN.match(paragraph)
    if not match:
        return None
    level = len(match.group(1))
    heading = match.group(2).strip()
    next_path = heading_path[: max(0, level - 1)]
    next_path.append(heading)
    return next_path


def _build_blocks(record: PaperRecord, doc_sha256: str) -> list[CanonicalBlock]:
    paragraphs = split_paragraphs(record.content_markdown, include_front_matter=False)
    heading_path: list[str] = []
    pending: list[dict[str, Any]] = []

    for source_index, paragraph in enumerate(paragraphs):
        next_heading_path = _update_heading_path(heading_path, paragraph)
        if next_heading_path is not None:
            heading_path = next_heading_path
            continue

        text = paragraph.strip()
        if not text:
            continue
        text_sha256 = _sha256_text(text)
        ordinal = len(pending) + 1
        pending.append(
            {
                "paper_id": record.safe_doi,
                "safe_doi": record.safe_doi,
                "doi": record.doi,
                "canonical_uri": f"{record.canonical_uri}#block={_block_id(ordinal, text_sha256)}",
                "block_id": _block_id(ordinal, text_sha256),
                "block_type": "paragraph",
                "heading_path": list(heading_path),
                "ordinal": ordinal,
                "source_paragraph_index": source_index,
                "text": text,
                "text_sha256": text_sha256,
                "doc_sha256": doc_sha256,
            }
        )

    blocks: list[CanonicalBlock] = []
    hashes = [str(item["text_sha256"]) for item in pending]
    for index, item in enumerate(pending):
        blocks.append(
            CanonicalBlock(
                **item,
                prev_hash=hashes[index - 1] if index > 0 else "",
                next_hash=hashes[index + 1] if index + 1 < len(hashes) else "",
                parser_version=CANONICAL_BLOCK_PARSER_VERSION,
            )
        )
    return blocks


def build_canonical_block_manifest(
    papers_dir: Path,
    *,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
) -> CanonicalBlockManifest | None:
    """Build a deterministic paragraph-block manifest from `papers/*.md`."""
    record = load_paper_record(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)
    if record is None:
        return None

    doc_sha256 = _sha256_text(record.content_markdown)
    blocks = _build_blocks(record, doc_sha256)
    return CanonicalBlockManifest(
        paper_id=record.safe_doi,
        safe_doi=record.safe_doi,
        doi=record.doi,
        canonical_uri=record.canonical_uri,
        doc_sha256=doc_sha256,
        parser_version=CANONICAL_BLOCK_PARSER_VERSION,
        block_count=len(blocks),
        blocks=blocks,
    )


def canonical_block_to_dict(block: CanonicalBlock) -> dict[str, Any]:
    """Return a JSON-serializable block payload."""
    return asdict(block)


def parse_block_ordinal(block_id: str) -> int | None:
    """Extract a 1-based paragraph ordinal from a registry block id."""
    match = _BLOCK_ID_PATTERN.match(block_id.strip())
    if not match:
        return None
    return int(match.group(1))


def find_block_for_paragraph_window(
    manifest: CanonicalBlockManifest,
    *,
    start_paragraph: int | None,
    paragraph_count: int | None = None,
) -> CanonicalBlock | None:
    """Find the first materialized block inside a saved-paper paragraph window."""
    if not manifest.blocks:
        return None
    if start_paragraph is None:
        return manifest.blocks[0]

    count = max(1, paragraph_count or 1)
    window_start = max(0, start_paragraph)
    window_end = window_start + count
    for block in manifest.blocks:
        if window_start <= block.source_paragraph_index < window_end:
            return block
    return None
