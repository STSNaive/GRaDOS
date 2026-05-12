from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from grados.cli import main
from grados.config import IndexingConfig
from grados.storage.fts import ensure_fts_index, fts_index_path, search_fts_blocks
from grados.storage.retrieval import PaperSearchResult
from grados.storage.search_pipeline import search_saved_library


def _write_paper(
    papers_dir: Path,
    safe_doi: str,
    *,
    doi: str,
    title: str,
    body: str,
    authors: list[str] | None = None,
    year: str = "2026",
    journal: str = "Composite Structures",
    source: str = "Crossref",
) -> None:
    papers_dir.mkdir(parents=True, exist_ok=True)
    authors_json = json.dumps(authors or ["Alice Smith"])
    (papers_dir / f"{safe_doi}.md").write_text(
        "---\n"
        f'doi: "{doi}"\n'
        f'title: "{title}"\n'
        f"authors_json: '{authors_json}'\n"
        f'year: "{year}"\n'
        f'journal: "{journal}"\n'
        f'source: "{source}"\n'
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )


def test_fts_index_searches_canonical_markdown_blocks(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"
    _write_paper(
        papers_dir,
        "10_1234_demo",
        doi="10.1234/demo",
        title="Composite Damping Study",
        body=(
            "# Composite Damping Study\n\n"
            "## Results\n\n"
            "Composite vibration damping improved attenuation after laminate treatment.\n"
        ),
    )

    stats = ensure_fts_index(papers_dir=papers_dir, chroma_dir=chroma_dir, force=True)
    results = search_fts_blocks(
        db_path=fts_index_path(chroma_dir),
        query="composite damping attenuation",
        limit=5,
        authors="alice",
        year_from=2025,
        journal="Composite",
    )

    assert stats.paper_count == 1
    assert stats.block_count == 1
    assert len(results) == 1
    assert results[0].safe_doi == "10_1234_demo"
    assert results[0].retriever == "fts_bm25"
    assert results[0].rank == 1
    assert results[0].section_name == "Results"
    assert results[0].heading_path == "Composite Damping Study > Results"
    assert "laminate treatment" in results[0].text


def test_search_pipeline_falls_back_to_fts_when_dense_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"
    _write_paper(
        papers_dir,
        "10_1234_fallback",
        doi="10.1234/fallback",
        title="Fallback Retrieval Study",
        body="# Fallback Retrieval Study\n\n## Abstract\n\nFTS fallback finds composite damping plus evidence.\n",
    )

    import grados.storage.vector as vector

    def dense_unavailable(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(vector, "search_papers", dense_unavailable)

    result = search_saved_library(
        chroma_dir=chroma_dir,
        papers_dir=papers_dir,
        query="composite damping evidence",
        limit=3,
        indexing_config=IndexingConfig(),
    )

    assert result.mode == "fts"
    assert result.retrievers == ["fts_bm25"]
    assert "Dense retriever unavailable" in result.warnings[0]
    assert result.results[0].safe_doi == "10_1234_fallback"
    assert result.results[0].mode == "fts"
    assert result.results[0].retriever == "fts_bm25"
    assert result.results[0].query == "composite damping evidence"
    assert result.results[0].trace["block_id"].startswith("10_1234_fallback::")


def test_search_pipeline_dense_only_does_not_fallback_when_dense_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"
    _write_paper(
        papers_dir,
        "10_1234_dense_only",
        doi="10.1234/dense-only",
        title="Dense Only Study",
        body="# Dense Only Study\n\n## Abstract\n\nFTS would otherwise find this fallback marker.\n",
    )

    import grados.storage.vector as vector

    def dense_unavailable(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(vector, "search_papers", dense_unavailable)

    result = search_saved_library(
        chroma_dir=chroma_dir,
        papers_dir=papers_dir,
        query="fallback marker",
        limit=3,
        use_reranking=False,
        indexing_config=IndexingConfig(),
    )

    assert result.mode == "dense_only"
    assert result.retrievers == []
    assert result.results == []
    assert "dense_only returned no results" in result.warnings[0]
    assert not fts_index_path(chroma_dir).exists()


def test_search_pipeline_hybrid_rrf_marks_retriever_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    papers_dir = tmp_path / "papers"
    chroma_dir = tmp_path / "database" / "chroma"
    _write_paper(
        papers_dir,
        "10_1234_hybrid",
        doi="10.1234/hybrid",
        title="Hybrid Retrieval Study",
        body="# Hybrid Retrieval Study\n\n## Results\n\nHybrid RRF combines dense and FTS damping candidates.\n",
    )

    import grados.storage.vector as vector

    monkeypatch.setattr(
        vector,
        "search_papers",
        lambda *args, **kwargs: [
            PaperSearchResult(
                doi="10.1234/hybrid",
                safe_doi="10_1234_hybrid",
                title="Hybrid Retrieval Study",
                authors=["Alice Smith"],
                score=0.9,
                dense_score=0.9,
                paragraph_start=1,
                paragraph_count=2,
                snippet="Dense candidate.",
            )
        ],
    )

    result = search_saved_library(
        chroma_dir=chroma_dir,
        papers_dir=papers_dir,
        query="hybrid damping candidates",
        limit=3,
        indexing_config=IndexingConfig(),
    )

    assert result.mode == "hybrid_rrf"
    assert set(result.retrievers) >= {"dense", "fts_bm25"}
    assert result.results[0].mode == "hybrid_rrf"
    assert result.results[0].retriever == "rrf"
    assert result.results[0].rank == 1
    assert result.results[0].block_id.startswith("10_1234_hybrid::")
    assert set(result.results[0].trace["retrievers"]) >= {"dense", "fts_bm25"}


def test_eval_retrieval_cli_reports_fixture_metrics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "grados-home"
    papers_dir = home / "papers"
    chroma_dir = home / "database" / "chroma"
    _write_paper(
        papers_dir,
        "10_1234_eval",
        doi="10.1234/eval",
        title="Eval Retrieval Study",
        body="# Eval Retrieval Study\n\n## Results\n\nLaminate attenuation treatment improved damping.\n",
    )
    fixture = tmp_path / "retrieval_eval.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "question": "laminate attenuation treatment",
                "gold_papers": ["10.1234/eval"],
                "acceptable_windows": [
                    {"safe_doi": "10_1234_eval", "paragraph_start": 1, "paragraph_count": 2}
                ],
                "answerability": True,
            }
        )
        + "\n"
        + json.dumps(
            {
                "question": "nonexistent catalyst marker",
                "gold_papers": [],
                "answerability": "no-answer",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "search_papers", lambda *args, **kwargs: [])

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["eval-retrieval", "--fixture", str(fixture), "--k", "3", "--json-output"],
        env={"GRADOS_HOME": str(home)},
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["cases"] == 2
    assert payload["summary"]["recall_at_k"] == 1.0
    assert payload["summary"]["mrr_at_k"] == 1.0
    assert payload["summary"]["block_hit_rate"] == 1.0
    assert payload["summary"]["no_answer_false_positive_rate"] == 0.0
    assert payload["summary"]["verify_window_readable_rate"] == 1.0
    assert payload["cases"][0]["top_results"][0]["mode"] == "fts"
    assert chroma_dir.parent.joinpath("fts.sqlite3").is_file()
