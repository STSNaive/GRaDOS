from __future__ import annotations

from pathlib import Path

import grados.research.draft_audit as draft_audit
import grados.research.evidence_pack as evidence_pack_module
from grados.publisher.common import safe_doi_filename
from grados.research.evidence_pack import (
    compute_pack_sha256,
    prepare_evidence_pack,
    read_evidence_pack,
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
    assert audit["claims"][0]["status"] == "supported"
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
    assert suggestion["suggestions"][0]["next_action"] == "prepare_or_extend_evidence_pack"
