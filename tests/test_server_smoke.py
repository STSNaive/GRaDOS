from __future__ import annotations

import asyncio
from pathlib import Path

from grados.server import (
    extract_paper_full_text,
    get_saved_paper_structure,
    import_local_pdf_library,
    mcp,
    read_saved_paper,
    search_saved_papers,
)


def test_server_registers_expected_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool_names = sorted(tool.name for tool in tools)

    assert tool_names == [
        "extract_paper_full_text",
        "get_saved_paper_structure",
        "import_local_pdf_library",
        "parse_pdf_file",
        "read_saved_paper",
        "save_paper_to_zotero",
        "search_academic_papers",
        "search_saved_papers",
    ]


def test_server_registers_expected_paper_resources() -> None:
    resources = asyncio.run(mcp.list_resources())
    templates = asyncio.run(mcp.list_resource_templates())

    assert sorted(str(resource.uri) for resource in resources) == ["grados://papers/index"]
    assert sorted(template.uri_template for template in templates) == ["grados://papers/{safe_doi}"]


def test_paper_resources_can_be_read_for_index_and_overview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.storage.vector as vector

    monkeypatch.setattr(
        vector,
        "list_paper_documents",
        lambda chroma: [
            {
                "doi": "10.1234/demo",
                "safe_doi": "10_1234_demo",
                "title": "Demo Paper Title",
                "source": "Crossref",
                "fetch_outcome": "native_full_text",
                "year": "2025",
                "journal": "Composite Structures",
                "section_headings": ["Abstract", "Methods"],
                "word_count": 42,
                "char_count": 256,
                "uri": "grados://papers/10_1234_demo",
                "content_markdown": "# Demo Paper Title",
            }
        ],
    )
    monkeypatch.setattr(
        vector,
        "get_paper_document",
        lambda chroma, safe: {
            "doi": "10.1234/demo",
            "safe_doi": safe,
            "title": "Demo Paper Title",
            "source": "Crossref",
            "fetch_outcome": "native_full_text",
            "authors": [],
            "year": "2025",
            "journal": "Composite Structures",
            "section_headings": ["Abstract", "Methods"],
            "assets_manifest_path": "",
            "content_hash": "hash",
            "indexed_at": "2026-04-03T00:00:00+00:00",
            "word_count": 42,
            "char_count": 256,
            "content_markdown": (
                "# Demo Paper Title\n\n"
                "## Abstract\n\n"
                "This study investigates layered composite vibration behavior.\n\n"
                "## Methods\n\n"
                "The methods section contains the experimental procedure."
            ),
        },
    )

    index_result = asyncio.run(mcp.read_resource("grados://papers/index"))
    paper_result = asyncio.run(mcp.read_resource("grados://papers/10_1234_demo"))

    assert "Demo Paper Title" in index_result.contents[0].content
    assert "grados://papers/10_1234_demo" in index_result.contents[0].content
    assert "## Preview" in paper_result.contents[0].content
    assert "Use `read_saved_paper`" in paper_result.contents[0].content


def test_search_saved_papers_reports_empty_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    result = asyncio.run(search_saved_papers("composite vibration"))

    assert "No saved papers found" in result


def test_read_saved_paper_can_serve_canonical_record_without_markdown_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.storage.vector as vector

    monkeypatch.setattr(
        vector,
        "get_paper_document",
        lambda chroma, safe: {
            "doi": "10.1234/demo",
            "safe_doi": safe,
            "title": "Demo Paper Title",
            "source": "Crossref",
            "fetch_outcome": "native_full_text",
            "authors": [],
            "year": "2025",
            "journal": "Composite Structures",
            "section_headings": ["Abstract", "Methods"],
            "assets_manifest_path": "",
            "content_hash": "hash",
            "indexed_at": "2026-04-03T00:00:00+00:00",
            "word_count": 12,
            "char_count": 128,
            "content_markdown": "# Demo Paper Title\n\n## Methods\n\nCanonical-only content.",
        },
    )

    result = asyncio.run(read_saved_paper(safe_doi="10_1234_demo", section_query="methods"))

    assert "## Reading: 10.1234/demo" in result
    assert "Canonical-only content." in result
    assert "Available Sections" in result


