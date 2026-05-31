from __future__ import annotations

from pathlib import Path

import grados.research.draft_audit as draft_audit
import grados.research.evidence_pack as evidence_pack_module
from grados.publisher.common import safe_doi_filename
from grados.research.evidence_pack import (
    EvidencePack,
    EvidencePackItem,
    compute_pack_sha256,
    prepare_evidence_pack,
    read_evidence_pack,
    save_evidence_pack,
    verify_evidence_pack,
)
from grados.research.pack_audit import audit_answer_against_pack, suggest_missing_evidence
from grados.storage.canonical_blocks import build_canonical_block_manifest, find_block_for_paragraph_window
from grados.storage.papers import save_paper_markdown
from grados.storage.vector import PaperSearchResult


def _chroma_dir(tmp_path: Path) -> Path:
    return tmp_path / "database" / "chroma"


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "database" / "research.sqlite3"


def _papers_dir(tmp_path: Path) -> Path:
    return tmp_path / "papers"


def _body(result_sentence: str = "Composite damping improved vibration attenuation by 18%.") -> str:
    return (
        "# Demo Paper\n\n"
        "## Results\n\n"
        f"{result_sentence}\n\n"
        "## Methods\n\n"
        "The experiment used a repeatable vibration rig."
    )


def _save_demo_paper(tmp_path: Path, body: str | None = None) -> None:
    save_paper_markdown(
        "10.1234/demo",
        body or _body(),
        _papers_dir(tmp_path),
        title="Demo Paper",
        source="fixture",
        authors=["Smith"],
        year="2025",
        journal="Composite Structures",
    )


def _patch_search(monkeypatch) -> None:  # noqa: ANN001
    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, limit)
        doi = kwargs.get("doi", "")
        if doi not in {"", "10.1234/demo"}:
            return []
        return [
            PaperSearchResult(
                doi="10.1234/demo",
                safe_doi=safe_doi_filename("10.1234/demo"),
                title="Demo Paper",
                authors=["Smith"],
                year="2025",
                journal="Composite Structures",
                section_name="Results",
                paragraph_start=2,
                paragraph_count=1,
                snippet="Composite damping improved vibration attenuation by 18%.",
                score=1.25,
                dense_score=1.0,
                lexical_score=0.25,
            )
        ]

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)


def test_canonical_block_manifest_is_stable_and_hashable(tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)

    first = build_canonical_block_manifest(_papers_dir(tmp_path), doi="10.1234/demo")
    second = build_canonical_block_manifest(_papers_dir(tmp_path), doi="10.1234/demo")

    assert first is not None
    assert second is not None
    assert first.doc_sha256 == second.doc_sha256
    assert [block.block_id for block in first.blocks] == [block.block_id for block in second.blocks]
    assert first.blocks[0].block_type == "paragraph"
    assert first.blocks[0].heading_path == ["Demo Paper", "Results"]
    assert first.blocks[0].text_sha256 == second.blocks[0].text_sha256
    assert first.blocks[0].canonical_uri.endswith(f"#block={first.blocks[0].block_id}")


def test_canonical_block_lookup_rejects_stale_windows(tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)

    manifest = build_canonical_block_manifest(_papers_dir(tmp_path), doi="10.1234/demo")

    assert manifest is not None
    assert find_block_for_paragraph_window(manifest, start_paragraph=None) == manifest.blocks[0]
    assert find_block_for_paragraph_window(manifest, start_paragraph=999, paragraph_count=1) is None


def test_prepare_read_verify_pack_round_trip_and_hash_invariance(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        subquestions=["How much attenuation is reported?"],
        scoped_dois=["10.1234/demo"],
    )
    loaded = read_evidence_pack(_db_path(tmp_path), pack_id=str(receipt["pack_id"]))
    verified = verify_evidence_pack(_db_path(tmp_path), _papers_dir(tmp_path), pack_id=str(receipt["pack_id"]))

    assert receipt["answerable"] is True
    assert receipt["evidence_count"] == 1
    assert loaded["found"] is True
    assert loaded["pack"]["evidence_items"][0]["text"] == (
        "Composite damping improved vibration attenuation by 18%."
    )
    assert compute_pack_sha256(loaded["pack"]) == receipt["pack_sha256"]
    assert verified["ok"] is True
    assert verified["snapshot_valid"] is True
    assert verified["current_valid"] is True


