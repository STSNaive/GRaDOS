"""Persistent state for research artifacts and failure memory."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

__all__ = [
    "RESEARCH_RUN_MANIFEST_KIND",
    "append_research_run_event",
    "build_research_run_config_lock",
    "create_research_run_manifest",
    "link_research_run_artifact",
    "manage_failure_cases",
    "query_research_artifacts",
    "read_research_run_manifest",
    "save_research_artifact",
]

RESEARCH_RUN_MANIFEST_KIND = "research_run_manifest"
RESEARCH_RUN_MANIFEST_SCHEMA_VERSION = 1
_SECRET_KEYS = {
    "apikey",
    "apikeys",
    "api_key",
    "api_keys",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "cookies",
    "csrf",
    "csrf_token",
    "csrftoken",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "secrets",
    "session",
    "session_cookie",
    "sessionid",
    "session_token",
    "set_cookie",
    "token",
    "tokens",
    "x_csrftoken",
    "xsrf",
    "xsrf_token",
}
_SECRET_KEY_PARTS = {
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "csrf",
    "password",
    "passwd",
    "secret",
    "secrets",
    "session",
    "token",
    "tokens",
}


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


def _normalize_secret_key(key: object) -> str:
    camel_spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[^a-z0-9]+", "_", camel_spaced.lower()).strip("_")


def _is_secret_key(key: object) -> bool:
    normalized_key = _normalize_secret_key(key)
    if not normalized_key:
        return False
    if normalized_key in _SECRET_KEYS:
        return True
    parts = set(normalized_key.split("_"))
    return (
        bool(parts & _SECRET_KEY_PARTS)
        or {"api", "key"}.issubset(parts)
        or {"api", "keys"}.issubset(parts)
        or {"private", "key"}.issubset(parts)
    )


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(key):
                redacted[str(key)] = "<redacted>" if item else item
            else:
                redacted[str(key)] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item) for item in value]
    return value


def _redact_secret_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    return cast(dict[str, Any], _redact_secrets(value or {}))


def build_research_run_config_lock(
    config: Any | None = None,
    *,
    paths: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redacted, run-level provenance snapshot.

    The lock is diagnostic context only. It intentionally records provider
    order, parser/indexing knobs, and artifact locations without preserving
    token, cookie, or API-key material.
    """

    try:
        from grados import __version__ as grados_version
    except Exception:  # pragma: no cover - defensive for editable metadata edge cases
        grados_version = "unknown"

    config_payload: dict[str, Any] = {}
    if config is not None:
        if hasattr(config, "model_dump"):
            config_payload = config.model_dump(mode="json")
        elif isinstance(config, dict):
            config_payload = dict(config)

    path_payload: dict[str, str] = {}
    if paths is not None:
        for name in (
            "root",
            "papers",
            "database_state",
            "database_chroma",
            "database_remote_metadata",
            "research_checkpoints",
            "paper_summaries",
        ):
            value = getattr(paths, name, None)
            if value is not None:
                path_payload[name] = str(value)

    lock = {
        "schema_version": RESEARCH_RUN_MANIFEST_SCHEMA_VERSION,
        "grados_version": grados_version,
        "captured_at": _utc_now(),
        "search": config_payload.get("search", {}),
        "extract": config_payload.get("extract", {}),
        "research": config_payload.get("research", {}),
        "indexing": config_payload.get("indexing", {}),
        "api_keys": config_payload.get("api_keys", {}),
        "retry_policy": config_payload.get("retry_policy", {}),
        "paths": path_payload,
    }
    if extra:
        lock["extra"] = extra
    return _redact_secret_dict(lock)


