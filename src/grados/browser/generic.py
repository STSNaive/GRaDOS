"""Browser-based PDF fetch: thin orchestration over shared runtime helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from grados._retry import (
    current_browser_deadline_seconds,
    current_browser_networkidle_timeout_ms,
    current_browser_poll_bounds,
)
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
from grados.browser.session_runtime import (
    acquire_browser_runtime,
    finalize_browser_error,
    finalize_browser_no_capture,
    finalize_browser_success,
)
from grados.browser.strategies import BrowserPageStrategyContext, build_browser_page_strategies
from grados.config import GRaDOSPaths, HeadlessBrowserConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserFetchResult:
    pdf_buffer: bytes | None = None
    source: str = ""
    outcome: str = ""
    warnings: list[str] = field(default_factory=list)


def next_browser_poll_delay(current: float, poll_min: float, poll_max: float) -> float:
    """Re-export poll-backoff helper for timeout tests and browser callers."""
    return _next_browser_poll_delay(current, poll_min, poll_max)


async def fetch_with_browser(
    doi: str,
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
) -> BrowserFetchResult:
    """Fetch a paper PDF using browser automation."""
    runtime = None
    listeners = None
    try:
        runtime = await acquire_browser_runtime(
            config,
            paths,
            resolve_browser_executable=resolve_browser_executable,
            random_viewport=random_viewport,
            get_or_create_reusable_session=get_or_create_reusable_session,
            launch_browser_session=launch_browser_session,
            close_secondary_pages=close_secondary_pages,
        )
        if runtime is None:
            return BrowserFetchResult(
                pdf_buffer=None,
                source="Headless Browser",
                outcome="no_browser",
                warnings=["No compatible browser executable found. Run 'grados setup'."],
            )

        state = BrowserFetchState()
        listeners = BrowserListenerRegistry(runtime.context, state)
        listeners.register(runtime.root_page)

        await navigate_to_doi_target(
            runtime.root_page,
            doi=doi,
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
            await finalize_browser_success(
                runtime,
                close_secondary_pages=close_secondary_pages,
                close_pdf_page_after_capture=config.close_pdf_page_after_capture,
            )
            return BrowserFetchResult(
                pdf_buffer=state.pdf_buffer,
                source=f"Headless Browser ({runtime.browser_label})",
                outcome="pdf_obtained",
                warnings=state.warnings,
            )

        await finalize_browser_no_capture(runtime)
        outcome = "publisher_challenge" if state.challenge_seen else "timed_out"
        return BrowserFetchResult(
            pdf_buffer=None,
            source=f"Headless Browser ({runtime.browser_label})",
            outcome=outcome,
            warnings=state.warnings + [f"Browser automation: {outcome}"],
        )
    except Exception as exc:
        if runtime is not None:
            await finalize_browser_error(runtime)
        source = "Headless Browser"
        if runtime is not None:
            source = f"Headless Browser ({runtime.browser_label})"
        return BrowserFetchResult(
            pdf_buffer=None,
            source=source,
            outcome="error",
            warnings=[str(exc)],
        )
    finally:
        if listeners is not None:
            listeners.detach()