def test_verify_evidence_pack_reuses_manifest_for_same_paper(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )
    loaded = read_evidence_pack(_db_path(tmp_path), pack_id=str(receipt["pack_id"]))
    pack = dict(loaded["pack"])
    pack["pack_id"] = "pack_duplicate_manifest_fixture"
    first_item = dict(pack["evidence_items"][0])
    second_item = {**first_item, "subquestion": "Which paper reports the same result?"}
    pack["evidence_items"] = [first_item, second_item]
    saved = save_evidence_pack(_db_path(tmp_path), pack)

    calls: list[tuple[str | None, str | None]] = []

    def spy_manifest(papers_dir, *, doi=None, safe_doi=None):  # noqa: ANN001
        calls.append((doi, safe_doi))
        return build_canonical_block_manifest(papers_dir, doi=doi, safe_doi=safe_doi)

    monkeypatch.setattr(evidence_pack_module, "build_canonical_block_manifest", spy_manifest)

    verified = verify_evidence_pack(_db_path(tmp_path), _papers_dir(tmp_path), pack_id=str(saved["pack_id"]))

    assert verified["ok"] is True
    assert verified["evidence_count"] == 2
    assert len(calls) == 1


def test_prepare_pack_counts_reused_block_as_subquestion_coverage(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        subquestions=[
            "How much attenuation is reported?",
            "What outcome is reported?",
        ],
        scoped_dois=["10.1234/demo"],
        max_windows=1,
    )

    assert receipt["answerable"] is True
    assert receipt["insufficient_evidence"] == []
    assert receipt["evidence_count"] == 1


def test_prepare_pack_filters_reference_only_scoped_doi(monkeypatch, tmp_path: Path) -> None:
    save_paper_markdown(
        "10.1234/refs",
        "# Reference Only\n\n## References\n\nSmith et al. 2024. DOI 10.1234/source.",
        _papers_dir(tmp_path),
        title="Reference Only",
        source="fixture",
    )

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, limit, kwargs)
        return [
            PaperSearchResult(
                doi="10.1234/refs",
                safe_doi=safe_doi_filename("10.1234/refs"),
                title="Reference Only",
                authors=["Smith"],
                year="2024",
                journal="References",
                section_name="References",
                paragraph_start=2,
                paragraph_count=1,
                snippet="Smith et al. 2024. DOI 10.1234/source.",
                score=1.4,
            )
        ]

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="reference leakage",
        scoped_dois=["10.1234/refs"],
    )
    loaded = read_evidence_pack(_db_path(tmp_path), pack_id=str(receipt["pack_id"]))

    assert receipt["answerable"] is False
    assert receipt["evidence_count"] == 0
    assert receipt["covered_dois"] == []
    assert receipt["missing_scoped_dois"] == ["10.1234/refs"]
    assert receipt["missing_reasons"]["10.1234/refs"] == "no_non_reference_evidence"
    assert loaded["pack"]["retrieval_trace"][-1]["rejection_reason"] == "backmatter_section"


def test_pack_audit_filters_reference_only_legacy_pack(tmp_path: Path) -> None:
    save_paper_markdown(
        "10.1234/refs",
        "# Reference Only\n\n## References\n\nSmith et al. 2024. Composite damping.",
        _papers_dir(tmp_path),
        title="Reference Only",
        source="fixture",
        authors=["Smith"],
        year="2024",
    )
    manifest = build_canonical_block_manifest(_papers_dir(tmp_path), doi="10.1234/refs")
    assert manifest is not None
    block = manifest.blocks[0]
    saved = save_evidence_pack(
        _db_path(tmp_path),
        EvidencePack(
            pack_id="pack_legacy_refs",
            topic="legacy refs",
            query="legacy refs",
            subquestions=["legacy refs"],
            answerable=True,
            evidence_items=[
                EvidencePackItem(
                    canonical_uri=block.canonical_uri,
                    paper_id=block.paper_id,
                    safe_doi=block.safe_doi,
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=block.text,
                    text_sha256=block.text_sha256,
                    doi=block.doi,
                    heading_path=list(block.heading_path),
                    ordinal=block.ordinal,
                    source_paragraph_index=block.source_paragraph_index,
                    doc_sha256=block.doc_sha256,
                    prev_hash=block.prev_hash,
                    next_hash=block.next_hash,
                    title="Reference Only",
                    authors=["Smith"],
                    year="2024",
                )
            ],
            pack_sha256="",
            created_at="2026-05-28T00:00:00+00:00",
        ),
    )

    audit = audit_answer_against_pack(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(saved["pack_id"]),
        draft="Composite damping is supported by this work (Smith, 2024).",
    )

    assert audit["filtered_evidence_count"] == 1
    assert audit["claims"][0]["verdict"] == "unverifiable"
    assert audit["claims"][0]["evidence"] == []


