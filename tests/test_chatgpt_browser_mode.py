from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from grados.browser.chatgpt.assistant_response import (
    _assistant_snapshot_expression,
    _copy_expression,
)
from grados.browser.chatgpt.composer import (
    _focus_composer_expression,
    _prompt_commit_expression,
    _send_button_expression,
)
from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.lock import CHATGPT_PROFILE_LOCK_FILENAME, chatgpt_profile_lock
from grados.browser.chatgpt.login import (
    _login_probe_expression,
    _should_open_chatgpt_for_login_probe,
    probe_chatgpt_login,
    wait_for_chatgpt_login,
)
from grados.browser.chatgpt.model_selection import (
    _pro_model_selection_expression,
    is_legacy_pro_label,
    select_latest_pro_label,
)
from grados.browser.chatgpt.profile import (
    chatgpt_profile_status,
    is_chatgpt_profile_initialized,
)
from grados.browser.chatgpt.protocol import (
    CHATGPT_PRO_LABEL_TOKENS,
    CHATGPT_PRO_TARGET_MODEL,
    CHATGPT_PRO_TEST_ID_TOKENS,
    CHATGPT_PRO_THINKING_LEVEL,
)
from grados.browser.chatgpt.selectors import (
    CHATGPT_BROWSER_CHROME_FLAGS,
    COMPOSER_MODEL_SIGNAL_SELECTOR,
    MENU_CONTAINER_SELECTOR,
    MENU_ITEM_SELECTOR,
    MODEL_BUTTON_SELECTOR,
)
from grados.browser.chatgpt.session_store import (
    ChatGPTSessionStore,
    is_valid_chatgpt_session_id,
    new_session_id,
)
from grados.browser.chatgpt.thinking import (
    _pro_thinking_expression,
    rank_thinking_label,
)
from grados.config import GRaDOSPaths, HeadlessBrowserConfig


def test_chatgpt_profile_initialization_uses_private_profile_markers(tmp_path: Path) -> None:
    profile = tmp_path / "chatgpt-profile"

    assert is_chatgpt_profile_initialized(profile) is False
    profile.mkdir()
    assert chatgpt_profile_status(profile)["initialized"] is False

    (profile / "Default").mkdir()

    assert is_chatgpt_profile_initialized(profile) is True
    status = chatgpt_profile_status(profile)
    assert status["path"] == str(profile)
    assert status["setup_command"] == "grados external-synthesis setup-browser"


def test_profile_lock_uses_chatgpt_lock_file(tmp_path: Path) -> None:
    profile = tmp_path / "chatgpt-profile"
    profile.mkdir()

    async def run() -> None:
        async with chatgpt_profile_lock(profile, purpose="test", session_id="s1") as lock:
            assert lock.lock_path == profile / CHATGPT_PROFILE_LOCK_FILENAME
            assert lock.lock_path.exists()

    asyncio.run(run())

    assert not (profile / CHATGPT_PROFILE_LOCK_FILENAME).exists()


def test_login_probe_only_opens_chatgpt_from_blank_pages() -> None:
    assert _should_open_chatgpt_for_login_probe("") is True
    assert _should_open_chatgpt_for_login_probe("about:blank") is True
    assert _should_open_chatgpt_for_login_probe("https://accounts.google.com/v3/signin/challenge/pwd") is False
    assert _should_open_chatgpt_for_login_probe("https://auth.openai.com/login") is False
    assert _should_open_chatgpt_for_login_probe("https://chatgpt.com/") is False


def test_login_probe_does_not_interrupt_external_auth_flow() -> None:
    events: list[str] = []

    class FakePage:
        url = "https://accounts.google.com/v3/signin/challenge/pwd"

        async def goto(self, url: str, **kwargs: Any) -> None:
            events.append(f"goto:{url}")

        async def evaluate(self, expression: str) -> dict[str, object]:
            assert "backend_authenticated" in expression
            events.append("evaluate")
            return {
                "ok": False,
                "status": 0,
                "dom_login_cta": False,
                "on_auth_page": True,
                "backend_authenticated": False,
                "error": "not_on_chatgpt_domain",
            }

    result = asyncio.run(probe_chatgpt_login(FakePage()))

    assert result["ok"] is False
    assert result["on_auth_page"] is True
    assert events == ["evaluate"]


