"""Durable state for DOI-bound local PDF parse/save attempts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.publisher.common import normalize_doi, safe_doi_filename

__all__ = [
    "ParseAttemptRecord",
    "build_parse_attempt_id",
    "complete_parse_attempt",
    "fail_parse_attempt",
    "get_parse_attempt",
    "mark_parse_attempt_interrupted",
    "restart_parse_attempt",
    "upsert_running_parse_attempt",
]


@dataclass(frozen=True)
class ParseAttemptRecord:
    attempt_id: str
    doi: str
    safe_doi: str
    input_pdf_path: str
    input_pdf_name: str
    input_pdf_hash: str
    copy_to_library: bool
    acquisition_via: str
    expected_title: str
    parser_config_json: str
    status: str
    started_at: str
    updated_at: str
    completed_at: str = ""
    receipt_text: str = ""
    error_message: str = ""
    failure_reason: str = ""
    canonical_uri: str = ""
    paper_path: str = ""
    canonical_pdf_path: str = ""
    canonical_pdf_hash: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ParseAttemptRecord:
        return cls(
            attempt_id=str(row["attempt_id"]),
            doi=str(row["doi"]),
            safe_doi=str(row["safe_doi"]),
            input_pdf_path=str(row["input_pdf_path"]),
            input_pdf_name=str(row["input_pdf_name"]),
            input_pdf_hash=str(row["input_pdf_hash"]),
            copy_to_library=bool(row["copy_to_library"]),
            acquisition_via=str(row["acquisition_via"]),
            expected_title=str(row["expected_title"]),
            parser_config_json=str(row["parser_config_json"]),
            status=str(row["status"]),
            started_at=str(row["started_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=str(row["completed_at"] or ""),
            receipt_text=str(row["receipt_text"] or ""),
            error_message=str(row["error_message"] or ""),
            failure_reason=str(row["failure_reason"] or ""),
            canonical_uri=str(row["canonical_uri"] or ""),
            paper_path=str(row["paper_path"] or ""),
            canonical_pdf_path=str(row["canonical_pdf_path"] or ""),
            canonical_pdf_hash=str(row["canonical_pdf_hash"] or ""),
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS parse_attempts (
            attempt_id TEXT PRIMARY KEY,
            doi TEXT NOT NULL,
            safe_doi TEXT NOT NULL,
            input_pdf_path TEXT NOT NULL DEFAULT '',
            input_pdf_name TEXT NOT NULL DEFAULT '',
            input_pdf_hash TEXT NOT NULL DEFAULT '',
            copy_to_library INTEGER NOT NULL DEFAULT 0,
            acquisition_via TEXT NOT NULL DEFAULT '',
            expected_title TEXT NOT NULL DEFAULT '',
            parser_config_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT '',
            receipt_text TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            failure_reason TEXT NOT NULL DEFAULT '',
            canonical_uri TEXT NOT NULL DEFAULT '',
            paper_path TEXT NOT NULL DEFAULT '',
            canonical_pdf_path TEXT NOT NULL DEFAULT '',
            canonical_pdf_hash TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_parse_attempts_doi_hash
            ON parse_attempts(doi, input_pdf_hash);
        CREATE INDEX IF NOT EXISTS idx_parse_attempts_status
            ON parse_attempts(status);
        CREATE INDEX IF NOT EXISTS idx_parse_attempts_updated_at
            ON parse_attempts(updated_at DESC);
        """
    )
    conn.commit()


