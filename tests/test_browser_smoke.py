from __future__ import annotations

import asyncio
from pathlib import Path

from grados._retry import install_runtime_defaults
from grados.browser.fetch_runtime import BrowserFetchState, try_backfill_from_url
from grados.browser.generic import build_browser_page_strategies
from grados.browser.manager import (
    VIEWPORTS,
    _get_managed_chromium_suffixes,
    random_viewport,
    resolve_browser_executable,
)
from grados.config import GRaDOSConfig, GRaDOSPaths, HeadlessBrowserConfig
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


def test_browser_fetch_state_rejects_oversized_pdf_capture() -> None:
    state = BrowserFetchState(max_capture_bytes=8)

    captured = state.try_capture(
        b"%PDF-1.4\n" + b"x" * 32,
        "application/pdf",
        "https://example.com/paper.pdf",
    )

    assert captured is False
    assert state.pdf_buffer is None
    assert any(
        "Browser PDF capture from https://example.com/paper.pdf exceeds configured size limit" in warning
        for warning in state.warnings
    )


def test_direct_pdf_backfill_rejects_oversized_content_length() -> None:
    class FakePage:
        url = "https://example.com/paper.pdf"

        def is_closed(self) -> bool:
            return False

    class FakeResponse:
        headers = {"content-length": "2048", "content-type": "application/pdf"}

        async def body(self) -> bytes:
            raise AssertionError("oversized response body should not be read")

    class FakeRequest:
        async def get(self, url: str, timeout: int | None = None) -> FakeResponse:
            _ = (url, timeout)
            return FakeResponse()

    class FakeContext:
        request = FakeRequest()

    warnings: list[str] = []

    asyncio.run(
        try_backfill_from_url(
            FakePage(),
            FakeContext(),
            set(),
            lambda *args: False,
            lambda: False,
            warnings.append,
            max_capture_bytes=8,
        )
    )

    assert any(
        "Browser PDF backfill from https://example.com/paper.pdf exceeds configured size limit" in warning
        for warning in warnings
    )


def test_direct_pdf_backfill_uses_runtime_timeout_config() -> None:
    class FakePage:
        url = "https://example.com/paper.pdf"

        def is_closed(self) -> bool:
            return False

    class FakeResponse:
        headers = {"content-type": "application/pdf"}

        async def body(self) -> bytes:
            return b"%PDF-1.4\n%ok"

    class FakeRequest:
        def __init__(self) -> None:
            self.timeout: int | None = None

        async def get(self, url: str, timeout: int | None = None) -> FakeResponse:
            _ = url
            self.timeout = timeout
            return FakeResponse()

    class FakeContext:
        def __init__(self) -> None:
            self.request = FakeRequest()

    cfg = GRaDOSConfig()
    cfg = cfg.model_copy(
        update={
            "extract": cfg.extract.model_copy(
                update={
                    "headless_browser": cfg.extract.headless_browser.model_copy(
                        update={"pdf_backfill_timeout": 42.0}
                    )
                }
            )
        }
    )
    context = FakeContext()
    install_runtime_defaults(cfg)
    try:
        asyncio.run(
            try_backfill_from_url(
                FakePage(),
                context,
                set(),
                lambda *args: True,
                lambda: False,
                lambda message: None,
            )
        )
    finally:
        install_runtime_defaults(None)

    assert context.request.timeout == 42000


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
    assert result.via == "browser"
    assert result.state == "timeout"
    assert any("ScienceDirect manual PDF fallback failed" in warning for warning in result.warnings)