def test_login_probe_expression_requires_backend_authentication() -> None:
    expression = _login_probe_expression(5000)

    assert "ok: authenticated && !loginSignals" in expression
    assert "backend_authenticated: authenticated" in expression
    assert "status === 401 || status === 403" in expression


def test_wait_for_chatgpt_login_requires_two_stable_successes(monkeypatch: pytest.MonkeyPatch) -> None:
    from grados.browser.chatgpt import login

    probes = [
        {"ok": True, "status": 200},
        {"ok": False, "status": 200, "dom_login_cta": True},
        {"ok": True, "status": 200},
        {"ok": True, "status": 200},
    ]
    events: list[str] = []

    async def fake_probe(page: object, *, timeout_ms: int) -> dict[str, object]:
        assert timeout_ms == 5000
        events.append("probe")
        return probes.pop(0)

    async def fake_sleep(seconds: float) -> None:
        assert seconds == 1.0
        events.append("sleep")

    monkeypatch.setattr(login, "probe_chatgpt_login", fake_probe)
    monkeypatch.setattr(login.asyncio, "sleep", fake_sleep)

    result = asyncio.run(wait_for_chatgpt_login(object(), timeout_seconds=10.0))

    assert result["ok"] is True
    assert result["stable_successes"] == 2
    assert events == ["probe", "sleep", "probe", "sleep", "probe", "sleep", "probe"]


def test_login_setup_uses_profile_lock_and_closes_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from grados.browser.chatgpt import runtime

    paths = GRaDOSPaths(tmp_path)
    events: list[str] = []

    class FakeLock:
        def __init__(self, purpose: str, session_id: str) -> None:
            self.purpose = purpose
            self.session_id = session_id

        async def __aenter__(self) -> FakeLock:
            events.append(f"enter:{self.purpose}:{bool(self.session_id)}")
            return self

        async def __aexit__(self, *args: object) -> None:
            events.append(f"exit:{self.purpose}")

    class FakePage:
        async def goto(self, url: str, **kwargs: Any) -> None:
            events.append(f"goto:{url}")

    class FakeSession:
        root_page = FakePage()

        async def cleanup(self) -> None:
            events.append("cleanup")

    def fake_lock(profile_dir: Path, *, purpose: str, session_id: str) -> FakeLock:
        assert profile_dir == paths.chatgpt_browser_profile
        return FakeLock(purpose, session_id)

    async def fake_launch(paths_arg: GRaDOSPaths, browser_config: HeadlessBrowserConfig) -> FakeSession:
        assert paths_arg == paths
        assert isinstance(browser_config, HeadlessBrowserConfig)
        events.append("launch")
        return FakeSession()

    async def fake_wait(page: Any, *, timeout_seconds: float) -> dict[str, object]:
        assert isinstance(page, FakePage)
        assert timeout_seconds == 1.0
        events.append("wait")
        return {"ok": True}

    monkeypatch.setattr(runtime, "chatgpt_profile_lock", fake_lock)
    monkeypatch.setattr(runtime, "_launch_private_profile", fake_launch)
    monkeypatch.setattr(runtime, "wait_for_chatgpt_login", fake_wait)

    result = asyncio.run(
        runtime.open_chatgpt_login_setup(
            paths,
            HeadlessBrowserConfig(),
            timeout_seconds=1.0,
            keep_open=False,
        )
    )

    assert result["ok"] is True
    assert result["profile"] == str(paths.chatgpt_browser_profile)
    assert events == [
        "enter:external_synthesis_setup:True",
        "launch",
        "goto:https://chatgpt.com/",
        "wait",
        "cleanup",
        "exit:external_synthesis_setup",
    ]


