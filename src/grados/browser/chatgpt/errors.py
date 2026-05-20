"""Structured errors for ChatGPT browser mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatGPTBrowserError(Exception):
    code: str
    message: str
    stage: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "stage": self.stage,
            "details": self.details,
        }
