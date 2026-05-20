"""Private ChatGPT profile helpers."""

from __future__ import annotations

from pathlib import Path

from grados.browser.chatgpt.errors import ChatGPTBrowserError


def is_chatgpt_profile_initialized(profile_dir: Path) -> bool:
    """Return whether the private profile has Chrome user-data markers."""
    try:
        entries = list(profile_dir.iterdir())
    except OSError:
        return False
    for entry in entries:
        name = entry.name
        if name in {"Default", "Local State"}:
            return True
        if name.startswith("Profile "):
            return True
    return False


def format_chatgpt_profile_setup_command(profile_dir: Path) -> str:
    return "grados external-synthesis setup-browser"


def chatgpt_profile_status(profile_dir: Path) -> dict[str, object]:
    initialized = is_chatgpt_profile_initialized(profile_dir)
    return {
        "path": str(profile_dir),
        "exists": profile_dir.exists(),
        "initialized": initialized,
        "setup_command": format_chatgpt_profile_setup_command(profile_dir),
    }


def ensure_chatgpt_profile_ready(profile_dir: Path, *, setup_mode: bool = False) -> None:
    """Fail fast unless the private ChatGPT profile is already initialized."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    if setup_mode or is_chatgpt_profile_initialized(profile_dir):
        return
    setup_command = format_chatgpt_profile_setup_command(profile_dir)
    raise ChatGPTBrowserError(
        code="chatgpt_profile_not_initialized",
        stage="profile",
        message=(
            "GRaDOS private ChatGPT browser profile is not initialized. "
            f"Run `{setup_command}`, sign in to ChatGPT in that window, then retry."
        ),
        details={
            "profile_dir": str(profile_dir),
            "setup_command": setup_command,
        },
    )
