from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from grados.config import GRaDOSPaths, IndexingConfig, generate_default_config
from grados.publisher.common import PublisherMetadata, safe_doi_filename
from grados.search.academic import PaperMetadata
from grados.search.resumable import ResumableSearchResult
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
    parse_pdf_file,
    query_research_artifacts,
    read_paper_asset,
    read_saved_paper,
    save_research_artifact,
    search_academic_papers,
    search_saved_papers,
)
from grados.storage.frontmatter import read_frontmatter_metadata_from_file
from grados.storage.papers import PaperListEntry, load_paper_record, save_paper_markdown
from grados.storage.vector import PaperSearchResult


def test_server_registers_expected_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool_names = sorted(tool.name for tool in tools)

    assert tool_names == [
        "audit_answer_against_pack",
        "audit_draft_support",
        "audit_external_synthesis_result",
        "build_evidence_grid",
        "compare_papers",
        "extract_paper_full_text",
        "get_citation_graph",
        "get_papers_full_context",
        "get_saved_paper_structure",
        "import_local_pdf_library",
        "ingest_codex_downloaded_pdf",
        "manage_failure_cases",
        "parse_pdf_file",
        "plan_library_pdf_cleanup",
        "prepare_evidence_pack",
        "prepare_external_synthesis_from_topic",
        "prepare_external_synthesis_packet",
        "preview_external_synthesis_packet",
        "query_research_artifacts",
        "read_evidence_pack",
        "read_paper_asset",
        "read_saved_paper",
        "run_external_synthesis",
        "save_external_synthesis_result",
        "save_paper_to_zotero",
        "save_research_artifact",
        "search_academic_papers",
        "search_saved_papers",
        "suggest_missing_evidence",
        "verify_evidence_pack",
    ]


def test_tool_metadata_exposes_clearer_llm_contracts() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    search_remote = tools["search_academic_papers"]
    assert "metadata only" in (search_remote.description or "")
    assert search_remote.parameters["properties"]["query"]["minLength"] == 1
    assert search_remote.parameters["properties"]["limit"]["maximum"] == 50
    assert "indepth" in search_remote.parameters["properties"]

    extract = tools["extract_paper_full_text"]
    assert "compact save receipt" in (extract.description or "")
    assert "force_refresh=true" in extract.parameters["properties"]["force_refresh"]["description"]
    assert "does not change fetch routing" in extract.parameters["properties"]["publisher"]["description"]

    read_tool = tools["read_saved_paper"]
    assert "Provide one of `doi`, `safe_doi`, or `uri`" in (read_tool.description or "")
    assert read_tool.parameters["properties"]["start_paragraph"]["minimum"] == 0
    assert read_tool.parameters["properties"]["max_paragraphs"]["maximum"] == 100

    asset_tool = tools["read_paper_asset"]
    assert "figures, tables, formulas" in (asset_tool.description or "")
    assert asset_tool.parameters["properties"]["limit"]["maximum"] == 100
    assert "include_image" in asset_tool.parameters["properties"]

    search_saved = tools["search_saved_papers"]
    assert "screening/reranking material" in (search_saved.description or "")
    assert search_saved.parameters["properties"]["limit"]["maximum"] == 25

    artifact = tools["save_research_artifact"]
    assert "reusable intermediate outputs" in (artifact.description or "")
    assert "search_snapshot" in artifact.parameters["properties"]["kind"]["description"]
    assert "project_id" not in artifact.parameters["properties"]

    full_context = tools["get_papers_full_context"]
    assert "context-budgeted batch" in (full_context.description or "")
    assert "additional batches" in (full_context.description or "")
    assert full_context.parameters["properties"]["max_total_tokens"]["maximum"] == 128000

    audit = tools["audit_draft_support"]
    assert "claim-level `verified`, `minor_distortion`, `major_distortion`" in (audit.description or "")
    assert "revision actions" in (audit.description or "")
    assert audit.parameters["properties"]["draft_text"]["minLength"] == 1
    assert audit.parameters["properties"]["candidate_limit"]["maximum"] == 25
    assert "project_id" not in tools["query_research_artifacts"].parameters["properties"]
    assert "project_id" not in audit.parameters["properties"]

    pack_audit = tools["audit_answer_against_pack"]
    assert "include_suggestions" in pack_audit.parameters["properties"]

    save_external = tools["save_external_synthesis_result"]
    assert save_external.parameters["properties"]["audit"]["default"] is True


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


def test_search_academic_papers_warns_when_continuation_token_is_not_applied(monkeypatch) -> None:
    import grados.server_tools.search_tools as search_tools

    async def fake_run_resumable_search(**kwargs):  # noqa: ANN003
        assert kwargs["continuation_token"] == "stale-token"
        return ResumableSearchResult(
            query=kwargs["query"],
            limit=kwargs["limit"],
            results=[
                PaperMetadata(
                    title="Composite Damping Study",
                    doi="10.1234/demo",
                    source="Crossref",
                )
            ],
            has_more=False,
            exhausted_sources=["Crossref"],
            next_continuation_token=None,
            warnings=[],
            continuation_applied=False,
        )

    monkeypatch.setattr(
        search_tools,
        "get_paths_and_config",
        lambda: (
            None,
            SimpleNamespace(
                search=SimpleNamespace(order=["Crossref"], enabled={"Crossref": True}),
                academic_etiquette_email="research@example.edu",
            ),
        ),
    )
    monkeypatch.setattr(search_tools, "get_api_keys", lambda config: {})
    monkeypatch.setattr("grados.search.resumable.run_resumable_search", fake_run_resumable_search)

    result = asyncio.run(search_academic_papers("composite damping", continuation_token="stale-token"))

    assert "continuation_token was not applied" in result
    assert "Results restarted from page 1." in result
    assert "Composite Damping Study" in result


