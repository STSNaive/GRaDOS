"""Generic non-ScienceDirect browser actions for PDF discovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

PDF_LINK_SELECTOR = 'a[href*="pdf" i], a[title*="PDF" i], a[aria-label*="PDF" i], a[class*="pdf" i]'


def _is_strong_pdf_href(url: str) -> bool:
    lowered = url.lower()
    return (
        ".pdf" in lowered
        or "/pdf" in lowered
        or "/pdfft" in lowered
        or "pdfdirect" in lowered
        or "/content/pdf/" in lowered
    )


def _pdf_href_score(url: str) -> int:
    lowered = url.lower()
    if lowered.endswith(".pdf") or ".pdf?" in lowered:
        return 0
    if "/content/pdf/" in lowered:
        return 1
    if "/pdfft" in lowered:
        return 2
    if "/pdf" in lowered or "pdfdirect" in lowered:
        return 3
    return 10


async def _collect_pdf_link_candidates(page: Any) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if hasattr(page, "evaluate"):
        try:
            raw_candidates = await page.evaluate(
                """selector => Array.from(document.querySelectorAll(selector)).slice(0, 12).map((node) => ({
                    href: node.getAttribute("href") || "",
                    text: node.textContent || "",
                    title: node.getAttribute("title") || "",
                    ariaLabel: node.getAttribute("aria-label") || "",
                    className: node.getAttribute("class") || ""
                }))""",
                PDF_LINK_SELECTOR,
            )
            if isinstance(raw_candidates, list):
                for item in raw_candidates:
                    if not isinstance(item, dict):
                        continue
                    href = str(item.get("href") or "")
                    label = " ".join(
                        str(item.get(key) or "") for key in ("text", "title", "ariaLabel", "className")
                    )
                    if href or "pdf" in label.lower():
                        candidates.append({"href": href, "label": label})
        except Exception:
            pass

    if not candidates:
        try:
            link = await page.query_selector(PDF_LINK_SELECTOR)
        except Exception:
            link = None
        if link:
            href = ""
            try:
                href = str(await link.get_attribute("href") or "")
            except Exception:
                href = ""
            candidates.append({"href": href, "label": ""})

    page_url = str(getattr(page, "url", "") or "")
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for candidate in candidates:
        href = candidate.get("href", "")
        absolute_href = urljoin(page_url, href) if href else ""
        key = absolute_href or candidate.get("label", "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append({"href": absolute_href, "label": candidate.get("label", "")})
    return sorted(deduped, key=lambda item: _pdf_href_score(item.get("href", "")))


async def try_generic_pdf_click(
    page: Any,
    context: Any,
    action_state: dict[str, Any],
    attempted_urls: set[str],
    track_page: Callable[[Any], None],
    pdf_captured: Any,
    report_warning: Callable[[str], None],
    record_event: Callable[..., None] | None = None,
    mark_manual_attention: Callable[..., Any] | None = None,
) -> None:
    """Open generic PDF links on non-ScienceDirect pages."""
    if pdf_captured() or page.is_closed():
        return
    if "sciencedirect.com" in page.url:
        return
    if action_state.get("generic_pdf_attempted"):
        return

    candidates = await _collect_pdf_link_candidates(page)
    if not candidates:
        return

    action_state["generic_pdf_attempted"] = True

    new_page_fn = getattr(context, "new_page", None)
    if callable(new_page_fn):
        for candidate in candidates:
            href = candidate.get("href", "")
            if not href or not _is_strong_pdf_href(href) or href in attempted_urls:
                continue
            if record_event is not None:
                record_event(
                    "strategy_action",
                    url=page.url,
                    details={"strategy": "GenericPdfClick", "action": "direct_pdf_navigation", "href": href},
                )
            pdf_page = await new_page_fn()
            track_page(pdf_page)
            try:
                await pdf_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                if record_event is not None:
                    record_event(
                        "strategy_action_confirmed",
                        url=href,
                        details={
                            "strategy": "GenericPdfClick",
                            "confirmation": "direct_pdf_navigation",
                            "automated": True,
                        },
                    )
                return
            except Exception as exc:
                report_warning(f"Generic PDF direct navigation failed for {href}: {exc.__class__.__name__}: {exc}")
                if record_event is not None:
                    record_event(
                        "strategy_action_failed",
                        url=href,
                        details={"strategy": "GenericPdfClick", "error": f"{exc.__class__.__name__}: {exc}"},
                    )

    try:
        link = await page.query_selector(PDF_LINK_SELECTOR)
        if link:
            href = ""
            try:
                href = str(await link.get_attribute("href") or "")
            except Exception:
                href = ""
            if record_event is not None:
                record_event(
                    "strategy_action",
                    url=page.url,
                    details={"strategy": "GenericPdfClick", "action": "click_pdf_link", "href": href},
                )
            clicked = False
            popup = None
            expect_page = getattr(context, "expect_page", None)
            if callable(expect_page):
                try:
                    async with expect_page(timeout=3000) as page_info:
                        await link.click()
                        clicked = True
                    popup = await page_info.value
                except Exception:
                    if not clicked:
                        await link.click()
                        clicked = True
            else:
                await link.click()
                clicked = True
            if popup is not None:
                track_page(popup)
                try:
                    await popup.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass
            try:
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                # After a click we already changed page state; waiting for load is
                # opportunistic and should not suppress other browser paths.
                pass
            if record_event is not None:
                record_event(
                    "strategy_action_confirmed",
                    url=page.url,
                    details={"strategy": "GenericPdfClick", "confirmation": "click_dispatched", "automated": True},
                )
    except Exception as exc:
        report_warning(f"Generic PDF click failed on {page.url}: {exc.__class__.__name__}: {exc}")
        if record_event is not None:
            record_event(
                "strategy_action_failed",
                url=page.url,
                details={"strategy": "GenericPdfClick", "error": f"{exc.__class__.__name__}: {exc}"},
            )
        if mark_manual_attention is not None:
            await mark_manual_attention(
                page,
                "pdf_auto_action_failed",
                details={"strategy": "GenericPdfClick", "error": f"{exc.__class__.__name__}: {exc}"},
            )
