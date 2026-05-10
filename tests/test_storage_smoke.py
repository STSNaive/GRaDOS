from __future__ import annotations

import json
from pathlib import Path

from grados.config import IndexingConfig
from grados.publisher.common import safe_doi_filename
from grados.storage.chunking import extract_reference_dois, split_paragraphs
from grados.storage.frontmatter import read_frontmatter_metadata
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
        paper_path = papers_dir / f"{safe}.md"
        assert paper_path.is_file()
        saved = paper_path.read_text(encoding="utf-8")
        metadata = read_frontmatter_metadata(saved)
        assert "# Demo Paper Title" in saved
        assert json.loads(metadata["authors_json"]) == ["Alice", "Bob"]
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
    expected_safe = safe_doi_filename("10.1234/demo")
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
    assert summary.safe_doi == expected_safe
    assert summary.uri == f"grados://papers/{expected_safe}"
    saved_content = Path(summary.file_path).read_text(encoding="utf-8")
    metadata = read_frontmatter_metadata(saved_content)
    assert metadata["year"] == "2025"
    assert metadata["journal"] == "Composite Structures"
    assert json.loads(metadata["authors_json"]) == ["Alice", "Bob"]
    assert metadata["corpus"] == "canonical"
    assert metadata["tier"] == "stable"
    assert metadata["workset_id"] == ""
    assert metadata["promoted_at"] == ""
    assert metadata["promote_reason"] == ""
    assert calls == [
        {
            "chroma": str(chroma_dir),
            "doi": "10.1234/demo",
            "safe_doi": expected_safe,
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
                "corpus": "canonical",
                "tier": "stable",
                "workset_id": "",
                "promoted_at": "",
                "promote_reason": "",
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
            file=f"{expected_safe}.md",
            doi="10.1234/demo",
            title="Demo Paper Title",
            safe_doi=expected_safe,
        )
    ]

    pdf_path = save_pdf("10.1234/demo", b"%PDF-1.4\n%stub", downloads_dir)
    assert pdf_path.is_file()


def test_safe_doi_filename_avoids_legacy_slug_collisions() -> None:
    first = safe_doi_filename("10.1000/a-b")
    second = safe_doi_filename("10.1000/a_b")

    assert first != second
    assert first.startswith("10_1000_a_b__")
    assert second.startswith("10_1000_a_b__")


def test_read_paper_rejects_traversal_selectors(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    (tmp_path / "secret.md").write_text("# Secret\n\noutside", encoding="utf-8")

    assert read_paper(papers_dir=papers_dir, safe_doi="../secret") is None
    assert read_paper(papers_dir=papers_dir, uri="grados://papers/../secret") is None
    assert get_paper_structure(papers_dir=papers_dir, safe_doi="../secret") is None


def test_read_paper_resolves_legacy_safe_doi_from_doi(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    (papers_dir / "10_1234_demo.md").write_text(
        '---\ndoi: "10.1234/demo"\ntitle: "Legacy Demo"\n---\n\n# Legacy Demo\n\nBody paragraph.',
        encoding="utf-8",
    )

    result = read_paper(papers_dir=papers_dir, doi="10.1234/demo")

    assert result is not None
    assert result.doi == "10.1234/demo"
    assert "Body paragraph" in result.text


def test_collision_safe_saves_do_not_overwrite(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    first = save_paper_markdown(
        doi="10.1000/a-b",
        markdown="# First\n\nFirst body paragraph.",
        papers_dir=papers_dir,
        title="First",
    )
    second = save_paper_markdown(
        doi="10.1000/a_b",
        markdown="# Second\n\nSecond body paragraph.",
        papers_dir=papers_dir,
        title="Second",
    )

    assert first.safe_doi != second.safe_doi
    assert Path(first.file_path).read_text(encoding="utf-8") != Path(second.file_path).read_text(encoding="utf-8")
    first_read = read_paper(papers_dir=papers_dir, doi="10.1000/a-b")
    second_read = read_paper(papers_dir=papers_dir, doi="10.1000/a_b")
    assert first_read is not None
    assert second_read is not None
    assert first_read.doi == "10.1000/a-b"
    assert second_read.doi == "10.1000/a_b"


def test_save_paper_markdown_passes_indexing_config_to_indexer(tmp_path: Path, monkeypatch) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"
    indexing_config = IndexingConfig(chunk_min_chars=20, chunk_max_chars=80)
    captured: dict[str, object] = {}

    import grados.storage.vector as vector

    def fake_index_paper(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        captured["indexing_config"] = kwargs.get("indexing_config")
        return 1

    monkeypatch.setattr(vector, "index_paper", fake_index_paper)

    save_paper_markdown(
        doi="10.1234/indexing-config",
        markdown="# Demo\n\n## Abstract\n\nConfig-aware indexing.",
        papers_dir=papers_dir,
        title="Demo",
        chroma_dir=chroma_dir,
        indexing_config=indexing_config,
    )

    assert captured["indexing_config"] is indexing_config


def test_list_saved_papers_reads_complete_frontmatter_header(tmp_path: Path, monkeypatch) -> None:
    papers_dir = tmp_path / "papers"

    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)

    long_abstract = (
        "Background:\n"
        + "Long context line with colon: retained.\n" * 40
        + "Closing line for semantic parse."
    )

    expected_safe = safe_doi_filename("10.7777/long-header")
    save_paper_markdown(
        doi="10.7777/long-header",
        markdown="# Long Header Demo\n\n## Abstract\n\nBody content.",
        papers_dir=papers_dir,
        title="Long Header Demo",
        extra_frontmatter={"abstract": long_abstract},
    )

    assert list_saved_papers(papers_dir) == [
        PaperListEntry(
            file=f"{expected_safe}.md",
            doi="10.7777/long-header",
            title="Long Header Demo",
            safe_doi=expected_safe,
        )
    ]


def test_chunking_helpers_strip_frontmatter_and_normalize_reference_dois() -> None:
    markdown = (
        "---\n"
        'title: "Demo"\n'
        "doi: 10.5555/demo\n"
        "---\n\n"
        "# Demo\n\n"
        "## References\n\n"
        "10.1000/Foo).\n\n"
        "10.1000/foo\n\n"
        "10.1000/bar;"
    )

    assert split_paragraphs(markdown, include_front_matter=False) == [
        "# Demo",
        "## References",
        "10.1000/Foo).",
        "10.1000/foo",
        "10.1000/bar;",
    ]
    assert extract_reference_dois(markdown) == ["10.1000/foo", "10.1000/bar"]


def test_save_paper_markdown_surfaces_index_failure_without_blocking_canonical_write(
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


def test_save_paper_markdown_skips_index_when_canonical_write_fails(
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
    expected_safe = safe_doi_filename("10.1234/demo")

    def failing_write_text(self: Path, data: str, encoding: str | None = None, errors=None, newline=None) -> int:
        if self.name == f"{expected_safe}.md":
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
        raise AssertionError("Expected canonical paper write failure")

    assert called is False
    assert not (papers_dir / f"{expected_safe}.md").exists()


def test_read_and_list_require_canonical_markdown_source_of_truth(
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
    )

    assert frontmatter_result is None

    result = read_paper(
        papers_dir=papers_dir,
        safe_doi="10_1234_demo",
        section_query="methods",
        max_paragraphs=2,
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