def test_search_academic_papers_upserts_remote_metadata(tmp_path: Path, monkeypatch) -> None:
    import grados.server_tools.search_tools as search_tools
    import grados.storage.remote_metadata as remote_metadata

    calls: list[dict[str, object]] = []

    async def fake_run_resumable_search(**kwargs):  # noqa: ANN003
        return ResumableSearchResult(
            query=kwargs["query"],
            limit=kwargs["limit"],
            results=[
                PaperMetadata(
                    title="Composite Damping Study",
                    doi="10.1234/demo",
                    abstract="A semantically searchable abstract.",
                    authors=["Alice Smith"],
                    year="2026",
                    source="Crossref",
                    url="https://doi.org/10.1234/demo",
                )
            ],
            has_more=False,
            exhausted_sources=["Crossref"],
            next_continuation_token=None,
            warnings=[],
            continuation_applied=True,
        )

    def fake_upsert(metadata_dir, records, *, indexing_config=None):  # noqa: ANN001
        calls.append(
            {
                "metadata_dir": metadata_dir,
                "records": records,
                "indexing_config": indexing_config,
            }
        )
        return len(records)

    monkeypatch.setattr(
        search_tools,
        "get_paths_and_config",
        lambda: (
            SimpleNamespace(
                database_chroma=tmp_path / "grados-home" / "database" / "chroma",
                database_remote_metadata=tmp_path / "grados-home" / "database" / "remote_metadata",
            ),
            SimpleNamespace(
                search=SimpleNamespace(order=["Crossref"], enabled={"Crossref": True}),
                academic_etiquette_email="research@example.edu",
                indexing=object(),
            ),
        ),
    )
    monkeypatch.setattr(search_tools, "get_api_keys", lambda config: {})
    monkeypatch.setattr("grados.search.resumable.run_resumable_search", fake_run_resumable_search)
    monkeypatch.setattr(remote_metadata, "upsert_remote_metadata", fake_upsert)

    result = asyncio.run(search_academic_papers("composite damping"))

    assert "Composite Damping Study" in result
    assert "Local State:" in result
    assert "fetch_status=" in result
    assert len(calls) == 1
    assert calls[0]["metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert calls[0]["records"][0].doi == "10.1234/demo"


def test_search_academic_papers_indepth_writes_checkpoint_and_summary(tmp_path: Path, monkeypatch) -> None:
    import grados.server_tools.search_tools as search_tools
    import grados.storage.remote_metadata as remote_metadata
    from grados.storage.papers import save_paper_markdown

    home = tmp_path / "grados-home"
    paths = SimpleNamespace(
        papers=home / "papers",
        database_chroma=home / "database" / "chroma",
        database_remote_metadata=home / "database" / "remote_metadata",
        research_checkpoints=home / "research_checkpoints",
        paper_summaries=home / "paper_summaries",
    )
    config = SimpleNamespace(
        search=SimpleNamespace(order=["Crossref"], enabled={"Crossref": True}),
        research=SimpleNamespace(indepth=SimpleNamespace(enabled=True, auto_summarize=True)),
        academic_etiquette_email="research@example.edu",
        indexing=IndexingConfig(),
    )

    async def fake_run_resumable_search(**kwargs):  # noqa: ANN003
        return ResumableSearchResult(
            query=kwargs["query"],
            limit=kwargs["limit"],
            results=[
                PaperMetadata(
                    title="Indepth Demo",
                    doi="10.1234/indepth",
                    abstract="A candidate for indepth extraction.",
                    year="2026",
                    source="Crossref",
                )
            ],
            has_more=False,
            exhausted_sources=["Crossref"],
            next_continuation_token=None,
            warnings=[],
            continuation_applied=True,
        )

    async def fake_extract_paper_full_text(**kwargs):  # noqa: ANN003
        expected_safe = safe_doi_filename(kwargs["doi"])
        save_paper_markdown(
            doi=kwargs["doi"],
            markdown=(
                "# Indepth Demo\n\n"
                "## Abstract\n\n"
                "This paper studies composite damping.\n\n"
                "## Methods\n\n"
                "The method uses a beam experiment.\n\n"
                "## Results\n\n"
                "The results show improved damping.\n\n"
                "## Limitations\n\n"
                "Only one material family is tested.\n"
            ),
            papers_dir=paths.papers,
            title="Indepth Demo",
        )
        return (
            "## Paper Extracted Successfully\n\n"
            "- **DOI:** 10.1234/indepth\n"
            f"- **Paper ID:** {expected_safe}\n"
            f"- **Safe DOI:** {expected_safe}\n"
            "- **Fetch Status:** fulltext\n"
            "- **Has Fulltext:** true\n"
            "- **Index Status:** indexed\n"
        )

    monkeypatch.setattr(search_tools, "get_paths_and_config", lambda: (paths, config))
    monkeypatch.setattr(search_tools, "get_api_keys", lambda loaded_config: {})
    monkeypatch.setattr("grados.search.resumable.run_resumable_search", fake_run_resumable_search)
    monkeypatch.setattr("grados.server_tools.library_tools.extract_paper_full_text", fake_extract_paper_full_text)
    monkeypatch.setattr(remote_metadata, "upsert_remote_metadata", lambda *args, **kwargs: 1)
    monkeypatch.setattr(remote_metadata, "get_remote_metadata_by_doi", lambda *args, **kwargs: None)

    result = asyncio.run(search_academic_papers("composite damping", limit=1))

    checkpoint_files = list(paths.research_checkpoints.glob("*/checkpoint.json"))
    assert "Indepth Checkpoint" in result
    assert checkpoint_files
    assert (paths.paper_summaries / f"{safe_doi_filename('10.1234/indepth')}.json").is_file()
    checkpoint = checkpoint_files[0].read_text(encoding="utf-8")
    assert "paper_summary_id" in checkpoint
    assert "fulltext" in checkpoint


def test_search_academic_papers_indepth_records_failed_extraction(tmp_path: Path, monkeypatch) -> None:
    import grados.server_tools.search_tools as search_tools
    import grados.storage.remote_metadata as remote_metadata

    home = tmp_path / "grados-home"
    paths = SimpleNamespace(
        papers=home / "papers",
        database_chroma=home / "database" / "chroma",
        database_remote_metadata=home / "database" / "remote_metadata",
        research_checkpoints=home / "research_checkpoints",
        paper_summaries=home / "paper_summaries",
    )
    config = SimpleNamespace(
        search=SimpleNamespace(order=["Crossref"], enabled={"Crossref": True}),
        research=SimpleNamespace(indepth=SimpleNamespace(enabled=True, auto_summarize=True)),
        academic_etiquette_email="research@example.edu",
        indexing=IndexingConfig(),
    )
    remote_calls: list[dict[str, object]] = []

    async def fake_run_resumable_search(**kwargs):  # noqa: ANN003
        return ResumableSearchResult(
            query=kwargs["query"],
            limit=kwargs["limit"],
            results=[
                PaperMetadata(
                    title="Failed Indepth Demo",
                    doi="10.1234/failed-indepth",
                    abstract="A candidate for failed indepth extraction.",
                    year="2026",
                    source="Crossref",
                )
            ],
            has_more=False,
            exhausted_sources=["Crossref"],
            next_continuation_token=None,
            warnings=[],
            continuation_applied=True,
        )

    async def fake_extract_paper_full_text(**kwargs):  # noqa: ANN003
        raise RuntimeError("publisher timeout")

    monkeypatch.setattr(search_tools, "get_paths_and_config", lambda: (paths, config))
    monkeypatch.setattr(search_tools, "get_api_keys", lambda loaded_config: {})
    monkeypatch.setattr("grados.search.resumable.run_resumable_search", fake_run_resumable_search)
    monkeypatch.setattr("grados.server_tools.library_tools.extract_paper_full_text", fake_extract_paper_full_text)
    monkeypatch.setattr(remote_metadata, "upsert_remote_metadata", lambda *args, **kwargs: 1)
    monkeypatch.setattr(remote_metadata, "get_remote_metadata_by_doi", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        remote_metadata,
        "record_remote_fetch_result",
        lambda metadata_dir, **kwargs: remote_calls.append({"metadata_dir": metadata_dir, **kwargs}) or 1,
    )

    result = asyncio.run(search_academic_papers("composite damping", limit=1))

    checkpoint_files = list(paths.research_checkpoints.glob("*/checkpoint.json"))
    assert "indepth extraction failed for 10.1234/failed-indepth" in result
    assert checkpoint_files
    checkpoint = checkpoint_files[0].read_text(encoding="utf-8")
    assert "failed" in checkpoint
    assert "publisher timeout" in checkpoint
    assert remote_calls[0]["metadata_dir"] == paths.database_remote_metadata
    assert remote_calls[0]["fetch_status"] == "failed"
    assert remote_calls[0]["has_fulltext"] is False


def test_search_academic_papers_indepth_uses_search_limit_without_hidden_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import grados.server_tools.search_tools as search_tools
    import grados.storage.remote_metadata as remote_metadata
    from grados.storage.papers import save_paper_markdown

    home = tmp_path / "grados-home"
    paths = SimpleNamespace(
        papers=home / "papers",
        database_chroma=home / "database" / "chroma",
        database_remote_metadata=home / "database" / "remote_metadata",
        research_checkpoints=home / "research_checkpoints",
        paper_summaries=home / "paper_summaries",
    )
    config = SimpleNamespace(
        search=SimpleNamespace(order=["Crossref"], enabled={"Crossref": True}),
        research=SimpleNamespace(indepth=SimpleNamespace(enabled=True, auto_summarize=False)),
        academic_etiquette_email="research@example.edu",
        indexing=IndexingConfig(),
    )
    extracted: list[str] = []

    async def fake_run_resumable_search(**kwargs):  # noqa: ANN003
        return ResumableSearchResult(
            query=kwargs["query"],
            limit=kwargs["limit"],
            results=[
                PaperMetadata(
                    title=f"Indepth Demo {index}",
                    doi=f"10.1234/indepth-{index}",
                    abstract="A candidate for indepth extraction.",
                    year="2026",
                    source="Crossref",
                )
                for index in range(10)
            ],
            has_more=False,
            exhausted_sources=["Crossref"],
            next_continuation_token=None,
            warnings=[],
            continuation_applied=True,
        )

    async def fake_extract_paper_full_text(**kwargs):  # noqa: ANN003
        doi = kwargs["doi"]
        extracted.append(doi)
        expected_safe = safe_doi_filename(doi)
        save_paper_markdown(
            doi=doi,
            markdown=(
                f"# Indepth Demo {doi}\n\n"
                "## Abstract\n\n"
                "This paper studies composite damping.\n\n"
                "## Results\n\n"
                "The results show improved damping.\n"
            ),
            papers_dir=paths.papers,
            title=f"Indepth Demo {doi}",
        )
        return (
            "## Paper Extracted Successfully\n\n"
            f"- **DOI:** {doi}\n"
            f"- **Paper ID:** {expected_safe}\n"
            f"- **Safe DOI:** {expected_safe}\n"
            "- **Fetch Status:** fulltext\n"
            "- **Has Fulltext:** true\n"
            "- **Index Status:** indexed\n"
        )

    monkeypatch.setattr(search_tools, "get_paths_and_config", lambda: (paths, config))
    monkeypatch.setattr(search_tools, "get_api_keys", lambda loaded_config: {})
    monkeypatch.setattr("grados.search.resumable.run_resumable_search", fake_run_resumable_search)
    monkeypatch.setattr("grados.server_tools.library_tools.extract_paper_full_text", fake_extract_paper_full_text)
    monkeypatch.setattr(remote_metadata, "upsert_remote_metadata", lambda *args, **kwargs: 1)
    monkeypatch.setattr(remote_metadata, "get_remote_metadata_by_doi", lambda *args, **kwargs: None)

    result = asyncio.run(search_academic_papers("composite damping", limit=10))

    assert len(extracted) == 10
    assert "Candidates processed: 10" in result
    assert "first 8" not in result


def test_search_saved_papers_rejects_invalid_year_range() -> None:
    result = asyncio.run(search_saved_papers("composite vibration", year_from=2025, year_to=2024))

    assert "Invalid year range" in result


def test_read_saved_paper_requires_canonical_markdown_source_of_truth(tmp_path: Path, monkeypatch) -> None:
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


def test_read_saved_paper_and_asset_tool_expose_parser_assets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.storage.vector as vector
    from grados.storage.assets import PendingAsset
    from grados.storage.papers import save_asset_bundle, save_paper_markdown

    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)
    paths = GRaDOSPaths()
    bundle = save_asset_bundle(
        doi="10.1234/demo",
        papers_dir=paths.papers,
        source="MinerU",
        assets=[
            PendingAsset(
                kind="figure",
                role="content",
                source_ref="images/fig1.png",
                filename="fig1.png",
                data=b"image-bytes",
                caption="Figure 1. Demo",
                page=2,
            ),
            PendingAsset(kind="table", role="content", html="<table><tr><td>A</td></tr></table>", csv="A\n1\n"),
            PendingAsset(kind="formula", role="content", latex="E = mc^2", page=3),
            PendingAsset(kind="page", role="page", filename="page.png", data=b"page-bytes"),
        ],
    )
    markdown = "# Demo\n\nSee ![Figure](images/fig1.png).\n\nFormula: E = mc^2"
    for original, replacement in bundle.markdown_rewrites.items():
        markdown = markdown.replace(original, replacement)
    summary = save_paper_markdown(
        doi="10.1234/demo",
        markdown=markdown,
        papers_dir=paths.papers,
        title="Demo",
        extra_frontmatter={"assets_manifest_path": bundle.manifest_path},
    )

    read_result = asyncio.run(read_saved_paper(safe_doi=summary.safe_doi, max_paragraphs=5))
    assert "### Asset References" in read_result
    assert "`fig_001`" in read_result

    listed = asyncio.run(read_paper_asset(safe_doi=summary.safe_doi))
    assert listed["found"] is True
    assert [asset["asset_id"] for asset in listed["assets"]] == ["fig_001", "table_001", "formula_001"]

    table = asyncio.run(read_paper_asset(safe_doi=summary.safe_doi, asset_id="table_001"))
    assert table["found"] is True
    assert "<table>" in table["table_html"]

    pages = asyncio.run(read_paper_asset(safe_doi=summary.safe_doi, include_pages=True))
    assert any(asset["asset_id"] == "page_001" for asset in pages["assets"])


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
                dense_score=1.2,
                lexical_score=0.9,
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
            text="## Methods\n\nCanonical paragraph window from papers file.",
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
    assert '"canonical_uri": "grados://papers/10_1234_demo"' in result
    assert '"paragraph_start": 2' in result
    assert '"paragraph_count": 2' in result
    assert '"dense_score": 1.2' in result
    assert '"lexical_score": 0.9' in result
    assert "Canonical Excerpt: ## Methods Canonical paragraph window from papers file." in result


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

    saved = save_paper_markdown(
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

    Path(saved.file_path).write_text(
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
        "Canonical paper excerpt confirms attenuation rose by 18 percent after laminate treatment.\n\n"
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
    import grados.storage.remote_metadata as remote_metadata
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
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    captured: dict[str, object] = {}

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        captured["indexing_config"] = kwargs.get("indexing_config")
        return 1

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    result = asyncio.run(
        extract_paper_full_text(
            doi="10.1234/demo",
            publisher="Elsevier",
            expected_title="Demo Paper Title",
        )
    )

    manifest_file = (
        tmp_path
        / "grados-home"
        / "papers"
        / "_assets"
        / f"{safe_doi_filename('10.1234/demo')}.json"
    )
    assert "Paper Extracted Successfully" in result
    assert manifest_file.is_file()
    assert isinstance(captured["indexing_config"], IndexingConfig)


def test_extract_paper_full_text_reports_partial_success_when_indexing_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            text="# Demo Paper Title\n\n## Abstract\n\n" + ("Composite vibration content. " * 80),
            outcome="native_full_text",
            source="Elsevier TDM",
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)

    captured: dict[str, object] = {}
    remote_calls: list[dict[str, object]] = []

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        captured["indexing_config"] = kwargs.get("indexing_config")
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)
    monkeypatch.setattr(
        remote_metadata,
        "record_remote_fetch_result",
        lambda metadata_dir, **kwargs: remote_calls.append({"metadata_dir": metadata_dir, **kwargs}) or 1,
    )

    result = asyncio.run(
        extract_paper_full_text(
            doi="10.1234/demo",
            publisher="Elsevier",
            expected_title="Demo Paper Title",
        )
    )

    assert "Paper Extracted with Partial Success" in result
    assert "Index Status:** failed" in result
    assert "Fetch Status:** partial_success" in result
    assert "saved to papers/ only" in result
    assert isinstance(captured["indexing_config"], IndexingConfig)
    assert remote_calls[0]["fetch_status"] == "partial_success"