def _make_research_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def _empty_research_run_manifest(
    *,
    research_run_id: str,
    user_question: str = "",
    search_queries: list[str] | None = None,
    config_lock: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    now = created_at or _utc_now()
    return {
        "schema_version": RESEARCH_RUN_MANIFEST_SCHEMA_VERSION,
        "research_run_id": research_run_id,
        "user_question": user_question,
        "search_queries": search_queries or [],
        "artifact_index": [],
        "event_ledger": [],
        "config_lock": _redact_secret_dict(config_lock),
        "created_at": now,
        "updated_at": now,
    }


def _decode_manifest(row: sqlite3.Row) -> dict[str, Any] | None:
    content = _decode_content(str(row["content_format"]), str(row["content_text"]))
    if not isinstance(content, dict):
        return None
    if str(content.get("research_run_id", "") or ""):
        return content
    return None


def _find_research_run_manifest_row(conn: sqlite3.Connection, research_run_id: str) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT artifact_id, kind, title, content_text, content_format,
               source_doi, metadata_json, created_at, updated_at
        FROM research_artifacts
        WHERE kind = ?
        ORDER BY updated_at DESC
        """,
        (RESEARCH_RUN_MANIFEST_KIND,),
    )
    for row in rows:
        metadata = _decode_metadata(str(row["metadata_json"]))
        if metadata.get("research_run_id") == research_run_id:
            return cast(sqlite3.Row, row)
        manifest = _decode_manifest(row)
        if manifest is not None and manifest.get("research_run_id") == research_run_id:
            return cast(sqlite3.Row, row)
    return None


def _manifest_title(research_run_id: str, title: str = "", user_question: str = "") -> str:
    if title.strip():
        return title.strip()
    if user_question.strip():
        return f"Research run: {user_question.strip()[:80]}"
    return f"Research run: {research_run_id}"


def _insert_manifest(
    conn: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    title: str = "",
    metadata: dict[str, Any] | None = None,
) -> str:
    artifact_id = f"artifact_{uuid.uuid4().hex[:12]}"
    now = str(manifest.get("created_at") or _utc_now())
    research_run_id = str(manifest.get("research_run_id", "") or "")
    metadata_payload = {"research_run_id": research_run_id, **_redact_secret_dict(metadata)}
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
            RESEARCH_RUN_MANIFEST_KIND,
            _manifest_title(research_run_id, title, str(manifest.get("user_question", "") or "")),
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            "json",
            "",
            json.dumps(metadata_payload, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
    return artifact_id


def _update_manifest_row(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    manifest: dict[str, Any],
    title: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    now = _utc_now()
    manifest["updated_at"] = now
    research_run_id = str(manifest.get("research_run_id", "") or "")
    metadata_payload = {"research_run_id": research_run_id, **_redact_secret_dict(metadata)}
    conn.execute(
        """
        UPDATE research_artifacts
        SET title = ?, content_text = ?, content_format = ?,
            metadata_json = ?, updated_at = ?
        WHERE artifact_id = ?
        """,
        (
            _manifest_title(research_run_id, title, str(manifest.get("user_question", "") or "")),
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            "json",
            json.dumps(metadata_payload, ensure_ascii=False, sort_keys=True),
            now,
            artifact_id,
        ),
    )


def _manifest_receipt(artifact_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "kind": RESEARCH_RUN_MANIFEST_KIND,
        "research_run_id": str(manifest.get("research_run_id", "") or ""),
        "event_count": len(manifest.get("event_ledger", []) or []),
        "artifact_count": len(manifest.get("artifact_index", []) or []),
        "created_at": str(manifest.get("created_at", "") or ""),
        "updated_at": str(manifest.get("updated_at", "") or ""),
    }


def create_research_run_manifest(
    db_path: Path,
    *,
    research_run_id: str = "",
    title: str = "",
    user_question: str = "",
    search_queries: list[str] | None = None,
    config_lock: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update the directory page for one research run."""

    run_id = research_run_id.strip() or _make_research_run_id()
    with _connect(db_path) as conn:
        row = _find_research_run_manifest_row(conn, run_id)
        if row is None:
            manifest = _empty_research_run_manifest(
                research_run_id=run_id,
                user_question=user_question,
                search_queries=search_queries,
                config_lock=config_lock,
            )
            manifest["event_ledger"].append(
                {
                    "event_id": f"event_{uuid.uuid4().hex[:12]}",
                    "event_type": "run_started",
                    "occurred_at": manifest["created_at"],
                    "source": "research_run_manifest",
                    "artifact_id": "",
                    "payload": {},
                }
            )
            artifact_id = _insert_manifest(conn, manifest=manifest, title=title, metadata=metadata)
        else:
            artifact_id = str(row["artifact_id"])
            manifest = _decode_manifest(row) or _empty_research_run_manifest(research_run_id=run_id)
            if user_question.strip():
                manifest["user_question"] = user_question.strip()
            if search_queries:
                existing_queries = [str(item) for item in manifest.get("search_queries", [])]
                for query in search_queries:
                    if query and query not in existing_queries:
                        existing_queries.append(query)
                manifest["search_queries"] = existing_queries
            if config_lock:
                manifest["config_lock"] = _redact_secret_dict(config_lock)
            _update_manifest_row(conn, artifact_id=artifact_id, manifest=manifest, title=title, metadata=metadata)
        conn.commit()
    return _manifest_receipt(artifact_id, manifest)


def read_research_run_manifest(db_path: Path, *, research_run_id: str) -> dict[str, Any]:
    """Read a research run manifest by run id."""

    run_id = research_run_id.strip()
    if not run_id:
        return {"found": False, "error": "missing_research_run_id"}
    with _connect(db_path) as conn:
        row = _find_research_run_manifest_row(conn, run_id)
    if row is None:
        return {"found": False, "research_run_id": run_id, "error": "research_run_manifest_not_found"}
    manifest = _decode_manifest(row)
    if manifest is None:
        return {"found": False, "research_run_id": run_id, "error": "invalid_research_run_manifest"}
    return {
        "found": True,
        "artifact_id": str(row["artifact_id"]),
        "kind": RESEARCH_RUN_MANIFEST_KIND,
        "research_run_id": run_id,
        "manifest": manifest,
    }


def append_research_run_event(
    db_path: Path,
    *,
    research_run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: str = "",
    artifact_id: str = "",
) -> dict[str, Any]:
    """Append one durable event to a research run manifest."""

    run_id = research_run_id.strip()
    if not run_id:
        raise ValueError("research_run_id is required")
    event = {
        "event_id": f"event_{uuid.uuid4().hex[:12]}",
        "event_type": event_type.strip() or "event",
        "occurred_at": _utc_now(),
        "source": source.strip(),
        "artifact_id": artifact_id.strip(),
        "payload": _redact_secret_dict(payload),
    }
    with _connect(db_path) as conn:
        row = _find_research_run_manifest_row(conn, run_id)
        if row is None:
            manifest = _empty_research_run_manifest(research_run_id=run_id)
            artifact_id_for_manifest = _insert_manifest(conn, manifest=manifest)
        else:
            artifact_id_for_manifest = str(row["artifact_id"])
            manifest = _decode_manifest(row) or _empty_research_run_manifest(research_run_id=run_id)
        manifest.setdefault("event_ledger", []).append(event)
        _update_manifest_row(conn, artifact_id=artifact_id_for_manifest, manifest=manifest)
        conn.commit()
    receipt = _manifest_receipt(artifact_id_for_manifest, manifest)
    receipt["event"] = event
    return receipt


def link_research_run_artifact(
    db_path: Path,
    *,
    research_run_id: str,
    artifact_id: str,
    kind: str,
    title: str = "",
    role: str = "",
    source_doi: str = "",
    path: str = "",
    metadata: dict[str, Any] | None = None,
    canonical_anchors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add or update one artifact pointer in a research run manifest."""

    run_id = research_run_id.strip()
    if not run_id:
        raise ValueError("research_run_id is required")
    entry = {
        "artifact_id": artifact_id.strip(),
        "kind": kind.strip(),
        "title": title.strip(),
        "role": role.strip(),
        "source_doi": source_doi.strip(),
        "path": path.strip(),
        "metadata": _redact_secret_dict(metadata),
        "canonical_anchors": canonical_anchors or [],
        "linked_at": _utc_now(),
    }
    with _connect(db_path) as conn:
        row = _find_research_run_manifest_row(conn, run_id)
        if row is None:
            manifest = _empty_research_run_manifest(research_run_id=run_id)
            artifact_id_for_manifest = _insert_manifest(conn, manifest=manifest)
        else:
            artifact_id_for_manifest = str(row["artifact_id"])
            manifest = _decode_manifest(row) or _empty_research_run_manifest(research_run_id=run_id)
        artifact_index = list(manifest.get("artifact_index", []) or [])
        dedupe_key = (
            entry["artifact_id"],
            entry["kind"],
            entry["role"],
            entry["path"],
        )
        artifact_index = [
            item
            for item in artifact_index
            if (
                str(item.get("artifact_id", "") or ""),
                str(item.get("kind", "") or ""),
                str(item.get("role", "") or ""),
                str(item.get("path", "") or ""),
            )
            != dedupe_key
        ]
        artifact_index.append(entry)
        manifest["artifact_index"] = artifact_index
        _update_manifest_row(conn, artifact_id=artifact_id_for_manifest, manifest=manifest)
        conn.commit()
    receipt = _manifest_receipt(artifact_id_for_manifest, manifest)
    receipt["linked_artifact"] = entry
    return receipt


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
    metadata_payload = _redact_secret_dict(metadata)
    metadata_json = json.dumps(metadata_payload, ensure_ascii=False, sort_keys=True)

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
    receipt = {
        "artifact_id": artifact_id,
        "kind": kind,
        "title": resolved_title,
        "content_format": content_format,
        "source_doi": source_doi.strip(),
        "metadata": metadata_payload,
        "created_at": now,
        "preview": preview,
    }
    research_run_id = str((metadata or {}).get("research_run_id", "") or "").strip()
    if research_run_id and kind != RESEARCH_RUN_MANIFEST_KIND:
        link_receipt = link_research_run_artifact(
            db_path,
            research_run_id=research_run_id,
            artifact_id=artifact_id,
            kind=kind,
            title=resolved_title,
            role=str((metadata or {}).get("research_run_role", "") or kind),
            source_doi=source_doi,
            metadata=metadata_payload,
        )
        append_research_run_event(
            db_path,
            research_run_id=research_run_id,
            event_type="artifact_saved",
            source="save_research_artifact",
            artifact_id=artifact_id,
            payload={
                "kind": kind,
                "title": resolved_title,
                "source_doi": source_doi.strip(),
                "manifest_artifact_id": link_receipt.get("artifact_id", ""),
            },
        )
        receipt["research_run"] = {
            "research_run_id": research_run_id,
            "manifest_artifact_id": link_receipt.get("artifact_id", ""),
        }
    return receipt


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
            "metadata": _redact_secret_dict(_decode_metadata(str(row["metadata_json"]))),
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
                f"{source_name}; retry with browser-assisted "
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
            "distinguish minor distortion from citation mismatch."
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
        redacted_context = _redact_secret_dict(context)
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
                    json.dumps(redacted_context, ensure_ascii=False, sort_keys=True),
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
            "context": _redact_secret_dict(_decode_metadata(str(row["context_json"]))),
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
