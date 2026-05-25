"""Browser page strategies used by the generic browser fetch loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from grados.browser.sciencedirect import follow_candidates as sd_follow_candidates
from grados.browser.sciencedirect import try_view_pdf_click as sd_try_view_pdf_click
from grados.browser.strategies.generic_pdf_click import try_generic_pdf_click


@dataclass(frozen=True)
class BrowserPageStrategyContext:
    page: Any
    context: Any
    action_state: dict[str, Any]
    attempted_urls: set[str]
    track_page: Callable[[Any], None]
    pdf_captured: Callable[[], bool]
    inspect_challenge: Callable[[Any], Awaitable[bool]]
    report_warning: Callable[[str], None]
    record_event: Callable[..., None] | None = None


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
        context.report_warning,
        context.record_event,
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
        context.report_warning,
        context.record_event,
    )


async def _run_generic_page_strategy(context: BrowserPageStrategyContext) -> None:
    await try_generic_pdf_click(
        context.page,
        context.context,
        context.action_state,
        context.track_page,
        context.pdf_captured,
        context.report_warning,
        context.record_event,
    )


BROWSER_PAGE_STRATEGY_REGISTRY: dict[str, BrowserPageStrategy] = {
    "ScienceDirect": cast(
        BrowserPageStrategy,
        _FunctionBrowserPageStrategy("ScienceDirect", _run_sciencedirect_page_strategy),
    ),
    "GenericPdfClick": cast(
        BrowserPageStrategy,
        _FunctionBrowserPageStrategy("GenericPdfClick", _run_generic_page_strategy),
    ),
}


def build_browser_page_strategies(order: list[str] | None = None) -> list[BrowserPageStrategy]:
    resolved_order = order or ["ScienceDirect", "GenericPdfClick"]
    return [BROWSER_PAGE_STRATEGY_REGISTRY[name] for name in resolved_order if name in BROWSER_PAGE_STRATEGY_REGISTRY]
