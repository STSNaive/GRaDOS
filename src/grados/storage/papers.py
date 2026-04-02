"""Paper storage: save/read markdown with YAML frontmatter, paragraph windowing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from grados.publisher.common import safe_doi_filename


# ── Frontmatter ──────────────────────────────────────────────────────────────


def build_front_matter(
    doi: str,
    title: str = "",
    source: str = "",
    publisher: str = "",
    fetch_outcome: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    """Build YAML front-matter for a saved paper."""
    lines = ["---"]
    lines.append(f'doi: "{doi}"')
    if title:
        lines.append(f'title: "{title.replace(chr(34), chr(39))}"')
    if source:
        lines.append(f'source: "{source}"')
    lines.append(f'fetched_at: "{datetime.now(timezone.utc).isoformat()}"')
    if publisher:
        lines.append(f'publisher: "{publisher}"')
    if fetch_outcome:
        lines.append(f'fetch_outcome: "{fetch_outcome}"')
    lines.append(f'extraction_status: "OK"')
    if extra:
        for k, v in extra.items():
            lines.append(f'{k}: "{v}"')
    lines.append("---")
    return "\n".join(lines)


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
) -> PaperSavedSummary:
    """Save parsed paper as markdown with YAML frontmatter.

    If chroma_dir is provided, also indexes the paper into ChromaDB.
    """
    papers_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_doi_filename(doi)
    file_path = papers_dir / f"{safe}.md"

    front = build_front_matter(doi, title, source, publisher, fetch_outcome, extra_frontmatter)
    content = f"{front}\n\n{markdown}"
    file_path.write_text(content, encoding="utf-8")

    headings = re.findall(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)

    # Auto-index into ChromaDB if configured
    if chroma_dir:
        try:
            from grados.storage.vector import index_paper

            index_paper(chroma_dir, doi, safe, title, markdown)
        except Exception:
            pass  # Non-fatal: indexing failure should not break paper saving

    return PaperSavedSummary(
        doi=doi,
        safe_doi=safe,
        file_path=str(file_path),
        uri=f"grados://papers/{safe}",
        word_count=len(markdown.split()),
        char_count=len(markdown),
        section_headings=headings[:20],
    )


def save_pdf(doi: str, pdf_buffer: bytes, downloads_dir: Path) -> Path:
    """Save raw PDF to downloads directory."""
    downloads_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_doi_filename(doi)
    path = downloads_dir / f"{safe}.pdf"
    path.write_bytes(pdf_buffer)
    return path


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
    # Resolve file
    if uri and uri.startswith("grados://papers/"):
        safe_doi = uri.replace("grados://papers/", "").strip("/")
    elif doi and not safe_doi:
        safe_doi = safe_doi_filename(doi)

    if not safe_doi:
        return None

    file_path = papers_dir / f"{safe_doi}.md"
    if not file_path.is_file():
        return None

    content = file_path.read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(content, include_front_matter)

    if not paragraphs:
        return None

    # Section query matching
    if section_query:
        start_paragraph = _find_section_start(paragraphs, section_query)

    start_paragraph = max(0, min(start_paragraph, len(paragraphs) - 1))
    window = paragraphs[start_paragraph : start_paragraph + max_paragraphs]
    headings = [p for p in paragraphs if re.match(r"^#{1,6}\s+", p)]

    return PaperReadResult(
        doi=doi or safe_doi,
        text="\n\n".join(window),
        start_paragraph=start_paragraph,
        paragraph_count=len(window),
        total_paragraphs=len(paragraphs),
        truncated=start_paragraph + max_paragraphs < len(paragraphs),
        section_headings=[re.sub(r"^#{1,6}\s+", "", h).strip() for h in headings[:20]],
    )


def _split_paragraphs(text: str, include_front_matter: bool) -> list[str]:
    """Split text into paragraphs, optionally stripping YAML front-matter."""
    if not include_front_matter and text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]
    parts = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in parts if p.strip()]


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


def list_saved_papers(papers_dir: Path) -> list[dict[str, str]]:
    """List all saved papers with basic metadata."""
    results = []
    if not papers_dir.is_dir():
        return results
    for f in sorted(papers_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8", errors="replace")[:500]
        doi = ""
        title = ""
        if content.startswith("---"):
            for line in content.split("\n"):
                if line.startswith("doi:"):
                    doi = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line == "---" and doi:
                    break
        results.append({"file": f.name, "doi": doi, "title": title, "safe_doi": f.stem})
    return results
