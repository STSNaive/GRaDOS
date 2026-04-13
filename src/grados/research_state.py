"""Persistent state for research artifacts and failure memory."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "manage_failure_cases",
    "query_research_artifacts",
    "save_research_artifact",
]


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
        CREATE TABLE IF NOT EXISTS research_artifacts (
            artifact_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            content_text TEXT NOT NULL,
            content_format TEXT NOT NULL,
            source_doi TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_research_artifacts_kind
            ON research_artifacts(kind);
        CREATE INDEX IF NOT EXISTS idx_research_artifacts_source_doi
            ON research_artifacts(source_doi);
        CREATE INDEX IF NOT EXISTS idx_research_artifacts_updated_at
            ON research_artifacts(updated_at DESC);

        CREATE TABLE IF NOT EXISTS failure_cases (
            failure_id TEXT PRIMARY KEY,
            failure_type TEXT NOT NULL,
            doi TEXT NOT NULL DEFAULT '',
            query_text TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_failure_cases_type
            ON failure_cases(failure_type);
        CREATE INDEX IF NOT EXISTS idx_failure_cases_doi
            ON failure_cases(doi);
        CREATE INDEX IF NOT EXISTS idx_failure_cases_query
            ON failure_cases(query_text);
        CREATE INDEX IF NOT EXISTS idx_failure_cases_created_at
            ON failure_cases(created_at DESC);
        """
    )
    conn.commit()


def _encode_content(content: Any) -> tuple[str, str]:
    if isinstance(content, str):
        return "markdown", content
    return "json", json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)


def _decode_content(content_format: str, content_text: str) -> Any:
    if content_format == "json":
        try:
            return json.loads(content_text)
        except json.JSONDecodeError:
            return content_text
    return content_text


