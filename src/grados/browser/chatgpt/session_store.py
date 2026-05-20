"""Operational session records for ChatGPT browser runs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SESSION_ID_PATTERN = re.compile(r"^chatgpt-\d{8}T\d{6}-[0-9a-f]{8}$")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"chatgpt-{stamp}-{uuid4().hex[:8]}"


def is_valid_chatgpt_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_PATTERN.fullmatch(session_id))


def _validate_session_id(session_id: str) -> str:
    if not is_valid_chatgpt_session_id(session_id):
        raise ValueError("invalid ChatGPT browser session id")
    return session_id


class ChatGPTSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def session_dir(self, session_id: str) -> Path:
        return self.root / _validate_session_id(session_id)

    def session_json(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def prompt_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "prompt.txt"

    def response_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "response.md"

    def create(
        self,
        *,
        session_id: str,
        pack_id: str,
        packet_artifact_id: str,
        prompt_hash: str,
        prompt: str,
        mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        directory = self.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.prompt_path(session_id).write_text(prompt, encoding="utf-8")
        record = {
            "session_id": session_id,
            "status": "running",
            "pack_id": pack_id,
            "packet_artifact_id": packet_artifact_id,
            "prompt_hash": prompt_hash,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "mode": mode,
            "conversation_url": "",
            "created_at": now,
            "updated_at": now,
            "metadata": metadata or {},
        }
        self.write(session_id, record)
        return record

    def read(self, session_id: str) -> dict[str, Any] | None:
        try:
            raw = self.session_json(session_id).read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def write(self, session_id: str, record: dict[str, Any]) -> None:
        directory = self.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {**record, "updated_at": utc_now()}
        self.session_json(session_id).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update(self, session_id: str, **updates: Any) -> dict[str, Any]:
        record = self.read(session_id) or {"session_id": session_id, "created_at": utc_now()}
        record.update(updates)
        self.write(session_id, record)
        return self.read(session_id) or record

    def save_response(self, session_id: str, response_text: str) -> str:
        self.response_path(session_id).write_text(response_text, encoding="utf-8")
        return str(self.response_path(session_id))
