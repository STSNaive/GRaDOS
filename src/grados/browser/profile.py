"""Profile readiness helpers for GRaDOS browser acquisition paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def browser_profile_status(profile_dir: Path) -> dict[str, Any]:
    """Return GRaDOS browser readiness markers for a persistent browser profile."""
    profile_dir = profile_dir.expanduser()
    default_dir = profile_dir / "Default"
    local_state = profile_dir / "Local State"
    profile_dirs = sorted(p.name for p in profile_dir.glob("Profile *") if p.is_dir()) if profile_dir.exists() else []
    initialized = default_dir.exists() or local_state.exists() or bool(profile_dirs)
    return {
        "profile_dir": str(profile_dir),
        "exists": profile_dir.exists(),
        "initialized": initialized,
        "markers": {
            "default": default_dir.exists(),
            "local_state": local_state.exists(),
            "profiles": profile_dirs,
        },
    }


def ensure_browser_profile_ready(profile_dir: Path, *, setup_hint: str = "grados setup") -> dict[str, Any]:
    """Create the profile root and return readiness status without faking login state."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    status = browser_profile_status(profile_dir)
    if not status["initialized"]:
        status = {
            **status,
            "hint": f"Open the managed browser once or run `{setup_hint}` to initialize the profile.",
        }
    return status
