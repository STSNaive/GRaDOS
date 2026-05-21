"""SQLite FTS5/BM25 retrieval index for canonical paper markdown."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.config import IndexingConfig
from grados.storage.chunking import chunk_text, split_paragraphs, strip_frontmatter
from grados.storage.frontmatter import parse_authors_metadata, read_frontmatter_metadata
from grados.storage.retrieval import extract_anchor_phrase, lexical_score, query_terms

FTS_DB_NAME = "fts.sqlite3"


@dataclass(frozen=True)
class FTSIndexStats:
    paper_count: int = 0
    block_count: int = 0
    db_path: Path | None = None
    current: bool = False
    snapshot: str = ""


@dataclass(frozen=True)
class FTSBlockResult:
    doi: str
    safe_doi: str
    title: str
    authors: list[str]
    year: str
    journal: str
    source: str
    block_id: str
    block_type: str
    section_name: str
    heading_path: str
    section_level: int
    paragraph_start: int
    paragraph_count: int
    text: str
    score: float
    raw_score: float
    retriever: str
    rank: int
    query: str


def fts_index_path(chroma_dir: Path) -> Path:
    """Keep lexical retrieval next to the rebuildable Chroma store."""
    return chroma_dir.parent / FTS_DB_NAME


def ensure_fts_index(
    *,
    papers_dir: Path,
    chroma_dir: Path,
    force: bool = False,
) -> FTSIndexStats:
    db_path = fts_index_path(chroma_dir)
    snapshot = _papers_snapshot(papers_dir)
    if force:
        return rebuild_fts_index(papers_dir=papers_dir, db_path=db_path)
    existing = get_fts_index_stats(db_path=db_path, papers_dir=papers_dir)
    if existing.current and existing.snapshot == snapshot:
        return existing
    return refresh_fts_index(papers_dir=papers_dir, db_path=db_path, snapshot=snapshot)


def get_fts_index_stats(*, db_path: Path, papers_dir: Path | None = None) -> FTSIndexStats:
    if not db_path.is_file():
        return FTSIndexStats(db_path=db_path, snapshot=_papers_snapshot(papers_dir) if papers_dir else "")

    try:
        with sqlite3.connect(db_path) as conn:
            paper_count = int(_get_meta(conn, "paper_count") or 0)
            block_count = int(_get_meta(conn, "block_count") or 0)
            snapshot = _get_meta(conn, "snapshot") or ""
    except sqlite3.Error:
        return FTSIndexStats(db_path=db_path, snapshot=_papers_snapshot(papers_dir) if papers_dir else "")

    current_snapshot = _papers_snapshot(papers_dir) if papers_dir else snapshot
    return FTSIndexStats(
        paper_count=paper_count,
        block_count=block_count,
        db_path=db_path,
        current=bool(snapshot and snapshot == current_snapshot),
        snapshot=snapshot,
    )


def rebuild_fts_index(*, papers_dir: Path, db_path: Path) -> FTSIndexStats:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = _papers_snapshot(papers_dir)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _create_schema(conn)
        conn.execute("DELETE FROM block_fts")
        conn.execute("DELETE FROM blocks")
        conn.execute("DELETE FROM papers")
        conn.execute("DELETE FROM meta")

        paper_count = 0
        block_count = 0
        for md_file in sorted(papers_dir.glob("*.md")) if papers_dir.is_dir() else []:
            indexed_blocks = _index_markdown_file(conn, md_file)
            if indexed_blocks <= 0:
                continue
            paper_count += 1
            block_count += indexed_blocks

        _set_meta(conn, "snapshot", snapshot)
        _set_meta(conn, "paper_count", str(paper_count))
        _set_meta(conn, "block_count", str(block_count))
        _set_meta(conn, "indexed_at", datetime.now(UTC).isoformat())
        conn.commit()

    return FTSIndexStats(
        paper_count=paper_count,
        block_count=block_count,
        db_path=db_path,
        current=True,
        snapshot=snapshot,
    )


def refresh_fts_index(*, papers_dir: Path, db_path: Path, snapshot: str | None = None) -> FTSIndexStats:
    """Incrementally refresh stale FTS rows for changed canonical markdown files."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot or _papers_snapshot(papers_dir)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            _create_schema(conn)

            current_files = _paper_files_by_safe_doi(papers_dir)
            indexed_stats = _indexed_file_stats(conn)

            for safe_doi in sorted(set(indexed_stats) - set(current_files)):
                _delete_indexed_paper(conn, safe_doi)

            for safe_doi, md_file in sorted(current_files.items()):
                try:
                    stat = md_file.stat()
                except OSError:
                    continue
                if indexed_stats.get(safe_doi) == (stat.st_mtime_ns, stat.st_size):
                    continue
                _delete_indexed_paper(conn, safe_doi)
                _index_markdown_file(conn, md_file)

            paper_count = _count_papers(conn)
            block_count = _count_blocks(conn)
            _set_meta(conn, "snapshot", snapshot)
            _set_meta(conn, "paper_count", str(paper_count))
            _set_meta(conn, "block_count", str(block_count))
            _set_meta(conn, "indexed_at", datetime.now(UTC).isoformat())
            conn.commit()
    except sqlite3.Error:
        return rebuild_fts_index(papers_dir=papers_dir, db_path=db_path)

    return FTSIndexStats(
        paper_count=paper_count,
        block_count=block_count,
        db_path=db_path,
        current=True,
        snapshot=snapshot,
    )


