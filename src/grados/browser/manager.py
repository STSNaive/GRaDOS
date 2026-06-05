"""Browser automation: executable resolution, session lifecycle, reuse."""

from __future__ import annotations

import json
import os
import platform
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grados.config import GRaDOSPaths, HeadlessBrowserConfig

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]


# ── Browser resolution ─────────────────────────────────────────────────────


@dataclass
class BrowserResolution:
    browser: str
    executable_path: str
    source: str  # "managed" | "configured" | "system"
    profile_directory: str | None = None


def _get_managed_chromium_suffixes() -> list[str]:
    """Platform-specific executable path suffixes inside chromium-* dirs."""
    sys_name = platform.system().lower()
    machine = platform.machine().lower()

    if sys_name == "darwin":
        suffixes = []
        if "arm" in machine or "aarch" in machine:
            suffixes.append(os.path.join(
                "chrome-mac-arm64", "Google Chrome for Testing.app",
                "Contents", "MacOS", "Google Chrome for Testing",
            ))
        suffixes.append(os.path.join(
            "chrome-mac-x64", "Google Chrome for Testing.app",
            "Contents", "MacOS", "Google Chrome for Testing",
        ))
        return suffixes

    if sys_name == "windows":
        return [os.path.join("chrome-win64", "chrome.exe")]

    return [
        os.path.join("chrome-linux64", "chrome"),
        os.path.join("chrome-linux", "chrome"),
    ]


def find_managed_chromium_executable(browser_dir: Path) -> str | None:
    """Scan managed browser directory for the newest chromium executable."""
    if not browser_dir.is_dir():
        return None

    revisions = sorted(
        [d for d in browser_dir.iterdir() if d.is_dir() and d.name.startswith("chromium-")],
        key=lambda d: d.name,
        reverse=True,
    )

    suffixes = _get_managed_chromium_suffixes()
    for rev_dir in revisions:
        for suffix in suffixes:
            candidate = rev_dir / suffix
            if candidate.exists():
                return str(candidate)

    return None


def _get_system_browser_candidates(browser: str) -> list[str]:
    """Known system browser paths by platform."""
    sys_name = platform.system().lower()

    if browser != "chrome":
        return []

    if sys_name == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    if sys_name == "windows":
        candidates = []
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_var, "")
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        return candidates
    # Linux
    return [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]


