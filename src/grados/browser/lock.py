"""Cross-process profile locking for GRaDOS browser acquisition paths."""

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

PDF_PROFILE_LOCK_FILENAME = "grados-browser.lock"
_PROCESS_LOCKS: dict[tuple[int, Path], asyncio.Lock] = {}


class BrowserProfileLockError(RuntimeError):
    """Raised when another live process owns the persistent browser profile."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


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


def read_browser_profile_lock(profile_dir: Path) -> dict[str, Any]:
    lock_path = profile_dir / PDF_PROFILE_LOCK_FILENAME
    try:
        parsed = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _process_lock_for(lock_path: Path) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), lock_path)
    lock = _PROCESS_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROCESS_LOCKS[key] = lock
    return lock


@dataclass
class BrowserProfileLock:
    profile_dir: Path
    purpose: str
    session_id: str
    timeout_seconds: float = 5.0
    poll_seconds: float = 0.25
    lock_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reentrant: bool = False
    owns_file_lock: bool = False
    _file_released: bool = False
    _process_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _process_lock_acquired: bool = field(default=False, init=False, repr=False)

    @property
    def lock_path(self) -> Path:
        return self.profile_dir / PDF_PROFILE_LOCK_FILENAME

    async def __aenter__(self) -> BrowserProfileLock:
        started_at = time.monotonic()
        self._process_lock = _process_lock_for(self.lock_path)
        try:
            if self.timeout_seconds <= 0:
                if self._process_lock.locked():
                    raise TimeoutError
                await self._process_lock.acquire()
            else:
                await asyncio.wait_for(self._process_lock.acquire(), timeout=self.timeout_seconds)
            self._process_lock_acquired = True
        except TimeoutError as exc:
            raise BrowserProfileLockError(
                "Another GRaDOS browser run is using the persistent publisher profile.",
                details={
                    "lock_path": str(self.lock_path),
                    "purpose": self.purpose,
                    "session_id": self.session_id,
                    "scope": "process",
                },
            ) from exc

        try:
            while True:
                try:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    with self.lock_path.open("x", encoding="utf-8") as handle:
                        json.dump(self._payload(), handle, indent=2)
                    self.owns_file_lock = True
                    return self
                except FileExistsError:
                    existing = self._read_lock()
                    if not existing:
                        await asyncio.sleep(0.05)
                        existing = self._read_lock()
                        if not existing:
                            self.lock_path.unlink(missing_ok=True)
                            continue

                    pid = int(existing.get("pid") or 0)
                    if pid == os.getpid():
                        self.reentrant = True
                        return self
                    if not _pid_alive(pid):
                        self.lock_path.unlink(missing_ok=True)
                        continue

                    elapsed = time.monotonic() - started_at
                    if elapsed >= self.timeout_seconds:
                        raise BrowserProfileLockError(
                            "Another GRaDOS browser run is using the persistent publisher profile.",
                            details={
                                "lock_path": str(self.lock_path),
                                "held_by_pid": pid,
                                "held_session_id": str(existing.get("sessionId") or ""),
                                "purpose": self.purpose,
                                "session_id": self.session_id,
                            },
                        )
                    await asyncio.sleep(min(self.poll_seconds, max(0.0, self.timeout_seconds - elapsed)))
        except Exception:
            self.release(release_file=False)
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, tb)
        self.release()

    def release(self, *, release_file: bool = True) -> None:
        if release_file and self.owns_file_lock and not self._file_released:
            existing = self._read_lock()
            if existing.get("lockId") == self.lock_id:
                self.lock_path.unlink(missing_ok=True)
            self._file_released = True
        self._release_process_lock()

    def _release_process_lock(self) -> None:
        if not self._process_lock_acquired or self._process_lock is None:
            return
        self._process_lock.release()
        self._process_lock_acquired = False

    def _payload(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "lockId": self.lock_id,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "purpose": self.purpose,
            "sessionId": self.session_id,
            "profileDir": str(self.profile_dir),
        }

    def _read_lock(self) -> dict[str, Any]:
        return read_browser_profile_lock(self.profile_dir)


def browser_profile_lock(
    profile_dir: Path,
    *,
    purpose: str,
    session_id: str,
    timeout_seconds: float = 5.0,
) -> BrowserProfileLock:
    return BrowserProfileLock(
        profile_dir=profile_dir,
        purpose=purpose,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
