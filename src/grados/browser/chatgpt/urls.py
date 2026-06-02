"""URL helpers for recoverable ChatGPT conversations."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_CHATGPT_CONVERSATION_HOSTS = {"chatgpt.com", "chat.openai.com"}
_CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def extract_chatgpt_conversation_id(url: str | object) -> str:
    """Return the ChatGPT conversation id only for recoverable conversation URLs."""

    raw_url = str(url or "").strip()
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    if parsed.scheme != "https":
        return ""
    if (parsed.hostname or "").lower() not in _CHATGPT_CONVERSATION_HOSTS:
        return ""

    segments = [segment for segment in parsed.path.split("/") if segment]
    for index, segment in enumerate(segments[:-1]):
        if segment != "c":
            continue
        if index != 0 and "project" not in segments[:index]:
            return ""
        conversation_id = segments[index + 1]
        if _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            return conversation_id
        return ""
    return ""


def is_recoverable_chatgpt_conversation_url(url: str | object) -> bool:
    return bool(extract_chatgpt_conversation_id(url))
