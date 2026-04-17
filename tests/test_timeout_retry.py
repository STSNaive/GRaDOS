"""Regression tests for ADR-008 timeout / retry / throttle behavior.

Covers TODO items:

- P0-1a: Browser networkidle 15s ceiling (logic-level, no real browser).
- P1-T3a: Search-layer retry on 503/ConnectError sequences.
- P1-T4a: Fetch / publisher retry on ConnectError.
- P1-T6a: Retry-After header honored + per-source rate limiter spacing.
- P1-T7a: Browser main-loop sleep sequence (0.5 → 1 → 2 cap).
"""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from grados._retry import (
    PUBMED_MIN_INTERVAL_NO_KEY,
    PUBMED_MIN_INTERVAL_WITH_KEY,
    RetryPolicy,
    _AsyncMinIntervalLimiter,
    _HeaderAwareWait,
    _parse_ratelimit_reset_seconds,
    _parse_retry_after_seconds,
    current_browser_networkidle_timeout_ms,
    current_browser_poll_bounds,
    current_pdf_timeout,
    current_search_timeout,
    http_retry,
    install_runtime_defaults,
    pubmed_min_interval,
    throttle_source,
)
from grados.browser.generic import next_browser_poll_delay
from grados.config import GRaDOSConfig

# ── helpers ──────────────────────────────────────────────────────────────


def _fast_policy(max_attempts: int = 3) -> RetryPolicy:
    """Retry policy with near-zero waits, for tests that exercise attempt counts."""

    return RetryPolicy(max_attempts=max_attempts, max_wait=0.0, respect_retry_after=False)


def _http_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    resp = httpx.Response(status, headers=headers or {})
    return httpx.HTTPStatusError("test", request=MagicMock(), response=resp)


# ── Header parsing ───────────────────────────────────────────────────────


def test_parse_retry_after_seconds_accepts_delta_seconds() -> None:
    assert _parse_retry_after_seconds("2") == 2.0
    assert _parse_retry_after_seconds("0.5") == 0.5
    assert _parse_retry_after_seconds("0") == 0.0


def test_parse_retry_after_seconds_rejects_garbage() -> None:
    assert _parse_retry_after_seconds("") is None
    assert _parse_retry_after_seconds("bogus") is None


def test_parse_ratelimit_reset_seconds_detects_epoch_vs_delta() -> None:
    # Small number → delta-seconds
    assert _parse_ratelimit_reset_seconds("5") == 5.0
    # Absolute epoch far in the future → positive delta, and it should be a
    # sizable gap since the epoch is well beyond now().
    future = time.time() + 3600
    assert _parse_ratelimit_reset_seconds(str(int(future))) > 3000


# ── T3a / T4a: retry on transient failures ───────────────────────────────


def test_http_retry_recovers_after_503_503_200() -> None:
    policy = _fast_policy(3)
    call_count = {"n": 0}

    @http_retry(policy=policy)
    async def flaky() -> str:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise _http_error(503)
        return "ok"

    assert asyncio.run(flaky()) == "ok"
    assert call_count["n"] == 3


def test_http_retry_recovers_after_connect_error() -> None:
    policy = _fast_policy(3)
    call_count = {"n": 0}

    @http_retry(policy=policy)
    async def flaky() -> int:
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.ConnectError("network down")
        return 42

    assert asyncio.run(flaky()) == 42
    assert call_count["n"] == 2


def test_http_retry_passes_404_immediately() -> None:
    policy = _fast_policy(5)
    call_count = {"n": 0}

    @http_retry(policy=policy)
    async def bad() -> None:
        call_count["n"] += 1
        raise _http_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(bad())
    assert call_count["n"] == 1


def test_http_retry_max_attempts_exhausts_eventually() -> None:
    policy = _fast_policy(2)
    call_count = {"n": 0}

    @http_retry(policy=policy)
    async def always_503() -> None:
        call_count["n"] += 1
        raise _http_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(always_503())
    assert call_count["n"] == 2


# ── T6a: Retry-After / X-RateLimit-Reset ─────────────────────────────────


def test_header_aware_wait_uses_retry_after() -> None:
    wait = _HeaderAwareWait(max_wait=8.0)
    exc = _http_error(429, {"Retry-After": "2"})

    state = MagicMock()
    outcome = MagicMock()
    outcome.failed = True
    outcome.exception.return_value = exc
    state.outcome = outcome

    delay = wait(state)
    assert delay == 2.0


