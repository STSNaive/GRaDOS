"""Unified retry / timeout policy for external HTTP calls (ADR-008).

Design notes:

* Policy is resolved at **call time**, not decorator-construction time. This lets
  `install_runtime_defaults(config)` update retry / timeout knobs for a running
  process (or — more commonly — be called once at startup so a fresh process
  picks up the user's `~/GRaDOS/config.json` without code changes). No import-
  time freezing (see TODO P1-T5.1).
* Retries cover transient errors: HTTP 429, HTTP 5xx, httpx.ConnectError,
  httpx.ReadTimeout, httpx.WriteError, httpx.PoolTimeout, httpx.RemoteProtocolError.
  Non-retryable errors propagate immediately.
* Getters (`current_search_timeout`, `current_fetch_timeout`,
  `current_pdf_timeout`, `current_browser_networkidle_timeout`,
  `current_browser_deadline`, `current_browser_poll_bounds`) return *live*
  values, so call sites avoid capturing defaults at import time.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)
from tenacity.wait import wait_base

if TYPE_CHECKING:  # pragma: no cover
    from grados.config import GRaDOSConfig

logger = logging.getLogger(__name__)

# HTTP status codes worth retrying: rate-limit + upstream transient failures.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


# ── Policy dataclasses ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    max_wait: float = 8.0
    respect_retry_after: bool = True  # reserved for T6 header-aware backoff


@dataclass(frozen=True)
class TimeoutPolicy:
    """Timeouts for the various HTTP call classes.

    `search`: academic metadata APIs (Crossref / PubMed / WoS / Elsevier Scopus
              / Springer Meta). Small JSON payloads.
    `fetch`:  OA lookup, Sci-Hub landing, Elsevier TDM / Springer OA JATS
              (non-PDF) and HTML fallbacks. Medium payloads.
    `pdf`:    Direct PDF downloads — keep read generous.
    """

    search_connect: float = 10.0
    search_read: float = 30.0
    fetch_connect: float = 15.0
    fetch_read: float = 60.0
    pdf_connect: float = 15.0
    pdf_read: float = 60.0


@dataclass(frozen=True)
class BrowserTimeoutPolicy:
    deadline_seconds: float = 120.0
    networkidle_timeout_seconds: float = 15.0
    poll_min_seconds: float = 0.5
    poll_max_seconds: float = 2.0


@dataclass(frozen=True)
class RuntimePolicy:
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeouts: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    browser: BrowserTimeoutPolicy = field(default_factory=BrowserTimeoutPolicy)


_DEFAULT_POLICY = RuntimePolicy()
_CURRENT: RuntimePolicy = _DEFAULT_POLICY


def install_runtime_defaults(config: GRaDOSConfig | None = None) -> RuntimePolicy:
    """Install runtime defaults from a validated `GRaDOSConfig`.

    Callers (MCP server boot, CLI entrypoint) invoke this once after loading
    config. Subsequent calls replace the active policy. Passing ``None`` resets
    to hardcoded defaults — useful in tests.
    """

    global _CURRENT
    if config is None:
        _CURRENT = _DEFAULT_POLICY
        return _CURRENT

    retry = RetryPolicy(
        max_attempts=int(config.retry_policy.max_attempts),
        max_wait=float(config.retry_policy.max_wait),
        respect_retry_after=bool(config.retry_policy.respect_retry_after),
    )
    timeouts = TimeoutPolicy(
        search_connect=float(config.search.connect_timeout),
        search_read=float(config.search.read_timeout),
        fetch_connect=float(config.extract.fetch_connect_timeout),
        fetch_read=float(config.extract.fetch_read_timeout),
        pdf_connect=float(config.extract.fetch_connect_timeout),
        pdf_read=float(config.extract.fetch_read_timeout),
    )
    browser_cfg = config.extract.headless_browser
    poll_min = float(browser_cfg.poll_min_seconds)
    poll_max = float(browser_cfg.poll_max_seconds)
    if poll_max < poll_min:
        logger.warning(
            "poll_max_seconds (%s) < poll_min_seconds (%s); clamping to poll_min",
            poll_max,
            poll_min,
        )
        poll_max = poll_min
    browser = BrowserTimeoutPolicy(
        deadline_seconds=float(browser_cfg.deadline_seconds),
        networkidle_timeout_seconds=float(browser_cfg.networkidle_timeout),
        poll_min_seconds=poll_min,
        poll_max_seconds=poll_max,
    )
    _CURRENT = RuntimePolicy(retry=retry, timeouts=timeouts, browser=browser)
    return _CURRENT


def current_policy() -> RuntimePolicy:
    return _CURRENT


# ── Live timeout getters (call these at the request site) ───────────────────


def current_search_timeout() -> httpx.Timeout:
    t = _CURRENT.timeouts
    return httpx.Timeout(
        connect=t.search_connect,
        read=t.search_read,
        write=t.search_read,
        pool=t.search_read,
    )


def current_fetch_timeout() -> httpx.Timeout:
    t = _CURRENT.timeouts
    return httpx.Timeout(
        connect=t.fetch_connect,
        read=t.fetch_read,
        write=t.fetch_read,
        pool=t.fetch_read,
    )


def current_pdf_timeout() -> httpx.Timeout:
    t = _CURRENT.timeouts
    return httpx.Timeout(
        connect=t.pdf_connect,
        read=t.pdf_read,
        write=t.pdf_read,
        pool=t.pdf_read,
    )


def current_browser_networkidle_timeout_ms() -> int:
    return int(_CURRENT.browser.networkidle_timeout_seconds * 1000)


def current_browser_deadline_seconds() -> float:
    return _CURRENT.browser.deadline_seconds


def current_browser_poll_bounds() -> tuple[float, float]:
    return (_CURRENT.browser.poll_min_seconds, _CURRENT.browser.poll_max_seconds)


# Backward-compat alias used by older call sites. Read the *current* value via
# the module attribute so a late install_runtime_defaults call still updates
# anyone grabbing `PDF_DOWNLOAD_TIMEOUT`; but most call sites now use
# `current_pdf_timeout()` directly.
PDF_DOWNLOAD_TIMEOUT = current_pdf_timeout()


# ── Retry classification ────────────────────────────────────────────────────


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors that warrant a retry."""
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteError,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return False