def _decode_metadata(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def save_research_artifact(
    db_path: Path,
    *,
    kind: str,
    title: str = "",
    content: Any,
    source_doi: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a reusable research artifact."""

    artifact_id = f"artifact_{uuid.uuid4().hex[:12]}"
    now = _utc_now()
    resolved_title = title.strip() or kind.replace("_", " ").strip().title()
    content_format, content_text = _encode_content(content)
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO research_artifacts (
                artifact_id, kind, title, content_text, content_format,
                source_doi, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                kind,
                resolved_title,
                content_text,
                content_format,
                source_doi.strip(),
                metadata_json,
                now,
                now,
            ),
        )
        conn.commit()

    preview = content_text if content_format == "markdown" else content_text[:400]
    if len(preview) > 280:
        preview = preview[:277].rstrip() + "..."
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "title": resolved_title,
        "content_format": content_format,
        "source_doi": source_doi.strip(),
        "metadata": metadata or {},
        "created_at": now,
        "preview": preview,
    }


def query_research_artifacts(
    db_path: Path,
    *,
    artifact_id: str = "",
    kind: str = "",
    query: str = "",
    detail: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Query previously saved research artifacts."""

    clauses: list[str] = []
    params: list[Any] = []

    if artifact_id.strip():
        clauses.append("artifact_id = ?")
        params.append(artifact_id.strip())
    if kind.strip():
        clauses.append("kind = ?")
        params.append(kind.strip())
    if query.strip():
        clauses.append("(title LIKE ? OR content_text LIKE ?)")
        needle = f"%{query.strip()}%"
        params.extend([needle, needle])

    sql = """
        SELECT artifact_id, kind, title, content_text, content_format,
               source_doi, metadata_json, created_at, updated_at
        FROM research_artifacts
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))

    with _connect(db_path) as conn:
        rows = list(conn.execute(sql, params))

    items: list[dict[str, Any]] = []
    for row in rows:
        content_text = str(row["content_text"])
        item = {
            "artifact_id": str(row["artifact_id"]),
            "kind": str(row["kind"]),
            "title": str(row["title"]),
            "content_format": str(row["content_format"]),
            "source_doi": str(row["source_doi"]),
            "metadata": _decode_metadata(str(row["metadata_json"])),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        if detail:
            item["content"] = _decode_content(str(row["content_format"]), content_text)
        else:
            preview = content_text[:280]
            if len(content_text) > 280:
                preview = preview.rstrip() + "..."
            item["preview"] = preview
        items.append(item)

    return {
        "count": len(items),
        "detail": detail,
        "items": items,
    }


def _retry_suggestions(
    *,
    failure_type: str,
    doi: str,
    query_text: str,
    source: str,
    error_message: str,
    similar_cases: list[dict[str, Any]],
) -> list[str]:
    error = error_message.lower()
    failure = failure_type.lower()
    source_name = source.strip() or "current backend"
    suggestions: list[str] = []

    if failure == "fetch":
        if "403" in error or "paywall" in error or "captcha" in error:
            suggestions.append(
                "Publisher access looks restricted on "
                f"{source_name}; retry with headless/browser-assisted "
                "extraction or a local PDF fallback."
            )
        if doi:
            suggestions.append(
                f"Retry extraction for {doi} with `expected_title` "
                "populated so QA can reject paywall stubs more safely."
            )
        suggestions.append(
            "If the DOI is high-value, record the extraction receipt "
            "and continue with neighboring cited papers first."
        )
    elif failure == "parse":
        suggestions.append(
            "Retry parsing with an alternate PDF parser order or "
            "re-run from a cleaner local PDF copy."
        )
        suggestions.append(
            "If parsing keeps failing, preserve the PDF receipt so "
            "downstream writing tools can explain the gap explicitly."
        )
    elif failure == "search":
        suggestions.append(
            "Narrow the English query and search the local saved-paper "
            "library before issuing another remote sweep."
        )
        suggestions.append(
            "Break the request into 2-3 subquestions and keep the "
            "evidence grid per subquestion instead of one broad query."
        )
    elif failure == "citation":
        suggestions.append(
            "Re-read the cited paragraph window with "
            "`read_saved_paper` before drafting or verifying the claim."
        )
        suggestions.append(
            "Run `audit_draft_support` on the affected paragraph to "
            "distinguish weak evidence from misattribution."
        )
    else:
        suggestions.append("Record the failure context and retry conservatively with a narrower scope.")

    if query_text:
        suggestions.append(
            f"Preserve the original query `{query_text}` inside the "
            "failure note so later retries can compare outcomes."
        )
    if similar_cases:
        suggestions.append(
            f"Found {len(similar_cases)} similar past failure case(s); "
            "inspect them before escalating to a new workflow."
        )
    return suggestions[:5]


def manage_failure_cases(
    db_path: Path,
    *,
    mode: str,
    failure_type: str = "",
    doi: str = "",
    query_text: str = "",
    source: str = "",
    error_message: str = "",
    context: dict[str, Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Record, query, or summarize failure cases."""

    resolved_limit = max(1, min(limit, 100))

    if mode == "record":
        failure_id = f"failure_{uuid.uuid4().hex[:12]}"
        created_at = _utc_now()
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO failure_cases (
                    failure_id, failure_type, doi, query_text, source,
                    error_message, context_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    failure_id,
                    failure_type.strip(),
                    doi.strip(),
                    query_text.strip(),
                    source.strip(),
                    error_message.strip(),
                    json.dumps(context or {}, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            conn.commit()
        return {
            "mode": "record",
            "failure_id": failure_id,
            "failure_type": failure_type.strip(),
            "doi": doi.strip(),
            "query": query_text.strip(),
            "source": source.strip(),
            "created_at": created_at,
        }

    clauses: list[str] = []
    params: list[Any] = []
    if failure_type.strip():
        clauses.append("failure_type = ?")
        params.append(failure_type.strip())
    if doi.strip():
        clauses.append("doi = ?")
        params.append(doi.strip())
    if source.strip():
        clauses.append("source LIKE ?")
        params.append(f"%{source.strip()}%")
    if query_text.strip():
        clauses.append("(query_text LIKE ? OR error_message LIKE ?)")
        needle = f"%{query_text.strip()}%"
        params.extend([needle, needle])

    sql = """
        SELECT failure_id, failure_type, doi, query_text, source,
               error_message, context_json, created_at
        FROM failure_cases
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(resolved_limit)

    with _connect(db_path) as conn:
        rows = list(conn.execute(sql, params))

    items = [
        {
            "failure_id": str(row["failure_id"]),
            "failure_type": str(row["failure_type"]),
            "doi": str(row["doi"]),
            "query": str(row["query_text"]),
            "source": str(row["source"]),
            "error_message": str(row["error_message"]),
            "context": _decode_metadata(str(row["context_json"])),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]

    if mode == "query":
        return {
            "mode": "query",
            "count": len(items),
            "items": items,
        }

    if mode == "suggest_retry":
        suggestions = _retry_suggestions(
            failure_type=failure_type,
            doi=doi,
            query_text=query_text,
            source=source,
            error_message=error_message,
            similar_cases=items,
        )
        return {
            "mode": "suggest_retry",
            "count": len(items),
            "similar_cases": items,
            "suggestions": suggestions,
        }

    return {
        "mode": mode,
        "error": "Unsupported mode. Use `record`, `query`, or `suggest_retry`.",
    }
