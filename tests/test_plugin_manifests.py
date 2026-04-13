from __future__ import annotations

import json
from pathlib import Path


def test_plugin_manifests_reference_existing_repo_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    codex_plugin = json.loads((repo_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_plugin = json.loads((repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_marketplace = json.loads((repo_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    codex_marketplace = json.loads((repo_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    plugin_mcp = json.loads((repo_root / "plugin.mcp.json").read_text(encoding="utf-8"))
    repo_mcp = json.loads((repo_root / ".mcp.json").read_text(encoding="utf-8"))

    assert codex_plugin["name"] == "grados"
    assert codex_plugin["skills"] == "./skills/"
    assert codex_plugin["mcpServers"] == "./plugin.mcp.json"
    assert (repo_root / codex_plugin["skills"][2:] / "grados" / "SKILL.md").is_file()
    assert (repo_root / codex_plugin["mcpServers"][2:]).is_file()

    assert claude_plugin["name"] == "grados"
    assert claude_plugin["mcpServers"] == "./plugin.mcp.json"

    assert claude_marketplace["name"] == "grados-plugins"
    assert claude_marketplace["plugins"][0]["name"] == "grados"
    assert claude_marketplace["plugins"][0]["source"] == "./"

    assert codex_marketplace["name"] == "grados-plugins"
    assert codex_marketplace["plugins"][0]["name"] == "grados"
    assert codex_marketplace["plugins"][0]["source"]["path"] == "./"

    assert plugin_mcp["mcpServers"]["grados"]["args"] == ["grados"]
    assert repo_mcp["mcpServers"]["grados"]["args"] == ["grados"]
