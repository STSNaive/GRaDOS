"""Browser-based PDF fetch: main orchestration loop, generic publisher flow."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from grados._retry import (
    current_browser_deadline_seconds,
    current_browser_networkidle_timeout_ms,
    current_browser_poll_bounds,
)
from grados.browser.manager import (
    BrowserSession,
    close_secondary_pages,
    get_or_create_reusable_session,
    launch_browser_session,
    random_viewport,
    resolve_browser_executable,
)
from grados.browser.sciencedirect import (
    follow_candidates as sd_follow_candidates,
)
from grados.browser.sciencedirect import (
    try_view_pdf_click as sd_try_view_pdf_click,
)
from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.publisher.common import classify_pdf_content, detect_bot_challenge

_BROWSER_LABELS = {"managed": "GRaDOS Chrome", "configured": "Chrome", "system": "Chrome"}


def next_browser_poll_delay(current: float, poll_min: float, poll_max: float) -> float:
    """Compute the next main-loop sleep interval.

    Starts at `poll_min`, doubles each tick until it reaches `poll_max`, then
    stays at `poll_max`. If `poll_max < poll_min`, returns `poll_min` (caller
    guarantees bounds are sane via config validation).
    """
    if poll_max < poll_min:
        return poll_min
    if current < poll_min:
        return poll_min
    return min(poll_max, current * 2)


@dataclass(frozen=True)
class BrowserFetchResult:
    pdf_buffer: bytes | None = None
    source: str = ""
    outcome: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BrowserPageStrategyContext:
    page: Any
    context: Any
    action_state: dict[str, Any]
    attempted_urls: set[str]
    track_page: Callable[[Any], None]
    pdf_captured: Callable[[], bool]
    inspect_challenge: Callable[[Any], Awaitable[bool]]


class BrowserPageStrategy(Protocol):
    name: str

    async def run(self, context: BrowserPageStrategyContext) -> None:
        ...


@dataclass(frozen=True)
class _FunctionBrowserPageStrategy:
    name: str
    runner: Callable[[BrowserPageStrategyContext], Awaitable[None]]

    async def run(self, context: BrowserPageStrategyContext) -> None:
        await self.runner(context)


async def _run_sciencedirect_page_strategy(context: BrowserPageStrategyContext) -> None:
    await sd_try_view_pdf_click(
        context.page,
        context.context,
        context.action_state,
        context.attempted_urls,
        context.track_page,
    )
    if context.pdf_captured():
        return
    await sd_follow_candidates(
        context.page,
        context.context,
        context.action_state,
        context.attempted_urls,
        context.track_page,
        context.pdf_captured,
        context.inspect_challenge,
    )


async def _run_generic_page_strategy(context: BrowserPageStrategyContext) -> None:
    await _try_generic_pdf_click(context.page, context.action_state, context.pdf_captured)


BROWSER_PAGE_STRATEGY_REGISTRY: dict[str, BrowserPageStrategy] = {
    "ScienceDirect": _FunctionBrowserPageStrategy("ScienceDirect", _run_sciencedirect_page_strategy),
    "GenericPdfClick": _FunctionBrowserPageStrategy("GenericPdfClick", _run_generic_page_strategy),
}


def build_browser_page_strategies(order: list[str] | None = None) -> list[BrowserPageStrategy]:
    resolved_order = order or ["ScienceDirect", "GenericPdfClick"]
    return [BROWSER_PAGE_STRATEGY_REGISTRY[name] for name in resolved_order if name in BROWSER_PAGE_STRATEGY_REGISTRY]


async def fetch_with_browser(
    doi: str,
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
) -> BrowserFetchResult:
    """Fetch a paper PDF using browser automation."""
    resolution = resolve_browser_executable(config, paths)
    if not resolution:
        return BrowserFetchResult(
            pdf_buffer=None,
            source="Headless Browser",
            outcome="no_browser",
            warnings=["No compatible browser executable found. Run 'grados setup'."],
        )

    browser_label = _BROWSER_LABELS.get(resolution.source, resolution.browser)
    retain = config.reuse_interactive_window and config.keep_interactive_window_open
    viewport = random_viewport()
    session: BrowserSession | None = None

    try:
        # Launch or reuse browser
        if retain:
            session = await get_or_create_reusable_session(
                executable_path=resolution.executable_path,
                viewport=viewport,
                user_data_dir=resolution.profile_directory,
            )
        else:
            session = await launch_browser_session(
                executable_path=resolution.executable_path,
                viewport=viewport,
                user_data_dir=resolution.profile_directory,
                headless=False,
            )

        context = session.context
        root_page = session.root_page

        if retain:
            await close_secondary_pages(context, root_page)
            try:
                await root_page.bring_to_front()
            except Exception:
                pass

        # ── Shared mutable state ───────────────────────────────────────────
        tracked_pages: set[Any] = set()
        attempted_urls: set[str] = set()
        action_states: dict[int, dict[str, Any]] = {}
        final_url = ""
        challenge_seen = False

        # Wrap the captured PDF in a mutable container so closures can update it.
        _buf: list[bytes | None] = [None]

        def pdf_captured() -> bool:
            return _buf[0] is not None

        def try_capture(data: bytes, content_type: str = "", source_url: str = "") -> bool:
            check = classify_pdf_content(data, content_type)
            if check["is_pdf"]:
                _buf[0] = data
                return True
            return False

        async def inspect_challenge(page: Any) -> bool:
            nonlocal challenge_seen, final_url
            try:
                title = await page.title()
                html = await page.content()
                url = page.url
            except Exception:
                return False
            if detect_bot_challenge(title, html, url):
                challenge_seen = True
                final_url = url
                return True
            return False

        def get_action_state(page: Any) -> dict[str, Any]:
            pid = id(page)
            if pid not in action_states:
                action_states[pid] = {}
            return action_states[pid]

        # ── Event handlers ─────────────────────────────────────────────────

        async def _on_response(response: Any) -> None:
            if pdf_captured():
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
                try_capture(body, ct, url)
            except Exception:
                pass

        async def _on_download(download: Any) -> None:
            if pdf_captured():
                return
            try:
                failure = await download.failure()
                if failure:
                    return
                dl_path = await download.path()
                if dl_path:
                    body = Path(dl_path).read_bytes()
                    try_capture(body, "application/pdf", download.url)
            except Exception:
                pass

        def _on_new_page(page: Any) -> None:
            _track_page(page)

        def _track_page(page: Any) -> None:
            if page in tracked_pages:
                return
            tracked_pages.add(page)
            page.on("response", _on_response)
            page.on("download", _on_download)

        # Register listeners
        context.on("page", _on_new_page)
        _track_page(root_page)

        # ── Navigate ───────────────────────────────────────────────────────

        try:
            await root_page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        # Explicit ceiling for networkidle (default 15s): SPA background
        # polling (analytics, live updates) can keep the network from ever
        # settling. Falling through on timeout hands control to the main
        # polling loop instead of silently eating the deadline inside
        # wait_for_load_state. See ADR-008. Config: extract.headlessBrowser.
        # networkidleTimeout.
        networkidle_ms = current_browser_networkidle_timeout_ms()
        try:
            await root_page.wait_for_load_state("networkidle", timeout=networkidle_ms)
        except Exception:
            # Common enough on SPA-heavy publisher sites that an INFO-level log
            # would be noisy; DEBUG records the event for operators tailing logs.
            import logging as _logging  # local import: avoids top-level churn

            _logging.getLogger(__name__).debug(
                "networkidle ceiling (%dms) hit for DOI %s; continuing to main polling loop",
                networkidle_ms,
                doi,
            )

        # ── Main polling loop (deadline from config) ───────────────────────

        deadline_seconds = current_browser_deadline_seconds()
        deadline = time.monotonic() + deadline_seconds
        poll_min, poll_max = current_browser_poll_bounds()
        current_sleep = poll_min
        page_strategies = build_browser_page_strategies()
        challenge_prompt_shown = False

        while time.monotonic() < deadline and not pdf_captured():
            challenge_active_this_tick = False

            for page in list(tracked_pages):
                if pdf_captured() or page.is_closed():
                    continue

                blocked = await inspect_challenge(page)
                challenge_active_this_tick = challenge_active_this_tick or blocked
                if blocked:
                    continue

                # Try backfill from page URL
                await _try_backfill_from_url(page, context, attempted_urls, try_capture, pdf_captured)
                if pdf_captured():
                    break

                state = get_action_state(page)
                strategy_context = BrowserPageStrategyContext(
                    page=page,
                    context=context,
                    action_state=state,
                    attempted_urls=attempted_urls,
                    track_page=_track_page,
                    pdf_captured=pdf_captured,
                    inspect_challenge=inspect_challenge,
                )
                for strategy in page_strategies:
                    await strategy.run(strategy_context)
                    if pdf_captured():
                        break
                if pdf_captured():
                    break

                # Second backfill attempt
                await _try_backfill_from_url(page, context, attempted_urls, try_capture, pdf_captured)

            if challenge_seen and not challenge_prompt_shown:
                challenge_prompt_shown = True

            # Exponential backoff between idle polls: poll_min → 2× → poll_max.
            # Keeps CPU and event-loop cost low on slow publisher pages while
            # still reacting quickly to state changes on the first few ticks.
            # See ADR-008. Config: extract.headlessBrowser.poll{Min,Max}Seconds.
            await asyncio.sleep(current_sleep)
            current_sleep = next_browser_poll_delay(current_sleep, poll_min, poll_max)

        # ── Cleanup & result ───────────────────────────────────────────────

        # Remove event listeners
        for page in tracked_pages:
            try:
                page.remove_listener("response", _on_response)
                page.remove_listener("download", _on_download)
            except Exception:
                pass
        try:
            context.remove_listener("page", _on_new_page)
        except Exception:
            pass

        if pdf_captured():
            if retain and config.close_pdf_page_after_capture:
                await close_secondary_pages(context, root_page)
            if not retain:
                await session.cleanup()
            else:
                try:
                    await root_page.bring_to_front()
                except Exception:
                    pass

            return BrowserFetchResult(
                pdf_buffer=_buf[0],
                source=f"Headless Browser ({browser_label})",
                outcome="pdf_obtained",
                warnings=[],
            )

        # No PDF captured
        if not retain:
            await session.cleanup()

        outcome = "publisher_challenge" if challenge_seen else "timed_out"
        return BrowserFetchResult(
            pdf_buffer=None,
            source=f"Headless Browser ({browser_label})",
            outcome=outcome,
            warnings=[f"Browser automation: {outcome}"],
        )

    except Exception as e:
        if session and not retain:
            try:
                await session.cleanup()
            except Exception:
                pass
        return BrowserFetchResult(
            pdf_buffer=None,
            source=f"Headless Browser ({browser_label})",
            outcome="error",
            warnings=[str(e)],
        )


# ── Helpers ────────────────────────────────────────────────────────────────


async def _try_backfill_from_url(
    page: Any,
    context: Any,
    attempted_urls: set[str],
    try_capture: Any,
    pdf_captured: Any,
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
    except Exception:
        pass


async def _try_generic_pdf_click(
    page: Any,
    action_state: dict[str, Any],
    pdf_captured: Any,
) -> None:
    """Click generic PDF links on non-ScienceDirect pages."""
    if pdf_captured() or page.is_closed():
        return
    if "sciencedirect.com" in page.url:
        return
    if action_state.get("generic_clicked"):
        return

    try:
        link = await page.query_selector('a[href*="pdf"], a[title*="PDF"], a[class*="pdf"]')
        if link:
            action_state["generic_clicked"] = True
            await link.click()
            try:
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
    except Exception:
        pass
