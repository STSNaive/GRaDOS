from __future__ import annotations

from pathlib import Path

import grados.research.evidence_pack as evidence_pack_module
from grados.publisher.common import safe_doi_filename
from grados.research.evidence_pack import prepare_evidence_pack
from grados.research.external_synthesis import (
    EXTERNAL_SYNTHESIS_PACKET_KIND,
    EXTERNAL_SYNTHESIS_RESULT_KIND,
    audit_external_synthesis_result,
    prepare_external_synthesis_from_topic,
    prepare_external_synthesis_packet,
    preview_external_synthesis_packet,
    save_external_synthesis_result,
)
from grados.research_state import query_research_artifacts
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


def _save_alt_paper(tmp_path: Path) -> None:
    save_paper_markdown(
        "10.1234/alt",
        _body("Layered damping reduced resonance amplitude by 12%."),
        _papers_dir(tmp_path),
        title="Alt Paper",
        source="fixture",
        authors=["Lee"],
        year="2024",
        journal="Vibration Control",
    )


def _search_result(
    *,
    doi: str,
    title: str,
    authors: list[str],
    year: str,
    journal: str,
    snippet: str,
) -> PaperSearchResult:
    return PaperSearchResult(
        doi=doi,
        safe_doi=safe_doi_filename(doi),
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        section_name="Results",
        paragraph_start=2,
        paragraph_count=1,
        snippet=snippet,
        score=1.25,
        dense_score=1.0,
        lexical_score=0.25,
    )


def _patch_search(monkeypatch, *, include_alt: bool = False) -> None:  # noqa: ANN001
    results_by_doi = {
        "10.1234/demo": _search_result(
            doi="10.1234/demo",
            title="Demo Paper",
            authors=["Smith"],
            year="2025",
            journal="Composite Structures",
            snippet="Composite damping improved vibration attenuation by 18%.",
        ),
    }
    if include_alt:
        results_by_doi["10.1234/alt"] = _search_result(
            doi="10.1234/alt",
            title="Alt Paper",
            authors=["Lee"],
            year="2024",
            journal="Vibration Control",
            snippet="Layered damping reduced resonance amplitude by 12%.",
        )

    def fake_search_papers(chroma_dir, query, limit=10, **kwargs):  # noqa: ANN001
        _ = (chroma_dir, query, limit)
        doi = kwargs.get("doi", "")
        if doi:
            result = results_by_doi.get(doi)
            return [result] if result is not None else []
        if not results_by_doi:
            return []
        return list(results_by_doi.values())

    monkeypatch.setattr(evidence_pack_module, "search_papers", fake_search_papers)


def _prepare_pack(monkeypatch, tmp_path: Path) -> dict[str, object]:  # noqa: ANN001
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)
    return prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        subquestions=["How much attenuation is reported?"],
        scoped_dois=["10.1234/demo"],
    )


def _prepare_two_item_pack(monkeypatch, tmp_path: Path) -> dict[str, object]:  # noqa: ANN001
    _save_demo_paper(tmp_path)
    _save_alt_paper(tmp_path)
    _patch_search(monkeypatch, include_alt=True)
    return prepare_evidence_pack(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        topic="composite damping",
        subquestions=["How much attenuation is reported?"],
        scoped_dois=["10.1234/demo", "10.1234/alt"],
    )


def test_preview_external_synthesis_packet_does_not_persist(monkeypatch, tmp_path: Path) -> None:
    receipt = _prepare_pack(monkeypatch, tmp_path)

    preview = preview_external_synthesis_packet(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
    )
    saved_packets = query_research_artifacts(
        _db_path(tmp_path),
        kind=EXTERNAL_SYNTHESIS_PACKET_KIND,
        detail=True,
    )

    assert preview["ok"] is True
    assert preview["sendable"] is True
    assert preview["saved"] is False
    assert preview["packet_item_count"] == 1
    assert preview["verify"]["current_valid"] is True
    assert "prompt_skeleton" in preview
    assert saved_packets["count"] == 0


def test_prepare_external_synthesis_packet_rejects_stale_pack(
    monkeypatch,
    tmp_path: Path,
) -> None:
    receipt = _prepare_pack(monkeypatch, tmp_path)
    _save_demo_paper(tmp_path, _body("Composite damping improved vibration attenuation by 9%."))

    packet = prepare_external_synthesis_packet(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
    )
    saved_packets = query_research_artifacts(
        _db_path(tmp_path),
        kind=EXTERNAL_SYNTHESIS_PACKET_KIND,
        detail=True,
    )

    assert packet["ok"] is False
    assert packet["sendable"] is False
    assert packet["error"] == "evidence_pack_not_current_valid"
    assert packet["verify"]["current_valid"] is False
    assert saved_packets["count"] == 0


