"""Parser provenance sidecars for canonical paper Markdown."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.storage.chunking import split_paragraphs

PARSED_SIDECAR_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ParsedSidecarSummary:
    has_parsed_manifest: bool
    manifest_path: str = ""
    schema_version: int = PARSED_SIDECAR_SCHEMA_VERSION
    parser: str = ""
    parser_version: str = ""
    block_count: int = 0
    page_range: str = ""
    has_source_pdf_hash: bool = False
    has_canonical_markdown_hash: bool = False
    assets_manifest_path: str = ""


@dataclass(frozen=True)
class ParsedManifestSaveResult:
    manifest_path: str = ""
    warnings: list[str] = field(default_factory=list)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parsed_manifest(
    *,
    doi: str,
    safe_doi: str,
    markdown: str,
    parser: str = "",
    parser_version: str = "",
    source_pdf: str = "",
    source_pdf_hash: str = "",
    canonical_markdown: str = "",
    assets_manifest_path: str = "",
) -> dict[str, Any]:
    """Build the minimal parser provenance sidecar from canonical Markdown.

    Most current parsers do not expose stable parser-native blocks at the GRaDOS
    boundary. The sidecar therefore records deterministic Markdown paragraph
    blocks plus parser/source hashes; future parser-native page/bbox data can be
    added without changing the canonical Markdown contract.
    """

    markdown_hash = sha256_text(markdown)
    paragraphs = split_paragraphs(markdown, include_front_matter=False)
    blocks: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs):
        text_hash = sha256_text(paragraph)
        blocks.append(
            {
                "block_id": f"md_p{index + 1:05d}_{text_hash[:12]}",
                "type": "paragraph",
                "page": None,
                "bbox": [],
                "text_hash": text_hash,
                "markdown_paragraph_start": index,
                "markdown_paragraph_count": 1,
            }
        )

    return {
        "schema_version": PARSED_SIDECAR_SCHEMA_VERSION,
        "doi": doi,
        "safe_doi": safe_doi,
        "source_pdf": source_pdf,
        "source_pdf_hash": source_pdf_hash,
        "canonical_markdown": canonical_markdown or f"{safe_doi}.md",
        "canonical_markdown_hash": markdown_hash,
        "parser": parser,
        "parser_version": parser_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "assets_manifest_path": assets_manifest_path,
        "blocks": blocks,
    }


def save_parsed_manifest(
    papers_dir: Path,
    *,
    doi: str,
    safe_doi: str,
    markdown: str,
    parser: str = "",
    parser_version: str = "",
    source_pdf: str = "",
    source_pdf_hash: str = "",
    canonical_markdown: str = "",
    assets_manifest_path: str = "",
) -> ParsedManifestSaveResult:
    """Persist `papers/_parsed/{safe_doi}.json` and return its relative path."""

    try:
        parsed_root = (papers_dir / "_parsed").resolve()
        manifest_path = (parsed_root / f"{safe_doi}.json").resolve()
        manifest_path.relative_to(parsed_root)
        parsed_root.mkdir(parents=True, exist_ok=True)
        payload = build_parsed_manifest(
            doi=doi,
            safe_doi=safe_doi,
            markdown=markdown,
            parser=parser,
            parser_version=parser_version,
            source_pdf=source_pdf,
            source_pdf_hash=source_pdf_hash,
            canonical_markdown=canonical_markdown,
            assets_manifest_path=assets_manifest_path,
        )
        manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return ParsedManifestSaveResult(manifest_path=str(manifest_path.relative_to(papers_dir.resolve())))
    except Exception as exc:
        return ParsedManifestSaveResult(
            warnings=[f"Parsed sidecar write failed: {exc.__class__.__name__}: {exc}"]
        )


def load_parsed_manifest(papers_dir: Path, manifest_path: str) -> dict[str, Any] | None:
    if not manifest_path:
        return None
    path = Path(manifest_path)
    if not path.is_absolute():
        path = papers_dir / path
    try:
        path = path.resolve()
        path.relative_to(papers_dir.resolve())
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def parsed_manifest_summary(papers_dir: Path, manifest_path: str) -> ParsedSidecarSummary:
    payload = load_parsed_manifest(papers_dir, manifest_path)
    if payload is None:
        return ParsedSidecarSummary(has_parsed_manifest=False, manifest_path=manifest_path)

    blocks = payload.get("blocks")
    block_items = [block for block in blocks if isinstance(block, dict)] if isinstance(blocks, list) else []
    pages = [
        int(block["page"])
        for block in block_items
        if isinstance(block.get("page"), int) and int(block["page"]) > 0
    ]
    page_range = ""
    if pages:
        page_range = f"{min(pages)}-{max(pages)}" if min(pages) != max(pages) else str(pages[0])

    return ParsedSidecarSummary(
        has_parsed_manifest=True,
        manifest_path=manifest_path,
        schema_version=int(payload.get("schema_version") or PARSED_SIDECAR_SCHEMA_VERSION),
        parser=str(payload.get("parser") or ""),
        parser_version=str(payload.get("parser_version") or ""),
        block_count=len(block_items),
        page_range=page_range,
        has_source_pdf_hash=bool(payload.get("source_pdf_hash")),
        has_canonical_markdown_hash=bool(payload.get("canonical_markdown_hash")),
        assets_manifest_path=str(payload.get("assets_manifest_path") or ""),
    )
