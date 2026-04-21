"""Generic non-ScienceDirect browser actions for PDF discovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


async def try_generic_pdf_click(
    page: Any,
    action_state: dict[str, Any],
    pdf_captured: Any,
    report_warning: Callable[[str], None],
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
                # After a click we already changed page state; waiting for load is
                # opportunistic and should not suppress other browser paths.
                pass
    except Exception as exc:
        report_warning(f"Generic PDF click failed on {page.url}: {exc.__class__.__name__}: {exc}")