def test_extract_paper_full_text_repairs_qa_failure_with_next_fetch_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    fetch_orders: list[list[str]] = []

    async def fake_fetch_paper(**kwargs):
        fetch_order = list(kwargs.get("fetch_order") or [])
        fetch_orders.append(fetch_order)
        if fetch_order and fetch_order[0] == "api":
            return fetch_module.FetchResult(
                text="# Wrong Paper\n\n## Abstract\n\n" + ("wrong composite damping content. " * 80),
                outcome="native_full_text",
                source="Elsevier TDM",
                via="api",
                metadata=PublisherMetadata(
                    doi="10.1234/demo",
                    title="API Metadata Title",
                    authors=["Alice Smith"],
                    year="2026",
                    journal="Composite Structures",
                    publisher="Elsevier",
                ),
                warnings=["api candidate was short"],
            )
        return fetch_module.FetchResult(
            text="# Repair Success\n\n## Abstract\n\n" + ("validated composite damping content. " * 80),
            outcome="native_full_text",
            source="Browser",
            via="browser",
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda markdown, *args: "Repair Success" in markdown)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    result = asyncio.run(
        extract_paper_full_text(
            doi="10.1234/demo",
            expected_title="Repair Success",
        )
    )

    assert "Paper Extracted Successfully" in result
    assert "Source:** Browser" in result
    assert "QA rejected Elsevier TDM" in result
    assert "Fetch Status:** fulltext" in result
    assert fetch_orders[0][0] == "api"
    assert fetch_orders[1] == ["browser", "codex", "scihub"]
    record = load_paper_record(tmp_path / "grados-home" / "papers", doi="10.1234/demo")
    assert record is not None
    assert record.title == "API Metadata Title"
    assert record.authors == ["Alice Smith"]
    assert record.year == "2026"
    assert record.journal == "Composite Structures"


