"""Operational session store for browser-based PDF acquisition."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.browser.pdf.types import (
    PDF_BROWSER_MODE_VERSION,
    PdfBrowserCapture,
    PdfBrowserEvent,
    PdfBrowserSessionRecord,
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_pdf_browser_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"pdf-{stamp}-{uuid.uuid4().hex[:8]}"


def _safe_session_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._") or "pdf-session"


def create_pdf_browser_session(
    session_root: Path,
    *,
    doi: str,
    target_url: str = "",
    resume: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> PdfBrowserSessionRecord:
    session_id = session_id or new_pdf_browser_session_id()
    session_dir = session_root / _safe_session_name(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    created_at = now_iso()
    record = PdfBrowserSessionRecord(
        session_id=session_id,
        created_at=created_at,
        updated_at=created_at,
        doi=doi,
        target_url=target_url,
        resume=dict(resume or {}),
        record_path=str(session_dir / "session.json"),
    )
    write_pdf_browser_session(record)
    return record


def append_pdf_browser_event(
    record: PdfBrowserSessionRecord,
    name: str,
    *,
    url: str = "",
    details: dict[str, Any] | None = None,
) -> PdfBrowserEvent:
    event = PdfBrowserEvent(timestamp=now_iso(), name=name, url=url, details=dict(details or {}))
    record.events.append(event)
    record.updated_at = event.timestamp
    write_pdf_browser_session(record)
    return event


def update_pdf_browser_session(
    record: PdfBrowserSessionRecord,
    *,
    status: str | None = None,
    outcome: str | None = None,
    source: str | None = None,
    browser_label: str | None = None,
    browser_source: str | None = None,
    profile_dir: str | None = None,
    final_url: str | None = None,
    host: str | None = None,
    manual: bool | None = None,
    capture: dict[str, Any] | PdfBrowserCapture | None = None,
    warnings: list[str] | None = None,
    events: list[dict[str, Any] | PdfBrowserEvent] | None = None,
) -> PdfBrowserSessionRecord:
    if status is not None:
        record.status = status
    if outcome is not None:
        record.outcome = outcome
    if source is not None:
        record.source = source
    if browser_label is not None:
        record.browser_label = browser_label
    if browser_source is not None:
        record.browser_source = browser_source
    if profile_dir is not None:
        record.profile_dir = profile_dir
    if final_url is not None:
        record.final_url = final_url
    if host is not None:
        record.host = host
    if manual is not None:
        record.manual = manual
    if capture is not None:
        record.capture = capture if isinstance(capture, PdfBrowserCapture) else PdfBrowserCapture(**capture)
    if warnings is not None:
        record.warnings = list(warnings)
    if events is not None:
        record.events = [
            event if isinstance(event, PdfBrowserEvent) else PdfBrowserEvent(**event)
            for event in events
        ]
    record.updated_at = now_iso()
    write_pdf_browser_session(record)
    return record


def write_pdf_browser_session(record: PdfBrowserSessionRecord) -> None:
    path = Path(record.record_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "browser_mode_version": PDF_BROWSER_MODE_VERSION,
        **asdict(record),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
