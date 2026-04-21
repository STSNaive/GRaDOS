from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_release_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("release_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_release_commit_message_uses_release_subject() -> None:
    module = _load_release_module()

    changed = [
        module.REPO / ".claude-plugin" / "plugin.json",
        module.REPO / "plugins" / "grados" / ".codex-plugin" / "plugin.json",
    ]

    assert module.build_release_commit_message("0.6.10", changed) == "chore: release v0.6.10"
