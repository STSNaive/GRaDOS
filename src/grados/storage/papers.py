"""Paper storage: canonical Markdown files under papers/."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grados.publisher.common import (
    is_safe_doi_filename,
    normalize_doi,
    safe_doi_filename,
    safe_doi_filename_candidates,
)
from grados.storage.chunking import split_paragraphs, strip_frontmatter
from grados.storage.corpus import normalize_corpus_metadata
from grados.storage.frontmatter import (
    build_front_matter,
    parse_authors_metadata,
    read_frontmatter_metadata,
    read_frontmatter_metadata_from_file,
)

if TYPE_CHECKING:
    from grados.config import IndexingConfig
    from grados.storage.assets import AssetBundleSaveResult, AssetLimits, PendingAsset

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
    indexing_config: IndexingConfig | None = None,
) -> PaperSavedSummary:
    """Save parsed paper as markdown with YAML frontmatter.

    `papers/` is the canonical source of truth. If `chroma_dir` is provided,
    the search index is refreshed from the same markdown body.
    """
    safe = _safe_doi_for_write(papers_dir, doi)
    file_path = _paper_file_for_safe_doi(papers_dir, safe)
    if file_path is None:
        raise ValueError(f"Unsafe safe DOI generated for {doi!r}")
    headings = re.findall(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)
    mirror_written = False
    index_status = "not_requested"
    index_error = ""
    normalized_frontmatter = normalize_corpus_metadata(extra_frontmatter)

    if write_mirror:
        papers_dir.mkdir(parents=True, exist_ok=True)
        front = build_front_matter(
            doi,
            title,
            source,
            publisher,
            "",
            authors=authors,
            year=year,
            journal=journal,
            extra=normalized_frontmatter,
        )
        content = f"{front}\n\n{markdown}"
        file_path.write_text(content, encoding="utf-8")
        mirror_written = True

    if chroma_dir and mirror_written:
        try:
            from grados.storage.vector import index_paper

            index_kwargs: dict[str, Any] = {
                "source": source,
                "fetch_outcome": fetch_outcome,
                "authors": authors,
                "year": year,
                "journal": journal,
                "section_headings": headings[:20],
                "assets_manifest_path": normalized_frontmatter.get("assets_manifest_path", ""),
                "corpus": normalized_frontmatter["corpus"],
                "tier": normalized_frontmatter["tier"],
                "workset_id": normalized_frontmatter["workset_id"],
                "promoted_at": normalized_frontmatter["promoted_at"],
                "promote_reason": normalized_frontmatter["promote_reason"],
            }
            if indexing_config is not None:
                index_kwargs["indexing_config"] = indexing_config

            index_paper(chroma_dir, doi, safe, title, markdown, **index_kwargs)
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

    safe = _safe_doi_for_write(papers_dir, doi)
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


def save_asset_bundle(
    doi: str,
    papers_dir: Path,
    *,
    source: str = "",
    assets: list[PendingAsset] | None = None,
    mode: str = "all",
    limits: AssetLimits | None = None,
) -> AssetBundleSaveResult:
    """Persist parser-generated assets in the v2 paper-bound bundle format."""
    from grados.storage.assets import persist_asset_bundle

    if not assets:
        from grados.storage.assets import AssetBundleSaveResult

        return AssetBundleSaveResult()
    safe = _safe_doi_for_write(papers_dir, doi)
    return persist_asset_bundle(
        doi=doi,
        safe_doi=safe,
        papers_dir=papers_dir,
        source=source,
        assets=assets,
        mode=mode,
        limits=limits,
    )


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
    safe_doi: str = ""
    assets_manifest_path: str = ""


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
    parsed_summary: PaperParsedSummary


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
    parsed_manifest_path: str
    word_count: int
    char_count: int
    content_markdown: str
    corpus: str = "canonical"
    tier: str = "stable"
    workset_id: str = ""
    promoted_at: str = ""
    promote_reason: str = ""


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
    formulas: int = 0
    pages: int = 0
    debug: int = 0
    skipped: int = 0
    schema_version: int = 1
    asset_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PaperParsedSummary:
    has_parsed_manifest: bool
    manifest_path: str = ""
    schema_version: int = 1
    parser: str = ""
    parser_version: str = ""
    block_count: int = 0
    page_range: str = ""
    has_source_pdf_hash: bool = False
    has_canonical_markdown_hash: bool = False
    assets_manifest_path: str = ""
    input_pdf_hash: str = ""
    canonical_pdf_hash: str = ""
    materialization_action: str = ""
    materialization_outcome: str = ""
    parse_outcome: str = ""


def load_paper_record(
    papers_dir: Path,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
) -> PaperRecord | None:
    """Load the canonical markdown-backed paper record from `papers/*.md`."""
    resolved = _resolve_paper_file(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)
    if not resolved:
        return None
    safe_doi, file_path = resolved

    raw_content = file_path.read_text(encoding="utf-8")
    metadata = read_frontmatter_metadata(raw_content)
    corpus_metadata = normalize_corpus_metadata(metadata)
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
        parsed_manifest_path=metadata.get("parsed_manifest_path", ""),
        word_count=len(content.split()),
        char_count=len(content),
        content_markdown=content,
        corpus=corpus_metadata["corpus"],
        tier=corpus_metadata["tier"],
        workset_id=corpus_metadata["workset_id"],
        promoted_at=corpus_metadata["promoted_at"],
        promote_reason=corpus_metadata["promote_reason"],
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
) -> PaperReadResult | None:
    """Read a saved paper with paragraph windowing."""
    resolved = _resolve_paper_file(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)
    if not resolved:
        return None
    safe_doi, file_path = resolved

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
        safe_doi=safe_doi,
        assets_manifest_path=metadata.get("assets_manifest_path", ""),
    )


def get_paper_structure(
    papers_dir: Path,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
) -> PaperStructureResult | None:
    """Return a compact, deterministic structure card for a saved paper."""
    record = load_paper_record(
        papers_dir,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
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
        parsed_summary=_load_parsed_summary(papers_dir, record),
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


def _selector_safe_doi(safe_doi: str | None, uri: str | None) -> str | None:
    if uri and uri.startswith("grados://papers/"):
        return uri.replace("grados://papers/", "").strip("/")
    if safe_doi:
        return safe_doi
    return None


def _candidate_safe_dois(doi: str | None, safe_doi: str | None, uri: str | None) -> list[str]:
    selected = _selector_safe_doi(safe_doi, uri)
    if selected:
        selected = selected.strip()
        return [selected] if is_safe_doi_filename(selected) else []
    if doi:
        return safe_doi_filename_candidates(doi)
    return []


def _paper_file_for_safe_doi(papers_dir: Path, safe_doi: str) -> Path | None:
    safe_doi = safe_doi.strip()
    if not is_safe_doi_filename(safe_doi):
        return None
    root = papers_dir.resolve()
    file_path = (papers_dir / f"{safe_doi}.md").resolve()
    try:
        file_path.relative_to(root)
    except ValueError:
        return None
    return file_path


def _resolve_paper_file(
    papers_dir: Path,
    *,
    doi: str | None = None,
    safe_doi: str | None = None,
    uri: str | None = None,
) -> tuple[str, Path] | None:
    for candidate in _candidate_safe_dois(doi, safe_doi, uri):
        file_path = _paper_file_for_safe_doi(papers_dir, candidate)
        if file_path is not None and file_path.is_file():
            if doi and not safe_doi and not uri and not _paper_file_matches_doi(file_path, doi):
                continue
            return candidate, file_path
    return None


def _safe_doi_for_write(papers_dir: Path, doi: str) -> str:
    """Prefer an existing same-DOI id, otherwise use the current collision-safe id."""
    for candidate in safe_doi_filename_candidates(doi):
        file_path = _paper_file_for_safe_doi(papers_dir, candidate)
        if file_path is not None and file_path.is_file() and _paper_file_matches_doi(file_path, doi):
            return candidate
    return safe_doi_filename(doi)


def _paper_file_matches_doi(file_path: Path, doi: str) -> bool:
    try:
        metadata = read_frontmatter_metadata_from_file(file_path)
    except OSError:
        return False
    return normalize_doi(metadata.get("doi", "")) == normalize_doi(doi)


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

    from grados.storage.assets import compact_asset_refs, load_asset_manifest, manifest_assets

    payload = load_asset_manifest(papers_dir, manifest_path)
    if payload is None:
        return summary

    assets = manifest_assets(payload)
    if assets:
        figures = sum(1 for asset in assets if asset.get("kind") == "figure")
        tables = sum(1 for asset in assets if asset.get("kind") == "table")
        formulas = sum(1 for asset in assets if asset.get("kind") == "formula")
        pages = sum(1 for asset in assets if asset.get("kind") == "page")
        debug = sum(1 for asset in assets if asset.get("kind") == "debug" or asset.get("role") in {"debug", "source"})
        objects = max(0, len(assets) - figures - tables - formulas - pages - debug)
        return PaperAssetsSummary(
            has_assets=True,
            manifest_path=manifest_path,
            figures=figures,
            tables=tables,
            objects=objects,
            formulas=formulas,
            pages=pages,
            debug=debug,
            skipped=len(payload.get("skipped_assets", [])) if isinstance(payload.get("skipped_assets"), list) else 0,
            schema_version=int(payload.get("schema_version") or 1),
            asset_refs=compact_asset_refs(payload, limit=12),
        )

    return PaperAssetsSummary(
        has_assets=True,
        manifest_path=manifest_path,
        figures=len(payload.get("figures", [])) if isinstance(payload, dict) else 0,
        tables=len(payload.get("tables", [])) if isinstance(payload, dict) else 0,
        formulas=len(payload.get("formulas", [])) if isinstance(payload, dict) else 0,
        pages=len(payload.get("pages", [])) if isinstance(payload, dict) else 0,
        debug=len(payload.get("debug", [])) if isinstance(payload, dict) else 0,
        objects=len(payload.get("objects", [])) if isinstance(payload, dict) else 0,
        skipped=len(payload.get("skipped_assets", [])) if isinstance(payload.get("skipped_assets"), list) else 0,
        schema_version=int(payload.get("schema_version") or 1),
    )


def _load_parsed_summary(papers_dir: Path, record: PaperRecord) -> PaperParsedSummary:
    manifest_path = record.parsed_manifest_path or ""
    if not manifest_path:
        return PaperParsedSummary(has_parsed_manifest=False)

    from grados.storage.parsed_sidecar import parsed_manifest_summary

    summary = parsed_manifest_summary(papers_dir, manifest_path)
    return PaperParsedSummary(
        has_parsed_manifest=summary.has_parsed_manifest,
        manifest_path=summary.manifest_path,
        schema_version=summary.schema_version,
        parser=summary.parser,
        parser_version=summary.parser_version,
        block_count=summary.block_count,
        page_range=summary.page_range,
        has_source_pdf_hash=summary.has_source_pdf_hash,
        has_canonical_markdown_hash=summary.has_canonical_markdown_hash,
        assets_manifest_path=summary.assets_manifest_path,
        input_pdf_hash=summary.input_pdf_hash,
        canonical_pdf_hash=summary.canonical_pdf_hash,
        materialization_action=summary.materialization_action,
        materialization_outcome=summary.materialization_outcome,
        parse_outcome=summary.parse_outcome,
    )


def resolve_safe_doi_for_write(papers_dir: Path, doi: str) -> str:
    """Return the collision-safe paper id that `save_paper_markdown` will use."""
    return _safe_doi_for_write(papers_dir, doi)


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
    extra: dict[str, str] = normalize_corpus_metadata({
        "corpus": record.corpus,
        "tier": record.tier,
        "workset_id": record.workset_id,
        "promoted_at": record.promoted_at,
        "promote_reason": record.promote_reason,
    })
    if record.assets_manifest_path:
        extra["assets_manifest_path"] = record.assets_manifest_path
    if record.parsed_manifest_path:
        extra["parsed_manifest_path"] = record.parsed_manifest_path

    front = build_front_matter(
        doi=record.doi,
        title=record.title,
        source=record.source,
        fetch_outcome=record.fetch_outcome,
        authors=[str(author) for author in record.authors if str(author)],
        year=record.year,
        journal=record.journal,
        extra=extra,
    )
    return f"{front}\n\n{markdown}"
