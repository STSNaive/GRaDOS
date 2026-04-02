from __future__ import annotations

import asyncio
from pathlib import Path

from grados.server import mcp, search_saved_papers


def test_server_registers_expected_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool_names = sorted(tool.name for tool in tools)

    assert tool_names == [
        "extract_paper_full_text",
        "parse_pdf_file",
        "read_saved_paper",
        "save_paper_to_zotero",
        "search_academic_papers",
        "search_saved_papers",
    ]


def test_search_saved_papers_reports_empty_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    result = asyncio.run(search_saved_papers("composite vibration"))

    assert "No saved papers found" in result
