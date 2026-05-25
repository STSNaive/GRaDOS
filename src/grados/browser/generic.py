"""Browser-based PDF fetch: thin orchestration over shared runtime helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from grados._retry import (
    current_browser_deadline_seconds,
    current_browser_networkidle_timeout_ms,
    current_browser_poll_bounds,
)
from grados.browser.constants import PDF_BROWSER_CHROME_FLAGS
from grados.browser.fetch_runtime import (
    BrowserFetchState,
    BrowserListenerRegistry,
    navigate_to_doi_target,
    run_browser_polling_loop,
)
from grados.browser.fetch_runtime import (
    next_browser_poll_delay as _next_browser_poll_delay,
)
from grados.browser.fetch_runtime import (
    try_backfill_from_url as _try_backfill_from_url,
)
from grados.browser.manager import (
    close_secondary_pages,
    get_or_create_reusable_session,
    launch_browser_session,
    random_viewport,
    resolve_browser_executable,
)
from grados.browser.pdf.session_store import (
    create_pdf_browser_session,
    update_pdf_browser_session,
)
from grados.browser.session_runtime import (
    acquire_browser_runtime,
    finalize_browser_error,
    finalize_browser_no_capture,
    finalize_browser_success,
)
from grados.browser.strategies import BrowserPageStrategyContext, build_browser_page_strategies
from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.http_limits import DEFAULT_MAX_BROWSER_CAPTURE_BYTES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserFetchResult:
    pdf_buffer: bytes | None = None
    source: str = ""
    outcome: str = ""
    via: str = "browser"
    state: str = ""
    manual: bool = False
    host: str = ""
    resume: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    session_id: str = ""
    session_record_path: str = ""
    capture: dict[str, object] = field(default_factory=dict)


def next_browser_poll_delay(current: float, poll_min: float, poll_max: float) -> float:
    """Re-export poll-backoff helper for timeout tests and browser callers."""
    return _next_browser_poll_delay(current, poll_min, poll_max)


def _page_url(page: object) -> str:
    try:
        return str(getattr(page, "url", "") or "")
    except Exception:
        return ""


def _host_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()


def _browser_resume_payload(
    *,
    doi: str,
    url: str,
    host: str,
    paths: GRaDOSPaths,
    config: HeadlessBrowserConfig,
) -> dict[str, str]:
    payload = {
        "kind": "browser_profile",
        "doi": doi,
        "url": url,
        "host": host,
        "action": "complete_publisher_verification_then_retry",
    }
    if config.use_persistent_profile:
        payload["profile_dir"] = str(paths.browser_profile)
    return payload


async def fetch_with_browser(
    doi: str,
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
    resume: dict[str, str] | None = None,
    target_url: str = "",
    max_capture_bytes: int = DEFAULT_MAX_BROWSER_CAPTURE_BYTES,
) -> BrowserFetchResult:
    """Fetch a paper PDF using browser automation."""
    runtime = None
    listeners = None
    state: BrowserFetchState | None = None
    start_url = (resume or {}).get("url", "") or target_url or f"https://doi.org/{doi}"
    session_record = create_pdf_browser_session(
        paths.browser_pdf_sessions,
        doi=doi,
        target_url=start_url,
        resume=resume,
    )

    def update_session(
        *,
        status: str,
        outcome: str,
        source: str = "",
        final_url: str = "",
        host: str = "",
        manual: bool = False,
        warnings: list[str] | None = None,
    ) -> None:
        update_pdf_browser_session(
            session_record,
            status=status,
            outcome=outcome,
            source=source,
            browser_label=getattr(runtime, "browser_label", "") if runtime is not None else "",
            browser_source=getattr(runtime, "browser_source", "") if runtime is not None else "",
            profile_dir=getattr(runtime, "profile_dir", "") if runtime is not None else "",
            final_url=final_url,
            host=host,
            manual=manual,
            capture=state.capture_payload() if state is not None else {},
            warnings=list(warnings or []),
            events=list(state.events) if state is not None else [],
        )

    try:
        runtime = await acquire_browser_runtime(
            config,
            paths,
            resolve_browser_executable=resolve_browser_executable,
            random_viewport=random_viewport,
            get_or_create_reusable_session=get_or_create_reusable_session,
            launch_browser_session=launch_browser_session,
            close_secondary_pages=close_secondary_pages,
            purpose="pdf_acquisition",
            session_id=session_record.session_id,
            extra_args=PDF_BROWSER_CHROME_FLAGS,
        )
        if runtime is None:
            warnings = ["No compatible browser executable found. Run 'grados setup'."]
            update_session(status="error", outcome="no_browser", source="Browser", warnings=warnings)
            return BrowserFetchResult(
                pdf_buffer=None,
                source="Browser",
                outcome="no_browser",
                state="nobrowser",
                warnings=warnings,
                session_id=session_record.session_id,
                session_record_path=session_record.record_path,
            )

        state = BrowserFetchState(max_capture_bytes=max_capture_bytes)
        state.record_event(
            "session_start",
            url=start_url,
            details={
                "doi": doi,
                "target_url": target_url,
                "resume": bool(resume),
                "browser_label": runtime.browser_label,
            },
        )
        listeners = BrowserListenerRegistry(runtime.context, state)
        listeners.register(runtime.root_page)

        await navigate_to_doi_target(
            runtime.root_page,
            doi=doi,
            target_url=(resume or {}).get("url", "") or target_url,
            state=state,
            networkidle_timeout_ms=current_browser_networkidle_timeout_ms(),
            logger=logger,
        )

        poll_min, poll_max = current_browser_poll_bounds()
        await run_browser_polling_loop(
            context=runtime.context,
            state=state,
            listeners=listeners,
            page_strategies=build_browser_page_strategies(),
            deadline_seconds=current_browser_deadline_seconds(),
            poll_min=poll_min,
            poll_max=poll_max,
            strategy_context_factory=BrowserPageStrategyContext,
            backfill_from_url=_try_backfill_from_url,
        )

        if state.pdf_captured():
            final_url = state.final_url or state.capture_url or _page_url(runtime.root_page)
            if runtime.retain and config.close_pdf_page_after_capture:
                protected = set()
                if not runtime.job_page_owned:
                    protected.add(runtime.session.root_page)
                await listeners.close_tracked_pages(except_pages=protected)
            await finalize_browser_success(
                runtime,
                close_secondary_pages=close_secondary_pages,
                close_pdf_page_after_capture=False,
            )
            source = f"Browser ({runtime.browser_label})"
            update_session(
                status="ok",
                outcome="pdf_obtained",
                source=source,
                final_url=final_url,
                host=_host_from_url(final_url),
                warnings=state.warnings,
            )
            return BrowserFetchResult(
                pdf_buffer=state.pdf_buffer,
                source=source,
                outcome="pdf_obtained",
                state="ok",
                warnings=state.warnings,
                session_id=session_record.session_id,
                session_record_path=session_record.record_path,
                capture=state.capture_payload(),
            )

        outcome = "publisher_challenge" if state.challenge_seen else "timed_out"
        final_url = state.final_url or _page_url(runtime.root_page)
        host = _host_from_url(final_url)
        await finalize_browser_no_capture(runtime, keep_job_page=state.challenge_seen)
        resume = (
            _browser_resume_payload(
                doi=doi,
                url=final_url,
                host=host,
                paths=paths,
                config=config,
            )
            if state.challenge_seen
            else {}
        )
        warnings = state.warnings + [f"Browser automation: {outcome}"]
        update_session(
            status="manual" if state.challenge_seen else "timeout",
            outcome=outcome,
            source=f"Browser ({runtime.browser_label})",
            final_url=final_url,
            host=host,
            manual=state.challenge_seen,
            warnings=warnings,
        )
        return BrowserFetchResult(
            pdf_buffer=None,
            source=f"Browser ({runtime.browser_label})",
            outcome=outcome,
            state="challenge" if state.challenge_seen else "timeout",
            manual=state.challenge_seen,
            host=host,
            resume=resume,
            warnings=warnings,
            session_id=session_record.session_id,
            session_record_path=session_record.record_path,
            capture=state.capture_payload(),
        )
    except Exception as exc:
        if runtime is not None:
            await finalize_browser_error(runtime)
        source = "Browser"
        if runtime is not None:
            source = f"Browser ({runtime.browser_label})"
        warnings = [str(exc)]
        final_url = _page_url(runtime.root_page) if runtime is not None else ""
        update_session(
            status="error",
            outcome="error",
            source=source,
            final_url=final_url,
            host=_host_from_url(final_url),
            warnings=warnings,
        )
        return BrowserFetchResult(
            pdf_buffer=None,
            source=source,
            outcome="error",
            state="error",
            warnings=warnings,
            session_id=session_record.session_id,
            session_record_path=session_record.record_path,
            capture=state.capture_payload() if state is not None else {},
        )
    finally:
        if listeners is not None:
            listeners.detach()
