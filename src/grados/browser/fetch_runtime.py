"""Shared runtime state and polling helpers for browser fetch flows."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grados.publisher.common import classify_pdf_content, detect_bot_challenge


def next_browser_poll_delay(current: float, poll_min: float, poll_max: float) -> float:
    """Compute the next main-loop sleep interval."""
    if poll_max < poll_min:
        return poll_min
    if current < poll_min:
        return poll_min
    return min(poll_max, current * 2)


@dataclass
class BrowserFetchState:
    attempted_urls: set[str] = field(default_factory=set)
    action_states: dict[int, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pdf_buffer: bytes | None = None
    challenge_seen: bool = False
    final_url: str = ""

    def pdf_captured(self) -> bool:
        return self.pdf_buffer is not None

    def report_warning(self, message: str) -> None:
        normalized = re.sub(r"\s+", " ", message).strip()
        if normalized and normalized not in self.warnings:
            self.warnings.append(normalized)

    def try_capture(self, data: bytes, content_type: str = "", source_url: str = "") -> bool:
        _ = source_url
        check = classify_pdf_content(data, content_type)
        if check["is_pdf"]:
            self.pdf_buffer = data
            return True
        return False

    async def inspect_challenge(self, page: Any) -> bool:
        try:
            title = await page.title()
            html = await page.content()
            url = page.url
        except Exception:
            return False
        if detect_bot_challenge(title, html, url):
            self.challenge_seen = True
            self.final_url = url
            return True
        return False

    def get_action_state(self, page: Any) -> dict[str, Any]:
        pid = id(page)
        if pid not in self.action_states:
            self.action_states[pid] = {}
        return self.action_states[pid]


@dataclass
class BrowserListenerRegistry:
    context: Any
    state: BrowserFetchState
    tracked_pages: set[Any] = field(default_factory=set)
    on_response: Callable[[Any], Awaitable[None]] = field(init=False)
    on_download: Callable[[Any], Awaitable[None]] = field(init=False)
    on_new_page: Callable[[Any], None] = field(init=False)

    def __post_init__(self) -> None:
        self.on_response = self._on_response
        self.on_download = self._on_download
        self.on_new_page = self._on_new_page

    async def _on_response(self, response: Any) -> None:
        if self.state.pdf_captured():
            return
        headers = response.headers
        ct = str(headers.get("content-type", ""))
        cd = str(headers.get("content-disposition", ""))
        url = response.url
        looks_pdf = (
            "application/pdf" in ct
            or "/pdfft" in url.lower()
            or ".pdf" in url.lower()
            or ".pdf" in cd.lower()
        )
        if not looks_pdf:
            return
        try:
            body = await response.body()
            self.state.try_capture(body, ct, url)
        except Exception:
            # Response hooks are opportunistic sniffers and must not stop the
            # main polling loop if the browser rejects body access.
            pass

    async def _on_download(self, download: Any) -> None:
        if self.state.pdf_captured():
            return
        try:
            failure = await download.failure()
            if failure:
                return
            dl_path = await download.path()
            if dl_path:
                body = Path(dl_path).read_bytes()
                self.state.try_capture(body, "application/pdf", download.url)
        except Exception:
            # Download persistence is best-effort; a broken temp file should
            # not abort other capture paths.
            pass

    def _on_new_page(self, page: Any) -> None:
        self.track_page(page)

    def track_page(self, page: Any) -> None:
        if page in self.tracked_pages:
            return
        self.tracked_pages.add(page)
        page.on("response", self.on_response)
        page.on("download", self.on_download)

    def register(self, root_page: Any) -> None:
        self.context.on("page", self.on_new_page)
        self.track_page(root_page)

    def detach(self) -> None:
        for page in list(self.tracked_pages):
            try:
                page.remove_listener("response", self.on_response)
                page.remove_listener("download", self.on_download)
            except Exception:
                # Listener cleanup runs after capture is over; keep teardown
                # best-effort so one bad page does not hide the final outcome.
                pass
        try:
            self.context.remove_listener("page", self.on_new_page)
        except Exception:
            # Same rationale as page listener cleanup above.
            pass


async def navigate_to_doi_target(
    root_page: Any,
    *,
    doi: str,
    state: BrowserFetchState,
    networkidle_timeout_ms: int,
    logger: logging.Logger,
) -> None:
    """Navigate to the DOI landing page before the main polling loop."""
    try:
        await root_page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        state.report_warning(f"Browser goto failed for DOI {doi}: {exc.__class__.__name__}: {exc}")

    try:
        await root_page.wait_for_load_state("networkidle", timeout=networkidle_timeout_ms)
    except Exception:
        logger.debug(
            "networkidle ceiling (%dms) hit for DOI %s; continuing to main polling loop",
            networkidle_timeout_ms,
            doi,
        )


async def run_browser_polling_loop(
    *,
    context: Any,
    state: BrowserFetchState,
    listeners: BrowserListenerRegistry,
    page_strategies: list[Any],
    deadline_seconds: float,
    poll_min: float,
    poll_max: float,
    strategy_context_factory: Callable[..., Any],
    backfill_from_url: Callable[[Any, Any, set[str], Any, Any, Callable[[str], None]], Awaitable[None]],
) -> None:
    """Poll tracked pages until a PDF is captured or the deadline expires."""
    deadline = time.monotonic() + deadline_seconds
    current_sleep = poll_min

    while time.monotonic() < deadline and not state.pdf_captured():
        for page in list(listeners.tracked_pages):
            if state.pdf_captured() or page.is_closed():
                continue

            blocked = await state.inspect_challenge(page)
            if blocked:
                continue

            await backfill_from_url(
                page,
                context,
                state.attempted_urls,
                state.try_capture,
                state.pdf_captured,
                state.report_warning,
            )
            if state.pdf_captured():
                break

            strategy_context = strategy_context_factory(
                page=page,
                context=context,
                action_state=state.get_action_state(page),
                attempted_urls=state.attempted_urls,
                track_page=listeners.track_page,
                pdf_captured=state.pdf_captured,
                inspect_challenge=state.inspect_challenge,
                report_warning=state.report_warning,
            )
            for strategy in page_strategies:
                await strategy.run(strategy_context)
                if state.pdf_captured():
                    break
            if state.pdf_captured():
                break

            await backfill_from_url(
                page,
                context,
                state.attempted_urls,
                state.try_capture,
                state.pdf_captured,
                state.report_warning,
            )

        if state.pdf_captured():
            break

        await asyncio.sleep(current_sleep)
        current_sleep = next_browser_poll_delay(current_sleep, poll_min, poll_max)


async def try_backfill_from_url(
    page: Any,
    context: Any,
    attempted_urls: set[str],
    try_capture: Any,
    pdf_captured: Any,
    report_warning: Callable[[str], None],
) -> None:
    """If the page URL looks like a direct PDF link, fetch it via context.request."""
    if pdf_captured() or page.is_closed():
        return
    url = page.url
    if not re.search(r"\.pdf(?:$|[?#])", url, re.IGNORECASE):
        return
    if url in attempted_urls:
        return

    attempted_urls.add(url)
    try:
        response = await context.request.get(url, timeout=20000)
        headers = response.headers
        ct = str(headers.get("content-type", ""))
        body = await response.body()
        try_capture(body, ct, url)
    except Exception as exc:
        report_warning(f"Direct PDF backfill failed for {url}: {exc.__class__.__name__}: {exc}")