def test_extract_paper_full_text_returns_metadata_only_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.storage.remote_metadata as remote_metadata

    calls: list[dict[str, object]] = []

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
            warnings=["Unpaywall lookup failed", "Browser fallback unavailable"],
        )

    def fake_record_remote_fetch_result(metadata_dir, **kwargs):  # noqa: ANN001, ANN003
        calls.append({"metadata_dir": metadata_dir, **kwargs})
        return 1

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", fake_record_remote_fetch_result)

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
    assert len(calls) == 1
    assert calls[0]["metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert calls[0]["fetch_status"] == "metadata_only"
    assert calls[0]["has_fulltext"] is False


def test_extract_paper_full_text_reuses_saved_paper_unless_force_refresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    papers_dir = tmp_path / "grados-home" / "papers"
    save_paper_markdown(
        doi="10.1234/demo",
        markdown="# Demo Paper Title\n\n## Abstract\n\nSaved canonical text.",
        papers_dir=papers_dir,
        title="Demo Paper Title",
        source="Existing",
    )
    fetch_calls: list[dict[str, object]] = []

    async def fake_fetch_paper(**kwargs):
        fetch_calls.append(kwargs)
        return fetch_module.FetchResult(
            text="# Demo Paper Title\n\n## Abstract\n\n" + ("Refreshed content. " * 80),
            outcome="native_full_text",
            source="Elsevier TDM",
        )

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    reused = asyncio.run(extract_paper_full_text(doi="10.1234/demo"))
    refreshed = asyncio.run(extract_paper_full_text(doi="10.1234/demo", force_refresh=True))

    assert "Paper Already Saved" in reused
    assert "Next Action:** read_saved_paper" in reused
    assert "force_refresh=true" in reused
    assert len(fetch_calls) == 1
    assert "Paper Extracted Successfully" in refreshed


def test_extract_paper_full_text_records_challenge_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.storage.remote_metadata as remote_metadata

    calls: list[dict[str, object]] = []

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            outcome="failed",
            source="Browser",
            via="browser",
            state="challenge",
            manual=True,
            host="www.sciencedirect.com",
            resume={
                "kind": "browser_profile",
                "doi": "10.1234/demo",
                "host": "www.sciencedirect.com",
                "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
                "profile_dir": str(tmp_path / "grados-home" / "browser" / "profile"),
                "action": "complete_publisher_verification_then_retry",
            },
            metadata=PublisherMetadata(
                doi="10.1234/demo",
                title="Challenge Demo",
                publisher="Elsevier",
            ),
            trace=[{"via": "browser", "state": "challenge", "host": "www.sciencedirect.com"}],
            warnings=["Browser automation: publisher_challenge"],
        )

    def fake_record_remote_fetch_result(metadata_dir, **kwargs):  # noqa: ANN001, ANN003
        calls.append({"metadata_dir": metadata_dir, **kwargs})
        return 1

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", fake_record_remote_fetch_result)

    result = asyncio.run(extract_paper_full_text(doi="10.1234/demo"))

    assert "Failed to fetch paper: 10.1234/demo" in result
    assert "Manual Browser Resume" in result
    assert "www.sciencedirect.com" in result
    assert len(calls) == 1
    assert calls[0]["metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert calls[0]["fetch_status"] == "challenge"
    assert calls[0]["fetch_via"] == "browser"
    assert calls[0]["fetch_state"] == "challenge"
    assert calls[0]["fetch_host"] == "www.sciencedirect.com"
    assert calls[0]["fetch_manual"] is True
    assert isinstance(calls[0]["fetch_resume"], dict)
    assert calls[0]["fetch_resume"]["kind"] == "browser_profile"
    assert calls[0]["fetch_trace"] == [
        {"via": "browser", "state": "challenge", "host": "www.sciencedirect.com"}
    ]
    assert calls[0]["has_fulltext"] is False


def test_extract_paper_full_text_returns_codex_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.storage.remote_metadata as remote_metadata

    calls: list[dict[str, object]] = []

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            outcome="host_action_required",
            source="Codex Chrome Extension",
            via="codex",
            state="host_action_required",
            manual=True,
            host="Google Chrome",
            resume={
                "kind": "codex",
                "doi": "10.1234/demo",
                "browser": "Google Chrome",
                "start_url": "https://doi.org/10.1234/demo",
                "issued_at": "2026-05-11T00:00:00+00:00",
                "download_watch_dir": str(tmp_path / "Downloads"),
                "download_max_age_seconds": "900",
                "action": "download_pdf_with_chrome_extension_then_call_ingest_codex_downloaded_pdf",
                "next_action": "download_with_chrome_extension_then_call_ingest_codex_downloaded_pdf",
                "required_host_plugin": "@chrome",
                "required_host_backend": "Codex Chrome plugin extension backend",
                "requested_route": "codex_chrome_plugin_extension",
                "documentation_url": "https://developers.openai.com/codex/app/chrome-extension",
            },
            warnings=["Codex Chrome extension host action required"],
        )

    def fake_record_remote_fetch_result(metadata_dir, **kwargs):  # noqa: ANN001, ANN003
        calls.append({"metadata_dir": metadata_dir, **kwargs})
        return 1

    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", fake_record_remote_fetch_result)

    result = asyncio.run(extract_paper_full_text(doi="10.1234/demo"))

    assert "Codex Chrome Extension Download" in result
    assert "Google Chrome" in result
    assert "Required Host Plugin:** @chrome" in result
    assert "Required Host Backend:** Codex Chrome plugin extension backend" in result
    assert "Requested Route:** codex_chrome_plugin_extension" in result
    assert "https://developers.openai.com/codex/app/chrome-extension" in result
    assert "ingest_codex_downloaded_pdf(doi=..., downloaded_file_path=...)" in result
    assert "parse_pdf_file(file_path=..., doi=..., copy_to_library=true" in result
    assert "Manual Browser Resume" not in result
    assert len(calls) == 1
    assert calls[0]["fetch_status"] == "host_action_required"
    assert calls[0]["fetch_via"] == "codex"
    assert calls[0]["fetch_state"] == "host_action_required"
    assert calls[0]["fetch_manual"] is True
    assert isinstance(calls[0]["fetch_resume"], dict)
    assert calls[0]["fetch_resume"]["kind"] == "codex"
    assert calls[0]["fetch_resume"]["browser"] == "Google Chrome"
    assert (
        calls[0]["fetch_resume"]["next_action"]
        == "download_with_chrome_extension_then_call_ingest_codex_downloaded_pdf"
    )
    assert calls[0]["has_fulltext"] is False


