"""ScienceDirect browser automation: View PDF flow, modal dismissal, candidate following."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from grados.publisher.elsevier import (
    extract_sciencedirect_pdf_candidates,
    parse_sciencedirect_intermediate_redirect,
)


def is_landing_page(url: str) -> bool:
    """Check if URL is a ScienceDirect article landing page (not a PDF flow page)."""
    return bool(
        re.search(r"sciencedirect\.com/science/article/pii/", url, re.IGNORECASE)
        and not re.search(r"/pdfft(?:[/?#]|$)", url, re.IGNORECASE)
    )


def is_pdf_flow_page(url: str) -> bool:
    """Check if URL is a ScienceDirect PDF delivery / intermediate page."""
    return bool(
        re.search(r"sciencedirect\.com/science/article/pii/.+/pdfft", url, re.IGNORECASE)
        or re.search(r"pdf\.sciencedirectassets\.com", url, re.IGNORECASE)
        or re.search(r"craft/capi/cfts/init", url, re.IGNORECASE)
    )


async def dismiss_interruptors(page: Any, action_state: dict[str, Any]) -> None:
    """Dismiss ScienceDirect modal overlays (cookie banners, sign-in prompts)."""
    if page.is_closed() or "sciencedirect.com" not in page.url:
        return
    if action_state.get("modal_dismissed"):
        return
    action_state["modal_dismissed"] = True

    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

    # Role-based close buttons
    for pattern in [r"close", r"dismiss", r"not now"]:
        try:
            locator = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE)).first
            if await locator.count() > 0:
                await locator.click(timeout=1500)
        except Exception:
            pass

    # CSS-based close buttons
    for sel in ['button[aria-label*="close" i]', '[role="dialog"] button']:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                await locator.click(timeout=1500)
        except Exception:
            pass


async def try_view_pdf_click(
    page: Any,
    context: Any,
    action_state: dict[str, Any],
    attempted_urls: set[str],
    track_page_fn: Callable[..., Any],
) -> None:
    """Click the 'View PDF' link on a ScienceDirect landing page.

    Watches for a popup tab; falls back to direct navigation if none appears.
    """
    if page.is_closed() or not is_landing_page(page.url):
        return
    if action_state.get("view_pdf_clicked"):
        return

    await dismiss_interruptors(page, action_state)

    role_loc = page.get_by_role("link", name=re.compile(r"View PDF", re.IGNORECASE)).first
    href_loc = page.locator('a[href*="/pdfft"]').first

    role_count = await role_loc.count()
    href_count = await href_loc.count()
    if role_count <= 0 and href_count <= 0:
        return

    action_state["view_pdf_clicked"] = True
    locator = role_loc if role_count > 0 else href_loc

    # Resolve absolute href
    href = None
    try:
        href = await locator.get_attribute("href")
    except Exception:
        pass
    absolute_href = urljoin(page.url, href) if href else None

    # Click and expect a popup tab
    popup = None
    try:
        async with context.expect_page(timeout=4000) as page_info:
            await locator.click(timeout=5000)
        popup = page_info.value
    except Exception:
        pass

    if popup:
        if absolute_href:
            attempted_urls.add(absolute_href)
        action_state["pdf_flow_delegated"] = True
        track_page_fn(popup)
        try:
            await popup.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        return

    # Fallback: open the href in a new tab manually
    if absolute_href and absolute_href not in attempted_urls:
        action_state["pdf_flow_delegated"] = True
        attempted_urls.add(absolute_href)
        new_page = await context.new_page()
        track_page_fn(new_page)
        try:
            await new_page.goto(absolute_href, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass


async def follow_candidates(
    page: Any,
    context: Any,
    action_state: dict[str, Any],
    attempted_urls: set[str],
    track_page_fn: Callable[..., Any],
    pdf_captured_fn: Callable[[], bool],
    inspect_challenge_fn: Callable[..., Any],
) -> None:
    """Extract PDF candidate URLs from a ScienceDirect landing page and follow them.

    Handles dropdown trigger, intermediate redirects, and challenge detection.
    """
    if page.is_closed() or not is_landing_page(page.url):
        return
    if action_state.get("pdf_flow_delegated"):
        return

    try:
        html = await page.content()
    except Exception:
        return

    candidates = extract_sciencedirect_pdf_candidates(html, page.url)

    # If no candidates, try clicking the dropdown trigger
    if not candidates and not action_state.get("dropdown_clicked"):
        try:
            dropdown = await page.query_selector("#pdfLink")
            if dropdown:
                action_state["dropdown_clicked"] = True
                await dropdown.click()
                await page.wait_for_timeout(750)
                html = await page.content()
                candidates = extract_sciencedirect_pdf_candidates(html, page.url)
        except Exception:
            pass

    for candidate in candidates:
        url = candidate["url"]
        if pdf_captured_fn() or url in attempted_urls:
            continue

        attempted_urls.add(url)
        new_page = await context.new_page()
        track_page_fn(new_page)
        try:
            await new_page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass

        if pdf_captured_fn():
            return

        # Inspect the latest non-root page
        pages = [p for p in context.pages if p != page and not p.is_closed()]
        if not pages:
            continue
        latest = pages[-1]

        await inspect_challenge_fn(latest)

        if is_pdf_flow_page(latest.url):
            continue

        # Check for intermediate redirect
        try:
            latest_html = await latest.content()
        except Exception:
            continue

        redirect_url = parse_sciencedirect_intermediate_redirect(latest_html, latest.url)
        if redirect_url and redirect_url not in attempted_urls:
            attempted_urls.add(redirect_url)
            redirect_page = await context.new_page()
            track_page_fn(redirect_page)
            try:
                await redirect_page.goto(redirect_url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass

            if pdf_captured_fn():
                return