def test_login_setup_keep_open_holds_lock_until_browser_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from grados.browser.chatgpt import runtime

    paths = GRaDOSPaths(tmp_path)
    events: list[str] = []

    class FakeLock:
        def __init__(self, purpose: str, session_id: str) -> None:
            self.purpose = purpose
            self.session_id = session_id

        async def __aenter__(self) -> FakeLock:
            events.append(f"enter:{self.purpose}:{bool(self.session_id)}")
            return self

        async def __aexit__(self, *args: object) -> None:
            events.append(f"exit:{self.purpose}")

    class FakeContext:
        async def wait_for_event(self, event: str, **kwargs: object) -> None:
            assert event == "close"
            assert kwargs == {"timeout": 0}
            events.append("wait_for_close")

    class FakePage:
        async def goto(self, url: str, **kwargs: Any) -> None:
            events.append(f"goto:{url}")

    class FakeSession:
        context = FakeContext()
        root_page = FakePage()

        async def cleanup(self) -> None:
            events.append("cleanup")

    def fake_lock(profile_dir: Path, *, purpose: str, session_id: str) -> FakeLock:
        assert profile_dir == paths.chatgpt_browser_profile
        return FakeLock(purpose, session_id)

    async def fake_launch(paths_arg: GRaDOSPaths, browser_config: HeadlessBrowserConfig) -> FakeSession:
        assert paths_arg == paths
        assert isinstance(browser_config, HeadlessBrowserConfig)
        events.append("launch")
        return FakeSession()

    async def fake_wait(page: Any, *, timeout_seconds: float) -> dict[str, object]:
        assert isinstance(page, FakePage)
        assert timeout_seconds == 1.0
        events.append("wait")
        return {"ok": True}

    monkeypatch.setattr(runtime, "chatgpt_profile_lock", fake_lock)
    monkeypatch.setattr(runtime, "_launch_private_profile", fake_launch)
    monkeypatch.setattr(runtime, "wait_for_chatgpt_login", fake_wait)

    result = asyncio.run(
        runtime.open_chatgpt_login_setup(
            paths,
            HeadlessBrowserConfig(),
            timeout_seconds=1.0,
            keep_open=True,
        )
    )

    assert result["ok"] is True
    assert result["profile"] == str(paths.chatgpt_browser_profile)
    assert events == [
        "enter:external_synthesis_setup:True",
        "launch",
        "goto:https://chatgpt.com/",
        "wait",
        "wait_for_close",
        "cleanup",
        "exit:external_synthesis_setup",
    ]


def test_live_login_check_uses_profile_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from grados.browser.chatgpt import runtime

    paths = GRaDOSPaths(tmp_path)
    (paths.chatgpt_browser_profile / "Default").mkdir(parents=True)
    events: list[str] = []

    class FakeLock:
        def __init__(self, purpose: str, session_id: str) -> None:
            self.purpose = purpose
            self.session_id = session_id

        async def __aenter__(self) -> FakeLock:
            events.append(f"enter:{self.purpose}:{bool(self.session_id)}")
            return self

        async def __aexit__(self, *args: object) -> None:
            events.append(f"exit:{self.purpose}")

    class FakePage:
        async def goto(self, url: str, **kwargs: Any) -> None:
            events.append(f"goto:{url}")

    class FakeSession:
        root_page = FakePage()

        async def cleanup(self) -> None:
            events.append("cleanup")

    def fake_lock(profile_dir: Path, *, purpose: str, session_id: str) -> FakeLock:
        assert profile_dir == paths.chatgpt_browser_profile
        return FakeLock(purpose, session_id)

    async def fake_launch(paths_arg: GRaDOSPaths, browser_config: HeadlessBrowserConfig) -> FakeSession:
        assert paths_arg == paths
        assert isinstance(browser_config, HeadlessBrowserConfig)
        events.append("launch")
        return FakeSession()

    async def fake_probe(page: Any) -> dict[str, object]:
        assert isinstance(page, FakePage)
        events.append("probe")
        return {"ok": True}

    monkeypatch.setattr(runtime, "chatgpt_profile_lock", fake_lock)
    monkeypatch.setattr(runtime, "_launch_private_profile", fake_launch)
    monkeypatch.setattr(runtime, "probe_chatgpt_login", fake_probe)

    result = asyncio.run(runtime.check_chatgpt_login(paths, HeadlessBrowserConfig()))

    assert result["ok"] is True
    assert result["profile"] == str(paths.chatgpt_browser_profile)
    assert events == [
        "enter:external_synthesis_doctor:True",
        "launch",
        "goto:https://chatgpt.com/",
        "probe",
        "cleanup",
        "exit:external_synthesis_doctor",
    ]