def test_extract_paper_full_text_resume_browser_uses_saved_resume(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.storage.remote_metadata as remote_metadata

    fetch_calls: list[dict[str, object]] = []
    metadata_calls: list[dict[str, object]] = []

    def fake_get_remote_metadata_by_doi(metadata_dir, doi):  # noqa: ANN001
        metadata_calls.append({"metadata_dir": metadata_dir, "doi": doi, "op": "get"})
        return SimpleNamespace(
            fetch_status="challenge",
            fetch_manual=True,
            fetch_resume=(
                '{"kind": "browser_profile", "doi": "10.1234/demo", '
                '"host": "www.sciencedirect.com", '
                '"url": "https://www.sciencedirect.com/science/article/pii/S1234567890"}'
            ),
        )

    async def fake_fetch_paper(**kwargs):
        fetch_calls.append(kwargs)
        return fetch_module.FetchResult(
            outcome="metadata_only",
            source="Browser",
            via="browser",
            state="partial",
            metadata=PublisherMetadata(
                doi="10.1234/demo",
                title="Resume Demo",
                publisher="Elsevier",
            ),
        )

    def fake_record_remote_fetch_result(metadata_dir, **kwargs):  # noqa: ANN001, ANN003
        metadata_calls.append({"metadata_dir": metadata_dir, "op": "record", **kwargs})
        return 1

    monkeypatch.setattr(remote_metadata, "get_remote_metadata_by_doi", fake_get_remote_metadata_by_doi)
    monkeypatch.setattr(fetch_module, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", fake_record_remote_fetch_result)

    result = asyncio.run(extract_paper_full_text(doi="10.1234/demo", resume_browser=True))

    assert "Paper Located but Full Text Unavailable" in result
    assert len(fetch_calls) == 1
    assert fetch_calls[0]["browser_resume"] == {
        "kind": "browser_profile",
        "doi": "10.1234/demo",
        "host": "www.sciencedirect.com",
        "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
    }
    assert len(metadata_calls) == 2
    assert metadata_calls[0]["metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert metadata_calls[0]["op"] == "get"
    assert metadata_calls[1]["metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert metadata_calls[1]["fetch_via"] == "browser"


def test_extract_paper_full_text_persists_typed_metadata_in_frontmatter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.extract.fetch as fetch_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    calls: list[dict[str, object]] = []

    async def fake_fetch_paper(**kwargs):
        return fetch_module.FetchResult(
            text="# Typed Metadata Demo\n\n## Abstract\n\n" + ("Composite vibration content. " * 80),
            outcome="native_full_text",
            source="Elsevier TDM",
            via="api",
            state="ok",
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
    monkeypatch.setattr(
        remote_metadata,
        "record_remote_fetch_result",
        lambda metadata_dir, **kwargs: calls.append({"metadata_dir": metadata_dir, **kwargs}) or 1,
    )

    result = asyncio.run(extract_paper_full_text(doi="10.1234/demo"))

    record = load_paper_record(tmp_path / "grados-home" / "papers", doi="10.1234/demo")

    assert record is not None
    assert record.title == "Typed Metadata Demo"
    assert record.authors == ["Alice Smith", "Bob Lee"]
    assert record.year == "2025"
    assert record.journal == "Composite Structures"
    assert record.source == "Elsevier TDM"
    assert record.corpus == "canonical"
    assert record.tier == "stable"
    assert "Via:** api" in result
    assert "State:** ok" in result
    assert len(calls) == 1
    assert calls[0]["metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert calls[0]["fetch_status"] == "fulltext"
    assert calls[0]["has_fulltext"] is True


def test_parse_pdf_file_returns_preview_and_qa_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))
    pdf_path = tmp_path / "preview.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%preview")

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module

    async def fake_parse_pdf(*args, **kwargs):
        return parse_module.ParsePipelineResult(
            markdown="# Preview Demo\n\n## Abstract\n\nToo short.",
            parser_used="PyMuPDF",
            warnings=["parser emitted partial text"],
            debug=["fallback:pymupdf"],
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: False)

    result = asyncio.run(parse_pdf_file(file_path=str(pdf_path), expected_title="Preview Demo"))

    assert "## PDF Parsed" in result
    assert "Parser Used:** PyMuPDF" in result
    assert "QA validation failed — content may be incomplete." in result
    assert "parser emitted partial text" in result
    assert "fallback:pymupdf" in result


def test_parse_pdf_file_rejects_oversized_local_pdf(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["security"]["max_local_pdf_bytes"] = 8
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GRADOS_HOME", str(home))

    pdf_path = tmp_path / "too-large.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n" + b"x" * 32)

    result = asyncio.run(parse_pdf_file(file_path=str(pdf_path)))

    assert "PDF file is too large" in result
    assert "Local PDF" in result


def test_parse_pdf_file_rejects_local_pdf_that_exceeds_limit_during_read(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["security"]["max_local_pdf_bytes"] = 12
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GRADOS_HOME", str(home))

    pdf_path = tmp_path / "grows-during-read.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n" + b"x" * 32)
    resolved_pdf_path = pdf_path.resolve()
    original_stat = Path.stat

    def fake_stat(self: Path, *args, **kwargs) -> os.stat_result:  # noqa: ANN002, ANN003
        file_stat = original_stat(self, *args, **kwargs)
        if self == resolved_pdf_path:
            values = list(file_stat)
            values[6] = 8
            return os.stat_result(values)
        return file_stat

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError("parse_pdf_file should use bounded stream reads")

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    result = asyncio.run(parse_pdf_file(file_path=str(pdf_path)))

    assert "PDF file is too large" in result
    assert "Local PDF" in result


def test_parse_pdf_file_does_not_materialize_when_parse_fails(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    monkeypatch.setenv("GRADOS_HOME", str(home))
    doi = "10.1234/parse-fails"
    pdf_path = paths.downloads / "publisher-name.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%parse-fails")

    import grados.extract.parse as parse_module

    async def fake_parse_pdf(*args, **kwargs):
        return parse_module.ParsePipelineResult(
            markdown="",
            parser_used="Docling",
            warnings=["parser failed"],
            debug=["docling:failed"],
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)

    result = asyncio.run(parse_pdf_file(file_path=str(pdf_path), doi=doi, copy_to_library=True))

    assert "All parsers failed" in result
    assert "parser failed" in result
    assert pdf_path.is_file()
    assert not (paths.downloads / f"{safe_doi_filename(doi)}.pdf").exists()


def test_parse_pdf_file_persists_canonical_markdown_and_reports_partial_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))
    pdf_path = tmp_path / "saved.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%saved")

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_parse_pdf(*args, **kwargs):
        return parse_module.ParsePipelineResult(
            markdown="# Saved Demo\n\n## Abstract\n\n" + ("Composite vibration content. " * 80),
            parser_used="Docling",
        )

    captured: dict[str, object] = {}

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        captured["indexing_config"] = kwargs.get("indexing_config")
        raise RuntimeError("embedding backend unavailable")

    def fake_record_remote_fetch_result(metadata_dir, **kwargs):  # noqa: ANN001, ANN003
        captured["remote_metadata_dir"] = metadata_dir
        captured["remote_metadata"] = kwargs
        return 1

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", fake_record_remote_fetch_result)
    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    result = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Saved Demo",
            doi="10.1234/local-parse",
            copy_to_library=True,
            acquisition_via="codex",
        )
    )

    assert "PDF Parsed & Saved with Partial Success" in result
    assert "Canonical PDF:**" in result
    assert "PDF Materialization:** copied" in result
    assert "Acquisition Via:** codex" in result
    assert "Index Status:** failed" in result
    assert "saved to papers/ only" in result
    assert isinstance(captured["indexing_config"], IndexingConfig)
    assert captured["remote_metadata_dir"] == tmp_path / "grados-home" / "database" / "remote_metadata"
    assert captured["remote_metadata"]["fetch_status"] == "partial_success"
    assert captured["remote_metadata"]["fetch_via"] == "codex"
    assert captured["remote_metadata"]["source"] == "Codex Chrome Extension"

    record = load_paper_record(tmp_path / "grados-home" / "papers", doi="10.1234/local-parse")
    assert record is not None
    assert record.title == "Saved Demo"
    frontmatter = read_frontmatter_metadata_from_file(
        tmp_path / "grados-home" / "papers" / f"{record.safe_doi}.md"
    )
    assert "acquisition_via" not in frontmatter
    assert "original_pdf_path" not in frontmatter
    assert "copied_pdf_path" not in frontmatter
    assert "source_pdf_hash" not in frontmatter
    assert "fetch_outcome" not in frontmatter
    parsed_manifest_path = tmp_path / "grados-home" / "papers" / frontmatter["parsed_manifest_path"]
    parsed_manifest = json.loads(parsed_manifest_path.read_text())
    assert parsed_manifest["input_pdf_path"] == str(pdf_path.resolve())
    assert parsed_manifest["input_pdf_hash"]
    assert parsed_manifest["canonical_pdf_path"].endswith(f"{record.safe_doi}.pdf")
    assert parsed_manifest["materialization_action"] == "copied"
    assert parsed_manifest["materialization_outcome"] == "success"
    assert parsed_manifest["parse_outcome"] == "success"
    assert (tmp_path / "grados-home" / "downloads" / f"{record.safe_doi}.pdf").is_file()


