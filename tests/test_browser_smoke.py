from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from grados._retry import install_runtime_defaults
from grados.browser.fetch_runtime import BrowserFetchState, try_backfill_from_url
from grados.browser.generic import build_browser_page_strategies
from grados.browser.lock import BrowserProfileLockError, browser_profile_lock, read_browser_profile_lock
from grados.browser.manager import (
    VIEWPORTS,
    _get_managed_chromium_suffixes,
    random_viewport,
    resolve_browser_executable,
)
from grados.browser.pdf.session_store import create_pdf_browser_session, update_pdf_browser_session
from grados.browser.profile import browser_profile_status
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


def test_browser_profile_status_uses_oracle_style_markers(tmp_path: Path) -> None:
    profile = tmp_path / "profile"

    status = browser_profile_status(profile)

    assert status["exists"] is False
    assert status["initialized"] is False

    (profile / "Default").mkdir(parents=True)
    status = browser_profile_status(profile)

    assert status["exists"] is True
    assert status["initialized"] is True
    assert status["markers"]["default"] is True


def test_browser_profile_lock_writes_and_releases_own_lock(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    lock = browser_profile_lock(profile, purpose="test", session_id="pdf-test", timeout_seconds=0.0)

    asyncio.run(lock.__aenter__())

    payload = read_browser_profile_lock(profile)
    assert payload["purpose"] == "test"
    assert payload["sessionId"] == "pdf-test"

    lock.release()

    assert read_browser_profile_lock(profile) == {}


def test_browser_profile_lock_recovers_stale_lock(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "grados-browser.lock").write_text(
        json.dumps({"pid": -1, "lockId": "stale", "sessionId": "old"}),
        encoding="utf-8",
    )

    lock = browser_profile_lock(profile, purpose="test", session_id="pdf-test", timeout_seconds=0.0)
    asyncio.run(lock.__aenter__())

    payload = read_browser_profile_lock(profile)
    assert payload["sessionId"] == "pdf-test"
    assert payload["lockId"] != "stale"

    lock.release()


def test_browser_profile_lock_blocks_same_process_parallel_use(tmp_path: Path) -> None:
    profile = tmp_path / "profile"

    async def run_lock_probe() -> None:
        first = browser_profile_lock(profile, purpose="test", session_id="pdf-first", timeout_seconds=0.1)
        await first.__aenter__()
        try:
            second = browser_profile_lock(profile, purpose="test", session_id="pdf-second", timeout_seconds=0.01)
            with pytest.raises(BrowserProfileLockError):
                await second.__aenter__()
        finally:
            first.release()

    asyncio.run(run_lock_probe())


def test_reusable_session_carries_profile_lock_when_replacing_same_profile(monkeypatch) -> None:
    import grados.browser.manager as browser_manager

    class FakePage:
        def __init__(self, *, closed: bool) -> None:
            self._closed = closed

        def is_closed(self) -> bool:
            return self._closed

    class FakeContext:
        pages: list[FakePage] = []

    class FakeLock:
        released = False

        def release(self) -> None:
            self.released = True

    old_lock = FakeLock()
    cleanup_called = False

    async def cleanup_old() -> None:
        nonlocal cleanup_called
        cleanup_called = True

    old_session = browser_manager.BrowserSession(
        playwright=None,
        browser=None,
        context=FakeContext(),
        root_page=FakePage(closed=True),
        cleanup=cleanup_old,
        executable_path="/tmp/chrome",
        user_data_dir="/tmp/profile",
        profile_lock=old_lock,
    )

    async def fake_launch_browser_session(**kwargs):  # noqa: ANN003
        _ = kwargs
        return browser_manager.BrowserSession(
            playwright=None,
            browser=None,
            context=FakeContext(),
            root_page=FakePage(closed=False),
            cleanup=lambda: None,
            executable_path="/tmp/chrome",
            user_data_dir="/tmp/profile",
        )

    monkeypatch.setattr(browser_manager, "_reusable_session", old_session)
    monkeypatch.setattr(browser_manager, "launch_browser_session", fake_launch_browser_session)
    try:
        session = asyncio.run(
            browser_manager.get_or_create_reusable_session(
                executable_path="/tmp/chrome",
                viewport={"width": 1366, "height": 768},
                user_data_dir="/tmp/profile",
            )
        )
    finally:
        monkeypatch.setattr(browser_manager, "_reusable_session", None)

    assert cleanup_called is True
    assert old_lock.released is False
    assert session.profile_lock is old_lock


def test_pdf_browser_session_store_round_trip(tmp_path: Path) -> None:
    record = create_pdf_browser_session(
        tmp_path / "sessions",
        doi="10.1234/demo",
        target_url="https://doi.org/10.1234/demo",
    )

    update_pdf_browser_session(
        record,
        status="ok",
        outcome="pdf_obtained",
        capture={"source": "response", "url": "https://example.com/paper.pdf", "bytes": 12},
        warnings=["one warning"],
        events=[{"timestamp": record.created_at, "name": "pdf_capture_success", "url": "https://example.com/paper.pdf"}],
    )

    payload = json.loads(Path(record.record_path).read_text(encoding="utf-8"))
    assert payload["browser_mode_version"] == "pdf-browser-v1"
    assert payload["doi"] == "10.1234/demo"
    assert payload["status"] == "ok"
    assert payload["capture"]["source"] == "response"
    assert payload["events"][0]["name"] == "pdf_capture_success"


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
    assert state.events[-1]["name"] == "pdf_capture_rejected"


def test_browser_fetch_state_records_success_capture_metadata() -> None:
    state = BrowserFetchState(max_capture_bytes=1024)

    captured = state.try_capture(
        b"%PDF-1.4\n%stub",
        "application/pdf",
        "https://example.com/paper.pdf",
        source_kind="response",
    )

    assert captured is True
    assert state.capture_payload() == {
        "source": "response",
        "url": "https://example.com/paper.pdf",
        "content_type": "application/pdf",
        "bytes": len(b"%PDF-1.4\n%stub"),
    }
    assert state.events[-1]["name"] == "pdf_capture_success"


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


def test_direct_pdf_backfill_records_attempt_and_success() -> None:
    class FakePage:
        url = "https://example.com/paper.pdf"

        def is_closed(self) -> bool:
            return False

    class FakeResponse:
        headers = {"content-type": "application/pdf"}

        async def body(self) -> bytes:
            return b"%PDF-1.4\n%ok"

    class FakeRequest:
        async def get(self, url: str, timeout: int | None = None) -> FakeResponse:
            _ = (url, timeout)
            return FakeResponse()

    class FakeContext:
        request = FakeRequest()

    events: list[dict[str, object]] = []

    def record_event(name: str, *, url: str = "", details: dict[str, object] | None = None) -> None:
        events.append({"name": name, "url": url, "details": details or {}})

    state = BrowserFetchState()
    asyncio.run(
        try_backfill_from_url(
            FakePage(),
            FakeContext(),
            set(),
            state.try_capture,
            state.pdf_captured,
            state.report_warning,
            record_event=record_event,
        )
    )

    assert state.pdf_captured()
    assert state.capture_source == "backfill"
    assert [event["name"] for event in events] == ["backfill_attempt", "backfill_success"]


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
                "profile_directory": str(paths.browser_profile),
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
                "profile_directory": str(paths.browser_profile),
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
                "profile_directory": str(paths.browser_profile),
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


def test_fetch_with_browser_records_session_and_never_writes_papers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import grados.browser.generic as browser_generic

    paths = GRaDOSPaths(tmp_path / "grados-home")

    class FakePage:
        url = "https://example.com/article"

        def __init__(self) -> None:
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
            return "<html><body>article</body></html>"

        def on(self, event: str, callback: object) -> None:
            self.listeners.setdefault(event, []).append(callback)

        def remove_listener(self, event: str, callback: object) -> None:
            listeners = self.listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)

    class FakeContext:
        def __init__(self, root_page: FakePage) -> None:
            self.pages = [root_page]
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

    root_page = FakePage()
    session = FakeSession(root_page)

    async def fake_launch_browser_session(**kwargs):  # noqa: ANN003
        _ = kwargs
        return session

    async def fake_run_loop(**kwargs) -> None:  # noqa: ANN003
        state = kwargs["state"]
        state.try_capture(
            b"%PDF-1.4\n%stub",
            "application/pdf",
            "https://example.com/paper.pdf",
            source_kind="response",
        )

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
                "profile_directory": str(paths.browser_profile),
            },
        )(),
    )
    monkeypatch.setattr(browser_generic, "launch_browser_session", fake_launch_browser_session)
    monkeypatch.setattr(browser_generic, "run_browser_polling_loop", fake_run_loop)
    monkeypatch.setattr(browser_generic, "current_browser_networkidle_timeout_ms", lambda: 1)

    result = asyncio.run(
        browser_generic.fetch_with_browser(
            "10.1234/demo",
            HeadlessBrowserConfig(
                reuse_interactive_window=False,
                keep_interactive_window_open=False,
            ),
            paths,
        )
    )

    assert result.outcome == "pdf_obtained"
    assert result.capture["source"] == "response"
    assert result.session_id.startswith("pdf-")
    assert Path(result.session_record_path).is_file()
    payload = json.loads(Path(result.session_record_path).read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["capture"]["url"] == "https://example.com/paper.pdf"
    assert not list(paths.papers.glob("*.md")) if paths.papers.exists() else True
    assert read_browser_profile_lock(paths.browser_profile) == {}
