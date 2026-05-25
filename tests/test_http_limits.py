from __future__ import annotations

import asyncio

import httpx

from grados.http_limits import limited_async_get


class _DecodedStreamResponse:
    status_code = 200
    headers = httpx.Headers(
        {
            "content-encoding": "gzip",
            "content-length": "999",
            "content-type": "application/xml",
            "transfer-encoding": "chunked",
        }
    )
    request = httpx.Request("GET", "https://example.test/article")
    extensions: dict[str, object] = {}

    async def aiter_bytes(self):
        yield b"<root>"
        yield b"already decoded"
        yield b"</root>"


class _StreamContext:
    async def __aenter__(self):
        return _DecodedStreamResponse()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None


class _StreamingClient:
    def stream(self, method: str, url: str, **kwargs):  # noqa: ANN003
        _ = method, url, kwargs
        return _StreamContext()


def test_limited_async_get_clones_decoded_stream_without_stale_encoding_headers() -> None:
    response = asyncio.run(
        limited_async_get(
            _StreamingClient(),
            "https://example.test/article",
            max_bytes=1024,
            label="Elsevier XML response",
        )
    )

    assert response.text == "<root>already decoded</root>"
    assert "content-encoding" not in response.headers
    assert "transfer-encoding" not in response.headers
    assert response.headers.get("content-length") != "999"