def test_parse_pdf_file_returns_in_progress_and_reconciles_background_save(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["parsing"]["foreground_wait_seconds"] = 0.01
    config["extract"]["parsing"]["attempt_stale_seconds"] = 30.0
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GRADOS_HOME", str(home))
    pdf_path = tmp_path / "slow.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%slow")
    doi = "10.1234/slow-parse"
    started = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_parse_pdf(*args, **kwargs):  # noqa: ANN002, ANN003
        calls["count"] += 1
        started.set()
        release.wait(timeout=2.0)
        return parse_module.ParsePipelineResult(
            markdown="# Slow Parse\n\n## Abstract\n\n" + ("background parser content. " * 80),
            parser_used="MinerU",
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)

    first = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Slow Parse",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )
    assert started.wait(timeout=1.0)
    assert first.startswith("## PDF Parse Accepted")
    assert "Outcome:** parse_in_progress" in first
    assert "Source PDF Hash:**" in first

    second = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Slow Parse",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )
    assert second.startswith("## PDF Parse Accepted")
    assert calls["count"] == 1

    release.set()
    deadline = time.monotonic() + 2.0
    record = None
    while time.monotonic() < deadline:
        record = load_paper_record(paths.papers, doi=doi)
        if record is not None:
            break
        time.sleep(0.02)

    assert record is not None
    third = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Slow Parse",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )

    assert third.startswith("## Paper Already Saved")
    assert calls["count"] == 1
    frontmatter = read_frontmatter_metadata_from_file(paths.papers / f"{record.safe_doi}.md")
    assert "source_pdf_hash" not in frontmatter
    assert "acquisition_via" not in frontmatter


