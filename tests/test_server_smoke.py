from __future__ import annotations

import asyncio
from pathlib import Path

from grados.publisher.common import PublisherMetadata
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
from grados.storage.papers import PaperListEntry, load_paper_record, save_paper_markdown
from grados.storage.vector import PaperSearchResult


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
    assert "author-year citations" in (audit.description or "")
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
    papers_dir = tmp_path / "grados-home" / "papers"
    papers_dir.mkdir(parents=True)
    (papers_dir / "10_1234_demo.md").write_text(
        '---\n'
        'doi: "10.1234/demo"\n'
        'title: "Demo Paper Title"\n'
        'source: "Crossref"\n'
        'year: "2025"\n'
        'journal: "Composite Structures"\n'
        '---\n\n'
        "# Demo Paper Title\n\n"
        "## Abstract\n\n"
        "This study investigates layered composite vibration behavior.\n\n"
        "## Methods\n\n"
        "The methods section contains the experimental procedure.\n",
        encoding="utf-8",
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


def test_read_saved_paper_requires_markdown_mirror_source_of_truth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    result = asyncio.run(read_saved_paper(safe_doi="10_1234_demo", section_query="methods"))

    assert "Paper not found." in result


def test_read_saved_paper_requires_a_locator() -> None:
    result = asyncio.run(read_saved_paper())

    assert "Provide at least one of doi, safe_doi, or uri." in result


def test_get_saved_paper_structure_returns_compact_structure_card(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))
    papers_dir = tmp_path / "grados-home" / "papers"
    papers_dir.mkdir(parents=True)
    (papers_dir / "10_1234_demo.md").write_text(
        '---\n'
        'doi: "10.1234/demo"\n'
        'title: "Demo Paper Title"\n'
        'source: "Crossref"\n'
        'year: "2025"\n'
        'journal: "Composite Structures"\n'
        'authors_json: \'["Alice", "Bob"]\'\n'
        '---\n\n'
        "# Demo Paper Title\n\n"
        "## Abstract\n\n"
        "This study investigates layered composite vibration behavior.\n\n"
        "## Methods\n\n"
        "The methods section contains the experimental procedure.\n\n"
        "## Results\n\n"
        "The results section summarizes the outcome.\n",
        encoding="utf-8",
    )

    result = asyncio.run(get_saved_paper_structure(safe_doi="10_1234_demo"))

    assert result["found"] is True
    assert result["canonical_uri"] == "grados://papers/10_1234_demo"
    assert result["title"] == "Demo Paper Title"
    assert result["preview_excerpt"].startswith("This study investigates")
    assert result["section_headings"] == ["Demo Paper Title", "Abstract", "Methods", "Results"]


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
        lambda papers_dir, chroma_dir=None: [
            PaperListEntry(
                file="10_1234_demo.md",
                doi="10.1234/demo",
                safe_doi="10_1234_demo",
                title="Demo",
            )
        ],
    )
    monkeypatch.setattr(
        vector,
        "get_index_stats",
        lambda chroma_dir, **kwargs: vector.IndexStats(unique_papers=1, total_chunks=3, reindex_required=False),
    )
    monkeypatch.setattr(
        vector,
        "search_papers",
        lambda chroma_dir, query, limit, **kwargs: [
            PaperSearchResult(
                doi="10.1234/demo",
                safe_doi="10_1234_demo",
                title="Demo Paper",
                authors=["Alice Smith"],
                year="2025",
                journal="Composite Structures",
                source="Crossref",
                score=2.1,
                paragraph_start=2,
                paragraph_count=2,
                snippet="Composite vibration damping is discussed in detail.",
            )
        ],
    )
    monkeypatch.setattr(
        papers,
        "read_paper",
        lambda **kwargs: papers.PaperReadResult(
            doi="10.1234/demo",
            text="## Methods\n\nCanonical paragraph window from papers mirror.",
            start_paragraph=2,
            paragraph_count=2,
            total_paragraphs=8,
            truncated=True,
            section_headings=["Abstract", "Methods"],
        ),
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
    assert "Paragraphs: 3–4" in result
    assert "Canonical Excerpt: ## Methods Canonical paragraph window from papers mirror." in result


def test_search_saved_papers_end_to_end_rereads_updated_canonical_excerpt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.storage.vector as vector

    class FakeBackend:
        provider = "test"
        model_id = "test-backend"
        query_prompt_mode = "none"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, query: str) -> list[float]:
            return [1.0, 0.0, 0.0, 0.0]

    monkeypatch.setattr(vector, "load_embedding_backend", lambda config=None: FakeBackend())
    monkeypatch.setattr(vector, "_ensure_index_compatible", lambda *args, **kwargs: None)

    papers_dir = tmp_path / "grados-home" / "papers"
    chroma_dir = tmp_path / "grados-home" / "database" / "chroma"

    save_paper_markdown(
        doi="10.1234/demo-server",
        markdown=(
            "# Composite Damping Study\n\n"
            "## Abstract\n\n"
            "This study investigates laminate damping behaviour.\n\n"
            "## Results\n\n"
            "Indexed wording reports a generic improvement in vibration response.\n\n"
            "## Discussion\n\n"
            "Closing discussion paragraph.\n"
        ),
        papers_dir=papers_dir,
        title="Composite Damping Study",
        source="Crossref",
        chroma_dir=chroma_dir,
    )

    (papers_dir / "10_1234_demo_server.md").write_text(
        '---\n'
        'doi: "10.1234/demo-server"\n'
        'title: "Composite Damping Study"\n'
        'source: "Crossref"\n'
        'fetched_at: "2026-04-15T00:00:00+00:00"\n'
        'extraction_status: "OK"\n'
        "---\n\n"
        "# Composite Damping Study\n\n"
        "## Abstract\n\n"
        "This study investigates laminate damping behaviour.\n\n"
        "## Results\n\n"
        "Canonical mirror excerpt confirms attenuation rose by 18 percent after laminate treatment.\n\n"
        "## Discussion\n\n"
        "Closing discussion paragraph.\n",
        encoding="utf-8",
    )

    result = asyncio.run(search_saved_papers("laminate attenuation treatment"))

    assert "Composite Damping Study" in result
    assert "Canonical Excerpt:" in result
    assert "attenuation rose by 18 percent after laminate treatment" in result.lower()
    assert "indexed wording reports a generic improvement" not in result.lower()


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


