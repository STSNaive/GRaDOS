"""ChatGPT browser mode for GRaDOS external synthesis."""

from __future__ import annotations

from grados.browser.chatgpt.runtime import (
    check_chatgpt_login,
    open_chatgpt_login_setup,
    run_chatgpt_browser_session,
)
from grados.browser.chatgpt.selectors import CHATGPT_URL
from grados.browser.chatgpt.types import (
    BROWSER_MODE_VERSION,
    ChatGPTBrowserResult,
    ChatGPTModelSelection,
    ChatGPTThinkingSelection,
)

__all__ = [
    "BROWSER_MODE_VERSION",
    "CHATGPT_URL",
    "ChatGPTBrowserResult",
    "ChatGPTModelSelection",
    "ChatGPTThinkingSelection",
    "check_chatgpt_login",
    "open_chatgpt_login_setup",
    "run_chatgpt_browser_session",
]
