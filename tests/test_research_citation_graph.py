from __future__ import annotations

import os
from pathlib import Path

import grados.research.citation_graph as citation_graph
from grados.research.citation_graph import get_citation_graph
from grados.storage.papers import save_paper_markdown


def _write_saved_papers(papers_dir: Path) -> None:
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


def test_get_citation_graph_builds_neighbors_and_common_references(tmp_path: Path) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"
    _write_saved_papers(papers_dir)

    graph = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")
    common = get_citation_graph(
        chroma_dir,
        mode="common_references",
        dois=["10.1000/a", "10.1000/b"],
    )

    assert graph.summary is not None
    assert graph.summary.cited_local[0].doi == "10.1000/b"
    assert graph.summary.cited_external == ["10.1000/shared"]
    assert common.common_references[0].doi == "10.1000/shared"


def test_citation_graph_reuses_cached_local_records_until_papers_change(
    monkeypatch,
    tmp_path: Path,
) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"
    _write_saved_papers(papers_dir)

    real_load_paper_record = citation_graph.load_paper_record
    load_calls: list[str] = []

    def counting_load_paper_record(
        papers_dir: Path,
        doi: str | None = None,
        safe_doi: str | None = None,
        uri: str | None = None,
    ):
        load_calls.append(safe_doi or doi or uri or "")
        return real_load_paper_record(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)

    monkeypatch.setattr(citation_graph, "load_paper_record", counting_load_paper_record)

    first = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")
    second = get_citation_graph(chroma_dir, mode="neighbors", doi="10.1000/a")

    assert first.summary is not None
    assert second.summary is not None
    assert first.summary.cited_local[0].doi == "10.1000/b"
    assert second.summary.cited_local[0].doi == "10.1000/b"
    assert load_calls == ["10_1000_a", "10_1000_b"]


def test_citation_graph_cache_invalidates_when_any_saved_paper_changes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    chroma_dir = tmp_path / "database" / "chroma"
    papers_dir = tmp_path / "papers"
    _write_saved_papers(papers_dir)

    real_load_paper_record = citation_graph.load_paper_record
    load_calls: list[str] = []

    def counting_load_paper_record(
        papers_dir: Path,
        doi: str | None = None,
        safe_doi: str | None = None,
        uri: str | None = None,
    ):
        load_calls.append(safe_doi or doi or uri or "")
        return real_load_paper_record(papers_dir, doi=doi, safe_doi=safe_doi, uri=uri)

    monkeypatch.setattr(citation_graph, "load_paper_record", counting_load_paper_record)

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