def test_extract_paper_full_text_reports_partial_success_when_indexing_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.vector as vector

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            text="# Demo Paper Title\n\n## Abstract\n\n" + ("Composite vibration content. " * 80),
            outcome="native_full_text",
            source="Elsevier TDM",
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    result = asyncio.run(
        extract_paper_full_text(
            doi="10.1234/demo",
            publisher="Elsevier",
            expected_title="Demo Paper Title",
        )
    )

    assert "Paper Extracted with Partial Success" in result
    assert "Index Status:** failed" in result
    assert "saved to papers/ only" in result


def test_extract_paper_full_text_returns_metadata_only_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            outcome="metadata_only",
            source="Elsevier TDM",
            metadata=PublisherMetadata(
                doi="10.1234/demo",
                title="Metadata Only Demo",
                authors=["Alice Smith", "Bob Lee"],
                year="2026",
                journal="Fallback Journal",
                publisher="Elsevier",
            ),
            asset_hints=[{"kind": "article_landing", "url": "https://example.com/article"}],
            warnings=["OA lookup failed", "Browser fallback unavailable"],
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)

    result = asyncio.run(
        extract_paper_full_text(
            doi="10.1234/demo",
            publisher="Elsevier",
        )
    )

    assert "Paper Located but Full Text Unavailable" in result
    assert "Outcome:** metadata_only" in result
    assert "Canonical Save:** not_written" in result
    assert "Metadata Only Demo" in result
    assert "https://example.com/article" in result
    assert not (tmp_path / "grados-home" / "papers" / "10_1234_demo.md").exists()


def test_extract_paper_full_text_persists_typed_metadata_in_frontmatter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.vector as vector

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            text="# Typed Metadata Demo\n\n## Abstract\n\n" + ("Composite vibration content. " * 80),
            outcome="native_full_text",
            source="Elsevier TDM",
            metadata=PublisherMetadata(
                doi="10.1234/demo",
                title="Typed Metadata Demo",
                authors=["Alice Smith", "Bob Lee"],
                year="2025",
                journal="Composite Structures",
                publisher="Elsevier",
            ),
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    asyncio.run(extract_paper_full_text(doi="10.1234/demo"))

    record = load_paper_record(tmp_path / "grados-home" / "papers", doi="10.1234/demo")

    assert record is not None
    assert record.title == "Typed Metadata Demo"
    assert record.authors == ["Alice Smith", "Bob Lee"]
    assert record.year == "2025"
    assert record.journal == "Composite Structures"
    assert record.source == "Elsevier TDM"


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

    papers_dir = tmp_path / "grados-home" / "papers"
    save_paper_markdown(
        "10.1000/a",
        (
            "## Abstract\n\nPaper A studies composite damping.\n\n"
            "## Methods\n\nPaper A uses modal analysis.\n\n"
            "## Results\n\nComposite damping improves vibration attenuation by 18%.\n\n"
            "## References\n\n10.1000/shared\n\n10.1000/b"
        ),
        papers_dir,
        title="Paper A",
        authors=["Smith"],
        year="2025",
        journal="Composite Structures",
    )
    save_paper_markdown(
        "10.1000/b",
        (
            "## Abstract\n\nPaper B studies vibration control.\n\n"
            "## Methods\n\nPaper B uses finite-element evaluation.\n\n"
            "## References\n\n10.1000/shared"
        ),
        papers_dir,
        title="Paper B",
        authors=["Lee"],
        year="2024",
        journal="Engineering Reports",
    )

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        doi = kwargs.get("doi", "")
        if "attenuation" in query.lower() or "composite damping" in query.lower():
            if doi and doi != "10.1000/a":
                return []
            return [
                PaperSearchResult(
                    doi="10.1000/a",
                    safe_doi="10_1000_a",
                    title="Paper A",
                    authors=["Smith"],
                    year="2025",
                    journal="Composite Structures",
                    section_name="Results",
                    snippet="Composite damping improves vibration attenuation by 18%.",
                    score=1.3,
                )
            ]
        return []

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
