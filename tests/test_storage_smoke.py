from __future__ import annotations

from pathlib import Path

from grados.storage.papers import (
    PaperListEntry,
    get_paper_structure,
    list_saved_papers,
    read_paper,
    save_asset_manifest,
    save_paper_markdown,
    save_pdf,
)


def test_save_read_list_and_pdf_workflow(tmp_path: Path, monkeypatch) -> None:
    papers_dir = tmp_path / "papers"
    downloads_dir = tmp_path / "downloads"
    chroma_dir = tmp_path / "database" / "chroma"
    calls: list[dict[str, object]] = []

    import grados.storage.vector as vector

    def fake_index_paper(chroma, doi, safe, title, markdown, **kwargs):
        mirror_path = papers_dir / f"{safe}.md"
        assert mirror_path.is_file()
        saved = mirror_path.read_text(encoding="utf-8")
        assert "# Demo Paper Title" in saved
        assert 'authors_json: \'["Alice", "Bob"]\'' in saved
        calls.append(
            {
                "chroma": str(chroma),
                "doi": doi,
                "safe_doi": safe,
                "title": title,
                "markdown": markdown,
                "kwargs": kwargs,
            }
        )
        return 1

    monkeypatch.setattr(
        vector,
        "index_paper",
        fake_index_paper,
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
        authors=["Alice", "Bob"],
        year="2025",
        journal="Composite Structures",
        chroma_dir=chroma_dir,
    )

    assert Path(summary.file_path).is_file()
    assert summary.safe_doi == "10_1234_demo"
    assert summary.uri == "grados://papers/10_1234_demo"
    saved_content = Path(summary.file_path).read_text(encoding="utf-8")
    assert 'year: "2025"' in saved_content
    assert 'journal: "Composite Structures"' in saved_content
    assert 'authors_json: \'["Alice", "Bob"]\'' in saved_content
    assert calls == [
        {
            "chroma": str(chroma_dir),
            "doi": "10.1234/demo",
            "safe_doi": "10_1234_demo",
            "title": "Demo Paper Title",
            "markdown": markdown,
            "kwargs": {
                "source": "Crossref",
                "fetch_outcome": "",
                "authors": ["Alice", "Bob"],
                "year": "2025",
                "journal": "Composite Structures",
                "section_headings": ["Demo Paper Title", "Abstract", "Methods", "Results"],
                "assets_manifest_path": "",
            },
        }
    ]

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
    assert papers == [
        PaperListEntry(
            file="10_1234_demo.md",
            doi="10.1234/demo",
            title="Demo Paper Title",
            safe_doi="10_1234_demo",
        )
    ]

    pdf_path = save_pdf("10.1234/demo", b"%PDF-1.4\n%stub", downloads_dir)
    assert pdf_path.is_file()


def test_save_paper_markdown_surfaces_index_failure_without_blocking_mirror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"

    import grados.storage.vector as vector

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    summary = save_paper_markdown(
        doi="10.1234/demo",
        markdown="# Demo\n\n## Abstract\n\n" + ("content " * 40),
        papers_dir=papers_dir,
        title="Demo",
        chroma_dir=chroma_dir,
    )

    assert Path(summary.file_path).is_file()
    assert summary.mirror_written is True
    assert summary.index_status == "failed"
    assert "embedding backend unavailable" in summary.index_error


def test_save_paper_markdown_skips_index_when_mirror_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"
    called = False

    import grados.storage.vector as vector

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal called
        called = True
        return 1

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    original_write_text = Path.write_text

    def failing_write_text(self: Path, data: str, encoding: str | None = None, errors=None, newline=None) -> int:
        if self.name == "10_1234_demo.md":
            raise OSError("disk full")
        return original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    try:
        save_paper_markdown(
            doi="10.1234/demo",
            markdown="# Demo\n\n## Abstract\n\ncontent",
            papers_dir=papers_dir,
            title="Demo",
            chroma_dir=chroma_dir,
        )
    except OSError as exc:
        assert "disk full" in str(exc)
    else:
        raise AssertionError("Expected mirror write failure")

    assert called is False
    assert not (papers_dir / "10_1234_demo.md").exists()


def test_read_and_list_require_markdown_mirror_source_of_truth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"

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
                "section_headings": ["Abstract", "Methods", "Results"],
                "word_count": 42,
                "char_count": 256,
                "uri": "grados://papers/10_1234_demo",
                "content_markdown": "# Demo Paper Title",
            }
        ],
    )

    frontmatter_result = read_paper(
        papers_dir=papers_dir,
        safe_doi="10_1234_demo",
        max_paragraphs=2,
        include_front_matter=True,
        chroma_dir=chroma_dir,
    )

    assert frontmatter_result is None

    result = read_paper(
        papers_dir=papers_dir,
        safe_doi="10_1234_demo",
        section_query="methods",
        max_paragraphs=2,
        chroma_dir=chroma_dir,
    )

    assert result is None

    papers = list_saved_papers(papers_dir, chroma_dir=chroma_dir)
    assert papers == []


def test_asset_manifest_is_saved_and_exposed_in_structure_summary(tmp_path: Path, monkeypatch) -> None:
    papers_dir = tmp_path / "papers"

    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    manifest_path = save_asset_manifest(
        doi="10.1234/demo",
        papers_dir=papers_dir,
        source="Elsevier TDM",
        asset_hints=[
            {"kind": "figure_image", "url": "https://example.com/fig1.png"},
            {"kind": "table_html", "url": "https://example.com/table1"},
            {"kind": "object_api_meta", "url": "https://example.com/object"},
        ],
    )

    save_paper_markdown(
        doi="10.1234/demo",
        markdown="# Demo Paper Title\n\n## Abstract\n\nStructured content.",
        papers_dir=papers_dir,
        title="Demo Paper Title",
        source="Elsevier TDM",
        extra_frontmatter={"assets_manifest_path": manifest_path},
    )

    structure = get_paper_structure(
        papers_dir=papers_dir,
        doi="10.1234/demo",
    )

    assert structure is not None
    assert structure.assets_summary.has_assets is True
    assert structure.assets_summary.figures == 1
    assert structure.assets_summary.tables == 1
    assert structure.assets_summary.objects == 1
