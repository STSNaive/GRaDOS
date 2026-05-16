from __future__ import annotations

import json
from pathlib import Path

from grados.publisher.common import safe_doi_filename
from grados.research_checkpoint import (
    EvidenceAnchor,
    ResearchCheckpointPaper,
    generate_paper_summary,
    make_research_checkpoint,
    paper_summary_status,
    write_research_checkpoint,
)
from grados.storage.papers import save_paper_markdown


def test_research_checkpoint_writes_json_and_markdown(tmp_path: Path) -> None:
    checkpoint = make_research_checkpoint(
        user_question="How do composites damp vibration?",
        search_queries=["composite vibration damping"],
        papers=[
            ResearchCheckpointPaper(
                doi="10.1234/demo",
                safe_doi="10_1234_demo",
                paper_id="10_1234_demo",
                title="Demo",
                fetch_status="fulltext",
                paper_uri="grados://papers/10_1234_demo",
            )
        ],
        evidence_anchors=[
            EvidenceAnchor(
                doi="10.1234/demo",
                safe_doi="10_1234_demo",
                canonical_uri="grados://papers/10_1234_demo",
                section_name="Results",
                paragraph_start=4,
                paragraph_count=2,
                claim="Composite damping improves vibration attenuation.",
                support_reason="Results paragraph reports attenuation.",
            )
        ],
    )

    folder = write_research_checkpoint(tmp_path / "research_checkpoints", checkpoint)

    assert folder.name.endswith(folder.name.split("_")[-1])
    assert (folder / "checkpoint.json").is_file()
    assert (folder / "checkpoint.md").is_file()
    payload = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["research_run_id"].startswith("run_")
    assert payload["conversation_id"].startswith("research_")
    assert payload["papers"][0]["paper_uri"] == "grados://papers/10_1234_demo"
    rendered = (folder / "checkpoint.md").read_text(encoding="utf-8")
    assert "Research Run ID" in rendered
    assert "Evidence Discipline" in rendered


def test_paper_summary_generation_and_stale_detection(tmp_path: Path, monkeypatch) -> None:
    import grados.storage.vector as vector

    monkeypatch.setattr(vector, "index_paper", lambda *args, **kwargs: 1)
    papers_dir = tmp_path / "papers"
    summary_root = tmp_path / "paper_summaries"
    saved = save_paper_markdown(
        doi="10.1234/demo",
        markdown=(
            "# Demo\n\n"
            "## Abstract\n\n"
            "This paper studies composite vibration damping.\n\n"
            "## Methods\n\n"
            "The method uses a layered composite beam experiment.\n\n"
            "## Results\n\n"
            "The results show improved damping under cyclic loading.\n\n"
            "## Limitations\n\n"
            "The study is limited to one material family.\n"
        ),
        papers_dir=papers_dir,
        title="Demo",
    )

    assert paper_summary_status(summary_root, papers_dir, doi="10.1234/demo") == "missing"
    summary = generate_paper_summary(summary_root, papers_dir, doi="10.1234/demo")

    assert summary.summary_id.startswith("summary_10_1234_demo_")
    assert summary.methods
    assert summary.key_findings
    assert summary.limitations
    assert summary.evidence_anchors[0].canonical_uri == f"grados://papers/{safe_doi_filename('10.1234/demo')}"
    assert paper_summary_status(summary_root, papers_dir, doi="10.1234/demo") == "valid"

    paper_file = Path(saved.file_path)
    paper_file.write_text(paper_file.read_text(encoding="utf-8") + "\n\nNew paragraph.", encoding="utf-8")

    assert paper_summary_status(summary_root, papers_dir, doi="10.1234/demo") == "stale"
