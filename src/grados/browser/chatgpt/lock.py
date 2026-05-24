"""GRaDOS browser profile locking for the private ChatGPT browser profile."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from grados.browser.chatgpt.errors import ChatGPTBrowserError

CHATGPT_PROFILE_LOCK_FILENAME = "grados-chatgpt-browser.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class ChatGPTProfileLock:
    lock_path: Path
    purpose: str
    session_id: str
    timeout_seconds: float = 30.0
    poll_seconds: float = 0.25
    lock_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    async def __aenter__(self) -> ChatGPTProfileLock:
        started_at = time.monotonic()
        while True:
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                with self.lock_path.open("x", encoding="utf-8") as handle:
                    json.dump(self._payload(), handle, indent=2)
                return self
            except FileExistsError:
                existing = self._read_lock()
                if not existing:
                    await asyncio.sleep(0.2)
                    existing = self._read_lock()
                    if not existing:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                pid = int(existing.get("pid") or 0)
                if not _pid_alive(pid):
                    self.lock_path.unlink(missing_ok=True)
                    continue
                elapsed = time.monotonic() - started_at
                if elapsed >= self.timeout_seconds:
                    raise ChatGPTBrowserError(
                        code="chatgpt_profile_locked",
                        stage="profile-lock",
                        message="Another GRaDOS ChatGPT browser run is using the private profile.",
                        details={
                            "lock_path": str(self.lock_path),
                            "held_by_pid": pid,
                            "purpose": self.purpose,
                            "session_id": self.session_id,
                        },
                    )
                await asyncio.sleep(min(self.poll_seconds, self.timeout_seconds - elapsed))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def release(self) -> None:
        existing = self._read_lock()
        if existing.get("lockId") != self.lock_id:
            return
        self.lock_path.unlink(missing_ok=True)

    def _payload(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "lockId": self.lock_id,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "purpose": self.purpose,
            "sessionId": self.session_id,
        }

    def _read_lock(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}


def chatgpt_profile_lock(profile_dir: Path, *, purpose: str, session_id: str) -> ChatGPTProfileLock:
    return ChatGPTProfileLock(
        lock_path=profile_dir / CHATGPT_PROFILE_LOCK_FILENAME,
        purpose=purpose,
        session_id=session_id,
    )