def test_prepare_pack_checks_all_scoped_dois_beyond_max_windows(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    save_paper_markdown(
        "10.1234/second",
        "# Second Paper\n\n## Results\n\nLayered damping reduced resonance amplitude by 12%.",
        _papers_dir(tmp_path),
        title="Second Paper",
        source="fixture",
        authors=["Lee"],
        year="2024",
    )
    calls: list[str] = []

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query)
        doi = kwargs.get("doi", "")
        calls.append(doi)
        snippets = {
            "10.1234/demo": "Composite damping improved vibration attenuation by 18%.",
            "10.1234/second": "Layered damping reduced resonance amplitude by 12%.",
        }
        if doi not in snippets:
            return []
        return [
            PaperSearchResult(
                doi=doi,
                safe_doi=safe_doi_filename(doi),
                title="Scoped Paper",
                authors=[],
                year="2025",
                journal="Composite Structures",
                section_name="Results",
                paragraph_start=2,
                paragraph_count=1,
                snippet=snippets[doi],
                score=1.2,
            )
        ]

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="damping",
        scoped_dois=["10.1234/demo", "10.1234/second"],
        max_windows=1,
    )

    assert calls == ["10.1234/demo", "10.1234/second"]
    assert receipt["answerable"] is True
    assert receipt["covered_dois"] == ["10.1234/demo", "10.1234/second"]
    assert receipt["missing_scoped_dois"] == []


def test_prepare_pack_fallback_skips_title_placeholder(monkeypatch, tmp_path: Path) -> None:
    save_paper_markdown(
        "10.1234/fallback",
        (
            "Fallback Paper\n\n"
            "## Results\n\n"
            "Composite damping improved vibration attenuation by 18%."
        ),
        _papers_dir(tmp_path),
        title="Fallback Paper",
        source="fixture",
    )

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, limit, kwargs)
        return []

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="fallback damping",
        scoped_dois=["10.1234/fallback"],
    )
    loaded = read_evidence_pack(_db_path(tmp_path), pack_id=str(receipt["pack_id"]))

    assert receipt["answerable"] is True
    assert receipt["evidence_count"] == 1
    assert loaded["pack"]["evidence_items"][0]["text"] == (
        "Composite damping improved vibration attenuation by 18%."
    )


def test_prepare_pack_fallback_skips_author_and_metadata_blocks(monkeypatch, tmp_path: Path) -> None:
    save_paper_markdown(
        "10.1234/meta",
        (
            "# Metadata Paper\n\n"
            "Authors: Alice Smith, Bob Lee\n\n"
            "DOI: 10.1234/meta\n\n"
            "Journal: Composite Structures\n\n"
            "## Results\n\n"
            "Composite damping improved vibration attenuation by 18%."
        ),
        _papers_dir(tmp_path),
        title="Metadata Paper",
        source="fixture",
        authors=["Alice Smith", "Bob Lee"],
        year="2026",
        journal="Composite Structures",
    )

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, limit, kwargs)
        return [
            PaperSearchResult(
                doi="10.1234/meta",
                safe_doi=safe_doi_filename("10.1234/meta"),
                title="Metadata Paper",
                authors=["Alice Smith", "Bob Lee"],
                year="2026",
                journal="Composite Structures",
                section_name="Metadata Paper",
                paragraph_start=1,
                paragraph_count=1,
                snippet="Authors: Alice Smith, Bob Lee",
                score=1.2,
            )
        ]

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="metadata leakage",
        scoped_dois=["10.1234/meta"],
    )
    loaded = read_evidence_pack(_db_path(tmp_path), pack_id=str(receipt["pack_id"]))

    assert receipt["answerable"] is True
    assert receipt["evidence_count"] == 1
    assert loaded["pack"]["evidence_items"][0]["text"] == (
        "Composite damping improved vibration attenuation by 18%."
    )
    filtered = [
        trace
        for trace in loaded["pack"]["retrieval_trace"]
        if trace.get("eligibility") == "rejected"
    ]
    assert any(trace.get("rejection_reason") in {"author_line", "doi_only", "journal_only"} for trace in filtered)


