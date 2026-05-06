from __future__ import annotations

from pathlib import Path

import grados.research.draft_audit as draft_audit
import grados.research.evidence_grid as evidence_grid
from grados.research.draft_audit import audit_draft_support
from grados.research.evidence_grid import build_evidence_grid
from grados.storage.vector import PaperSearchResult


def _patch_search_papers(monkeypatch, fake_search_papers) -> None:  # noqa: ANN001
    monkeypatch.setattr(evidence_grid, "search_papers", fake_search_papers)
    monkeypatch.setattr(draft_audit, "search_papers", fake_search_papers)


def test_build_evidence_grid_and_audit_draft_support(monkeypatch, tmp_path: Path) -> None:
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
                    paragraph_start=4,
                    paragraph_count=2,
                    snippet="Composite damping improved vibration attenuation by 18%.",
                    score=1.35,
                    dense_score=1.1,
                    lexical_score=0.25,
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
                    paragraph_start=7,
                    paragraph_count=1,
                    snippet="A different baseline was evaluated.",
                    score=0.9,
                    dense_score=0.7,
                    lexical_score=0.2,
                )
            ]
        return []

    _patch_search_papers(monkeypatch, fake_search_papers)

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
    assert grid.grids[0].rows[0].canonical_uri == "grados://papers/10_1234_demo"
    assert grid.grids[0].rows[0].paragraph_start == 4
    assert grid.grids[0].rows[0].paragraph_count == 2
    assert grid.grids[0].rows[0].dense_score == 1.1
    assert grid.grids[0].rows[0].lexical_score == 0.25
    assert supported.claims[0].status == "supported"
    assert supported.claims[0].evidence[0].canonical_uri == "grados://papers/10_1234_demo"
    assert supported.claims[0].evidence[0].paragraph_start == 4
    assert supported.claims[0].evidence[0].paragraph_count == 2
    assert misattributed.claims[0].status == "misattributed"
    assert numeric.claims[0].status == "weak"


def test_audit_draft_support_handles_chinese_claims_and_author_year_citations(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, limit, kwargs)
        calls.append(query)
        if "振动衰减" in query:
            return [
                PaperSearchResult(
                    doi="10.1234/demo-a",
                    safe_doi="10_1234_demo_a",
                    title="Composite Damping Study",
                    authors=["张三"],
                    year="2025",
                    journal="Composite Structures",
                    section_name="Results",
                    paragraph_start=3,
                    paragraph_count=1,
                    snippet="复合阻尼将振动衰减提高了18%。",
                    score=1.35,
                )
            ]
        if "低资源场景" in query:
            return [
                PaperSearchResult(
                    doi="10.5678/demo-b",
                    safe_doi="10_5678_demo_b",
                    title="Low-Resource Stability Study",
                    authors=["李四"],
                    year="2025",
                    journal="Mechanical Systems",
                    section_name="Discussion",
                    paragraph_start=6,
                    paragraph_count=2,
                    snippet="该方法在低资源场景下仍然稳定。",
                    score=1.2,
                )
            ]
        return []

    _patch_search_papers(monkeypatch, fake_search_papers)

    result = audit_draft_support(
        tmp_path / "chroma",
        draft_text=(
            "复合阻尼将振动衰减提高了18%（张三，2025）。"
            "另一项实验表明它在低资源场景下仍然稳定（李四，2025）。"
        ),
        strictness="strict",
    )

    assert result.claims_checked == 2
    assert [claim.status for claim in result.claims] == ["supported", "supported"]
    assert [claim.query_text for claim in result.claims] == [
        "复合阻尼将振动衰减提高了18%。",
        "另一项实验表明它在低资源场景下仍然稳定。",
    ]
    assert [claim.citation_marker_present for claim in result.claims] == [True, True]
    assert result.claims[0].citations[0].author == "张三"
    assert result.claims[1].citations[0].author == "李四"
    assert calls == [
        "复合阻尼将振动衰减提高了18%。",
        "另一项实验表明它在低资源场景下仍然稳定。",
    ]


def test_audit_draft_support_deduplicates_repeated_queries(monkeypatch, tmp_path: Path) -> None:
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
                paragraph_start=4,
                paragraph_count=2,
                snippet="Composite damping improved vibration attenuation by 18%.",
                score=1.35,
            )
        ]

    _patch_search_papers(monkeypatch, fake_search_papers)

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


def test_audit_draft_support_uses_configurable_candidate_limit(monkeypatch, tmp_path: Path) -> None:
    captured_limits: list[int] = []

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, kwargs)
        captured_limits.append(limit)
        return []

    _patch_search_papers(monkeypatch, fake_search_papers)

    result = audit_draft_support(
        tmp_path / "chroma",
        draft_text="Composite damping improves vibration attenuation by 18%.",
        candidate_limit=9,
    )

    assert result.claims_checked == 1
    assert captured_limits == [9]
