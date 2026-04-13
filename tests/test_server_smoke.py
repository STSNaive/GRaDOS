from __future__ import annotations

import asyncio
from pathlib import Path

from grados.server import (
    audit_draft_support,
    build_evidence_grid,
    compare_papers,
    extract_paper_full_text,
    get_citation_graph,
    get_papers_full_context,
    get_saved_paper_structure,
    import_local_pdf_library,
    manage_failure_cases,
    mcp,
    query_research_artifacts,
    read_saved_paper,
    save_research_artifact,
    search_saved_papers,
)


def test_server_registers_expected_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool_names = sorted(tool.name for tool in tools)

    assert tool_names == [
        "audit_draft_support",
        "build_evidence_grid",
        "compare_papers",
        "extract_paper_full_text",
        "get_citation_graph",
        "get_papers_full_context",
        "get_saved_paper_structure",
        "import_local_pdf_library",
        "manage_failure_cases",
        "parse_pdf_file",
        "query_research_artifacts",
        "read_saved_paper",
        "save_paper_to_zotero",
        "save_research_artifact",
        "search_academic_papers",
        "search_saved_papers",
    ]


def test_tool_metadata_exposes_clearer_llm_contracts() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    search_remote = tools["search_academic_papers"]
    assert "metadata only" in (search_remote.description or "")
    assert search_remote.parameters["properties"]["query"]["minLength"] == 1
    assert search_remote.parameters["properties"]["limit"]["maximum"] == 50

    extract = tools["extract_paper_full_text"]
    assert "compact save receipt" in (extract.description or "")
    assert "does not change fetch routing" in extract.parameters["properties"]["publisher"]["description"]

    read_tool = tools["read_saved_paper"]
    assert "Provide one of `doi`, `safe_doi`, or `uri`" in (read_tool.description or "")
    assert read_tool.parameters["properties"]["start_paragraph"]["minimum"] == 0
    assert read_tool.parameters["properties"]["max_paragraphs"]["maximum"] == 100

    search_saved = tools["search_saved_papers"]
    assert "screening hints" in (search_saved.description or "")
    assert search_saved.parameters["properties"]["limit"]["maximum"] == 25

    artifact = tools["save_research_artifact"]
    assert "reusable intermediate outputs" in (artifact.description or "")
    assert "search_snapshot" in artifact.parameters["properties"]["kind"]["description"]
    assert "project_id" not in artifact.parameters["properties"]

    full_context = tools["get_papers_full_context"]
    assert "CAG-style deep-reading pass" in (full_context.description or "")
    assert full_context.parameters["properties"]["max_total_tokens"]["maximum"] == 128000

    audit = tools["audit_draft_support"]
    assert "claim-level `supported`, `weak`, `unsupported`, or `misattributed`" in (audit.description or "")
    assert audit.parameters["properties"]["draft_text"]["minLength"] == 1
    assert "project_id" not in tools["query_research_artifacts"].parameters["properties"]
    assert "project_id" not in audit.parameters["properties"]


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


def test_search_saved_papers_rejects_invalid_year_range() -> None:
    result = asyncio.run(search_saved_papers("composite vibration", year_from=2025, year_to=2024))

    assert "Invalid year range" in result


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


def test_read_saved_paper_requires_a_locator() -> None:
    result = asyncio.run(read_saved_paper())

    assert "Provide at least one of doi, safe_doi, or uri." in result


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


def test_get_saved_paper_structure_requires_a_locator() -> None:
    result = asyncio.run(get_saved_paper_structure())

    assert result["found"] is False
    assert "Provide at least one of doi, safe_doi, or uri." in result["message"]


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
    monkeypatch.setattr(
        vector,
        "get_index_stats",
        lambda chroma_dir, **kwargs: {"unique_papers": 1, "total_chunks": 3, "reindex_required": False},
    )
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


def test_stage_b_state_tools_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    artifact = asyncio.run(
        save_research_artifact(
            kind="evidence_table",
            title="Composite Grid",
            content={"topic": "composite damping", "rows": [{"doi": "10.1234/demo"}]},
            source_doi="10.1234/demo",
        )
    )
    queried = asyncio.run(query_research_artifacts(kind="evidence_table", detail=True))
    recorded = asyncio.run(
        manage_failure_cases(
            mode="record",
            failure_type="fetch",
            doi="10.1234/demo",
            query_text="composite damping",
            source="Elsevier TDM",
            error_message="403 paywall",
            context={"stage": "extract"},
        )
    )
    suggestion = asyncio.run(
        manage_failure_cases(
            mode="suggest_retry",
            failure_type="fetch",
            doi="10.1234/demo",
            query_text="composite damping",
            source="Elsevier TDM",
            error_message="403 paywall",
        )
    )

    assert artifact["artifact_id"].startswith("artifact_")
    assert queried["items"][0]["content"]["topic"] == "composite damping"
    assert recorded["failure_id"].startswith("failure_")
    assert any("browser-assisted extraction" in item for item in suggestion["suggestions"])