def _parse_retry_after_seconds(value: str) -> float | None:
    """Parse a Retry-After header value (delta-seconds or HTTP-date)."""
    value = (value or "").strip()
    if not value:
        return None
    # delta-seconds (e.g. "2")
    try:
        seconds = float(value)
        if seconds >= 0:
            return seconds
    except ValueError:
        pass
    # HTTP-date
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    now = time.time()
    delta = target.timestamp() - now
    return max(0.0, delta) if delta is not None else None


def _parse_ratelimit_reset_seconds(value: str) -> float | None:
    """Parse X-RateLimit-Reset: epoch seconds or delta-seconds.

    Convention varies — Elsevier returns an absolute epoch, GitHub also epoch,
    some APIs return delta-seconds. We distinguish by magnitude: a value above
    10^9 is almost certainly an absolute epoch.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        raw = float(value)
    except ValueError:
        return None
    if raw <= 0:
        return 0.0
    if raw > 1_000_000_000:  # absolute epoch seconds
        return max(0.0, raw - time.time())
    return raw


class _HeaderAwareWait(wait_base):
    """Respect Retry-After / X-RateLimit-Reset before falling back to expo+jitter."""

    def __init__(self, max_wait: float) -> None:
        self._fallback = wait_exponential(multiplier=1, min=1, max=max_wait) + wait_random(0, 1)

    def __call__(self, retry_state: RetryCallState) -> float:  # type: ignore[override]
        outcome = retry_state.outcome
        if outcome is not None and outcome.failed:
            exc = outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError):
                headers = exc.response.headers
                hinted = _parse_retry_after_seconds(headers.get("Retry-After", ""))
                if hinted is None:
                    hinted = _parse_ratelimit_reset_seconds(headers.get("X-RateLimit-Reset", ""))
                if hinted is not None:
                    # Cap at 60s: a misbehaving server returning a huge reset
                    # should not block the caller indefinitely.
                    return max(0.0, min(hinted, 60.0))
        return self._fallback(retry_state)


def _build_async_retrying(policy: RetryPolicy | None = None) -> AsyncRetrying:
    p = policy or _CURRENT.retry
    wait_strategy: wait_base
    if p.respect_retry_after:
        wait_strategy = _HeaderAwareWait(p.max_wait)
    else:
        wait_strategy = wait_exponential(multiplier=1, min=1, max=p.max_wait) + wait_random(0, 1)
    return AsyncRetrying(
        stop=stop_after_attempt(p.max_attempts),
        wait=wait_strategy,
        retry=retry_if_exception(_is_retryable),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )


# ── Per-source rate limiting ────────────────────────────────────────────────
#
# Some APIs impose hard inter-request minimums. Crossref uses polite-pool
# (mailto header); PubMed E-utils requires 3 req/s (no key) or 10 req/s (with
# key); Web of Science allows 2 req/s. We enforce minimum spacing with a small
# async semaphore-style limiter keyed by source name. Shared across tasks in
# the same process, so concurrent PubMed calls still respect 334ms pacing.
#
# Limiter is configured once per-source. Pass the resolved min-interval from
# the call site (e.g. PubMed config depends on whether an API key is present).


class _AsyncMinIntervalLimiter:
    """Ensure consecutive calls are spaced by at least `min_interval` seconds."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, min_interval)
        self._lock = asyncio.Lock()
        self._last: float = 0.0

    async def throttle(self) -> None:
        if self._min_interval <= 0.0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            wait_seconds = self._min_interval - elapsed
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._last = time.monotonic()

    def set_min_interval(self, value: float) -> None:
        self._min_interval = max(0.0, value)