def test_header_aware_wait_caps_retry_after_at_60s() -> None:
    wait = _HeaderAwareWait(max_wait=8.0)
    exc = _http_error(429, {"Retry-After": "3600"})

    state = MagicMock()
    outcome = MagicMock()
    outcome.failed = True
    outcome.exception.return_value = exc
    state.outcome = outcome

    delay = wait(state)
    assert delay == 60.0


def test_header_aware_wait_falls_back_when_no_header() -> None:
    wait = _HeaderAwareWait(max_wait=4.0)
    exc = _http_error(503)

    state = MagicMock()
    state.attempt_number = 1
    outcome = MagicMock()
    outcome.failed = True
    outcome.exception.return_value = exc
    state.outcome = outcome
    # Fallback uses wait_exponential + jitter; we just check bounds.
    delay = wait(state)
    assert 0.0 <= delay <= 5.0  # min=1, max=4, jitter[0,1]


def test_retry_after_honored_end_to_end() -> None:
    """Full loop: 2x 429+Retry-After:1 then success should wait ~2s total."""

    policy = RetryPolicy(max_attempts=3, max_wait=0.0, respect_retry_after=True)
    call_count = {"n": 0}

    @http_retry(policy=policy)
    async def flaky() -> str:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise _http_error(429, {"Retry-After": "1"})
        return "done"

    start = time.monotonic()
    assert asyncio.run(flaky()) == "done"
    elapsed = time.monotonic() - start
    assert call_count["n"] == 3
    # 2 waits of 1s each → ~2s, allow some slack.
    assert 1.8 <= elapsed < 3.0, f"expected ~2s, got {elapsed:.2f}"


# ── T6a: rate-limiter spacing ────────────────────────────────────────────


def test_async_min_interval_limiter_spaces_calls() -> None:
    async def main() -> float:
        limiter = _AsyncMinIntervalLimiter(min_interval=0.1)
        start = time.monotonic()
        for _ in range(5):
            await limiter.throttle()
        return time.monotonic() - start

    elapsed = asyncio.run(main())
    # 5 calls with 0.1s min-interval → at least 4 intervals = 0.4s.
    assert elapsed >= 0.4, f"expected >= 0.4s, got {elapsed:.3f}"


def test_throttle_source_respects_pubmed_no_key_interval() -> None:
    """5 PubMed calls without a key should take >= 4 * 334ms."""

    async def main() -> float:
        # Fresh shared limiter — pin interval to PubMed no-key spec.
        start = time.monotonic()
        for _ in range(5):
            await throttle_source("pubmed_test_no_key", PUBMED_MIN_INTERVAL_NO_KEY)
        return time.monotonic() - start

    elapsed = asyncio.run(main())
    # 4 spacers of 0.334s each ≈ 1.336s. Allow small jitter.
    assert elapsed >= 4 * PUBMED_MIN_INTERVAL_NO_KEY - 0.05, (
        f"expected >= {4 * PUBMED_MIN_INTERVAL_NO_KEY:.3f}s, got {elapsed:.3f}"
    )


def test_pubmed_min_interval_switches_on_api_key() -> None:
    assert pubmed_min_interval(has_api_key=False) == PUBMED_MIN_INTERVAL_NO_KEY
    assert pubmed_min_interval(has_api_key=True) == PUBMED_MIN_INTERVAL_WITH_KEY


# ── T7a: browser main-loop sleep sequence ────────────────────────────────


def test_next_browser_poll_delay_matches_0_5_1_2_sequence() -> None:
    sequence: list[float] = []
    current = 0.5
    for _ in range(6):
        sequence.append(current)
        current = next_browser_poll_delay(current, poll_min=0.5, poll_max=2.0)

    # First six ticks: 0.5 → 1.0 → 2.0 → 2.0 → 2.0 → 2.0
    assert sequence == [0.5, 1.0, 2.0, 2.0, 2.0, 2.0]


def test_next_browser_poll_delay_clamps_inverted_bounds() -> None:
    # Misconfig: max < min should stick at min.
    assert next_browser_poll_delay(0.5, poll_min=1.0, poll_max=0.1) == 1.0


# ── P0-1a: networkidle 15s ceiling reachable from runtime ────────────────