def test_stage_b_evidence_tools_are_wired_to_local_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.research_tools as research_tools

    documents = [
        {
            "doi": "10.1000/a",
            "safe_doi": "10_1000_a",
            "title": "Paper A",
            "year": "2025",
            "journal": "Composite Structures",
            "section_headings": ["Abstract", "Methods", "References"],
            "cites": ["10.1000/shared", "10.1000/b"],
            "content_markdown": "",
        },
        {
            "doi": "10.1000/b",
            "safe_doi": "10_1000_b",
            "title": "Paper B",
            "year": "2024",
            "journal": "Engineering Reports",
            "section_headings": ["Abstract", "Methods", "References"],
            "cites": ["10.1000/shared"],
            "content_markdown": "",
        },
    ]
    doc_map = {
        "10_1000_a": {
            "doi": "10.1000/a",
            "safe_doi": "10_1000_a",
            "title": "Paper A",
            "authors": ["Smith"],
            "year": "2025",
            "journal": "Composite Structures",
            "section_headings": ["Abstract", "Methods", "Results", "References"],
            "content_markdown": (
                "## Abstract\n\nPaper A studies composite damping.\n\n"
                "## Methods\n\nPaper A uses modal analysis.\n\n"
                "## Results\n\nComposite damping improves vibration attenuation by 18%.\n\n"
                "## References\n\n10.1000/shared\n\n10.1000/b"
            ),
        },
        "10_1000_b": {
            "doi": "10.1000/b",
            "safe_doi": "10_1000_b",
            "title": "Paper B",
            "authors": ["Lee"],
            "year": "2024",
            "journal": "Engineering Reports",
            "section_headings": ["Abstract", "Methods", "References"],
            "content_markdown": (
                "## Abstract\n\nPaper B studies vibration control.\n\n"
                "## Methods\n\nPaper B uses finite-element evaluation.\n\n"
                "## References\n\n10.1000/shared"
            ),
        },
    }

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        doi = kwargs.get("doi", "")
        if "attenuation" in query.lower() or "composite damping" in query.lower():
            if doi and doi != "10.1000/a":
                return []
            return [
                {
                    "doi": "10.1000/a",
                    "safe_doi": "10_1000_a",
                    "title": "Paper A",
                    "authors": ["Smith"],
                    "year": "2025",
                    "journal": "Composite Structures",
                    "section_name": "Results",
                    "snippet": "Composite damping improves vibration attenuation by 18%.",
                    "score": 1.3,
                }
            ]
        return []

    monkeypatch.setattr(research_tools, "list_paper_documents", lambda chroma_dir: documents)
    monkeypatch.setattr(research_tools, "get_paper_document", lambda chroma_dir, safe_doi: doc_map.get(safe_doi))
    monkeypatch.setattr(research_tools, "search_papers", fake_search_papers)

    graph = asyncio.run(get_citation_graph(mode="neighbors", doi="10.1000/a"))
    context = asyncio.run(get_papers_full_context(dois=["10.1000/a"], mode="full", max_total_tokens=500))
    grid = asyncio.run(
        build_evidence_grid(
            topic="composite damping",
            subquestions=["How much attenuation is reported?"],
            dois=["10.1000/a"],
        )
    )
    comparison = asyncio.run(compare_papers(dois=["10.1000/a", "10.1000/b"], focus="methods"))
    audit = asyncio.run(
        audit_draft_support(
            draft_text="Composite damping improves vibration attenuation by 18% [Smith et al., 2025].",
            strictness="strict",
        )
    )

    assert graph["summary"]["cited_local"][0]["doi"] == "10.1000/b"
    assert context["papers"][0]["sections"][0]["content"].startswith("## Abstract")
    assert grid["grids"][0]["rows"][0]["support_strength"] == "high"
    assert "| Paper |" in comparison["rendered"]
    assert audit["claims"][0]["status"] == "supported"
