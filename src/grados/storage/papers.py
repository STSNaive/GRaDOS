"""Paper storage: canonical markdown mirrors under papers/."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.publisher.common import safe_doi_filename
from grados.storage.chunking import split_paragraphs, strip_frontmatter
from grados.storage.frontmatter import (
    build_front_matter,
    parse_authors_metadata,
    read_frontmatter_metadata,
    read_frontmatter_metadata_from_file,
)

# ── Save / Read ──────────────────────────────────────────────────────────────


@dataclass
class PaperSavedSummary:
    doi: str
    safe_doi: str
    file_path: str
    uri: str
    word_count: int
    char_count: int
    section_headings: list[str]
    mirror_written: bool
    index_status: str
    index_error: str


def save_paper_markdown(
    doi: str,
    markdown: str,
    papers_dir: Path,
    title: str = "",
    source: str = "",
    publisher: str = "",
    fetch_outcome: str = "",
    extra_frontmatter: dict[str, str] | None = None,
    chroma_dir: Path | None = None,
    *,
    authors: list[str] | None = None,
    year: str = "",
    journal: str = "",
    write_mirror: bool = True,
) -> PaperSavedSummary:
    """Save parsed paper as markdown with YAML frontmatter.

    `papers/` is the canonical source of truth. If `chroma_dir` is provided,
    the search index is refreshed from the same markdown body.
    """
    safe = safe_doi_filename(doi)
    file_path = papers_dir / f"{safe}.md"
    headings = re.findall(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)
    mirror_written = False
    index_status = "not_requested"
    index_error = ""

    if write_mirror:
        papers_dir.mkdir(parents=True, exist_ok=True)
        front = build_front_matter(
            doi,
            title,
            source,
            publisher,
            fetch_outcome,
            authors=authors,
            year=year,
            journal=journal,
            extra=extra_frontmatter,
        )
        content = f"{front}\n\n{markdown}"
        file_path.write_text(content, encoding="utf-8")
        mirror_written = True

    if chroma_dir and mirror_written:
        try:
            from grados.storage.vector import index_paper

            index_paper(
                chroma_dir,
                doi,
                safe,
                title,
                markdown,
                source=source,
                fetch_outcome=fetch_outcome,
                authors=authors,
                year=year,
                journal=journal,
                section_headings=headings[:20],
                assets_manifest_path=(extra_frontmatter or {}).get("assets_manifest_path", ""),
            )
            index_status = "indexed"
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}" if str(exc).strip() else exc.__class__.__name__
            index_status = "failed"
            index_error = re.sub(r"\s+", " ", message).strip()

    return PaperSavedSummary(
        doi=doi,
        safe_doi=safe,
        file_path=str(file_path),
        uri=f"grados://papers/{safe}",
        word_count=len(markdown.split()),
        char_count=len(markdown),
        section_headings=headings[:20],
        mirror_written=mirror_written,
        index_status=index_status,
        index_error=index_error,
    )


def save_pdf(doi: str, pdf_buffer: bytes, downloads_dir: Path) -> Path:
    """Save raw PDF to downloads directory."""
    downloads_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_doi_filename(doi)
    path = downloads_dir / f"{safe}.pdf"
    path.write_bytes(pdf_buffer)
    return path


def save_asset_manifest(
    doi: str,
    papers_dir: Path,
    *,
    source: str = "",
    asset_hints: list[dict[str, Any]] | None = None,
) -> str:
    """Persist asset hints as a paper-bound sidecar manifest.

    Returns a path relative to papers_dir for frontmatter / canonical metadata use.
    """
    hints = asset_hints or []
    if not hints:
        return ""

    safe = safe_doi_filename(doi)
    assets_dir = papers_dir / "_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = assets_dir / f"{safe}.json"

    figures = [hint for hint in hints if "figure" in str(hint.get("kind", "")).lower()]
    tables = [hint for hint in hints if "table" in str(hint.get("kind", "")).lower()]
    objects = [
        hint for hint in hints
        if hint not in figures and hint not in tables
    ]

    payload = {
        "doi": doi,
        "safe_doi": safe,
        "source": source,
        "generated_at": datetime.now(UTC).isoformat(),
        "figures": figures,
        "tables": tables,
        "objects": objects,
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(manifest_path.relative_to(papers_dir))


# ── Paper reading with paragraph windowing ───────────────────────────────────


@dataclass
class PaperReadResult:
    doi: str
    text: str
    start_paragraph: int
    paragraph_count: int
    total_paragraphs: int
    truncated: bool
    section_headings: list[str]


@dataclass
class PaperStructureResult:
    doi: str
    safe_doi: str
    canonical_uri: str
    title: str
    source: str
    fetch_outcome: str
    authors: list[str]
    year: str
    journal: str
    word_count: int
    char_count: int
    paragraph_count: int
    preview_excerpt: str
    section_headings: list[str]
    section_outline: list[PaperSectionOutlineEntry]
    assets_summary: PaperAssetsSummary


@dataclass(frozen=True)
class PaperRecord:
    doi: str
    safe_doi: str
    canonical_uri: str
    title: str
    source: str
    fetch_outcome: str
    authors: list[str]
    year: str
    journal: str
    section_headings: list[str]
    assets_manifest_path: str
    word_count: int
    char_count: int
    content_markdown: str


@dataclass(frozen=True)
class PaperListEntry:
    file: str
    doi: str
    title: str
    safe_doi: str


@dataclass(frozen=True)
class PaperSectionOutlineEntry:
    heading: str
    level: int
    paragraph_index: int


@dataclass(frozen=True)
class PaperAssetsSummary:
    has_assets: bool
    manifest_path: str
    figures: int
    tables: int
    objects: int


def load_paper_record(
    papers_dir: Path,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
    chroma_dir: Path | None = None,
) -> PaperRecord | None:
    """Load the canonical markdown-backed paper record from `papers/*.md`."""
    safe_doi = _resolve_safe_doi(doi=doi, safe_doi=safe_doi, uri=uri)
    if not safe_doi:
        return None

    _ = chroma_dir
    file_path = papers_dir / f"{safe_doi}.md"
    if not file_path.is_file():
        return None

    raw_content = file_path.read_text(encoding="utf-8")
    metadata = read_frontmatter_metadata(raw_content)
    content = strip_frontmatter(raw_content)
    paragraphs = split_paragraphs(raw_content, include_front_matter=False)
    headings = [
        re.sub(r"^#{1,6}\s+", "", paragraph).strip()
        for paragraph in paragraphs
        if re.match(r"^#{1,6}\s+", paragraph)
    ][:20]
    title = metadata.get("title", "").strip() or _infer_title_from_paragraphs(paragraphs)

    return PaperRecord(
        doi=metadata.get("doi", doi or safe_doi),
        safe_doi=safe_doi,
        canonical_uri=f"grados://papers/{safe_doi}",
        title=title,
        source=metadata.get("source", ""),
        fetch_outcome=metadata.get("fetch_outcome", ""),
        authors=parse_authors_metadata(metadata),
        year=metadata.get("year", ""),
        journal=metadata.get("journal", ""),
        section_headings=headings,
        assets_manifest_path=metadata.get("assets_manifest_path", ""),
        word_count=len(content.split()),
        char_count=len(content),
        content_markdown=content,
    )


def read_paper(
    papers_dir: Path,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
    start_paragraph: int = 0,
    max_paragraphs: int = 20,
    section_query: str | None = None,
    include_front_matter: bool = False,
    chroma_dir: Path | None = None,
) -> PaperReadResult | None:
    """Read a saved paper with paragraph windowing."""
    if uri and uri.startswith("grados://papers/"):
        safe_doi = uri.replace("grados://papers/", "").strip("/")
    elif doi and not safe_doi:
        safe_doi = safe_doi_filename(doi)

    if not safe_doi:
        return None

    _ = chroma_dir
    file_path = papers_dir / f"{safe_doi}.md"
    if not file_path.is_file():
        return None

    content = file_path.read_text(encoding="utf-8")
    paragraphs = split_paragraphs(content, include_front_matter=include_front_matter)
    headings = [
        re.sub(r"^#{1,6}\s+", "", p).strip()
        for p in paragraphs
        if re.match(r"^#{1,6}\s+", p)
    ]
    metadata = read_frontmatter_metadata(content)
    resolved_doi = metadata.get("doi", doi or safe_doi)

    if not paragraphs:
        return None

    if section_query:
        start_paragraph = _find_section_start(paragraphs, section_query)

    start_paragraph = max(0, min(start_paragraph, len(paragraphs) - 1))
    window = paragraphs[start_paragraph : start_paragraph + max_paragraphs]

    return PaperReadResult(
        doi=resolved_doi,
        text="\n\n".join(window),
        start_paragraph=start_paragraph,
        paragraph_count=len(window),
        total_paragraphs=len(paragraphs),
        truncated=start_paragraph + max_paragraphs < len(paragraphs),
        section_headings=headings[:20],
    )


def get_paper_structure(
    papers_dir: Path,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
    chroma_dir: Path | None = None,
) -> PaperStructureResult | None:
    """Return a compact, deterministic structure card for a saved paper."""
    record = load_paper_record(
        papers_dir,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
        chroma_dir=chroma_dir,
    )
    if not record:
        return None

    safe_doi = record.safe_doi
    content = record.content_markdown
    paragraphs = split_paragraphs(content, include_front_matter=False)
    headings = list(record.section_headings)
    if not headings:
        headings = [
            re.sub(r"^#{1,6}\s+", "", paragraph).strip()
            for paragraph in paragraphs
            if re.match(r"^#{1,6}\s+", paragraph)
        ][:20]

    return PaperStructureResult(
        doi=record.doi or (doi or safe_doi),
        safe_doi=safe_doi,
        canonical_uri=record.canonical_uri or f"grados://papers/{safe_doi}",
        title=record.title.strip() or _infer_title_from_paragraphs(paragraphs),
        source=record.source,
        fetch_outcome=record.fetch_outcome,
        authors=[str(author) for author in record.authors if str(author)],
        year=record.year,
        journal=record.journal,
        word_count=record.word_count or len(content.split()),
        char_count=record.char_count or len(content),
        paragraph_count=len(paragraphs),
        preview_excerpt=_preview_excerpt(paragraphs),
        section_headings=headings[:20],
        section_outline=_build_section_outline(paragraphs),
        assets_summary=_load_assets_summary(papers_dir, record),
    )


def _find_section_start(paragraphs: list[str], query: str) -> int:
    """Find the paragraph index matching a section heading query."""
    norm_q = _normalize_text(query)

    # 1. Exact heading match
    for i, p in enumerate(paragraphs):
        if re.match(r"^#{1,6}\s+", p):
            heading = re.sub(r"^#{1,6}\s+", "", p).strip()
            if _normalize_text(heading) == norm_q:
                return i

    # 2. Partial heading match
    for i, p in enumerate(paragraphs):
        if re.match(r"^#{1,6}\s+", p):
            if norm_q in _normalize_text(p):
                return i

    # 3. Any paragraph match
    for i, p in enumerate(paragraphs):
        if norm_q in _normalize_text(p):
            return i

    return 0


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", s.lower())).strip()


def _resolve_safe_doi(doi: str | None, safe_doi: str | None, uri: str | None) -> str | None:
    if uri and uri.startswith("grados://papers/"):
        return uri.replace("grados://papers/", "").strip("/")
    if safe_doi:
        return safe_doi
    if doi:
        return safe_doi_filename(doi)
    return None


def _infer_title_from_paragraphs(paragraphs: list[str]) -> str:
    if not paragraphs:
        return ""
    first = paragraphs[0]
    return re.sub(r"^#{1,6}\s+", "", first).strip()


def _preview_excerpt(paragraphs: list[str], max_chars: int = 320) -> str:
    for paragraph in paragraphs:
        if re.match(r"^#{1,6}\s+", paragraph):
            continue
        excerpt = re.sub(r"\s+", " ", paragraph).strip()
        if len(excerpt) <= max_chars:
            return excerpt
        return excerpt[: max_chars - 3].rstrip() + "..."
    return ""


def _build_section_outline(paragraphs: list[str]) -> list[PaperSectionOutlineEntry]:
    outline: list[PaperSectionOutlineEntry] = []
    for index, paragraph in enumerate(paragraphs):
        match = re.match(r"^(#{1,6})\s+(.+)$", paragraph)
        if not match:
            continue
        outline.append(
            PaperSectionOutlineEntry(
                heading=match.group(2).strip(),
                level=len(match.group(1)),
                paragraph_index=index,
            )
        )
    return outline[:40]


def _load_assets_summary(papers_dir: Path, record: PaperRecord) -> PaperAssetsSummary:
    manifest_path = record.assets_manifest_path or ""
    summary = PaperAssetsSummary(
        has_assets=False,
        manifest_path=manifest_path,
        figures=0,
        tables=0,
        objects=0,
    )
    if not manifest_path:
        return summary

    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = papers_dir / manifest_file
    if not manifest_file.is_file():
        return PaperAssetsSummary(
            has_assets=False,
            manifest_path=manifest_path,
            figures=0,
            tables=0,
            objects=0,
        )

    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return summary

    return PaperAssetsSummary(
        has_assets=True,
        manifest_path=manifest_path,
        figures=len(payload.get("figures", [])) if isinstance(payload, dict) else 0,
        tables=len(payload.get("tables", [])) if isinstance(payload, dict) else 0,
        objects=len(payload.get("objects", [])) if isinstance(payload, dict) else 0,
    )


def list_saved_papers(papers_dir: Path, chroma_dir: Path | None = None) -> list[PaperListEntry]:
    """List all saved papers with basic metadata."""
    _ = chroma_dir

    results: list[PaperListEntry] = []
    if not papers_dir.is_dir():
        return results
    for f in sorted(papers_dir.glob("*.md")):
        metadata = read_frontmatter_metadata_from_file(f)
        results.append(
            PaperListEntry(
                file=f.name,
                doi=metadata.get("doi", ""),
                title=metadata.get("title", ""),
                safe_doi=f.stem,
            )
        )
    return results


def _prepend_front_matter(markdown: str, record: PaperRecord) -> str:
    extra: dict[str, str] = {}
    if record.assets_manifest_path:
        extra["assets_manifest_path"] = record.assets_manifest_path

    front = build_front_matter(
        doi=record.doi,
        title=record.title,
        source=record.source,
        fetch_outcome=record.fetch_outcome,
        authors=[str(author) for author in record.authors if str(author)],
        year=record.year,
        journal=record.journal,
        extra=extra or None,
    )
    return f"{front}\n\n{markdown}"
