"""Shared byte-size guards for fetched and parsed document payloads."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_MAX_REMOTE_PDF_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_REMOTE_TEXT_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_LOCAL_PDF_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_BROWSER_CAPTURE_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_MINERU_ZIP_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_MINERU_FULL_MD_BYTES = 100 * 1024 * 1024


class SizeLimitError(ValueError):
    """Raised when a document payload exceeds a configured byte limit."""


def format_byte_limit(value: int) -> str:
    """Return a compact binary-size label for user-facing warnings."""
    if value >= 1024 * 1024:
        amount = value / (1024 * 1024)
        return f"{amount:.0f} MiB"
    if value >= 1024:
        amount = value / 1024
        return f"{amount:.0f} KiB"
    return f"{value} bytes"


def _content_length(headers: Any) -> int | None:
    get = getattr(headers, "get", None)
    if not callable(get):
        return None
    raw = str(get("content-length", "") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def ensure_byte_limit(size: int, *, max_bytes: int, label: str) -> None:
    """Raise if size exceeds max_bytes."""
    if size > max_bytes:
        raise SizeLimitError(
            f"{label} exceeds configured size limit "
            f"({format_byte_limit(size)} > {format_byte_limit(max_bytes)})"
        )


def ensure_content_length_allowed(headers: Any, *, max_bytes: int, label: str) -> None:
    length = _content_length(headers)
    if length is not None:
        ensure_byte_limit(length, max_bytes=max_bytes, label=label)


def ensure_response_within_limit(response: Any, *, max_bytes: int, label: str) -> None:
    """Check a response's declared and already-buffered byte size."""
    ensure_content_length_allowed(getattr(response, "headers", {}), max_bytes=max_bytes, label=label)
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        ensure_byte_limit(len(content), max_bytes=max_bytes, label=label)


def ensure_bytes_within_limit(data: bytes, *, max_bytes: int, label: str) -> None:
    ensure_byte_limit(len(data), max_bytes=max_bytes, label=label)


def _clone_response(response: httpx.Response, content: bytes) -> httpx.Response:
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=content,
        request=response.request,
        extensions=response.extensions,
    )


async def limited_async_get(
    client: Any,
    url: str,
    *,
    max_bytes: int,
    label: str,
    **kwargs: Any,
) -> Any:
    """GET a URL with a byte ceiling, streaming when the client supports it."""
    stream = getattr(client, "stream", None)
    if callable(stream):
        async with stream("GET", url, **kwargs) as response:
            ensure_content_length_allowed(response.headers, max_bytes=max_bytes, label=label)
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                ensure_byte_limit(len(buffer), max_bytes=max_bytes, label=label)
            return _clone_response(response, bytes(buffer))

    response = await client.get(url, **kwargs)
    ensure_response_within_limit(response, max_bytes=max_bytes, label=label)
    return response


def limited_sync_get(
    client: Any,
    url: str,
    *,
    max_bytes: int,
    label: str,
    **kwargs: Any,
) -> Any:
    """Synchronous GET with the same byte ceiling as limited_async_get."""
    stream = getattr(client, "stream", None)
    if callable(stream):
        with stream("GET", url, **kwargs) as response:
            ensure_content_length_allowed(response.headers, max_bytes=max_bytes, label=label)
            buffer = bytearray()
            for chunk in response.iter_bytes():
                buffer.extend(chunk)
                ensure_byte_limit(len(buffer), max_bytes=max_bytes, label=label)
            return _clone_response(response, bytes(buffer))

    response = client.get(url, **kwargs)
    ensure_response_within_limit(response, max_bytes=max_bytes, label=label)
    return response
