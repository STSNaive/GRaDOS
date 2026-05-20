"""Typed session records for browser-based PDF acquisition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PDF_BROWSER_MODE_VERSION = "pdf-browser-v1"


@dataclass
class PdfBrowserEvent:
    timestamp: str
    name: str
    url: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PdfBrowserCapture:
    source: str = ""
    url: str = ""
    content_type: str = ""
    bytes: int = 0


@dataclass
class PdfBrowserSessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    doi: str
    target_url: str = ""
    resume: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    outcome: str = ""
    source: str = ""
    browser_label: str = ""
    browser_source: str = ""
    profile_dir: str = ""
    final_url: str = ""
    host: str = ""
    manual: bool = False
    capture: PdfBrowserCapture = field(default_factory=PdfBrowserCapture)
    warnings: list[str] = field(default_factory=list)
    events: list[PdfBrowserEvent] = field(default_factory=list)
    record_path: str = ""
