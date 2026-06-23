from __future__ import annotations

import pytest

from grados.url_safety import UnsafeURLError, validate_public_http_url


def test_validate_public_http_url_accepts_public_http_urls() -> None:
    assert validate_public_http_url("https://example.com/paper.pdf") == "https://example.com/paper.pdf"
    assert (
        validate_public_http_url("/downloads/paper.pdf", base_url="https://publisher.example/article")
        == "https://publisher.example/downloads/paper.pdf"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:pass@example.com/paper.pdf",
        "http://localhost/paper.pdf",
        "http://127.0.0.1/paper.pdf",
        "http://10.0.0.5/paper.pdf",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/paper.pdf",
    ],
)
def test_validate_public_http_url_rejects_local_or_private_targets(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url(url)