def test_prepare_pack_keeps_one_eligible_item_per_scoped_doi(monkeypatch, tmp_path: Path) -> None:
    save_paper_markdown(
        "10.1234/multi",
        (
            "# Multi Paper\n\n"
            "## Results\n\n"
            "Composite damping improved vibration attenuation by 18%.\n\n"
            "Layered damping reduced resonance amplitude by 12%."
        ),
        _papers_dir(tmp_path),
        title="Multi Paper",
        source="fixture",
    )

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, limit, kwargs)
        return [
            PaperSearchResult(
                doi="10.1234/multi",
                safe_doi=safe_doi_filename("10.1234/multi"),
                title="Multi Paper",
                authors=[],
                year="2025",
                journal="Composite Structures",
                section_name="Results",
                paragraph_start=2,
                paragraph_count=1,
                snippet="Composite damping improved vibration attenuation by 18%.",
                score=1.2,
            ),
            PaperSearchResult(
                doi="10.1234/multi",
                safe_doi=safe_doi_filename("10.1234/multi"),
                title="Multi Paper",
                authors=[],
                year="2025",
                journal="Composite Structures",
                section_name="Results",
                paragraph_start=3,
                paragraph_count=1,
                snippet="Layered damping reduced resonance amplitude by 12%.",
                score=1.1,
            ),
        ]

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)

    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="damping",
        scoped_dois=["10.1234/multi"],
        max_windows=2,
    )

    assert receipt["answerable"] is True
    assert receipt["evidence_count"] == 1
    assert receipt["covered_dois"] == ["10.1234/multi"]


def test_verify_fails_when_canonical_markdown_changes(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )

    _save_demo_paper(tmp_path, _body("Composite damping improved vibration attenuation by 9%."))
    verified = verify_evidence_pack(_db_path(tmp_path), _papers_dir(tmp_path), pack_id=str(receipt["pack_id"]))

    assert verified["ok"] is False
    assert verified["snapshot_valid"] is True
    assert verified["current_valid"] is False
    assert verified["doc_changed"]
    assert verified["hash_mismatch"]


def test_verify_detects_relocated_block_without_silent_pass(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )

    _save_demo_paper(
        tmp_path,
        (
            "# Demo Paper\n\n"
            "## Results\n\n"
            "A new leading paragraph was inserted before the cited evidence.\n\n"
            "Composite damping improved vibration attenuation by 18%.\n\n"
            "## Methods\n\n"
            "The experiment used a repeatable vibration rig."
        ),
    )
    verified = verify_evidence_pack(_db_path(tmp_path), _papers_dir(tmp_path), pack_id=str(receipt["pack_id"]))

    assert verified["ok"] is False
    assert verified["current_valid"] is False
    assert verified["block_shifted_relocated"]
    assert verified["block_shifted_relocated"][0]["relocated_block_id"] != (
        verified["block_shifted_relocated"][0]["item"]["block_id"]
    )


def test_strict_pack_audit_does_not_search_full_library(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )

    def forbidden_search(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("strict pack audit must not retrieve replacement evidence")

    monkeypatch.setattr(evidence_pack_module, "search_papers", forbidden_search)
    monkeypatch.setattr(draft_audit, "search_papers", forbidden_search)
    audit = audit_answer_against_pack(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        draft="Composite damping improved vibration attenuation by 18% (Smith, 2025).",
        strict=True,
    )

    assert audit["search_scope"] == "pack_only"
    assert audit["claims"][0]["verdict"] == "verified"
    assert audit["verdict_counts"] == {"verified": 1}
    assert audit["claim_map"][0]["verdict"] == "verified"
    assert audit["claim_map"][0]["evidence_block_ids"]


def test_suggest_missing_evidence_is_separate_from_strict_audit(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )

    suggestion = suggest_missing_evidence(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        draft="The method completely guarantees fatigue resistance (Smith, 2025).",
    )

    assert suggestion["mode"] == "suggestion_only"
    assert suggestion["search_scope"] == "none"
    assert suggestion["suggestions"][0]["verdict"] in {
        "minor_distortion",
        "major_distortion",
        "unverifiable",
        "unverifiable_access",
    }
    assert suggestion["suggestions"][0]["next_action"] in {
        "revise_wording_or_add_locator",
        "rewrite_or_replace_citation",
        "search_and_prepare_evidence_pack",
        "reacquire_full_text_or_switch_parser",
    }

    audit_with_suggestions = audit_answer_against_pack(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        draft="The method completely guarantees fatigue resistance (Smith, 2025).",
        include_suggestions=True,
    )
    assert audit_with_suggestions["suggestions"]["mode"] == "suggestion_only"
    assert audit_with_suggestions["suggestions"]["suggestion_count"] == suggestion["suggestion_count"]


def test_pack_audit_uses_unverifiable_access_for_stale_pack(monkeypatch, tmp_path: Path) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    receipt = prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )

    _save_demo_paper(tmp_path, _body("Composite damping improved vibration attenuation by 9%."))
    audit = audit_answer_against_pack(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        draft="Composite damping improved vibration attenuation by 18% (Smith, 2025).",
        strict=True,
    )

    assert audit["verify"]["current_valid"] is False
    assert audit["claims"][0]["verdict"] == "unverifiable_access"
    assert audit["claims"][0]["issue_type"] == "stale_pack"