_PATH_NAMES = {
    "chrome": ["chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
}


def resolve_browser_executable(
    config: HeadlessBrowserConfig,
    paths: GRaDOSPaths,
) -> BrowserResolution | None:
    """Resolve browser executable with priority: managed → configured → system PATH."""
    profile_dir = str(paths.browser_profile) if config.use_persistent_profile else None

    # 1. Managed browser
    if config.prefer_managed_browser:
        managed = find_managed_chromium_executable(paths.browser_chromium)
        if managed:
            return BrowserResolution("chrome", managed, "managed", profile_dir)

    # 2. Configured executable
    if config.executable_path:
        exe = Path(config.executable_path).expanduser().resolve()
        if exe.exists():
            p_dir = profile_dir if config.browser == "chrome" else None
            return BrowserResolution(config.browser, str(exe), "configured", p_dir)

    # 3. System candidates
    for candidate in _get_system_browser_candidates(config.browser):
        if os.path.exists(candidate):
            p_dir = profile_dir if config.browser == "chrome" else None
            return BrowserResolution(config.browser, candidate, "system", p_dir)

    # 4. PATH lookup
    for name in _PATH_NAMES.get(config.browser, []):
        found = shutil.which(name)
        if found:
            p_dir = profile_dir if config.browser == "chrome" else None
            return BrowserResolution(config.browser, found, "system", p_dir)

    return None


def random_viewport() -> dict[str, int]:
    """Pick a random viewport for fingerprint variance."""
    return random.choice(VIEWPORTS)


# ── Session lifecycle ──────────────────────────────────────────────────────


@dataclass
class BrowserSession:
    playwright: Any
    browser: Any  # Browser instance or None (persistent context)
    context: Any  # BrowserContext
    root_page: Any  # Page
    cleanup: Any  # async callable
    executable_path: str = ""
    user_data_dir: str | None = None
    extra_args: list[str] = field(default_factory=list)
    session_id: str = ""
    download_dir: str = ""
    disable_pdf_viewer: bool = False
    profile_lock: Any | None = None


def _merge_preference_updates(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict):
            child = target.get(key)
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            _merge_preference_updates(child, value)
        else:
            target[key] = value


def configure_chrome_profile_preferences(
    user_data_dir: str | Path,
    *,
    download_dir: str | Path | None = None,
    disable_pdf_viewer: bool = False,
) -> Path:
    """Write the minimal Chrome profile prefs GRaDOS needs for browser PDF capture."""
    profile_dir = Path(user_data_dir) / "Default"
    profile_dir.mkdir(parents=True, exist_ok=True)
    preferences_path = profile_dir / "Preferences"
    preferences: dict[str, Any] = {}
    if preferences_path.is_file():
        try:
            loaded = json.loads(preferences_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                preferences = loaded
        except Exception:
            preferences = {}

    updates: dict[str, Any] = {}
    if download_dir is not None:
        resolved_download_dir = Path(download_dir)
        resolved_download_dir.mkdir(parents=True, exist_ok=True)
        updates["download"] = {
            "default_directory": str(resolved_download_dir),
            "directory_upgrade": True,
            "prompt_for_download": False,
        }
    updates["plugins"] = {"always_open_pdf_externally": bool(disable_pdf_viewer)}

    if updates:
        _merge_preference_updates(preferences, updates)
        tmp_path = preferences_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(preferences, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(preferences_path)
    return preferences_path


async def _set_download_behavior(context: Any, root_page: Any, download_dir: str | Path | None) -> None:
    if download_dir is None or not hasattr(context, "new_cdp_session"):
        return
    try:
        cdp = await context.new_cdp_session(root_page)
        await cdp.send(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(download_dir),
                "eventsEnabled": True,
            },
        )
    except Exception:
        return


async def launch_browser_session(
    executable_path: str,
    viewport: dict[str, int],
    user_data_dir: str | None = None,
    headless: bool = False,
    extra_args: list[str] | None = None,
    session_id: str = "",
    download_dir: str | Path | None = None,
    disable_pdf_viewer: bool = False,
) -> BrowserSession:
    """Launch a Patchright browser session (persistent or ephemeral)."""
    from patchright.async_api import async_playwright

    pw = await async_playwright().start()
    args = ["--disable-blink-features=AutomationControlled", "--new-window"]
    if extra_args:
        args = list(dict.fromkeys([*args, *extra_args]))
    download_path = Path(download_dir).expanduser() if download_dir is not None else None
    if download_path is not None:
        download_path.mkdir(parents=True, exist_ok=True)

    try:
        if user_data_dir:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            configure_chrome_profile_preferences(
                user_data_dir,
                download_dir=download_path,
                disable_pdf_viewer=disable_pdf_viewer,
            )
            context = await pw.chromium.launch_persistent_context(
                user_data_dir,
                executable_path=executable_path,
                headless=headless,
                args=args,
                viewport=viewport,  # type: ignore[arg-type]
                accept_downloads=True,
                downloads_path=download_path,
            )
            root_page = context.pages[0] if context.pages else await context.new_page()
            await _set_download_behavior(context, root_page, download_path)

            async def cleanup() -> None:
                await context.close()
                await pw.stop()

            return BrowserSession(
                pw,
                None,
                context,
                root_page,
                cleanup,
                executable_path=executable_path,
                user_data_dir=user_data_dir,
                extra_args=args,
                session_id=session_id,
                download_dir=str(download_path or ""),
                disable_pdf_viewer=disable_pdf_viewer,
            )
        else:
            browser = await pw.chromium.launch(
                executable_path=executable_path,
                headless=headless,
                args=args,
                downloads_path=download_path,
            )
            context = await browser.new_context(viewport=viewport, accept_downloads=True)  # type: ignore[arg-type]
            root_page = await context.new_page()
            await _set_download_behavior(context, root_page, download_path)

            async def cleanup() -> None:
                await context.close()
                await browser.close()
                await pw.stop()

            return BrowserSession(
                pw,
                browser,
                context,
                root_page,
                cleanup,
                executable_path=executable_path,
                user_data_dir=user_data_dir,
                extra_args=args,
                session_id=session_id,
                download_dir=str(download_path or ""),
                disable_pdf_viewer=disable_pdf_viewer,
            )
    except Exception:
        await pw.stop()
        raise


# ── Reusable session ───────────────────────────────────────────────────────

_reusable_session: BrowserSession | None = None


def _is_session_alive(session: BrowserSession) -> bool:
    try:
        if not session.context:
            return False
        if session.root_page and session.root_page.is_closed():
            return False
        if session.browser and not session.browser.is_connected():
            return False
        return True
    except Exception:
        return False


async def get_or_create_reusable_session(
    executable_path: str,
    viewport: dict[str, int],
    user_data_dir: str | None = None,
    extra_args: list[str] | None = None,
    session_id: str = "",
    download_dir: str | Path | None = None,
    disable_pdf_viewer: bool = False,
) -> BrowserSession:
    """Return a live reusable session or create a new one."""
    global _reusable_session
    normalized_download_dir = str(Path(download_dir).expanduser()) if download_dir is not None else ""

    if (
        _reusable_session
        and _is_session_alive(_reusable_session)
        and _reusable_session.executable_path == executable_path
        and _reusable_session.user_data_dir == user_data_dir
        and _reusable_session.download_dir == normalized_download_dir
        and _reusable_session.disable_pdf_viewer == disable_pdf_viewer
    ):
        s = _reusable_session
        if s.root_page.is_closed():
            s.root_page = s.context.pages[0] if s.context.pages else await s.context.new_page()
        return s

    carry_profile_lock = None
    if _reusable_session:
        carry_profile_lock = (
            _reusable_session.profile_lock
            if _reusable_session.user_data_dir == user_data_dir
            else None
        )
        try:
            await _reusable_session.cleanup()
        except Exception:
            pass
        try:
            if _reusable_session.profile_lock and carry_profile_lock is None:
                _reusable_session.profile_lock.release()
        except Exception:
            pass
        _reusable_session = None

    session = await launch_browser_session(
        executable_path=executable_path,
        viewport=viewport,
        user_data_dir=user_data_dir,
        headless=False,
        extra_args=extra_args,
        session_id=session_id,
        download_dir=download_dir,
        disable_pdf_viewer=disable_pdf_viewer,
    )
    if carry_profile_lock is not None:
        session.profile_lock = carry_profile_lock
    _reusable_session = session
    return session


async def close_secondary_pages(context: Any, root_page: Any) -> None:
    """Close all pages in context except the root page."""
    for page in context.pages:
        if page != root_page:
            try:
                await page.close()
            except Exception:
                pass
