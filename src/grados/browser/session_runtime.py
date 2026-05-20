"""Shared browser session acquisition and teardown helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grados.browser.lock import browser_profile_lock
from grados.browser.manager import BrowserSession
from grados.config import GRaDOSPaths, HeadlessBrowserConfig

_BROWSER_LABELS = {"managed": "GRaDOS Chrome", "configured": "Chrome", "system": "Chrome"}


@dataclass(frozen=True)
class BrowserRuntime:
    browser_label: str
    browser_source: str
    retain: bool
    session: BrowserSession
    context: Any
    root_page: Any
    session_id: str = ""
    profile_dir: str = ""
    profile_lock: Any | None = None


async def acquire_browser_runtime(
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
    *,
    resolve_browser_executable: Callable[[HeadlessBrowserConfig, GRaDOSPaths], Any],
    random_viewport: Callable[[], dict[str, int]],
    get_or_create_reusable_session: Callable[..., Awaitable[BrowserSession]],
    launch_browser_session: Callable[..., Awaitable[BrowserSession]],
    close_secondary_pages: Callable[[Any, Any], Awaitable[None]],
    purpose: str = "pdf_acquisition",
    session_id: str = "",
    extra_args: list[str] | None = None,
    lock_timeout_seconds: float = 5.0,
) -> BrowserRuntime | None:
    """Resolve, open, and prepare the browser session for one fetch attempt."""
    resolution = resolve_browser_executable(config, paths)
    if not resolution:
        return None

    browser_label = _BROWSER_LABELS.get(resolution.source, resolution.browser)
    retain = config.reuse_interactive_window and config.keep_interactive_window_open
    viewport = random_viewport()
    profile_lock = None

    if resolution.profile_directory:
        profile_lock = browser_profile_lock(
            Path(resolution.profile_directory),
            purpose=purpose,
            session_id=session_id,
            timeout_seconds=lock_timeout_seconds,
        )
        await profile_lock.__aenter__()

    try:
        if retain:
            session = await get_or_create_reusable_session(
                executable_path=resolution.executable_path,
                viewport=viewport,
                user_data_dir=resolution.profile_directory,
                extra_args=extra_args,
                session_id=session_id,
            )
        else:
            session = await launch_browser_session(
                executable_path=resolution.executable_path,
                viewport=viewport,
                user_data_dir=resolution.profile_directory,
                headless=False,
                extra_args=extra_args,
                session_id=session_id,
            )
    except Exception:
        if profile_lock is not None:
            profile_lock.release(release_file=not getattr(profile_lock, "reentrant", False))
        raise

    if profile_lock is not None and not getattr(profile_lock, "reentrant", False):
        session.profile_lock = profile_lock

    runtime = BrowserRuntime(
        browser_label=browser_label,
        browser_source=str(resolution.source),
        retain=retain,
        session=session,
        context=session.context,
        root_page=session.root_page,
        session_id=session_id or getattr(session, "session_id", ""),
        profile_dir=resolution.profile_directory or "",
        profile_lock=profile_lock,
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
        try:
            await focus_root_page(runtime.root_page)
        finally:
            release_runtime_lock(runtime, release_file=False)
        return
    try:
        await runtime.session.cleanup()
    finally:
        release_runtime_lock(runtime)


async def finalize_browser_no_capture(runtime: BrowserRuntime) -> None:
    """Release ephemeral sessions after timed-out or challenge-only runs."""
    if runtime.retain:
        release_runtime_lock(runtime, release_file=False)
        return
    try:
        await runtime.session.cleanup()
    finally:
        release_runtime_lock(runtime)


async def finalize_browser_error(runtime: BrowserRuntime) -> None:
    """Release ephemeral sessions after unexpected errors."""
    if runtime.retain:
        release_runtime_lock(runtime, release_file=False)
        return
    try:
        await runtime.session.cleanup()
    except Exception:
        # Teardown failure should not replace the original browser error.
        pass
    release_runtime_lock(runtime)


def release_runtime_lock(runtime: BrowserRuntime, *, release_file: bool = True) -> None:
    """Release profile lock state for a completed browser operation."""
    lock = runtime.profile_lock or getattr(runtime.session, "profile_lock", None)
    if lock is None:
        return
    try:
        lock.release(release_file=release_file)
    except Exception:
        pass
