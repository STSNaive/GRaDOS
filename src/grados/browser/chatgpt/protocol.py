"""GRaDOS ChatGPT browser protocol constants.

Keep model and thinking choices here so future ChatGPT route updates are a
small internal patch, not a config or workflow change.
"""

from __future__ import annotations

CHATGPT_PRO_TARGET_MODEL = "gpt-5.5-pro"
CHATGPT_PRO_MODEL_SELECTION_STRATEGY = "chatgpt_pro_model_picker"

CHATGPT_PRO_VISIBLE_ALIASES = (
    "pro",
    "chatgpt pro",
    "pro extended",
    "extended pro",
)

CHATGPT_PRO_LABEL_TOKENS = (
    "gpt-5.5-pro",
    "gpt 5.5 pro",
    "gpt-5-5-pro",
    "gpt55pro",
    "pro",
    "pro extended",
    "extended pro",
    "chatgpt pro",
)

CHATGPT_PRO_TEST_ID_TOKENS = (
    "model-switcher-gpt-5.5-pro",
    "model-switcher-gpt-5-5-pro",
    "gpt-5.5-pro",
    "gpt-5-5-pro",
    "gpt55pro",
    "pro",
    "proresearch",
)

CHATGPT_CURRENT_PRO_TEXT_TOKENS = (
    "5 5",
    "gpt55",
    "gpt 5 5",
)

CHATGPT_CURRENT_PRO_TEST_ID_TOKENS = (
    "5-5",
    "5.5",
    "gpt55",
)

CHATGPT_LEGACY_PRO_TOKENS = (
    "gpt 5 pro",
    "gpt 5 4",
    "gpt 5 2",
    "gpt 5 1",
    "gpt 5 0",
    "gpt54",
    "gpt52",
    "gpt51",
    "gpt50",
)

CHATGPT_PRO_THINKING_ALIAS = "pro_extended"
CHATGPT_PRO_THINKING_LEVEL = "extended"

CHATGPT_THINKING_LEVEL_TOKENS = {
    "light": ("light", "轻"),
    "standard": ("standard", "标准"),
    "extended": ("extended", "扩展", "深度", "加强"),
    "heavy": ("heavy", "重度", "加重", "高"),
}
