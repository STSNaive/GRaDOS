"""Oracle-aligned ChatGPT browser protocol constants.

Keep model and thinking choices here so future Oracle route updates are a
small internal patch, not a config or workflow change.
"""

from __future__ import annotations

ORACLE_CHATGPT_PRO_MODEL = "gpt-5.5-pro"
ORACLE_MODEL_SELECTION_STRATEGY = "oracle_model_picker"

ORACLE_PRO_VISIBLE_ALIASES = (
    "pro",
    "chatgpt pro",
    "pro extended",
    "extended pro",
)

ORACLE_PRO_LABEL_TOKENS = (
    "gpt-5.5-pro",
    "gpt 5.5 pro",
    "gpt-5-5-pro",
    "gpt55pro",
    "pro",
    "pro extended",
    "extended pro",
    "chatgpt pro",
)

ORACLE_PRO_TEST_ID_TOKENS = (
    "model-switcher-gpt-5.5-pro",
    "model-switcher-gpt-5-5-pro",
    "gpt-5.5-pro",
    "gpt-5-5-pro",
    "gpt55pro",
    "pro",
    "proresearch",
)

ORACLE_CURRENT_PRO_TEXT_TOKENS = (
    "5 5",
    "gpt55",
    "gpt 5 5",
)

ORACLE_CURRENT_PRO_TEST_ID_TOKENS = (
    "5-5",
    "5.5",
    "gpt55",
)

ORACLE_LEGACY_PRO_TOKENS = (
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

ORACLE_PRO_THINKING_ALIAS = "pro_extended"
ORACLE_PRO_THINKING_LEVEL = "extended"

ORACLE_THINKING_LEVEL_TOKENS = {
    "light": ("light", "轻"),
    "standard": ("standard", "标准"),
    "extended": ("extended", "扩展", "深度", "加强"),
    "heavy": ("heavy", "重度", "加重", "高"),
}
