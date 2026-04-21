"""Shared browser session acquisition and teardown helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from grados.browser.manager import BrowserSession
from grados.config import GRaDOSPaths, HeadlessBrowserConfig

_BROWSER_LABELS = {"managed": "GRaDOS Chrome", "configured": "Chrome", "system": "Chrome"}


@dataclass(frozen=True)
class BrowserRuntime:
    browser_label: str
    retain: bool
    session: BrowserSession
    context: Any
    root_page: Any


async def acquire_browser_runtime(
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
    *,
    resolve_browser_executable: Callable[[HeadlessBrowserConfig, GRaDOSPaths], Any],
    random_viewport: Callable[[], dict[str, int]],
    get_or_create_reusable_session: Callable[..., Awaitable[BrowserSession]],
    launch_browser_session: Callable[..., Awaitable[BrowserSession]],
    close_secondary_pages: Callable[[Any, Any], Awaitable[None]],
) -> BrowserRuntime | None:
    """Resolve, open, and prepare the browser session for one fetch attempt."""
    resolution = resolve_browser_executable(config, paths)
    if not resolution:
        return None

    browser_label = _BROWSER_LABELS.get(resolution.source, resolution.browser)
    retain = config.reuse_interactive_window and config.keep_interactive_window_open
    viewport = random_viewport()

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

    runtime = BrowserRuntime(
        browser_label=browser_label,
        retain=retain,
        session=session,
        context=session.context,
        root_page=session.root_page,
    )

    if retain:
        await close_secondary_pages(runtime.context, runtime.root_page)
        await focus_root_page(runtime.root_page)

    return runtime


async def focus_root_page(root_page: Any) -> None:
    """Best-effort focus restoration for retained windows."""
    try:
        await root_page.bring_to_front()
    except Exception:
        # Window focus is cosmetic; browser capture can continue without it.
        pass


async def finalize_browser_success(
    runtime: BrowserRuntime,
    *,
    close_secondary_pages: Callable[[Any, Any], Awaitable[None]],
    close_pdf_page_after_capture: bool,
) -> None:
    """Tear down or refocus the browser after a successful PDF capture."""
    if runtime.retain and close_pdf_page_after_capture:
        await close_secondary_pages(runtime.context, runtime.root_page)
    if runtime.retain:
        await focus_root_page(runtime.root_page)
        return
    await runtime.session.cleanup()


async def finalize_browser_no_capture(runtime: BrowserRuntime) -> None:
    """Release ephemeral sessions after timed-out or challenge-only runs."""
    if runtime.retain:
        return
    await runtime.session.cleanup()


async def finalize_browser_error(runtime: BrowserRuntime) -> None:
    """Release ephemeral sessions after unexpected errors."""
    if runtime.retain:
        return
    try:
        await runtime.session.cleanup()
    except Exception:
        # Teardown failure should not replace the original browser error.
        pass