_LIMITERS: dict[str, _AsyncMinIntervalLimiter] = {}


def get_rate_limiter(name: str, min_interval: float = 0.0) -> _AsyncMinIntervalLimiter:
    """Return the shared limiter for `name`, creating/updating it in place.

    Pass a fresh `min_interval` each call; callers typically derive it from
    runtime state (e.g. "PubMed has an API key → 100ms" vs. "no key → 334ms").
    """
    limiter = _LIMITERS.get(name)
    if limiter is None:
        limiter = _AsyncMinIntervalLimiter(min_interval)
        _LIMITERS[name] = limiter
    else:
        limiter.set_min_interval(min_interval)
    return limiter


async def throttle_source(name: str, min_interval: float) -> None:
    """Shorthand: throttle the named source to `min_interval` seconds."""
    await get_rate_limiter(name, min_interval).throttle()


# ── Per-source interval defaults (ADR-008) ─────────────────────────────────
#
# Helpers for callers. Callers should pass the right interval because it often
# depends on local state (PubMed API key presence). These constants document
# the upstream-facing minimums.

PUBMED_MIN_INTERVAL_NO_KEY = 0.334  # 3 req/s
PUBMED_MIN_INTERVAL_WITH_KEY = 0.100  # 10 req/s
WOS_MIN_INTERVAL = 0.500  # 2 req/s


def pubmed_min_interval(has_api_key: bool) -> float:
    return PUBMED_MIN_INTERVAL_WITH_KEY if has_api_key else PUBMED_MIN_INTERVAL_NO_KEY


def http_retry(policy: RetryPolicy | None = None):
    """Decorator factory for idempotent async HTTP calls.

    Resolves the retry policy at **call time**, so `install_runtime_defaults`
    on process start (or later) takes effect without re-decorating anything.
    If a caller passes `policy` explicitly, that policy is used for every
    invocation of the wrapped function (useful in tests).
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            retrying = _build_async_retrying(policy)
            async for attempt in retrying:
                with attempt:
                    return await fn(*args, **kwargs)
            # AsyncRetrying with reraise=True always raises on exhaustion, so
            # we never actually fall through here. Guard for type-checkers.
            raise RuntimeError("http_retry exhausted without reraise")

        return wrapper

    return decorator


def http_retrying(policy: RetryPolicy | None = None) -> AsyncRetrying:
    """Build an AsyncRetrying iterator for inline retry blocks.

    Usage::

        async for attempt in http_retrying():
            with attempt:
                resp = await client.get(url, timeout=current_search_timeout())
                resp.raise_for_status()
    """
    return _build_async_retrying(policy)