def test_parse_pdf_file_returns_already_saved_without_reparsing(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    monkeypatch.setenv("GRADOS_HOME", str(home))
    doi = "10.1234/already-local"
    save_paper_markdown(
        doi,
        "# Already Local\n\n## Abstract\n\n" + ("saved content. " * 80),
        paths.papers,
        title="Already Local",
    )
    pdf_path = tmp_path / "already.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%already")

    import grados.extract.parse as parse_module

    async def forbidden_parse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("already saved DOI should not be reparsed")

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", forbidden_parse)

    result = asyncio.run(parse_pdf_file(file_path=str(pdf_path), doi=doi, copy_to_library=True))

    assert result.startswith("## Paper Already Saved")


def test_parse_pdf_file_restarts_stale_running_attempt_without_worker(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["parsing"]["foreground_wait_seconds"] = 1.0
    config["extract"]["parsing"]["attempt_stale_seconds"] = 1.0
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GRADOS_HOME", str(home))
    pdf_path = tmp_path / "stale.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stale")
    doi = "10.1234/stale-parse"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    from grados.storage.parse_attempts import build_parse_attempt_id, upsert_running_parse_attempt

    parser_config = {
        "order": ["Docling", "MinerU", "PyMuPDF"],
        "enabled": {"Docling": True, "MinerU": True, "PyMuPDF": True},
        "marker_timeout": 120000,
        "mineru_timeout": 300000,
        "mineru_poll_interval": 3.0,
        "mineru_model_version": "vlm",
        "mineru_language": "en",
        "mineru_enable_formula": True,
        "mineru_enable_table": True,
        "mineru_is_ocr": False,
    }
    attempt_id = build_parse_attempt_id(
        doi=doi,
        input_pdf_hash=pdf_hash,
        copy_to_library=True,
        acquisition_via="codex",
        parser_config=parser_config,
    )
    upsert_running_parse_attempt(
        paths.database_state,
        attempt_id=attempt_id,
        doi=doi,
        input_pdf_path=str(pdf_path.resolve()),
        input_pdf_name=pdf_path.name,
        input_pdf_hash=pdf_hash,
        copy_to_library=True,
        acquisition_via="codex",
        expected_title="Stale Parse",
        parser_config=parser_config,
    )
    with sqlite3.connect(paths.database_state) as conn:
        conn.execute(
            "UPDATE parse_attempts SET updated_at = ? WHERE attempt_id = ?",
            ("2000-01-01T00:00:00+00:00", attempt_id),
        )

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    async def fake_parse_pdf(*args, **kwargs):  # noqa: ANN002, ANN003
        return parse_module.ParsePipelineResult(
            markdown="# Stale Parse\n\n## Abstract\n\n" + ("restarted background parser content. " * 80),
            parser_used="Docling",
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)

    result = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Stale Parse",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )

    assert result.startswith("## PDF Parsed & Saved")
    record = load_paper_record(paths.papers, doi=doi)
    assert record is not None


