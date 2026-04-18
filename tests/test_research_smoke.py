from __future__ import annotations

import os
from pathlib import Path

from grados.research_state import manage_failure_cases, query_research_artifacts, save_research_artifact
from grados.research_tools import (
    audit_draft_support,
    build_evidence_grid,
    compare_papers,
    get_citation_graph,
    get_papers_full_context,
)
from grados.storage.papers import save_paper_markdown
from grados.storage.vector import PaperSearchResult


def test_research_artifacts_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite3"

    receipt = save_research_artifact(
        db_path,
        kind="evidence_table",
        title="Composite Damping Grid",
        content={"topic": "composite damping", "rows": [{"doi": "10.1234/demo"}]},
        source_doi="10.1234/demo",
        metadata={"query": "composite damping"},
    )

    result = query_research_artifacts(db_path, kind="evidence_table", detail=True)

    assert receipt["artifact_id"].startswith("artifact_")
    assert result["count"] == 1
    assert result["items"][0]["content"]["topic"] == "composite damping"
    assert result["items"][0]["source_doi"] == "10.1234/demo"


def test_failure_memory_records_queries_and_suggests_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite3"

    recorded = manage_failure_cases(
        db_path,
        mode="record",
        failure_type="fetch",
        doi="10.1234/demo",
        query_text="composite damping",
        source="Elsevier TDM",
        error_message="403 paywall",
        context={"stage": "extract"},
    )
    queried = manage_failure_cases(db_path, mode="query", failure_type="fetch")
    suggestion = manage_failure_cases(
        db_path,
        mode="suggest_retry",
        failure_type="fetch",
        doi="10.1234/demo",
        query_text="composite damping",
        source="Elsevier TDM",
        error_message="403 paywall",
    )

    assert recorded["failure_id"].startswith("failure_")
    assert queried["count"] == 1
    assert queried["items"][0]["context"]["stage"] == "extract"
    assert any("browser-assisted extraction" in item for item in suggestion["suggestions"])


def test_citation_graph_full_context_and_compare_papers(monkeypatch, tmp_path: Path) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"

    save_paper_markdown(
        "10.1000/a",
        (
            "## Abstract\n\n"
            "Paper A studies composite damping.\n\n"
            "## Methods\n\n"
            "Paper A uses modal analysis on laminate plates.\n\n"
            "## References\n\n"
            "10.1000/shared\n\n"
            "10.1000/b"
        ),
        papers_dir,
        title="Paper A",
        year="2025",
        journal="Composite Structures",
    )
    save_paper_markdown(
        "10.1000/b",
        (
            "## Abstract\n\n"
            "Paper B studies vibration control.\n\n"
            "## Methods\n\n"
            "Paper B uses finite-element evaluation on sandwich panels.\n\n"
            "## References\n\n"
            "10.1000/shared"
        ),
        papers_dir,
        title="Paper B",
        year="2024",
        journal="Engineering Reports",
    )

    graph = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")
    common = get_citation_graph(
        chroma_dir,
        mode="common_references",
        dois=["10.1000/a", "10.1000/b"],
    )
    context = get_papers_full_context(
        chroma_dir,
        dois=["10.1000/a"],
        mode="full",
        max_total_tokens=500,
    )
    comparison = compare_papers(
        chroma_dir,
        dois=["10.1000/a", "10.1000/b"],
        focus="methods",
        comparison_axes=["method"],
    )

    assert graph.summary is not None
    assert graph.summary.cited_local[0].doi == "10.1000/b"
    assert common.common_references[0].doi == "10.1000/shared"
    assert context.papers[0].sections[0].content.startswith("## Abstract")
    assert "| Paper | method |" in comparison.rendered


def test_citation_graph_reuses_cached_local_records_until_papers_change(monkeypatch, tmp_path: Path) -> None:
    import grados.research_tools as research_tools

    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"

    save_paper_markdown(
        "10.1000/a",
        (
            "## Abstract\n\n"
            "Paper A studies composite damping.\n\n"
            "## References\n\n"
            "10.1000/b"
        ),
        papers_dir,
        title="Paper A",
        year="2025",
        journal="Composite Structures",
    )
    save_paper_markdown(
        "10.1000/b",
        (
            "## Abstract\n\n"
            "Paper B studies vibration control.\n\n"
            "## References\n\n"
            "10.1000/shared"
        ),
        papers_dir,
        title="Paper B",
        year="2024",
        journal="Engineering Reports",
    )

    real_load_paper_record = research_tools.load_paper_record
    load_calls: list[str] = []

    def counting_load_paper_record(
        papers_dir: Path,
        doi: str | None = None,
        safe_doi: str | None = None,
        uri: str | None = None,
    ):
        load_calls.append(safe_doi or doi or uri or "")
        return real_load_paper_record(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)

    monkeypatch.setattr(research_tools, "load_paper_record", counting_load_paper_record)

    first = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")
    second = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")

    assert first.summary is not None
    assert second.summary is not None
    assert first.summary.cited_local[0].doi == "10.1000/b"
    assert second.summary.cited_local[0].doi == "10.1000/b"
    assert load_calls == ["10_1000_a", "10_1000_b"]


