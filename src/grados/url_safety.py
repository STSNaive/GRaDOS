"""Safety checks for externally supplied fetch URLs."""

from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlsplit


class UnsafeURLError(ValueError):
    """Raised when an external URL targets a disallowed local or private resource."""


def _public_ip_address(hostname: str) -> bool | None:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return None
    return bool(address.is_global and not address.is_multicast)


def _local_hostname(hostname: str) -> bool:
    lowered = hostname.rstrip(".").lower()
    return lowered == "localhost" or lowered.endswith(".localhost")


def validate_public_http_url(raw_url: str, *, source: str = "URL", base_url: str = "") -> str:
    """Return a normalized URL if it is safe for public HTTP(S) fetching."""
    candidate = urljoin(base_url, raw_url.strip()) if base_url else raw_url.strip()
    if not candidate:
        raise UnsafeURLError(f"{source} is empty")

    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"{source} uses disallowed scheme {parsed.scheme or '<none>'!r}")
    if not parsed.hostname:
        raise UnsafeURLError(f"{source} is missing a host")
    if parsed.username or parsed.password:
        raise UnsafeURLError(f"{source} must not include credentials")

    # Accessing .port validates the netloc and raises for malformed ports.
    try:
        _ = parsed.port
    except ValueError as exc:
        raise UnsafeURLError(f"{source} has an invalid port") from exc

    hostname = parsed.hostname
    if _local_hostname(hostname):
        raise UnsafeURLError(f"{source} targets localhost")

    public_ip = _public_ip_address(hostname)
    if public_ip is False:
        raise UnsafeURLError(f"{source} targets a non-public IP address")

    return parsed.geturl()
