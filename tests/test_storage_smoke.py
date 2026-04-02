from __future__ import annotations

from pathlib import Path

from grados.storage.papers import list_saved_papers, read_paper, save_paper_markdown, save_pdf


def test_save_read_list_and_pdf_workflow(tmp_path: Path, monkeypatch) -> None:
    papers_dir = tmp_path / "papers"
    downloads_dir = tmp_path / "downloads"
    chroma_dir = tmp_path / "database" / "chroma"
    calls: list[tuple[str, str, str, str]] = []

    import grados.storage.vector as vector

    monkeypatch.setattr(
        vector,
        "index_paper",
        lambda chroma, doi, safe, title, markdown: calls.append((str(chroma), doi, safe, title)) or 1,
    )

    markdown = (
        "# Demo Paper Title\n\n"
        "## Abstract\n\n"
        "This study investigates layered composite vibration behavior.\n\n"
        "## Methods\n\n"
        "The methods section contains the experimental procedure.\n\n"
        "## Results\n\n"
        "The results section summarizes the outcome."
    )
    summary = save_paper_markdown(
        doi="10.1234/demo",
        markdown=markdown,
        papers_dir=papers_dir,
        title="Demo Paper Title",
        source="Crossref",
        chroma_dir=chroma_dir,
    )

    assert Path(summary.file_path).is_file()
    assert summary.safe_doi == "10_1234_demo"
    assert summary.uri == "grados://papers/10_1234_demo"
    assert calls == [(str(chroma_dir), "10.1234/demo", "10_1234_demo", "Demo Paper Title")]

    read_result = read_paper(
        papers_dir=papers_dir,
        doi="10.1234/demo",
        section_query="methods",
        max_paragraphs=2,
    )
    assert read_result is not None
    assert "Methods" in read_result.text
    assert read_result.start_paragraph > 0

    papers = list_saved_papers(papers_dir)
    assert papers == [{
        "file": "10_1234_demo.md",
        "doi": "10.1234/demo",
        "title": "Demo Paper Title",
        "safe_doi": "10_1234_demo",
    }]

    pdf_path = save_pdf("10.1234/demo", b"%PDF-1.4\n%stub", downloads_dir)
    assert pdf_path.is_file()
