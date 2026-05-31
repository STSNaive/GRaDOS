"""Durable operation registry for long-running GRaDOS workflows."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

__all__ = [
    "OPERATION_STATUS_ACCEPTED",
    "OPERATION_STATUS_CANCELLED",
    "OPERATION_STATUS_COMPLETED",
    "OPERATION_STATUS_FAILED",
    "OPERATION_STATUS_NEEDS_INPUT",
    "OPERATION_STATUS_PENDING",
    "OPERATION_STATUS_RUNNING",
    "OPERATION_STATUS_STALE",
    "OPERATION_STATUS_WAITING_MANUAL",
    "OPERATION_STATUS_WAITING_RETRY",
    "OPERATION_STATUSES",
    "OperationEvent",
    "OperationRecord",
    "append_operation_event",
    "build_operation_debug_bundle",
    "complete_operation",
    "create_operation",
    "fail_operation",
    "find_operation_by_idempotency_key",
    "get_operation",
    "heartbeat_operation",
    "list_operation_events",
    "mark_operation_stale",
    "new_operation_id",
    "operation_is_stale",
    "operation_status_payload",
    "update_operation",
]

OPERATION_STATUS_ACCEPTED = "accepted"
OPERATION_STATUS_RUNNING = "running"
OPERATION_STATUS_WAITING_MANUAL = "waiting_manual"
OPERATION_STATUS_WAITING_RETRY = "waiting_retry"
OPERATION_STATUS_PENDING = "pending"
OPERATION_STATUS_COMPLETED = "completed"
OPERATION_STATUS_FAILED = "failed"
OPERATION_STATUS_STALE = "stale"
OPERATION_STATUS_CANCELLED = "cancelled"
OPERATION_STATUS_NEEDS_INPUT = "needs_input"

OPERATION_STATUSES = (
    OPERATION_STATUS_ACCEPTED,
    OPERATION_STATUS_RUNNING,
    OPERATION_STATUS_WAITING_MANUAL,
    OPERATION_STATUS_WAITING_RETRY,
    OPERATION_STATUS_PENDING,
    OPERATION_STATUS_COMPLETED,
    OPERATION_STATUS_FAILED,
    OPERATION_STATUS_STALE,
    OPERATION_STATUS_CANCELLED,
    OPERATION_STATUS_NEEDS_INPUT,
)
TERMINAL_OPERATION_STATUSES = {
    OPERATION_STATUS_COMPLETED,
    OPERATION_STATUS_FAILED,
    OPERATION_STATUS_STALE,
    OPERATION_STATUS_CANCELLED,
}
OPERATION_SCHEMA_VERSION = 1

_SECRET_KEYS = {
    "apikey",
    "api_key",
    "api_keys",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "private_key",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "session_cookie",
    "sessionid",
    "session_token",
    "set_cookie",
    "token",
    "tokens",
}
_SECRET_KEY_PARTS = {
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "password",
    "secret",
    "session",
    "token",
}


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    kind: str
    status: str
    stage: str
    created_at: str
    updated_at: str
    heartbeat_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    idempotency_key: str = ""
    parent_operation_id: str = ""
    input: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    schema_version: int = OPERATION_SCHEMA_VERSION

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OperationRecord:
        return cls(
            operation_id=str(row["operation_id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            heartbeat_at=str(row["heartbeat_at"] or ""),
            started_at=str(row["started_at"] or ""),
            completed_at=str(row["completed_at"] or ""),
            idempotency_key=str(row["idempotency_key"] or ""),
            parent_operation_id=str(row["parent_operation_id"] or ""),
            input=_decode_json_object(str(row["input_json"] or "{}")),
            progress=_decode_json_object(str(row["progress_json"] or "{}")),
            runtime=_decode_json_object(str(row["runtime_json"] or "{}")),
            recovery=_decode_json_object(str(row["recovery_json"] or "{}")),
            result=_decode_json_object(str(row["result_json"] or "{}")),
            error=_decode_json_object(str(row["error_json"] or "{}")),
            schema_version=int(row["schema_version"] or OPERATION_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class OperationEvent:
    event_id: str
    operation_id: str
    event_type: str
    occurred_at: str
    stage: str
    message: str
    payload: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OperationEvent:
        return cls(
            event_id=str(row["event_id"]),
            operation_id=str(row["operation_id"]),
            event_type=str(row["event_type"]),
            occurred_at=str(row["occurred_at"]),
            stage=str(row["stage"] or ""),
            message=str(row["message"] or ""),
            payload=_decode_json_object(str(row["payload_json"] or "{}")),
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
        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            parent_operation_id TEXT NOT NULL DEFAULT '',
            input_json TEXT NOT NULL DEFAULT '{}',
            progress_json TEXT NOT NULL DEFAULT '{}',
            runtime_json TEXT NOT NULL DEFAULT '{}',
            recovery_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            error_json TEXT NOT NULL DEFAULT '{}',
            schema_version INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_operations_kind
            ON operations(kind);
        CREATE INDEX IF NOT EXISTS idx_operations_status
            ON operations(status);
        CREATE INDEX IF NOT EXISTS idx_operations_idempotency_key
            ON operations(idempotency_key);
        CREATE INDEX IF NOT EXISTS idx_operations_parent
            ON operations(parent_operation_id);
        CREATE INDEX IF NOT EXISTS idx_operations_updated_at
            ON operations(updated_at DESC);

        CREATE TABLE IF NOT EXISTS operation_events (
            event_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_operation_events_operation_id
            ON operation_events(operation_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_operation_events_type
            ON operation_events(event_type);
        """
    )
    conn.commit()