def test_parse_pdf_file_failed_attempt_waits_then_retries_after_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    config = generate_default_config(paths)
    config["extract"]["parsing"]["foreground_wait_seconds"] = 1.0
    config["extract"]["parsing"]["attempt_stale_seconds"] = 60.0
    paths.config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GRADOS_HOME", str(home))
    pdf_path = tmp_path / "failed-then-retry.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%failed-then-retry")
    doi = "10.1234/failed-then-retry"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    parser_config = {
        "order": ["Docling", "MinerU", "PyMuPDF"],
        "enabled": {"Docling": True, "MinerU": True, "PyMuPDF": True},
        "marker_timeout": 120000,
        "mineru_timeout": 300000,
        "mineru_poll_interval": 3.0,
        "mineru_model_version": "vlm",
        "mineru_language": "en",
        "mineru_enable_formula": True,
        "mineru_enable_table": True,
        "mineru_is_ocr": False,
    }

    from grados.storage.parse_attempts import build_parse_attempt_id, fail_parse_attempt, upsert_running_parse_attempt

    attempt_id = build_parse_attempt_id(
        doi=doi,
        input_pdf_hash=pdf_hash,
        copy_to_library=True,
        acquisition_via="codex",
        parser_config=parser_config,
    )
    upsert_running_parse_attempt(
        paths.database_state,
        attempt_id=attempt_id,
        doi=doi,
        input_pdf_path=str(pdf_path.resolve()),
        input_pdf_name=pdf_path.name,
        input_pdf_hash=pdf_hash,
        copy_to_library=True,
        acquisition_via="codex",
        expected_title="Failed Then Retry",
        parser_config=parser_config,
    )
    fail_parse_attempt(
        paths.database_state,
        attempt_id,
        receipt_text="All parsers failed for: failed-then-retry.pdf",
        failure_reason="parse_failed",
        error_message="All parsers failed",
    )

    import grados.extract.parse as parse_module
    import grados.extract.qa as qa_module
    import grados.storage.remote_metadata as remote_metadata
    import grados.storage.vector as vector

    calls = {"count": 0}

    async def fake_parse_pdf(*args, **kwargs):  # noqa: ANN002, ANN003
        calls["count"] += 1
        return parse_module.ParsePipelineResult(
            markdown="# Failed Then Retry\n\n## Abstract\n\n" + ("recovered parser content. " * 80),
            parser_used="Docling",
        )

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", fake_parse_pdf)
    monkeypatch.setattr(qa_module, "is_valid_paper_content", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_metadata, "record_remote_fetch_result", lambda *args, **kwargs: 1)
    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: None)

    retry_wait = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Failed Then Retry",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )
    assert retry_wait.startswith("## PDF Parse Accepted")
    assert "Outcome:** parse_retry_wait" in retry_wait
    assert calls["count"] == 0

    with sqlite3.connect(paths.database_state) as conn:
        conn.execute(
            "UPDATE parse_attempts SET updated_at = ? WHERE attempt_id = ?",
            ("2000-01-01T00:00:00+00:00", attempt_id),
        )

    recovered = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Failed Then Retry",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )

    assert recovered.startswith("## PDF Parsed & Saved")
    assert calls["count"] == 1
    assert load_paper_record(paths.papers, doi=doi) is not None


def test_parse_pdf_file_keeps_materialization_conflict_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    paths.config_file.write_text(json.dumps(generate_default_config(paths)), encoding="utf-8")
    monkeypatch.setenv("GRADOS_HOME", str(home))
    pdf_path = tmp_path / "conflict.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%conflict")
    doi = "10.1234/conflict-terminal"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    parser_config = {
        "order": ["Docling", "MinerU", "PyMuPDF"],
        "enabled": {"Docling": True, "MinerU": True, "PyMuPDF": True},
        "marker_timeout": 120000,
        "mineru_timeout": 300000,
        "mineru_poll_interval": 3.0,
        "mineru_model_version": "vlm",
        "mineru_language": "en",
        "mineru_enable_formula": True,
        "mineru_enable_table": True,
        "mineru_is_ocr": False,
    }

    from grados.storage.parse_attempts import build_parse_attempt_id, fail_parse_attempt, upsert_running_parse_attempt

    attempt_id = build_parse_attempt_id(
        doi=doi,
        input_pdf_hash=pdf_hash,
        copy_to_library=True,
        acquisition_via="codex",
        parser_config=parser_config,
    )
    upsert_running_parse_attempt(
        paths.database_state,
        attempt_id=attempt_id,
        doi=doi,
        input_pdf_path=str(pdf_path.resolve()),
        input_pdf_name=pdf_path.name,
        input_pdf_hash=pdf_hash,
        copy_to_library=True,
        acquisition_via="codex",
        expected_title="Conflict Terminal",
        parser_config=parser_config,
    )
    conflict_receipt = "## PDF Materialization Conflict\n\n- **DOI:** 10.1234/conflict-terminal"
    fail_parse_attempt(
        paths.database_state,
        attempt_id,
        receipt_text=conflict_receipt,
        failure_reason="pdf_materialization_conflict",
        error_message="PDF materialization conflict",
    )
    with sqlite3.connect(paths.database_state) as conn:
        conn.execute(
            "UPDATE parse_attempts SET updated_at = ? WHERE attempt_id = ?",
            ("2000-01-01T00:00:00+00:00", attempt_id),
        )

    import grados.extract.parse as parse_module

    async def forbidden_parse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("materialization conflicts should not auto-retry")

    monkeypatch.setattr(parse_module, "parse_pdf_with_diagnostics", forbidden_parse)

    result = asyncio.run(
        parse_pdf_file(
            file_path=str(pdf_path),
            expected_title="Conflict Terminal",
            doi=doi,
            copy_to_library=True,
            acquisition_via="codex",
        )
    )

    assert result == conflict_receipt


def test_stage_b_state_tools_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    artifact = asyncio.run(
        save_research_artifact(
            kind="evidence_grid",
            title="Composite Grid",
            content={"topic": "composite damping", "rows": [{"doi": "10.1234/demo"}]},
            source_doi="10.1234/demo",
        )
    )
    queried = asyncio.run(query_research_artifacts(kind="evidence_grid", detail=True))
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


def test_external_synthesis_tools_respect_config_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    from grados.server_tools.research_tools_api import preview_external_synthesis_packet

    result = asyncio.run(preview_external_synthesis_packet(pack_id="pack_missing"))

    assert result["ok"] is False
    assert result["error"] == "external_synthesis_disabled"
    assert result["sendable"] is False


def test_stage_b_evidence_tools_are_wired_to_local_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRADOS_HOME", str(tmp_path / "grados-home"))

    import grados.research.draft_audit as draft_audit
    import grados.research.evidence_grid as evidence_grid

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
                    paragraph_start=4,
                    paragraph_count=1,
                    snippet="Composite damping improves vibration attenuation by 18%.",
                    score=1.3,
                )
            ]
        return []

    monkeypatch.setattr(evidence_grid, "search_papers", fake_search_papers)
    monkeypatch.setattr(draft_audit, "search_papers", fake_search_papers)

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
    assert audit["claims"][0]["verdict"] == "verified"