def search_fts_blocks(
    *,
    db_path: Path,
    query: str,
    limit: int,
    doi: str = "",
    authors: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    journal: str = "",
    source: str = "",
) -> list[FTSBlockResult]:
    if limit <= 0 or not db_path.is_file():
        return []

    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    try:
        rows = _query_fts(
            db_path=db_path,
            fts_query=fts_query,
            limit=limit,
            doi=doi,
            authors=authors,
            year_from=year_from,
            year_to=year_to,
            journal=journal,
            source=source,
        )
    except sqlite3.Error:
        fallback_query = _fallback_fts_query(query)
        if not fallback_query or fallback_query == fts_query:
            return []
        try:
            rows = _query_fts(
                db_path=db_path,
                fts_query=fallback_query,
                limit=limit,
                doi=doi,
                authors=authors,
                year_from=year_from,
                year_to=year_to,
                journal=journal,
                source=source,
            )
        except sqlite3.Error:
            return []

    return [_row_to_result(row, retriever="fts_bm25", rank=index, query=query) for index, row in enumerate(rows, 1)]


def search_exact_blocks(
    *,
    db_path: Path,
    query: str,
    limit: int,
    doi: str = "",
    authors: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    journal: str = "",
    source: str = "",
) -> list[FTSBlockResult]:
    if limit <= 0 or not db_path.is_file():
        return []

    query_value = query.strip()
    anchor = extract_anchor_phrase(query_value)
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", query_value, re.IGNORECASE)
    phrase = anchor or (doi_match.group(0) if doi_match else "")
    if not phrase and 3 <= len(query_value) <= 120:
        phrase = query_value
    if not phrase and not doi:
        return []

    filters, params = _filter_sql(
        doi=doi or (doi_match.group(0) if doi_match else ""),
        authors=authors,
        year_from=year_from,
        year_to=year_to,
        journal=journal,
        source=source,
    )
    exact_terms = query_terms(query_value)
    phrase_like = f"%{phrase.lower()}%" if phrase else ""
    where = [*filters]
    if phrase_like:
        where.append(
            "(lower(b.text) LIKE ? OR lower(b.title) LIKE ? OR lower(b.doi) = ? "
            "OR lower(b.journal) LIKE ? OR lower(b.authors_json) LIKE ?)"
        )
        params.extend([phrase_like, phrase_like, phrase.lower(), phrase_like, phrase_like])
    where_sql = " AND ".join(where) if where else "1=1"

    sql = (
        "SELECT b.*, 0.0 AS bm25_rank FROM blocks b "
        "JOIN papers p ON p.safe_doi = b.safe_doi "
        f"WHERE {where_sql} "
        "ORDER BY b.safe_doi, b.ordinal "
        "LIMIT ?"
    )
    params.append(max(limit * 3, limit))

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        score = _exact_score(row, phrase=phrase, terms=exact_terms, explicit_doi=doi)
        if score <= 0:
            continue
        scored.append((score, row))

    ranked_rows = sorted(scored, key=lambda item: (-item[0], item[1]["safe_doi"], item[1]["ordinal"]))[:limit]
    results: list[FTSBlockResult] = []
    for index, (score, row) in enumerate(ranked_rows, 1):
        payload = dict(row)
        payload["bm25_rank"] = -score
        results.append(_row_to_result(payload, retriever="exact", rank=index, query=query))
    return results


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS papers (
            safe_doi TEXT PRIMARY KEY,
            doi TEXT NOT NULL,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            year TEXT NOT NULL,
            journal TEXT NOT NULL,
            source TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            file_mtime_ns INTEGER NOT NULL,
            file_size INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blocks (
            id INTEGER PRIMARY KEY,
            block_id TEXT UNIQUE NOT NULL,
            safe_doi TEXT NOT NULL,
            doi TEXT NOT NULL,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            year TEXT NOT NULL,
            journal TEXT NOT NULL,
            source TEXT NOT NULL,
            section_name TEXT NOT NULL,
            heading_path TEXT NOT NULL,
            section_level INTEGER NOT NULL,
            paragraph_start INTEGER NOT NULL,
            paragraph_count INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            text TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            FOREIGN KEY(safe_doi) REFERENCES papers(safe_doi)
        );
        CREATE INDEX IF NOT EXISTS idx_blocks_safe_doi ON blocks(safe_doi);
        CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
        CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
        CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(
            block_id UNINDEXED,
            safe_doi UNINDEXED,
            doi,
            title,
            authors,
            year,
            journal,
            section_name,
            heading_path,
            block_type UNINDEXED,
            text,
            tokenize = 'unicode61 tokenchars ''._-/'''
        );
        """
    )


def _index_markdown_file(conn: sqlite3.Connection, md_file: Path) -> int:
    raw = md_file.read_text(encoding="utf-8", errors="replace")
    body = strip_frontmatter(raw)
    if not body.strip():
        return 0

    metadata = read_frontmatter_metadata(raw)
    safe_doi = md_file.stem
    doi = metadata.get("doi", safe_doi)
    title = metadata.get("title", "") or _infer_title(body)
    authors = parse_authors_metadata(metadata)
    year = metadata.get("year", "")
    journal = metadata.get("journal", "")
    source = metadata.get("source", "")
    authors_json = json.dumps(authors, ensure_ascii=False)
    stat = md_file.stat()

    conn.execute(
        """
        INSERT INTO papers (
            safe_doi, doi, title, authors_json, year, journal, source,
            content_hash, file_mtime_ns, file_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            safe_doi,
            doi,
            title,
            authors_json,
            year,
            journal,
            source,
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
            stat.st_mtime_ns,
            stat.st_size,
        ),
    )

    blocks = chunk_text(body, IndexingConfig(), fallback_title=title)
    if not blocks:
        paragraphs = split_paragraphs(body)
        blocks = [
            {
                "text": paragraph,
                "section_name": "",
                "section_level": 0,
                "paragraph_start": index,
                "paragraph_count": 1,
            }
            for index, paragraph in enumerate(paragraphs)
        ]

    heading_paths = _heading_paths_by_paragraph(body)
    inserted = 0
    for ordinal, block in enumerate(blocks):
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        paragraph_start = int(block.get("paragraph_start", 0) or 0)
        paragraph_count = int(block.get("paragraph_count", 0) or 0)
        section_name = str(block.get("section_name", "") or "")
        heading_path = heading_paths.get(paragraph_start) or section_name
        section_level = int(block.get("section_level", 0) or 0)
        block_id = f"{safe_doi}::p{paragraph_start}:{paragraph_count}:{ordinal}"
        cursor = conn.execute(
            """
            INSERT INTO blocks (
                block_id, safe_doi, doi, title, authors_json, year, journal, source,
                section_name, heading_path, section_level, paragraph_start,
                paragraph_count, block_type, text, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                block_id,
                safe_doi,
                doi,
                title,
                authors_json,
                year,
                journal,
                source,
                section_name,
                heading_path,
                section_level,
                paragraph_start,
                paragraph_count,
                "block",
                text,
                ordinal,
            ),
        )
        if cursor.lastrowid is None:
            continue
        rowid = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO block_fts (
                rowid, block_id, safe_doi, doi, title, authors, year, journal,
                section_name, heading_path, block_type, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rowid,
                block_id,
                safe_doi,
                doi,
                title,
                " ".join(authors),
                year,
                journal,
                section_name,
                heading_path,
                "block",
                text,
            ),
        )
        inserted += 1
    return inserted


def _query_fts(
    *,
    db_path: Path,
    fts_query: str,
    limit: int,
    doi: str,
    authors: str,
    year_from: int | None,
    year_to: int | None,
    journal: str,
    source: str,
) -> list[sqlite3.Row]:
    filters, params = _filter_sql(
        doi=doi,
        authors=authors,
        year_from=year_from,
        year_to=year_to,
        journal=journal,
        source=source,
    )
    where_sql = " AND ".join(["block_fts MATCH ?", *filters])
    sql = (
        "SELECT b.*, bm25(block_fts, 0.0, 0.0, 8.0, 5.0, 3.0, 1.0, 2.0, 2.0, 2.0, 0.0, 1.5) "
        "AS bm25_rank FROM block_fts "
        "JOIN blocks b ON b.id = block_fts.rowid "
        "JOIN papers p ON p.safe_doi = b.safe_doi "
        f"WHERE {where_sql} "
        "ORDER BY bm25_rank ASC, b.safe_doi, b.ordinal "
        "LIMIT ?"
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, [fts_query, *params, limit]).fetchall()


def _filter_sql(
    *,
    doi: str,
    authors: str,
    year_from: int | None,
    year_to: int | None,
    journal: str,
    source: str,
) -> tuple[list[str], list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if doi:
        filters.append("lower(p.doi) = ?")
        params.append(doi.lower())
    if authors:
        filters.append("lower(p.authors_json) LIKE ?")
        params.append(f"%{authors.lower()}%")
    if journal:
        filters.append("lower(p.journal) LIKE ?")
        params.append(f"%{journal.lower()}%")
    if source:
        filters.append("lower(p.source) LIKE ?")
        params.append(f"%{source.lower()}%")
    if year_from is not None:
        filters.append("CAST(NULLIF(p.year, '') AS INTEGER) >= ?")
        params.append(year_from)
    if year_to is not None:
        filters.append("CAST(NULLIF(p.year, '') AS INTEGER) <= ?")
        params.append(year_to)
    return filters, params


def _row_to_result(row: sqlite3.Row | dict[str, Any], *, retriever: str, rank: int, query: str) -> FTSBlockResult:
    get = row.get if isinstance(row, dict) else row.__getitem__
    authors = _decode_authors(get("authors_json"))
    raw_score = float(get("bm25_rank") or 0.0)
    return FTSBlockResult(
        doi=str(get("doi") or ""),
        safe_doi=str(get("safe_doi") or ""),
        title=str(get("title") or ""),
        authors=authors,
        year=str(get("year") or ""),
        journal=str(get("journal") or ""),
        source=str(get("source") or ""),
        block_id=str(get("block_id") or ""),
        block_type=str(get("block_type") or ""),
        section_name=str(get("section_name") or ""),
        heading_path=str(get("heading_path") or ""),
        section_level=int(get("section_level") or 0),
        paragraph_start=int(get("paragraph_start") or 0),
        paragraph_count=int(get("paragraph_count") or 0),
        text=str(get("text") or ""),
        score=round(-raw_score, 6),
        raw_score=raw_score,
        retriever=retriever,
        rank=rank,
        query=query,
    )


def _exact_score(row: sqlite3.Row, *, phrase: str, terms: list[str], explicit_doi: str) -> float:
    payload = {
        "doi": str(row["doi"]).lower(),
        "title": str(row["title"]).lower(),
        "authors": str(row["authors_json"]).lower(),
        "year": str(row["year"]).lower(),
        "journal": str(row["journal"]).lower(),
        "section": str(row["section_name"]).lower(),
        "text": str(row["text"]).lower(),
    }
    score = 0.0
    phrase_lower = phrase.lower()
    if explicit_doi and payload["doi"] == explicit_doi.lower():
        score += 4.0
    if phrase_lower:
        if payload["doi"] == phrase_lower:
            score += 4.0
        if phrase_lower in payload["title"]:
            score += 2.5
        if phrase_lower in payload["authors"]:
            score += 2.0
        if phrase_lower in payload["journal"]:
            score += 1.5
        if phrase_lower in payload["section"]:
            score += 1.0
        if phrase_lower in payload["text"]:
            score += 2.0
    score += lexical_score(payload["text"], terms, phrase_lower)
    return score


def _build_fts_query(query: str) -> str:
    stripped = query.strip()
    if not stripped:
        return ""
    if any(token in stripped.upper() for token in (" NEAR", " AND ", " OR ", " NOT ")) or '"' in stripped:
        return stripped
    return _fallback_fts_query(stripped)


def _fallback_fts_query(query: str) -> str:
    terms = query_terms(query)
    if not terms:
        return ""
    return " OR ".join(_quote_fts_term(term) for term in terms)


def _quote_fts_term(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _papers_snapshot(papers_dir: Path | None) -> str:
    if papers_dir is None or not papers_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    rows: list[str] = []
    for md_file in sorted(papers_dir.glob("*.md")):
        try:
            stat = md_file.stat()
        except OSError:
            continue
        rows.append(f"{md_file.name}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _paper_files_by_safe_doi(papers_dir: Path | None) -> dict[str, Path]:
    if papers_dir is None or not papers_dir.is_dir():
        return {}
    return {md_file.stem: md_file for md_file in papers_dir.glob("*.md")}


def _indexed_file_stats(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    rows = conn.execute("SELECT safe_doi, file_mtime_ns, file_size FROM papers").fetchall()
    return {str(row[0]): (int(row[1]), int(row[2])) for row in rows}


def _delete_indexed_paper(conn: sqlite3.Connection, safe_doi: str) -> None:
    rowids = [
        int(row[0])
        for row in conn.execute("SELECT id FROM blocks WHERE safe_doi = ?", (safe_doi,)).fetchall()
    ]
    conn.executemany("DELETE FROM block_fts WHERE rowid = ?", [(rowid,) for rowid in rowids])
    conn.execute("DELETE FROM blocks WHERE safe_doi = ?", (safe_doi,))
    conn.execute("DELETE FROM papers WHERE safe_doi = ?", (safe_doi,))


def _count_papers(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM papers").fetchone()
    return int(row[0]) if row else 0


def _count_blocks(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()
    return int(row[0]) if row else 0


def _get_meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else ""


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))


def _decode_authors(raw: Any) -> list[str]:
    try:
        loaded = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(value) for value in loaded if str(value)]


def _infer_title(body: str) -> str:
    for paragraph in split_paragraphs(body):
        match = re.match(r"^#{1,6}\s+(.+)$", paragraph)
        if match:
            return match.group(1).strip()
    return ""


def _heading_paths_by_paragraph(body: str) -> dict[int, str]:
    paths: dict[int, str] = {}
    stack: list[tuple[int, str]] = []
    for index, paragraph in enumerate(split_paragraphs(body)):
        match = re.match(r"^(#{1,6})\s+(.+)$", paragraph)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            stack = [(item_level, item_heading) for item_level, item_heading in stack if item_level < level]
            stack.append((level, heading))
        if stack:
            paths[index] = " > ".join(item_heading for _level, item_heading in stack)
    return paths
