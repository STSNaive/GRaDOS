"""Browser-based PDF fetch: main orchestration loop, generic publisher flow."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

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
    try_view_pdf_click as sd_try_view_pdf_click,
)
from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.publisher.common import classify_pdf_content, detect_bot_challenge

_BROWSER_LABELS = {"managed": "GRaDOS Chrome", "configured": "Chrome", "system": "Chrome"}


async def fetch_with_browser(
    doi: str,
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
) -> dict[str, Any]:
    """Fetch a paper PDF using browser automation.

    Returns dict: pdf_buffer (bytes|None), source (str), outcome (str), warnings (list[str]).
    """
    resolution = resolve_browser_executable(config, paths)
    if not resolution:
        return {
            "pdf_buffer": None,
            "source": "Headless Browser",
            "outcome": "no_browser",
            "warnings": ["No compatible browser executable found. Run 'grados setup --with browser'."],
        }

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
        action_states: dict[int, dict] = {}
        pdf_buffer: bytearray | None = None  # mutable container for closure
        final_url = ""
        challenge_seen = False

        # Wrap pdf_buffer in a list so closures can mutate it
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

        def get_action_state(page: Any) -> dict:
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
        try:
            await root_page.wait_for_load_state("networkidle")
        except Exception:
            pass

        # ── Main polling loop (2-minute deadline) ──────────────────────────

        deadline = time.monotonic() + 120
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

                # ScienceDirect flow
                state = get_action_state(page)
                await sd_try_view_pdf_click(page, context, state, attempted_urls, _track_page)
                if pdf_captured():
                    break
                await sd_follow_candidates(
                    page, context, state, attempted_urls, _track_page,
                    pdf_captured, inspect_challenge,
                )
                if pdf_captured():
                    break

                # Generic publisher flow
                await _try_generic_pdf_click(page, state, pdf_captured)
                if pdf_captured():
                    break

                # Second backfill attempt
                await _try_backfill_from_url(page, context, attempted_urls, try_capture, pdf_captured)

            if challenge_seen and not challenge_prompt_shown:
                challenge_prompt_shown = True

            await asyncio.sleep(1)

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

            return {
                "pdf_buffer": _buf[0],
                "source": f"Headless Browser ({browser_label})",
                "outcome": "pdf_obtained",
                "warnings": [],
            }

        # No PDF captured
        if not retain:
            await session.cleanup()

        outcome = "publisher_challenge" if challenge_seen else "timed_out"
        return {
            "pdf_buffer": None,
            "source": f"Headless Browser ({browser_label})",
            "outcome": outcome,
            "warnings": [f"Browser automation: {outcome}"],
        }

    except Exception as e:
        if session and not retain:
            try:
                await session.cleanup()
            except Exception:
                pass
        return {
            "pdf_buffer": None,
            "source": f"Headless Browser ({browser_label})",
            "outcome": "error",
            "warnings": [str(e)],
        }


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
    action_state: dict,
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