def test_get_saved_paper_structure_returns_compact_structure_card(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.storage.vector as vector

    monkeypatch.setattr(
        vector,
        "get_paper_document",
        lambda chroma, safe: {
            "doi": "10.1234/demo",
            "safe_doi": safe,
            "title": "Demo Paper Title",
            "source": "Crossref",
            "fetch_outcome": "native_full_text",
            "authors": ["Alice", "Bob"],
            "year": "2025",
            "journal": "Composite Structures",
            "section_headings": ["Abstract", "Methods", "Results"],
            "assets_manifest_path": "",
            "content_hash": "hash",
            "indexed_at": "2026-04-03T00:00:00+00:00",
            "word_count": 42,
            "char_count": 256,
            "content_markdown": (
                "# Demo Paper Title\n\n"
                "## Abstract\n\n"
                "This study investigates layered composite vibration behavior.\n\n"
                "## Methods\n\n"
                "The methods section contains the experimental procedure.\n\n"
                "## Results\n\n"
                "The results section summarizes the outcome."
            ),
        },
    )

    result = asyncio.run(get_saved_paper_structure(safe_doi="10_1234_demo"))

    assert result["found"] is True
    assert result["canonical_uri"] == "grados://papers/10_1234_demo"
    assert result["title"] == "Demo Paper Title"
    assert result["preview_excerpt"].startswith("This study investigates")
    assert result["section_headings"] == ["Abstract", "Methods", "Results"]


def test_import_local_pdf_library_tool_returns_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.importing as importing

    async def fake_import(**kwargs):
        assert kwargs["source_path"] == Path("/tmp/papers")
        return importing.ImportLibraryResult(
            source_path="/tmp/papers",
            scanned=3,
            imported=2,
            skipped=1,
            failed=0,
            warnings=["paper-b.pdf: QA validation failed, imported with warning."],
            items=[
                importing.ImportItemResult(
                    source_path="/tmp/papers/paper-a.pdf",
                    status="imported",
                    doi="10.1234/demo-a",
                    safe_doi="10_1234_demo_a",
                    title="Demo Paper A",
                )
            ],
        )

    monkeypatch.setattr(importing, "import_local_pdf_library", fake_import)

    result = asyncio.run(import_local_pdf_library(source_path="/tmp/papers"))

    assert result["imported"] == 2
    assert result["warnings"] == ["paper-b.pdf: QA validation failed, imported with warning."]
    assert result["items"][0]["safe_doi"] == "10_1234_demo_a"


def test_search_saved_papers_reports_hybrid_results_with_filters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.storage.papers as papers
    import grados.storage.vector as vector

    monkeypatch.setattr(
        papers,
        "list_saved_papers",
        lambda papers_dir, chroma_dir=None: [{"doi": "10.1234/demo", "safe_doi": "10_1234_demo", "title": "Demo"}],
    )
    monkeypatch.setattr(vector, "get_index_stats", lambda chroma_dir: {"unique_papers": 1, "total_chunks": 3})
    monkeypatch.setattr(
        vector,
        "search_papers",
        lambda chroma_dir, query, limit, **kwargs: [
            {
                "doi": "10.1234/demo",
                "safe_doi": "10_1234_demo",
                "title": "Demo Paper",
                "authors": ["Alice Smith"],
                "year": "2025",
                "journal": "Composite Structures",
                "source": "Crossref",
                "score": 2.1,
                "snippet": "Composite vibration damping is discussed in detail.",
            }
        ],
    )

    result = asyncio.run(
        search_saved_papers(
            "composite vibration",
            authors="alice",
            year_from=2024,
            journal="Composite",
        )
    )

    assert "hybrid reranked" in result
    assert "filters: authors~alice, year=2024..-, journal~Composite" in result
    assert "Composite Structures" in result


def test_extract_paper_full_text_writes_asset_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.vector as vector

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            text="# Demo Paper Title\n\n## Abstract\n\n" + ("Composite vibration content. " * 80),
            outcome="native_full_text",
            source="Elsevier TDM",
            asset_hints=[
                {"kind": "figure_image", "url": "https://example.com/fig1.png"},
                {"kind": "object_api_meta", "url": "https://example.com/object"},
            ],
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    result = asyncio.run(
        extract_paper_full_text(
            doi="10.1234/demo",
            publisher="Elsevier",
            expected_title="Demo Paper Title",
        )
    )

    manifest_file = tmp_path / "grados-home" / "papers" / "_assets" / "10_1234_demo.json"
    assert "Paper Extracted Successfully" in result
    assert manifest_file.is_file()
