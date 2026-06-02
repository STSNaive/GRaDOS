"""Capture ChatGPT assistant responses via the ChatGPT copy-first route."""

from __future__ import annotations

from typing import Any

from grados.browser.chatgpt.assistant_response import (
    capture_assistant_markdown,
    read_assistant_snapshot,
)
from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.types import ChatGPTCapture


async def capture_final_response(page: Any, min_turn_index: int | None = None) -> ChatGPTCapture:
    snapshot = await read_assistant_snapshot(page, min_turn_index=min_turn_index)
    meta = {
        "messageId": snapshot.get("messageId") if snapshot else None,
        "turnId": snapshot.get("turnId") if snapshot else None,
        "minTurnIndex": min_turn_index,
    }
    copied = await capture_assistant_markdown(page, meta)
    if copied:
        return ChatGPTCapture(response_text=copied, method="copy_turn_action_button")

    text = str(snapshot.get("text") if snapshot else "").strip()
    if not text:
        raise ChatGPTBrowserError(
            code="capture_failed",
            stage="capture",
            message="Unable to capture the final ChatGPT assistant response.",
            details={"conversation_url": getattr(page, "url", ""), "min_turn_index": min_turn_index},
        )
    return ChatGPTCapture(
        response_text=text,
        method="chatgpt_dom_snapshot_fallback",
        warnings=["copy_button_unavailable"],
    )