def test_prepare_external_synthesis_from_topic_persists_pack_and_packet(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _save_demo_paper(tmp_path)
    _patch_search(monkeypatch)

    packet = prepare_external_synthesis_from_topic(
        _chroma_dir(tmp_path),
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        topic="composite damping",
        scoped_dois=["10.1234/demo"],
    )
    saved_packets = query_research_artifacts(
        _db_path(tmp_path),
        kind=EXTERNAL_SYNTHESIS_PACKET_KIND,
        detail=True,
    )

    assert packet["ok"] is True
    assert packet["saved"] is True
    assert packet["route"] == "prepare_external_synthesis_from_topic"
    assert packet["pack_id"]
    assert packet["pack_artifact_id"]
    assert packet["evidence_pack"]["evidence_count"] == 1
    assert saved_packets["count"] == 1


def test_external_synthesis_result_round_trip_and_audit(monkeypatch, tmp_path: Path) -> None:
    receipt = _prepare_pack(monkeypatch, tmp_path)
    packet = prepare_external_synthesis_packet(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
    )
    saved_packets = query_research_artifacts(
        _db_path(tmp_path),
        kind=EXTERNAL_SYNTHESIS_PACKET_KIND,
        detail=True,
    )
    saved_packet_content = saved_packets["items"][0]["content"]

    saved = save_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        packet_artifact_id=str(packet["artifact_id"]),
        response="Using anchor_001, composite damping improved vibration attenuation by 18% (Smith, 2025).",
        model_label="GPT-5.5 Pro",
        thinking_label="extended",
        claims=[
            {
                "text": "Composite damping improved vibration attenuation by 18%.",
                "anchor_ids": ["anchor_001"],
            }
        ],
    )
    audit = audit_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        result_id=str(saved["artifact_id"]),
    )
    saved_results = query_research_artifacts(
        _db_path(tmp_path),
        kind=EXTERNAL_SYNTHESIS_RESULT_KIND,
        detail=True,
    )

    assert saved["ok"] is True
    assert saved["audited"] is True
    assert saved["audit"]["ready_for_canonical_reread"] is True
    assert saved_results["count"] == 1
    assert "host_prompt" in packet
    assert "host_prompt" not in packet["packet"]
    assert "host_prompt" not in saved_packet_content
    assert saved_packet_content["prompt_hash"] == packet["prompt_hash"]
    assert saved_packet_content["items"] == packet["packet"]["items"]
    assert audit["allowed_reference_scope"] == "packet"
    assert audit["unknown_anchor_ids"] == []
    assert audit["pack_outside_dois"] == []
    assert audit["ready_for_canonical_reread"] is True
    assert audit["audit"]["verdict_counts"] == {"verified": 1}


def test_external_synthesis_audit_uses_packet_reference_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    receipt = _prepare_two_item_pack(monkeypatch, tmp_path)
    pack_artifact = query_research_artifacts(
        _db_path(tmp_path),
        artifact_id=str(receipt["artifact_id"]),
        detail=True,
    )
    second_item = pack_artifact["items"][0]["content"]["evidence_items"][1]
    packet = prepare_external_synthesis_packet(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        max_items=1,
    )

    saved = save_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        packet_artifact_id=str(packet["artifact_id"]),
        response=(
            "Layered damping reduced resonance amplitude by 12% according to anchor_002 "
            f"at {second_item['block_id']} and {second_item['canonical_uri']}."
        ),
        claims=[
            {
                "text": "Layered damping reduced resonance amplitude by 12%.",
                "anchor_ids": ["anchor_002"],
            }
        ],
    )
    audit = audit_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        result_id=str(saved["artifact_id"]),
    )

    assert receipt["evidence_count"] == 2
    assert packet["packet_item_count"] == 1
    assert audit["allowed_reference_scope"] == "packet"
    assert audit["referenced_anchor_ids"] == ["anchor_002"]
    assert audit["unknown_anchor_ids"] == ["anchor_002"]
    assert audit["unknown_block_ids"] == [second_item["block_id"]]
    assert audit["unknown_canonical_uris"] == [second_item["canonical_uri"]]
    assert audit["ready_for_canonical_reread"] is False


def test_external_synthesis_structured_claim_anchors_can_gate_without_prose_citations(
    monkeypatch,
    tmp_path: Path,
) -> None:
    receipt = _prepare_pack(monkeypatch, tmp_path)
    packet = prepare_external_synthesis_packet(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
    )

    saved = save_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        packet_artifact_id=str(packet["artifact_id"]),
        response={
            "claims": [
                {
                    "text": "Composite damping improved vibration attenuation by 18%.",
                    "anchor_ids": ["anchor_001"],
                    "confidence": "high",
                    "caveat": "single fixture passage",
                }
            ],
            "missing_evidence": [],
        },
    )
    audit = audit_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        result_id=str(saved["artifact_id"]),
    )

    assert audit["ready_for_canonical_reread"] is True
    assert audit["usable_claim_ids"] == ["external_claim_1"]
    assert audit["claims_requiring_revision"] == []
    assert audit["structured_claims_checked"] == 1
    assert audit["structured_claims"][0]["verdict"] == "verified"


def test_external_synthesis_audit_flags_pack_external_references(
    monkeypatch,
    tmp_path: Path,
) -> None:
    receipt = _prepare_pack(monkeypatch, tmp_path)
    saved = save_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        pack_id=str(receipt["pack_id"]),
        response=(
            "Composite damping improved vibration attenuation by 18% (Smith, 2025). "
            "A separate result from 10.9999/outside should also be cited via anchor_999."
        ),
        claims=[
            {
                "text": "A separate outside result should also be cited.",
                "anchor_ids": ["anchor_999"],
            }
        ],
    )

    audit = audit_external_synthesis_result(
        _db_path(tmp_path),
        _papers_dir(tmp_path),
        result_id=str(saved["artifact_id"]),
    )

    assert audit["ok"] is False
    assert audit["unknown_anchor_ids"] == ["anchor_999"]
    assert audit["pack_outside_dois"] == ["10.9999/outside"]
    assert audit["ready_for_canonical_reread"] is False
