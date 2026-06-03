"""Internal types for ChatGPT browser-mode external consult."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

BROWSER_MODE_VERSION = "external-consult-browser-v1"
DEFAULT_PROMPT_CHAR_LIMIT = 120_000

ChatGPTBrowserStatus = Literal[
    "created",
    "prompt_submitted_once",
    "waiting_for_assistant",
    "recovering",
    "captured",
    "saved",
    "completed",
    "audited",
    "incomplete_capture",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class ChatGPTModelSelection:
    requested: str
    resolved_label: str
    available_labels: list[str] = field(default_factory=list)
    strategy: str = "latest_pro"
    verified: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChatGPTThinkingSelection:
    requested: str
    resolved_label: str
    available_labels: list[str] = field(default_factory=list)
    rank: int = 0
    verified: bool = False
    strategy: str = "highest"
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChatGPTCapture:
    response_text: str
    method: str
    warnings: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChatGPTBrowserResult:
    ok: bool
    status: ChatGPTBrowserStatus
    session_id: str
    response_text: str = ""
    conversation_url: str = ""
    model: ChatGPTModelSelection | None = None
    thinking: ChatGPTThinkingSelection | None = None
    capture: ChatGPTCapture | None = None
    error: str = ""
    error_code: str = ""
    session_record_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def model_label(self) -> str:
        return self.model.resolved_label if self.model else ""

    @property
    def thinking_label(self) -> str:
        return self.thinking.resolved_label if self.thinking else ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["browser_mode_version"] = BROWSER_MODE_VERSION
        return payload