def test_networkidle_ceiling_is_15s_by_default() -> None:
    install_runtime_defaults(None)
    assert current_browser_networkidle_timeout_ms() == 15000


def test_runtime_policy_reflects_config_edits() -> None:
    cfg = GRaDOSConfig()
    cfg = cfg.model_copy(
        update={
            "search": cfg.search.model_copy(update={"connect_timeout": 5.0, "read_timeout": 20.0}),
            "extract": cfg.extract.model_copy(
                update={
                    "fetch_connect_timeout": 8.0,
                    "fetch_read_timeout": 90.0,
                    "headless_browser": cfg.extract.headless_browser.model_copy(
                        update={"networkidle_timeout": 7.5, "poll_min_seconds": 0.25, "poll_max_seconds": 1.0}
                    ),
                }
            ),
        }
    )
    install_runtime_defaults(cfg)
    try:
        assert current_search_timeout().connect == 5.0
        assert current_search_timeout().read == 20.0
        assert current_pdf_timeout().read == 90.0
        assert current_browser_networkidle_timeout_ms() == 7500
        assert current_browser_poll_bounds() == (0.25, 1.0)
    finally:
        install_runtime_defaults(None)


# ── P0-1a (logic-level): wait_for_load_state error propagation ───────────


class _FakePage:
    """Minimal page stub exercising the try/except around wait_for_load_state."""

    def __init__(self, *, raise_on_networkidle: bool) -> None:
        self.raise_on_networkidle = raise_on_networkidle
        self.calls: list[tuple[str, int]] = []

    async def wait_for_load_state(self, state: str, timeout: int = 0) -> None:
        self.calls.append((state, timeout))
        if state == "networkidle" and self.raise_on_networkidle:
            raise TimeoutError("SPA never went idle")


def test_networkidle_timeout_is_caught_and_does_not_crash_caller() -> None:
    """Mirrors the shape of the generic.py try/except path without a real browser."""

    page = _FakePage(raise_on_networkidle=True)
    networkidle_ms = current_browser_networkidle_timeout_ms()

    async def exercise() -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=networkidle_ms)
        except Exception:
            return

    asyncio.run(exercise())
    assert page.calls == [("networkidle", networkidle_ms)]


# ── T3a: search retry sequence via mocked client ─────────────────────────


class _ScriptedClient:
    """Mimics httpx.AsyncClient.get with a scripted response/exception sequence."""

    def __init__(self, scripted: list[Any]) -> None:
        # Each entry: either a callable returning (status, json_body) or an
        # Exception instance to raise.
        self._iter = iter(scripted)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        item = next(self._iter)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))


def test_crossref_request_retries_on_503_then_200() -> None:
    from grados.search.academic import _crossref_request

    install_runtime_defaults(
        GRaDOSConfig().model_copy(
            update={
                "retry_policy": GRaDOSConfig().retry_policy.model_copy(
                    update={"max_attempts": 3, "max_wait": 0.0}
                )
            }
        )
    )

    try:
        success_body = {"message": {"items": [], "next-cursor": None}}
        client = _ScriptedClient([(503, {}), (503, {}), (200, success_body)])

        async def run() -> dict[str, Any]:
            # _crossref_request calls raise_for_status() internally on 503s; the
            # retry wrapper catches these and moves on to the next scripted tuple.
            return await _crossref_request(client, {"query": "x"}, "test@example.com")

        result = asyncio.run(run())
        assert result == success_body
    finally:
        install_runtime_defaults(None)


def test_unpaywall_retry_recovers_from_connect_error() -> None:
    from grados.extract.fetch import _unpaywall_lookup

    install_runtime_defaults(
        GRaDOSConfig().model_copy(
            update={
                "retry_policy": GRaDOSConfig().retry_policy.model_copy(
                    update={"max_attempts": 3, "max_wait": 0.0}
                )
            }
        )
    )

    try:
        err = httpx.ConnectError("network down")
        client = _ScriptedClient([err, (200, {"oa_locations": []})])

        async def run() -> httpx.Response:
            return await _unpaywall_lookup(client, "10.1234/test", "test@example.com")

        resp = asyncio.run(run())
        assert resp.status_code == 200
    finally:
        install_runtime_defaults(None)


# Avoid unused import warnings for helpers we expose but exercise above.
_ = (itertools,)
