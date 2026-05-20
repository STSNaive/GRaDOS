"""Shared browser runtime constants for GRaDOS-managed browser paths."""

from __future__ import annotations

COMMON_CHROME_FLAGS = [
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-features=TranslateUI,AutomationControlled",
    "--disable-popup-blocking",
    "--disable-sync",
    "--no-default-browser-check",
    "--no-first-run",
    "--accept-lang=en-US,en",
]

PDF_BROWSER_CHROME_FLAGS = [
    *COMMON_CHROME_FLAGS,
    "--disable-component-update",
]