def build_parse_attempt_id(
    *,
    doi: str,
    input_pdf_hash: str,
    copy_to_library: bool,
    acquisition_via: str,
    parser_config: dict[str, Any],
) -> str:
    payload = {
        "doi": normalize_doi(doi) or doi.strip().lower(),
        "input_pdf_hash": input_pdf_hash,
        "copy_to_library": bool(copy_to_library),
        "acquisition_via": acquisition_via.strip(),
        "parser_config": parser_config,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_parse_attempt(db_path: Path, attempt_id: str) -> ParseAttemptRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parse_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    return ParseAttemptRecord.from_row(row) if row is not None else None


def upsert_running_parse_attempt(
    db_path: Path,
    *,
    attempt_id: str,
    doi: str,
    input_pdf_path: str,
    input_pdf_name: str,
    input_pdf_hash: str,
    copy_to_library: bool,
    acquisition_via: str,
    expected_title: str,
    parser_config: dict[str, Any],
) -> tuple[ParseAttemptRecord, bool]:
    now = _utc_now()
    parser_config_json = json.dumps(parser_config, sort_keys=True, ensure_ascii=False)
    safe_doi = safe_doi_filename(doi)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO parse_attempts (
                attempt_id, doi, safe_doi, input_pdf_path, input_pdf_name, input_pdf_hash,
                copy_to_library, acquisition_via, expected_title, parser_config_json,
                status, started_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                attempt_id,
                doi,
                safe_doi,
                input_pdf_path,
                input_pdf_name,
                input_pdf_hash,
                1 if copy_to_library else 0,
                acquisition_via,
                expected_title,
                parser_config_json,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM parse_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"parse attempt insert failed for {attempt_id}")
    return ParseAttemptRecord.from_row(row), cursor.rowcount == 1


def mark_parse_attempt_interrupted(
    db_path: Path,
    attempt_id: str,
    *,
    error_message: str = "",
) -> ParseAttemptRecord | None:
    now = _utc_now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE parse_attempts
            SET status = 'interrupted',
                updated_at = ?,
                completed_at = ?,
                error_message = ?
            WHERE attempt_id = ? AND status = 'running'
            """,
            (now, now, error_message, attempt_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM parse_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    return ParseAttemptRecord.from_row(row) if row is not None else None


def restart_parse_attempt(db_path: Path, attempt_id: str) -> ParseAttemptRecord | None:
    now = _utc_now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE parse_attempts
            SET status = 'running',
                updated_at = ?,
                completed_at = '',
                receipt_text = '',
                error_message = '',
                failure_reason = '',
                canonical_uri = '',
                paper_path = '',
                canonical_pdf_path = '',
                canonical_pdf_hash = ''
            WHERE attempt_id = ?
            """,
            (now, attempt_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM parse_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    return ParseAttemptRecord.from_row(row) if row is not None else None


def complete_parse_attempt(
    db_path: Path,
    attempt_id: str,
    *,
    receipt_text: str,
    canonical_uri: str = "",
    paper_path: str = "",
    canonical_pdf_path: str = "",
    canonical_pdf_hash: str = "",
) -> ParseAttemptRecord | None:
    now = _utc_now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE parse_attempts
            SET status = 'completed',
                updated_at = ?,
                completed_at = ?,
                receipt_text = ?,
                error_message = '',
                failure_reason = '',
                canonical_uri = ?,
                paper_path = ?,
                canonical_pdf_path = ?,
                canonical_pdf_hash = ?
            WHERE attempt_id = ?
            """,
            (
                now,
                now,
                receipt_text,
                canonical_uri,
                paper_path,
                canonical_pdf_path,
                canonical_pdf_hash,
                attempt_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM parse_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    return ParseAttemptRecord.from_row(row) if row is not None else None


def fail_parse_attempt(
    db_path: Path,
    attempt_id: str,
    *,
    receipt_text: str = "",
    failure_reason: str = "parse_failed",
    error_message: str = "",
) -> ParseAttemptRecord | None:
    now = _utc_now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE parse_attempts
            SET status = 'failed',
                updated_at = ?,
                completed_at = ?,
                receipt_text = ?,
                error_message = ?,
                failure_reason = ?
            WHERE attempt_id = ?
            """,
            (now, now, receipt_text, error_message, failure_reason, attempt_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM parse_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    return ParseAttemptRecord.from_row(row) if row is not None else None
