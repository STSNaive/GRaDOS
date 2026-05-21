"""Bounded local file reads used by local import and parse flows."""

from __future__ import annotations

import os
from pathlib import Path

from grados.http_limits import SizeLimitError, ensure_byte_limit


class LocalFileReadError(RuntimeError):
    """Raised when a local file cannot be read safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        int(file_stat.st_mode),
        int(file_stat.st_size),
        int(file_stat.st_mtime_ns),
    )


def read_bounded_local_file(path: Path, *, max_bytes: int, label: str) -> bytes:
    """Read a local file while enforcing max_bytes at the read boundary."""
    try:
        before = path.stat()
        ensure_byte_limit(before.st_size, max_bytes=max_bytes, label=label)
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        after = path.stat()
    except SizeLimitError:
        raise
    except OSError as exc:
        raise LocalFileReadError("read_error", f"{label} could not be read: {exc.__class__.__name__}: {exc}") from exc

    ensure_byte_limit(len(data), max_bytes=max_bytes, label=label)
    if _identity(before) != _identity(after):
        raise LocalFileReadError("file_changed", f"{label} changed while it was being read; try again.")
    return data