def test_session_store_rejects_path_escape_ids(tmp_path: Path) -> None:
    store = ChatGPTSessionStore(tmp_path / "chatgpt-sessions")
    valid_session_id = new_session_id()

    assert is_valid_chatgpt_session_id(valid_session_id) is True
    assert store.session_dir(valid_session_id) == tmp_path / "chatgpt-sessions" / valid_session_id

    for bad_session_id in ["../outside", "/tmp/chatgpt-session", "chatgpt-test"]:
        assert is_valid_chatgpt_session_id(bad_session_id) is False
        with pytest.raises(ValueError):
            store.session_dir(bad_session_id)


def test_latest_pro_model_rejects_legacy_pro_when_current_pro_visible() -> None:
    assert is_legacy_pro_label("GPT-5.4 Pro") is True
    assert select_latest_pro_label(["Instant", "GPT-5.4 Pro", "Pro"]) == "Pro"
    assert select_latest_pro_label(["GPT-5.5 Pro", "Thinking"]) == "GPT-5.5 Pro"


def test_latest_pro_model_fails_without_current_pro() -> None:
    try:
        select_latest_pro_label(["Instant", "GPT-5.4 Pro", "Thinking"])
    except ChatGPTBrowserError as exc:
        assert exc.code == "model_unavailable"
    else:  # pragma: no cover
        raise AssertionError("expected model_unavailable")


def test_pro_extended_thinking_rank_preserves_localized_labels() -> None:
    assert CHATGPT_PRO_THINKING_LEVEL == "extended"
    assert rank_thinking_label("Extended") == 50
    assert rank_thinking_label("深度思考") == 50


def test_chatgpt_selectors_match_browser_contract() -> None:
    assert MODEL_BUTTON_SELECTOR == (
        '[data-testid="model-switcher-dropdown-button"], button.__composer-pill[aria-haspopup="menu"]'
    )
    assert MENU_CONTAINER_SELECTOR == '[role="menu"], [data-radix-collection-root]'
    assert MENU_ITEM_SELECTOR == (
        'button, [role="menuitem"], [role="menuitemradio"], [data-testid*="model-switcher-"]'
    )
    assert COMPOSER_MODEL_SIGNAL_SELECTOR == '[data-testid="composer-footer-actions"]'
    assert "--disable-background-networking" in CHATGPT_BROWSER_CHROME_FLAGS
    assert "--disable-features=TranslateUI,AutomationControlled" in CHATGPT_BROWSER_CHROME_FLAGS
    assert "--accept-lang=en-US,en" in CHATGPT_BROWSER_CHROME_FLAGS


def test_model_expression_uses_pro_picker_controls() -> None:
    expression = _pro_model_selection_expression()

    assert "model-switcher-dropdown-button" in expression
    assert "data-radix-collection-root" in expression
    assert "data-model-picker-thinking-effort-action" in expression
    assert "pro extended" in expression
    assert CHATGPT_PRO_TARGET_MODEL in expression


def test_chatgpt_protocol_constants_centralize_model_route() -> None:
    assert CHATGPT_PRO_TARGET_MODEL == "gpt-5.5-pro"
    assert CHATGPT_PRO_TARGET_MODEL in CHATGPT_PRO_LABEL_TOKENS
    assert CHATGPT_PRO_TARGET_MODEL in CHATGPT_PRO_TEST_ID_TOKENS
    assert CHATGPT_PRO_THINKING_LEVEL == "extended"


def test_thinking_expression_uses_pro_effort_controls() -> None:
    expression = _pro_thinking_expression()

    assert "data-model-picker-thinking-effort-action" in expression
    assert "data-model-picker-thinking-effort-row" in expression
    assert "aria-controls" in expression
    assert "model-kind-not-found" in expression


def test_composer_expressions_use_chatgpt_prompt_commit_route() -> None:
    focus = _focus_composer_expression()
    send = _send_button_expression()
    commit = _prompt_commit_expression("hello", 0)

    assert "dispatchClickSequence" in focus
    assert "textarea[data-id" in focus
    assert "button[data-testid=\\\"send-button\\\"]" in send
    assert "composerCleared" in commit
    assert "normalizedPromptPrefix" in commit


def test_capture_expressions_use_chatgpt_snapshot_and_copy_route() -> None:
    snapshot = _assistant_snapshot_expression()
    copy = _copy_expression({"messageId": "m1", "turnId": "t1"})

    assert "answer now" in snapshot
    assert "copy-turn-action-button" in copy
    assert "interceptClipboard" in copy
    assert "dispatchClickSequence" in copy