def test_citation_graph_cache_invalidates_when_any_saved_paper_changes(monkeypatch, tmp_path: Path) -> None:
    import grados.research_tools as research_tools

    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"

    save_paper_markdown(
        "10.1000/a",
        (
            "## Abstract\n\n"
            "Paper A studies composite damping.\n\n"
            "## References\n\n"
            "10.1000/shared"
        ),
        papers_dir,
        title="Paper A",
        year="2025",
        journal="Composite Structures",
    )
    save_paper_markdown(
        "10.1000/b",
        (
            "## Abstract\n\n"
            "Paper B studies vibration control.\n\n"
            "## References\n\n"
            "10.1000/shared"
        ),
        papers_dir,
        title="Paper B",
        year="2024",
        journal="Engineering Reports",
    )

    real_load_paper_record = research_tools.load_paper_record
    load_calls: list[str] = []

    def counting_load_paper_record(
        papers_dir: Path,
        doi: str | None = None,
        safe_doi: str | None = None,
        uri: str | None = None,
    ):
        load_calls.append(safe_doi or doi or uri or "")
        return real_load_paper_record(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)

    monkeypatch.setattr(research_tools, "load_paper_record", counting_load_paper_record)

    first = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")
    assert first.summary is not None
    assert first.summary.cited_by_local == []
    assert load_calls == ["10_1000_a", "10_1000_b"]

    paper_b_path = papers_dir / "10_1000_b.md"
    previous_mtime = paper_b_path.stat().st_mtime_ns
    save_paper_markdown(
        "10.1000/b",
        (
            "## Abstract\n\n"
            "Paper B studies vibration control.\n\n"
            "## References\n\n"
            "10.1000/shared\n\n"
            "10.1000/a"
        ),
        papers_dir,
        title="Paper B",
        year="2024",
        journal="Engineering Reports",
    )
    current_stat = paper_b_path.stat()
    if current_stat.st_mtime_ns == previous_mtime:
        os.utime(paper_b_path, ns=(current_stat.st_atime_ns, previous_mtime + 1))

    second = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")

    assert second.summary is not None
    assert [item.doi for item in second.summary.cited_by_local] == ["10.1000/b"]
    assert load_calls == ["10_1000_a", "10_1000_b", "10_1000_a", "10_1000_b"]


def test_build_evidence_grid_and_audit_draft_support(monkeypatch, tmp_path: Path) -> None:
    import grados.research_tools as research_tools

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        doi = kwargs.get("doi", "")
        if "composite damping" in query.lower() and doi in {"", "10.1234/demo"}:
            return [
                PaperSearchResult(
                    doi="10.1234/demo",
                    safe_doi="10_1234_demo",
                    title="Composite Damping Study",
                    authors=["Smith", "Lee"],
                    year="2025",
                    journal="Composite Structures",
                    section_name="Results",
                    snippet="Composite damping improved vibration attenuation by 18%.",
                    score=1.35,
                )
            ]
        if "baseline mismatch" in query.lower():
            return [
                PaperSearchResult(
                    doi="10.5555/other",
                    safe_doi="10_5555_other",
                    title="Other Study",
                    authors=["Garcia"],
                    year="2024",
                    journal="Mechanical Systems",
                    section_name="Discussion",
                    snippet="A different baseline was evaluated.",
                    score=0.9,
                )
            ]
        return []

    monkeypatch.setattr(research_tools, "search_papers", fake_search_papers)

    grid = build_evidence_grid(
        tmp_path / "chroma",
        topic="composite damping",
        subquestions=["How much attenuation is reported?"],
        dois=["10.1234/demo"],
    )
    supported = audit_draft_support(
        tmp_path / "chroma",
        draft_text="Composite damping improves vibration attenuation by 18% [Smith et al., 2025].",
        strictness="strict",
    )
    misattributed = audit_draft_support(
        tmp_path / "chroma",
        draft_text="Baseline mismatch is resolved in the experiment [Smith et al., 2025].",
        strictness="strict",
    )
    numeric = audit_draft_support(
        tmp_path / "chroma",
        draft_text="Baseline mismatch is resolved in the experiment [12].",
        citation_style="numeric",
        strictness="strict",
    )

    assert grid.grids[0].rows[0].support_strength == "high"
    assert supported.claims[0].status == "supported"
    assert misattributed.claims[0].status == "misattributed"
    assert numeric.claims[0].status == "weak"


def test_audit_draft_support_deduplicates_repeated_queries(monkeypatch, tmp_path: Path) -> None:
    import grados.research_tools as research_tools

    calls: list[str] = []

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, limit, kwargs)
        calls.append(query)
        return [
            PaperSearchResult(
                doi="10.1234/demo",
                safe_doi="10_1234_demo",
                title="Composite Damping Study",
                authors=["Smith", "Lee"],
                year="2025",
                journal="Composite Structures",
                section_name="Results",
                snippet="Composite damping improved vibration attenuation by 18%.",
                score=1.35,
            )
        ]

    monkeypatch.setattr(research_tools, "search_papers", fake_search_papers)

    repeated_claim = "Composite damping improves vibration attenuation by 18% [Smith et al., 2025]."
    draft_text = "\n\n".join(repeated_claim for _ in range(20))

    result = audit_draft_support(
        tmp_path / "chroma",
        draft_text=draft_text,
        strictness="strict",
    )

    assert result.claims_checked == 20
    assert all(claim.status == "supported" for claim in result.claims)
    assert calls == ["Composite damping improves vibration attenuation by 18% ."]