def test_fetch_with_browser_cleans_up_listeners_after_reused_session_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.browser.generic as browser_generic

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url
            self.listeners: dict[str, list[object]] = {}

        def is_closed(self) -> bool:
            return False

        async def bring_to_front(self) -> None:
            return None

        async def goto(self, url: str, **kwargs) -> None:  # noqa: ANN003
            _ = kwargs
            self.url = url

        async def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
            _ = (state, timeout)

        async def title(self) -> str:
            return "Article"

        async def content(self) -> str:
            return "<html><body>example article</body></html>"

        def on(self, event: str, callback: object) -> None:
            self.listeners.setdefault(event, []).append(callback)

        def remove_listener(self, event: str, callback: object) -> None:
            listeners = self.listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

    class FakeContext:
        def __init__(self) -> None:
            self.listeners: dict[str, list[object]] = {}

        def on(self, event: str, callback: object) -> None:
            self.listeners.setdefault(event, []).append(callback)

        def remove_listener(self, event: str, callback: object) -> None:
            listeners = self.listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

    class FakeSession:
        def __init__(self, root_page: FakePage) -> None:
            self.root_page = root_page
            self.context = FakeContext()
            self.cleaned = False

        async def cleanup(self) -> None:
            self.cleaned = True

    class BoomStrategy:
        name = "Boom"

        async def run(self, context) -> None:  # noqa: ANN001
            _ = context
            raise RuntimeError("boom during polling")

    root_page = FakePage("https://example.org/article")
    session = FakeSession(root_page)

    async def fake_get_or_create_reusable_session(**kwargs):  # noqa: ANN003
        _ = kwargs
        return session

    async def fake_noop(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        _ = (args, kwargs)

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
    monkeypatch.setattr(browser_generic, "get_or_create_reusable_session", fake_get_or_create_reusable_session)
    monkeypatch.setattr(browser_generic, "close_secondary_pages", fake_noop)
    monkeypatch.setattr(browser_generic, "_try_backfill_from_url", fake_noop)
    monkeypatch.setattr(browser_generic, "build_browser_page_strategies", lambda: [BoomStrategy()])
    monkeypatch.setattr(browser_generic, "current_browser_networkidle_timeout_ms", lambda: 1)

    result = asyncio.run(
        browser_generic.fetch_with_browser(
            "10.1234/demo",
            HeadlessBrowserConfig(
                reuse_interactive_window=True,
                keep_interactive_window_open=True,
            ),
            GRaDOSPaths(tmp_path / "grados-home"),
        )
    )

    assert result.outcome == "error"
    assert result.via == "browser"
    assert result.state == "error"
    assert result.warnings == ["boom during polling"]
    assert root_page.listeners.get("response", []) == []
    assert root_page.listeners.get("download", []) == []
    assert session.context.listeners.get("page", []) == []
    assert session.cleaned is False


def test_fetch_with_browser_returns_publisher_challenge_when_detected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.browser.generic as browser_generic

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url
            self.listeners: dict[str, list[object]] = {}

        def is_closed(self) -> bool:
            return False

        async def bring_to_front(self) -> None:
            return None

        async def goto(self, url: str, **kwargs) -> None:  # noqa: ANN003
            _ = kwargs
            self.url = url

        async def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
            _ = (state, timeout)

        async def title(self) -> str:
            return "Just a moment..."

        async def content(self) -> str:
            return "<html><body>captcha challenge</body></html>"

        def on(self, event: str, callback: object) -> None:
            self.listeners.setdefault(event, []).append(callback)

        def remove_listener(self, event: str, callback: object) -> None:
            listeners = self.listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

    class FakeRequest:
        async def get(self, url: str, timeout: int | None = None):  # noqa: ANN201
            _ = (url, timeout)
            raise AssertionError("challenge pages should not trigger direct PDF backfill")

    class FakeContext:
        def __init__(self, root_page: FakePage) -> None:
            self.pages = [root_page]
            self.request = FakeRequest()
            self.listeners: dict[str, list[object]] = {}

        def on(self, event: str, callback: object) -> None:
            self.listeners.setdefault(event, []).append(callback)

        def remove_listener(self, event: str, callback: object) -> None:
            listeners = self.listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

    class FakeSession:
        def __init__(self, root_page: FakePage) -> None:
            self.root_page = root_page
            self.context = FakeContext(root_page)
            self.cleaned = False

        async def cleanup(self) -> None:
            self.cleaned = True

    root_page = FakePage("https://publisher.example/article")
    session = FakeSession(root_page)

    async def fake_launch_browser_session(**kwargs):  # noqa: ANN003
        _ = kwargs
        return session

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
    monkeypatch.setattr(browser_generic, "current_browser_deadline_seconds", lambda: 0.01)
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
            resume={
                "kind": "browser_profile",
                "doi": "10.1234/demo",
                "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
            },
        )
    )

    assert result.outcome == "publisher_challenge"
    assert result.via == "browser"
    assert result.state == "challenge"
    assert result.manual is True
    assert result.host == "www.sciencedirect.com"
    assert result.resume["kind"] == "browser_profile"
    assert result.resume["profile_dir"].endswith("browser/profile")
    assert result.warnings[-1] == "Browser automation: publisher_challenge"
    assert root_page.listeners.get("response", []) == []
    assert root_page.listeners.get("download", []) == []
    assert session.context.listeners.get("page", []) == []
    assert session.cleaned is True