def _normalize_secret_key(key: object) -> str:
    camel_spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[^a-z0-9]+", "_", camel_spaced.lower()).strip("_")


def _is_secret_key(key: object) -> bool:
    normalized_key = _normalize_secret_key(key)
    if not normalized_key:
        return False
    if normalized_key in {
        "browser_session_id",
        "chatgpt_session_id",
        "recover_session_id",
        "session_id",
        "browser_session_record",
        "session_record",
        "session_record_path",
    }:
        return False
    if normalized_key in _SECRET_KEYS:
        return True
    parts = set(normalized_key.split("_"))
    return (
        bool(parts & _SECRET_KEY_PARTS)
        or {"api", "key"}.issubset(parts)
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


def _json_dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(_redact_secrets(value or {}), ensure_ascii=False, sort_keys=True)


def _decode_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _merge_json(current: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    if patch is None:
        return current or {}
    return {**(current or {}), **cast(dict[str, Any], _redact_secrets(patch))}


def _validate_status(status: str) -> str:
    normalized = status.strip()
    if normalized not in OPERATION_STATUSES:
        raise ValueError(f"invalid operation status: {status}")
    return normalized


def _row_for_operation(conn: sqlite3.Connection, operation_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM operations WHERE operation_id = ?",
        (operation_id.strip(),),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def _record_from_conn(conn: sqlite3.Connection, operation_id: str) -> OperationRecord | None:
    row = _row_for_operation(conn, operation_id)
    return OperationRecord.from_row(row) if row is not None else None


def new_operation_id(kind: str) -> str:
    normalized_kind = re.sub(r"[^a-z0-9]+", "_", kind.lower()).strip("_") or "operation"
    return f"op_{normalized_kind}_{uuid.uuid4().hex[:12]}"


def find_operation_by_idempotency_key(db_path: Path, idempotency_key: str) -> OperationRecord | None:
    key = idempotency_key.strip()
    if not key:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM operations
            WHERE idempotency_key = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    return OperationRecord.from_row(row) if row is not None else None


def create_operation(
    db_path: Path,
    *,
    kind: str,
    operation_id: str = "",
    status: str = OPERATION_STATUS_ACCEPTED,
    stage: str = "",
    idempotency_key: str = "",
    parent_operation_id: str = "",
    input_data: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    append_event: bool = True,
) -> tuple[OperationRecord, bool]:
    """Create an operation or return the existing idempotent record."""

    if not kind.strip():
        raise ValueError("operation kind is required")
    existing_by_key = find_operation_by_idempotency_key(db_path, idempotency_key) if idempotency_key else None
    if existing_by_key is not None and not operation_id:
        return existing_by_key, False

    op_id = operation_id.strip() or new_operation_id(kind)
    now = _utc_now()
    clean_status = _validate_status(status)
    started_at = now if clean_status not in {OPERATION_STATUS_ACCEPTED, OPERATION_STATUS_NEEDS_INPUT} else ""
    completed_at = now if clean_status in TERMINAL_OPERATION_STATUSES else ""
    heartbeat_at = now if clean_status in {OPERATION_STATUS_RUNNING, OPERATION_STATUS_PENDING} else ""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO operations (
                operation_id, kind, status, stage, created_at, updated_at,
                heartbeat_at, started_at, completed_at, idempotency_key,
                parent_operation_id, input_json, progress_json, runtime_json,
                recovery_json, result_json, error_json, schema_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                kind.strip(),
                clean_status,
                stage.strip(),
                now,
                now,
                heartbeat_at,
                started_at,
                completed_at,
                idempotency_key.strip(),
                parent_operation_id.strip(),
                _json_dumps(input_data),
                _json_dumps(progress),
                _json_dumps(runtime),
                _json_dumps(recovery),
                _json_dumps(result),
                _json_dumps(error),
                OPERATION_SCHEMA_VERSION,
            ),
        )
        conn.commit()
        record = _record_from_conn(conn, op_id)
    if record is None:
        raise RuntimeError(f"operation insert failed for {op_id}")
    created = cursor.rowcount == 1
    if created and append_event:
        append_operation_event(
            db_path,
            operation_id=op_id,
            event_type="operation_created",
            stage=stage or clean_status,
            payload={"kind": kind.strip(), "status": clean_status},
        )
    return record, created


def get_operation(db_path: Path, operation_id: str) -> OperationRecord | None:
    op_id = operation_id.strip()
    if not op_id:
        return None
    with _connect(db_path) as conn:
        row = _row_for_operation(conn, op_id)
    return OperationRecord.from_row(row) if row is not None else None


def update_operation(
    db_path: Path,
    operation_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    clear_error: bool = False,
    heartbeat: bool = False,
    event_type: str = "",
    event_message: str = "",
    event_payload: dict[str, Any] | None = None,
) -> OperationRecord | None:
    op_id = operation_id.strip()
    if not op_id:
        return None
    now = _utc_now()
    with _connect(db_path) as conn:
        current = _record_from_conn(conn, op_id)
        if current is None:
            return None
        next_status = _validate_status(status) if status is not None else current.status
        next_stage = stage.strip() if stage is not None else current.stage
        started_at = current.started_at
        if not started_at and next_status in {OPERATION_STATUS_RUNNING, OPERATION_STATUS_PENDING}:
            started_at = now
        completed_at = current.completed_at
        if next_status in TERMINAL_OPERATION_STATUSES and (not completed_at or current.status != next_status):
            completed_at = now
        if current.status in TERMINAL_OPERATION_STATUSES and next_status not in TERMINAL_OPERATION_STATUSES:
            completed_at = ""
        heartbeat_at = now if heartbeat else current.heartbeat_at
        next_error = _merge_json({}, error) if clear_error else _merge_json(current.error, error)
        conn.execute(
            """
            UPDATE operations
            SET status = ?,
                stage = ?,
                updated_at = ?,
                heartbeat_at = ?,
                started_at = ?,
                completed_at = ?,
                progress_json = ?,
                runtime_json = ?,
                recovery_json = ?,
                result_json = ?,
                error_json = ?
            WHERE operation_id = ?
            """,
            (
                next_status,
                next_stage,
                now,
                heartbeat_at,
                started_at,
                completed_at,
                _json_dumps(_merge_json(current.progress, progress)),
                _json_dumps(_merge_json(current.runtime, runtime)),
                _json_dumps(_merge_json(current.recovery, recovery)),
                _json_dumps(_merge_json(current.result, result)),
                _json_dumps(next_error),
                op_id,
            ),
        )
        conn.commit()
        updated = _record_from_conn(conn, op_id)
    if event_type:
        append_operation_event(
            db_path,
            operation_id=op_id,
            event_type=event_type,
            stage=next_stage,
            message=event_message,
            payload=event_payload,
        )
        return get_operation(db_path, op_id)
    return updated


def heartbeat_operation(
    db_path: Path,
    operation_id: str,
    *,
    stage: str | None = None,
    progress: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> OperationRecord | None:
    return update_operation(
        db_path,
        operation_id,
        status=OPERATION_STATUS_PENDING,
        stage=stage,
        progress=progress,
        runtime=runtime,
        heartbeat=True,
    )


def complete_operation(
    db_path: Path,
    operation_id: str,
    *,
    stage: str = "completed",
    progress: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    event_payload: dict[str, Any] | None = None,
) -> OperationRecord | None:
    return update_operation(
        db_path,
        operation_id,
        status=OPERATION_STATUS_COMPLETED,
        stage=stage,
        progress=progress,
        result=result,
        clear_error=True,
        event_type="operation_completed",
        event_payload=event_payload or result,
    )


def fail_operation(
    db_path: Path,
    operation_id: str,
    *,
    stage: str = "failed",
    error: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    event_payload: dict[str, Any] | None = None,
) -> OperationRecord | None:
    return update_operation(
        db_path,
        operation_id,
        status=OPERATION_STATUS_FAILED,
        stage=stage,
        result=result,
        error=error,
        event_type="operation_failed",
        event_payload=event_payload or error,
    )


def mark_operation_stale(
    db_path: Path,
    operation_id: str,
    *,
    message: str = "",
) -> OperationRecord | None:
    return update_operation(
        db_path,
        operation_id,
        status=OPERATION_STATUS_STALE,
        stage="stale",
        error={"message": message or "operation heartbeat is stale"},
        event_type="operation_stale",
        event_message=message,
    )


def append_operation_event(
    db_path: Path,
    *,
    operation_id: str,
    event_type: str,
    stage: str = "",
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> OperationEvent:
    op_id = operation_id.strip()
    if not op_id:
        raise ValueError("operation_id is required")
    if not event_type.strip():
        raise ValueError("event_type is required")
    event = OperationEvent(
        event_id=f"event_{uuid.uuid4().hex[:12]}",
        operation_id=op_id,
        event_type=event_type.strip(),
        occurred_at=_utc_now(),
        stage=stage.strip(),
        message=message.strip(),
        payload=cast(dict[str, Any], _redact_secrets(payload or {})),
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO operation_events (
                event_id, operation_id, event_type, occurred_at, stage, message, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.operation_id,
                event.event_type,
                event.occurred_at,
                event.stage,
                event.message,
                _json_dumps(event.payload),
            ),
        )
        conn.commit()
    return event


def list_operation_events(db_path: Path, operation_id: str, *, limit: int = 200) -> list[OperationEvent]:
    op_id = operation_id.strip()
    if not op_id:
        return []
    bounded_limit = max(1, min(int(limit), 1000))
    with _connect(db_path) as conn:
        rows = list(
            conn.execute(
                """
                SELECT * FROM operation_events
                WHERE operation_id = ?
                ORDER BY occurred_at ASC, event_id ASC
                LIMIT ?
                """,
                (op_id, bounded_limit),
            )
        )
    return [OperationEvent.from_row(row) for row in rows]


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def operation_is_stale(record: OperationRecord, *, stale_seconds: float) -> bool:
    if record.status in TERMINAL_OPERATION_STATUSES:
        return False
    reference = _parse_time(record.heartbeat_at) or _parse_time(record.updated_at)
    if reference is None:
        return False
    now = datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return (now - reference).total_seconds() >= stale_seconds


def _event_payload(event: OperationEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "stage": event.stage,
        "message": event.message,
        "payload": event.payload or {},
    }


def operation_status_payload(
    record: OperationRecord,
    *,
    events: list[OperationEvent] | None = None,
    detail: bool = False,
) -> dict[str, Any]:
    result = record.result or {}
    recovery = record.recovery or {}
    error = record.error or {}
    result_path = str(result.get("result_path") or result.get("path") or "")
    result_artifact_id = str(result.get("result_artifact_id") or result.get("artifact_id") or "")
    next_action = str(
        result.get("next_action")
        or recovery.get("next_action")
        or ("read_result" if record.status == OPERATION_STATUS_COMPLETED else "")
    )
    payload: dict[str, Any] = {
        "found": True,
        "operation_id": record.operation_id,
        "kind": record.kind,
        "status": record.status,
        "stage": record.stage,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "heartbeat_at": record.heartbeat_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "progress": record.progress or {},
        "next_action": next_action,
        "result_artifact_id": result_artifact_id,
        "result_path": result_path,
        "error": str(error.get("message") or error.get("error") or ""),
        "recovery_metadata": recovery,
    }
    if detail:
        payload.update(
            {
                "input": record.input or {},
                "runtime": record.runtime or {},
                "result": result,
                "error_detail": error,
                "events": [_event_payload(event) for event in events or []],
                "schema_version": record.schema_version,
                "parent_operation_id": record.parent_operation_id,
                "idempotency_key": record.idempotency_key,
            }
        )
    return payload


def build_operation_debug_bundle(db_path: Path, operation_id: str) -> dict[str, Any]:
    record = get_operation(db_path, operation_id)
    if record is None:
        return {"found": False, "operation_id": operation_id, "error": "operation_not_found"}
    events = list_operation_events(db_path, operation_id)
    linked_paths: dict[str, str] = {}
    for source in (record.recovery or {}, record.result or {}, record.runtime or {}):
        for key, value in source.items():
            if key.endswith("_path") or key.endswith("_record") or key in {
                "session_record",
                "run_manifest_path",
                "parse_attempt_id",
                "response_path",
            }:
                linked_paths[str(key)] = str(value)
    return {
        "found": True,
        "operation": operation_status_payload(record, events=events, detail=True),
        "linked_paths": linked_paths,
    }
