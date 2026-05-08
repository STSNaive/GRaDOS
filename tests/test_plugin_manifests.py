from __future__ import annotations

import json
from pathlib import Path


def test_plugin_manifests_reference_existing_repo_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    codex_plugin_root = repo_root / "plugins" / "grados"
    codex_plugin = json.loads(
        (codex_plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude_plugin = json.loads((repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_marketplace = json.loads((repo_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    codex_marketplace = json.loads((repo_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    codex_plugin_mcp = json.loads((codex_plugin_root / "plugin.mcp.json").read_text(encoding="utf-8"))
    plugin_mcp = json.loads((repo_root / "plugin.mcp.json").read_text(encoding="utf-8"))
    repo_mcp = json.loads((repo_root / ".mcp.json").read_text(encoding="utf-8"))

    assert codex_plugin["name"] == "grados"
    assert codex_plugin["skills"] == "./skills/"
    assert codex_plugin["mcpServers"] == "./plugin.mcp.json"
    assert (codex_plugin_root / codex_plugin["skills"][2:] / "grados" / "SKILL.md").is_file()
    assert (codex_plugin_root / codex_plugin["mcpServers"][2:]).is_file()

    assert claude_plugin["name"] == "grados"
    assert claude_plugin["mcpServers"] == "./plugin.mcp.json"

    assert claude_marketplace["name"] == "grados-plugins"
    assert claude_marketplace["plugins"][0]["name"] == "grados"
    assert claude_marketplace["plugins"][0]["source"] == "./"

    assert codex_marketplace["name"] == "grados-plugins"
    assert codex_marketplace["interface"]["displayName"] == "GRaDOS Plugins"
    assert codex_marketplace["plugins"][0]["name"] == "grados"
    assert codex_marketplace["plugins"][0]["source"]["source"] == "local"
    assert codex_marketplace["plugins"][0]["source"]["path"] == "./plugins/grados"
    bundled_plugin_root = repo_root / codex_marketplace["plugins"][0]["source"]["path"][2:]
    assert (bundled_plugin_root / ".codex-plugin" / "plugin.json").is_file()
    assert (bundled_plugin_root / "plugin.mcp.json").is_file()

    assert codex_plugin_mcp == plugin_mcp
    assert plugin_mcp["mcpServers"]["grados"]["args"] == ["grados"]
    assert repo_mcp["mcpServers"]["grados"]["args"] == ["grados"]

    canonical_skill_root = repo_root / "skills" / "grados"
    bundled_skill_root = codex_plugin_root / "skills" / "grados"
    mirrored_files = [
        "SKILL.md",
        "agents/openai.yaml",
        "references/indepth.md",
        "references/tools.md",
    ]
    for relative_path in mirrored_files:
        assert (
            (bundled_skill_root / relative_path).read_text(encoding="utf-8")
            == (canonical_skill_root / relative_path).read_text(encoding="utf-8")
        )

    tools_reference = (canonical_skill_root / "references" / "tools.md").read_text(encoding="utf-8")
    assert "`codex` is a disabled-by-default fetch-strategy entry" in tools_reference
    assert "Docling -> MinerU -> Marker -> PyMuPDF" in tools_reference
    assert "TDM -> OA -> Sci-Hub -> Headless" not in tools_reference
    assert "PyMuPDF -> Marker -> Docling" not in tools_reference
