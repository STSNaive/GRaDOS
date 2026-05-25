"""Shared runtime state and polling helpers for browser fetch flows."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from grados._retry import current_browser_pdf_backfill_timeout_ms
from grados.http_limits import (
    DEFAULT_MAX_BROWSER_CAPTURE_BYTES,
    SizeLimitError,
    ensure_byte_limit,
    ensure_bytes_within_limit,
    ensure_content_length_allowed,
)
from grados.publisher.common import classify_pdf_content, detect_bot_challenge

logger = logging.getLogger(__name__)


def is_pdf_like_browser_response(url: str, content_type: str = "", content_disposition: str = "") -> bool:
    lowered_url = url.lower()
    lowered_ct = content_type.lower()
    lowered_cd = content_disposition.lower()
    return (
        "application/pdf" in lowered_ct
        or ".pdf" in lowered_url
        or "/pdfft" in lowered_url
        or "pdfdirect" in lowered_url
        or "/content/pdf/" in lowered_url
        or ".pdf" in lowered_cd
        or "application/pdf" in lowered_cd
        or ("filename=" in lowered_cd and "pdf" in lowered_cd)
    )


def next_browser_poll_delay(current: float, poll_min: float, poll_max: float) -> float:
    """Compute the next main-loop sleep interval."""
    if poll_max < poll_min:
        return poll_min
    if current < poll_min:
        return poll_min
    return min(poll_max, current * 2)


class BrowserBackfill(Protocol):
    def __call__(
        self,
        page: Any,
        context: Any,
        attempted_urls: set[str],
        try_capture: Any,
        pdf_captured: Any,
        report_warning: Callable[[str], None],
        max_capture_bytes: int = DEFAULT_MAX_BROWSER_CAPTURE_BYTES,
        backfill_timeout_ms: int | None = None,
        record_event: Callable[..., None] | None = None,
    ) -> Awaitable[None]:
        ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BrowserFetchState:
    attempted_urls: set[str] = field(default_factory=set)
    action_states: dict[int, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pdf_buffer: bytes | None = None
    challenge_seen: bool = False
    challenge_reason: str = ""
    final_url: str = ""
    max_capture_bytes: int = DEFAULT_MAX_BROWSER_CAPTURE_BYTES
    events: list[dict[str, Any]] = field(default_factory=list)
    capture_source: str = ""
    capture_url: str = ""
    capture_content_type: str = ""
    capture_bytes: int = 0

    def pdf_captured(self) -> bool:
        return self.pdf_buffer is not None

    def record_event(self, name: str, *, url: str = "", details: dict[str, Any] | None = None) -> None:
        self.events.append(
            {
                "timestamp": _now_iso(),
                "name": name,
                "url": url,
                "details": dict(details or {}),
            }
        )

    def report_warning(self, message: str) -> None:
        normalized = re.sub(r"\s+", " ", message).strip()
        if normalized and normalized not in self.warnings:
            self.warnings.append(normalized)

    def try_capture(
        self,
        data: bytes,
        content_type: str = "",
        source_url: str = "",
        *,
        source_kind: str = "capture",
    ) -> bool:
        label = f"Browser PDF capture from {source_url}" if source_url else "Browser PDF capture"
        try:
            ensure_bytes_within_limit(data, max_bytes=self.max_capture_bytes, label=label)
        except SizeLimitError as exc:
            self.report_warning(str(exc))
            self.record_event(
                "pdf_capture_rejected",
                url=source_url,
                details={"source": source_kind, "reason": "size_limit", "bytes": len(data)},
            )
            return False
        check = classify_pdf_content(data, content_type)
        if check["is_pdf"]:
            self.pdf_buffer = data
            self.capture_source = source_kind
            self.capture_url = source_url
            self.capture_content_type = content_type
            self.capture_bytes = len(data)
            self.record_event(
                "pdf_capture_success",
                url=source_url,
                details={
                    "source": source_kind,
                    "content_type": content_type,
                    "bytes": len(data),
                },
            )
            return True
        self.record_event(
            "pdf_capture_rejected",
            url=source_url,
            details={
                "source": source_kind,
                "reason": check.get("reason", "not_pdf"),
                "content_type": content_type,
                "bytes": len(data),
            },
        )
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
            self.challenge_reason = "bot_or_verification_challenge"
            self.record_event(
                "publisher_challenge",
                url=url,
                details={"title": title[:200], "reason": self.challenge_reason},
            )
            return True
        return False

    def get_action_state(self, page: Any) -> dict[str, Any]:
        pid = id(page)
        if pid not in self.action_states:
            self.action_states[pid] = {}
        return self.action_states[pid]

    def capture_payload(self) -> dict[str, Any]:
        return {
            "source": self.capture_source,
            "url": self.capture_url,
            "content_type": self.capture_content_type,
            "bytes": self.capture_bytes,
        }


@dataclass
class BrowserListenerRegistry:
    context: Any
    state: BrowserFetchState
    tracked_pages: set[Any] = field(default_factory=set)
    cdp_pdf_candidates: dict[str, dict[str, str]] = field(default_factory=dict)
    cdp_sessions: dict[Any, Any] = field(default_factory=dict)
    cdp_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    context_page_listener_registered: bool = False
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
        if not is_pdf_like_browser_response(url, ct, cd):
            return
        self.state.record_event(
            "response_pdf_candidate",
            url=url,
            details={"content_type": ct, "content_disposition": cd},
        )
        try:
            ensure_content_length_allowed(
                headers,
                max_bytes=self.state.max_capture_bytes,
                label=f"Browser PDF response from {url}",
            )
            body = await response.body()
            self.state.try_capture(body, ct, url, source_kind="response")
        except SizeLimitError as exc:
            self.state.report_warning(str(exc))
            self.state.record_event(
                "response_pdf_rejected",
                url=url,
                details={"reason": "size_limit", "message": str(exc)},
            )
        except Exception as exc:
            # Response hooks are opportunistic sniffers and must not stop the
            # main polling loop if the browser rejects body access.
            logger.debug("Browser response body capture failed for %s: %s", url, exc)

    async def _on_download(self, download: Any) -> None:
        if self.state.pdf_captured():
            return
        try:
            failure = await download.failure()
            if failure:
                return
            dl_path = await download.path()
            if dl_path:
                path = Path(dl_path)
                self.state.record_event(
                    "download_candidate",
                    url=download.url,
                    details={"path": str(path), "bytes": path.stat().st_size},
                )
                ensure_byte_limit(
                    path.stat().st_size,
                    max_bytes=self.state.max_capture_bytes,
                    label=f"Browser PDF download from {download.url}",
                )
                body = path.read_bytes()
                self.state.try_capture(body, "application/pdf", download.url, source_kind="download")
        except SizeLimitError as exc:
            self.state.report_warning(str(exc))
            self.state.record_event(
                "download_rejected",
                url=getattr(download, "url", ""),
                details={"reason": "size_limit", "message": str(exc)},
            )
        except Exception as exc:
            # Download persistence is best-effort; a broken temp file should
            # not abort other capture paths.
            logger.debug("Browser download capture failed for %s: %s", getattr(download, "url", ""), exc)

    def _on_new_page(self, page: Any) -> None:
        self.track_page(page)

    def _schedule_cdp_task(self, coro: Coroutine[Any, Any, None]) -> None:
        try:
            task: asyncio.Task[None] = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self.cdp_tasks.add(task)
        task.add_done_callback(self.cdp_tasks.discard)

    def track_page(self, page: Any) -> None:
        if page in self.tracked_pages:
            return
        self.tracked_pages.add(page)
        page.on("response", self.on_response)
        page.on("download", self.on_download)
        self._schedule_cdp_task(self._attach_cdp_response_capture(page))

    async def _attach_cdp_response_capture(self, page: Any) -> None:
        if not hasattr(self.context, "new_cdp_session"):
            return
        try:
            cdp = await self.context.new_cdp_session(page)
            await cdp.send("Network.enable")
        except Exception as exc:
            logger.debug("CDP response capture attach failed: %s", exc)
            return
        self.cdp_sessions[page] = cdp

        def on_response_received(event: dict[str, Any]) -> None:
            response = event.get("response") if isinstance(event, dict) else {}
            if not isinstance(response, dict):
                return
            raw_headers = response.get("headers")
            headers: dict[str, Any] = raw_headers if isinstance(raw_headers, dict) else {}
            ct = str(headers.get("content-type") or headers.get("Content-Type") or response.get("mimeType") or "")
            cd = str(headers.get("content-disposition") or headers.get("Content-Disposition") or "")
            url = str(response.get("url") or "")
            if not is_pdf_like_browser_response(url, ct, cd):
                return
            request_id = str(event.get("requestId") or "")
            if not request_id:
                return
            key = self._cdp_request_key(cdp, request_id)
            self.cdp_pdf_candidates[key] = {
                "url": url,
                "content_type": ct,
                "content_disposition": cd,
            }
            self.state.record_event(
                "cdp_response_pdf_candidate",
                url=url,
                details={"content_type": ct, "content_disposition": cd},
            )

        def on_loading_finished(event: dict[str, Any]) -> None:
            self._schedule_cdp_task(self._capture_cdp_response_body(cdp, event))

        try:
            cdp.on("Network.responseReceived", on_response_received)
            cdp.on("Network.loadingFinished", on_loading_finished)
        except Exception as exc:
            logger.debug("CDP response capture listener install failed: %s", exc)

    def _cdp_request_key(self, cdp: Any, request_id: str) -> str:
        return f"{id(cdp)}:{request_id}"

    async def _capture_cdp_response_body(self, cdp: Any, event: dict[str, Any]) -> None:
        if self.state.pdf_captured():
            return
        request_id = str(event.get("requestId") or "")
        if not request_id:
            return
        candidate = self.cdp_pdf_candidates.pop(self._cdp_request_key(cdp, request_id), None)
        if not candidate:
            return
        url = candidate.get("url", "")
        ct = candidate.get("content_type", "")
        try:
            payload = await cdp.send("Network.getResponseBody", {"requestId": request_id})
            raw_body = payload.get("body", "") if isinstance(payload, dict) else ""
            if isinstance(payload, dict) and bool(payload.get("base64Encoded")):
                body = base64.b64decode(str(raw_body), validate=False)
            else:
                body = str(raw_body).encode("utf-8", errors="replace")
            self.state.try_capture(body, ct, url, source_kind="cdp_response_body")
        except Exception as exc:
            logger.debug("CDP response body capture failed for %s: %s", url, exc)
            self.state.record_event(
                "cdp_response_body_rejected",
                url=url,
                details={"reason": "body_unavailable", "message": f"{exc.__class__.__name__}: {exc}"},
            )

    def register(self, root_page: Any, *, track_context_pages: bool = False) -> None:
        if track_context_pages:
            self.context.on("page", self.on_new_page)
            self.context_page_listener_registered = True
        self.track_page(root_page)

    def detach(self) -> None:
        for task in list(self.cdp_tasks):
            if not task.done():
                task.cancel()
        for page in list(self.tracked_pages):
            try:
                page.remove_listener("response", self.on_response)
                page.remove_listener("download", self.on_download)
            except Exception:
                # Listener cleanup runs after capture is over; keep teardown
                # best-effort so one bad page does not hide the final outcome.
                pass
        if self.context_page_listener_registered:
            try:
                self.context.remove_listener("page", self.on_new_page)
            except Exception:
                # Same rationale as page listener cleanup above.
                pass
            self.context_page_listener_registered = False

    async def close_tracked_pages(self, *, except_pages: set[Any] | None = None) -> None:
        protected = except_pages or set()
        for page in list(self.tracked_pages):
            if page in protected:
                continue
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass


async def navigate_to_doi_target(
    root_page: Any,
    *,
    doi: str,
    target_url: str = "",
    state: BrowserFetchState,
    networkidle_timeout_ms: int,
    logger: logging.Logger,
) -> None:
    """Navigate to the DOI landing page before the main polling loop."""
    destination = target_url or f"https://doi.org/{doi}"
    try:
        await root_page.goto(destination, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        state.report_warning(f"Browser goto failed for {destination}: {exc.__class__.__name__}: {exc}")

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
    backfill_from_url: BrowserBackfill,
) -> None:
    """Poll tracked pages until a PDF is captured or the deadline expires."""
    deadline = time.monotonic() + deadline_seconds
    current_sleep = poll_min

    while time.monotonic() < deadline and not state.pdf_captured():
        saw_open_page = False
        saw_actionable_page = False
        for page in list(listeners.tracked_pages):
            if state.pdf_captured() or page.is_closed():
                continue

            saw_open_page = True
            blocked = await state.inspect_challenge(page)
            if blocked:
                continue

            saw_actionable_page = True
            await backfill_from_url(
                page,
                context,
                state.attempted_urls,
                state.try_capture,
                state.pdf_captured,
                state.report_warning,
                state.max_capture_bytes,
                record_event=state.record_event,
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
                record_event=state.record_event,
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
                state.max_capture_bytes,
                record_event=state.record_event,
            )

        if state.pdf_captured():
            break
        if saw_open_page and state.challenge_seen and not saw_actionable_page:
            state.record_event(
                "polling_stopped_for_manual_resume",
                url=state.final_url,
                details={"reason": state.challenge_reason or "publisher_challenge"},
            )
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
    max_capture_bytes: int = DEFAULT_MAX_BROWSER_CAPTURE_BYTES,
    backfill_timeout_ms: int | None = None,
    record_event: Callable[..., None] | None = None,
) -> None:
    """If the page URL looks like a direct PDF link, fetch it via context.request."""
    if pdf_captured() or page.is_closed():
        return
    url = page.url
    if not is_pdf_like_browser_response(url):
        return
    if url in attempted_urls:
        return

    attempted_urls.add(url)
    if record_event is not None:
        record_event("backfill_attempt", url=url)
    try:
        response = await context.request.get(
            url,
            timeout=backfill_timeout_ms or current_browser_pdf_backfill_timeout_ms(),
        )
        headers = response.headers
        ensure_content_length_allowed(
            headers,
            max_bytes=max_capture_bytes,
            label=f"Browser PDF backfill from {url}",
        )
        ct = str(headers.get("content-type", ""))
        body = await response.body()
        try:
            captured = bool(try_capture(body, ct, url, source_kind="backfill"))
        except TypeError:
            captured = bool(try_capture(body, ct, url))
        if record_event is not None:
            record_event(
                "backfill_success" if captured else "backfill_rejected",
                url=url,
                details={"content_type": ct, "bytes": len(body)},
            )
    except SizeLimitError as exc:
        report_warning(str(exc))
        if record_event is not None:
            record_event("backfill_rejected", url=url, details={"reason": "size_limit", "message": str(exc)})
    except Exception as exc:
        report_warning(f"Direct PDF backfill failed for {url}: {exc.__class__.__name__}: {exc}")
        if record_event is not None:
            record_event(
                "backfill_error",
                url=url,
                details={"error": f"{exc.__class__.__name__}: {exc}"},
            )
