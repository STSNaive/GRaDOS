from __future__ import annotations

import asyncio
from pathlib import Path

from grados.browser.generic import build_browser_page_strategies
from grados.browser.manager import (
    VIEWPORTS,
    _get_managed_chromium_suffixes,
    random_viewport,
    resolve_browser_executable,
)
from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.publisher.common import classify_pdf_content, detect_bot_challenge


def test_resolve_browser_executable_prefers_managed_browser(tmp_path: Path) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    suffix = _get_managed_chromium_suffixes()[0]
    executable = paths.browser_chromium / "chromium-1234" / suffix
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("stub", encoding="utf-8")

    resolution = resolve_browser_executable(HeadlessBrowserConfig(), paths)

    assert resolution is not None
    assert resolution.source == "managed"
    assert resolution.executable_path == str(executable)
    assert resolution.profile_directory == str(paths.browser_profile)


def test_random_viewport_uses_known_fingerprint_set() -> None:
    assert random_viewport() in VIEWPORTS


def test_pdf_classification_and_bot_detection() -> None:
    pdf_data = b"%PDF-1.4\n%stub"
    html_data = b"<html><title>Just a moment</title><body>captcha</body></html>"

    assert classify_pdf_content(pdf_data, "application/pdf") == {"is_pdf": True, "reason": "ok"}
    assert classify_pdf_content(html_data, "text/html")["reason"] == "html_or_challenge_page"
    assert detect_bot_challenge("Just a moment...", "<html>captcha</html>", "https://example.com") is True


def test_browser_page_strategy_registry_preserves_order_and_filters_unknown_names() -> None:
    strategies = build_browser_page_strategies(["GenericPdfClick", "Missing", "ScienceDirect"])

    assert [strategy.name for strategy in strategies] == ["GenericPdfClick", "ScienceDirect"]


def test_fetch_with_browser_surfaces_sciencedirect_manual_fallback_warning(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.browser.generic as browser_generic

    class FakeLocator:
        def __init__(self, href: str, count: int = 1) -> None:
            self._href = href
            self._count = count
            self.first = self

        async def count(self) -> int:
            return self._count

        async def get_attribute(self, name: str) -> str | None:
            return self._href if name == "href" else None

        async def click(self, timeout: int | None = None) -> None:
            _ = timeout

    class FakePage:
        def __init__(
            self,
            url: str,
            *,
            href: str = "",
            goto_error: Exception | None = None,
            sticky_url: bool = False,
        ) -> None:
            self.url = url
            self._href = href
            self._goto_error = goto_error
            self._sticky_url = sticky_url
            self._listeners: dict[str, list[object]] = {}

        def is_closed(self) -> bool:
            return False

        async def bring_to_front(self) -> None:
            return None

        async def goto(self, url: str, **kwargs) -> None:  # noqa: ANN003
            _ = kwargs
            if self._goto_error is not None:
                raise self._goto_error
            if not self._sticky_url:
                self.url = url

        async def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
            _ = (state, timeout)

        async def title(self) -> str:
            return "Article"

        async def content(self) -> str:
            return "<html><body>science direct article</body></html>"

        def on(self, event: str, callback: object) -> None:
            self._listeners.setdefault(event, []).append(callback)

        def remove_listener(self, event: str, callback: object) -> None:
            listeners = self._listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

        def get_by_role(self, role: str, name=None):  # noqa: ANN001, ANN202
            _ = (role, name)
            return FakeLocator(self._href, count=1 if self._href else 0)

        def locator(self, selector: str):  # noqa: ANN202
            _ = selector
            return FakeLocator(self._href, count=1 if self._href else 0)

        async def query_selector(self, selector: str):  # noqa: ANN202
            _ = selector
            return None

    class FakeExpectPage:
        value = None

        async def __aenter__(self) -> FakeExpectPage:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            _ = (exc_type, exc, tb)
            raise TimeoutError("no popup")

    class FakeRequest:
        async def get(self, url: str, timeout: int | None = None):  # noqa: ANN201
            _ = (url, timeout)
            raise AssertionError("direct PDF backfill should not run in this scenario")

    class FakeContext:
        def __init__(self, root_page: FakePage) -> None:
            self.pages = [root_page]
            self.request = FakeRequest()

        def on(self, event: str, callback: object) -> None:
            _ = (event, callback)

        def remove_listener(self, event: str, callback: object) -> None:
            _ = (event, callback)

        def expect_page(self, timeout: int | None = None) -> FakeExpectPage:
            _ = timeout
            return FakeExpectPage()

        async def new_page(self) -> FakePage:
            page = FakePage(
                "about:blank",
                goto_error=RuntimeError("manual fallback failed"),
            )
            self.pages.append(page)
            return page

    class FakeSession:
        def __init__(self, root_page: FakePage) -> None:
            self.root_page = root_page
            self.context = FakeContext(root_page)

        async def cleanup(self) -> None:
            return None

    root_page = FakePage(
        "https://www.sciencedirect.com/science/article/pii/S1234567890",
        href="/science/article/pii/S1234567890/pdfft",
        sticky_url=True,
    )

    async def fake_launch_browser_session(**kwargs):  # noqa: ANN003
        _ = kwargs
        return FakeSession(root_page)

    monkeypatch.setattr(
        browser_generic,
        "resolve_browser_executable",
        lambda config, paths: type(
            "Resolution",
            (),
            {
                "source": "managed",
                "browser": "Chrome",
                "executable_path": "/tmp/chrome",
                "profile_directory": "/tmp/profile",
            },
        )(),
    )
    monkeypatch.setattr(browser_generic, "launch_browser_session", fake_launch_browser_session)
    monkeypatch.setattr(browser_generic, "current_browser_deadline_seconds", lambda: 0.02)
    monkeypatch.setattr(browser_generic, "current_browser_poll_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(browser_generic, "current_browser_networkidle_timeout_ms", lambda: 1)

    result = asyncio.run(
        browser_generic.fetch_with_browser(
            "10.1234/demo",
            HeadlessBrowserConfig(
                reuse_interactive_window=False,
                keep_interactive_window_open=False,
            ),
            GRaDOSPaths(tmp_path / "grados-home"),
        )
    )

    assert result.outcome == "timed_out"
    assert any("ScienceDirect manual PDF fallback failed" in warning for warning in result.warnings)
