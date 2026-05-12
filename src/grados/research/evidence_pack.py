"""Evidence-pack lifecycle helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.research_state import query_research_artifacts, save_research_artifact
from grados.storage.canonical_blocks import (
    CanonicalBlock,
    CanonicalBlockManifest,
    build_canonical_block_manifest,
    find_block_for_paragraph_window,
    parse_block_ordinal,
)
from grados.storage.papers import load_paper_record
from grados.storage.paths import resolve_papers_dir
from grados.storage.retrieval import PaperSearchResult
from grados.storage.vector import search_papers

__all__ = [
    "EVIDENCE_PACK_KIND",
    "EvidencePack",
    "EvidencePackItem",
    "compute_pack_sha256",
    "evidence_pack_from_dict",
    "evidence_pack_to_dict",
    "prepare_evidence_pack",
    "read_evidence_pack",
    "save_evidence_pack",
    "verify_evidence_pack",
]

EVIDENCE_PACK_KIND = "evidence_pack"


@dataclass(frozen=True)
class EvidencePackItem:
    canonical_uri: str
    paper_id: str
    safe_doi: str
    block_id: str
    block_type: str
    text: str
    text_sha256: str
    doi: str = ""
    heading_path: list[str] = field(default_factory=list)
    ordinal: int = 0
    source_paragraph_index: int = -1
    doc_sha256: str = ""
    prev_hash: str = ""
    next_hash: str = ""
    subquestion: str = ""
    query_used: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    retrieval_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePack:
    pack_id: str
    topic: str
    query: str
    subquestions: list[str]
    answerable: bool
    evidence_items: list[EvidencePackItem]
    pack_sha256: str
    created_at: str
    insufficient_evidence: list[str] = field(default_factory=list)
    retrieval_trace: list[dict[str, Any]] = field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_pack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stable = dict(payload)
    stable.pop("pack_sha256", None)
    stable.pop("artifact_id", None)
    return stable


def compute_pack_sha256(pack: EvidencePack | dict[str, Any]) -> str:
    """Compute the content hash for a pack, excluding its stored hash field."""
    payload = evidence_pack_to_dict(pack) if isinstance(pack, EvidencePack) else dict(pack)
    return hashlib.sha256(_stable_json(_stable_pack_payload(payload)).encode("utf-8")).hexdigest()


def evidence_pack_to_dict(pack: EvidencePack) -> dict[str, Any]:
    return asdict(pack)


def _item_from_dict(payload: dict[str, Any]) -> EvidencePackItem:
    fields = EvidencePackItem.__dataclass_fields__
    values = {key: payload.get(key) for key in fields if key in payload}
    return EvidencePackItem(
        canonical_uri=str(values.get("canonical_uri") or ""),
        paper_id=str(values.get("paper_id") or values.get("safe_doi") or ""),
        safe_doi=str(values.get("safe_doi") or values.get("paper_id") or ""),
        block_id=str(values.get("block_id") or ""),
        block_type=str(values.get("block_type") or "paragraph"),
        text=str(values.get("text") or ""),
        text_sha256=str(values.get("text_sha256") or ""),
        doi=str(values.get("doi") or ""),
        heading_path=[str(item) for item in (values.get("heading_path") or [])],
        ordinal=int(values.get("ordinal") or 0),
        source_paragraph_index=int(values.get("source_paragraph_index") or -1),
        doc_sha256=str(values.get("doc_sha256") or ""),
        prev_hash=str(values.get("prev_hash") or ""),
        next_hash=str(values.get("next_hash") or ""),
        subquestion=str(values.get("subquestion") or ""),
        query_used=str(values.get("query_used") or ""),
        title=str(values.get("title") or ""),
        authors=[str(item) for item in (values.get("authors") or [])],
        year=str(values.get("year") or ""),
        journal=str(values.get("journal") or ""),
        retrieval_trace=dict(values.get("retrieval_trace") or {}),
    )


def evidence_pack_from_dict(payload: dict[str, Any]) -> EvidencePack:
    items = [
        _item_from_dict(item)
        for item in payload.get("evidence_items", [])
        if isinstance(item, dict)
    ]
    return EvidencePack(
        pack_id=str(payload.get("pack_id") or ""),
        topic=str(payload.get("topic") or payload.get("query") or ""),
        query=str(payload.get("query") or payload.get("topic") or ""),
        subquestions=[str(item) for item in payload.get("subquestions", [])],
        answerable=bool(payload.get("answerable")),
        evidence_items=items,
        pack_sha256=str(payload.get("pack_sha256") or ""),
        created_at=str(payload.get("created_at") or ""),
        insufficient_evidence=[str(item) for item in payload.get("insufficient_evidence", [])],
        retrieval_trace=[
            dict(item) for item in payload.get("retrieval_trace", []) if isinstance(item, dict)
        ],
    )


def _new_pack_id() -> str:
    return f"pack_{uuid.uuid4().hex[:12]}"


def _build_pack(
    *,
    topic: str,
    query: str,
    subquestions: list[str],
    evidence_items: list[EvidencePackItem],
    insufficient_evidence: list[str],
    retrieval_trace: list[dict[str, Any]],
) -> EvidencePack:
    payload: dict[str, Any] = {
        "pack_id": _new_pack_id(),
        "topic": topic,
        "query": query,
        "subquestions": subquestions,
        "answerable": bool(evidence_items) and not insufficient_evidence,
        "evidence_items": [asdict(item) for item in evidence_items],
        "pack_sha256": "",
        "created_at": _utc_now(),
        "insufficient_evidence": insufficient_evidence,
        "retrieval_trace": retrieval_trace,
    }
    payload["pack_sha256"] = compute_pack_sha256(payload)
    return evidence_pack_from_dict(payload)


def _block_to_item(
    block: CanonicalBlock,
    *,
    subquestion: str,
    query_used: str,
    trace: dict[str, Any],
) -> EvidencePackItem:
    record = trace.get("paper_record")
    title = ""
    authors: list[str] = []
    year = ""
    journal = ""
    if record is not None:
        title = str(getattr(record, "title", "") or "")
        authors = [str(item) for item in getattr(record, "authors", [])]
        year = str(getattr(record, "year", "") or "")
        journal = str(getattr(record, "journal", "") or "")

    clean_trace = {key: value for key, value in trace.items() if key != "paper_record"}
    return EvidencePackItem(
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
        subquestion=subquestion,
        query_used=query_used,
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        retrieval_trace=clean_trace,
    )


def _candidate_to_item(
    papers_dir: Path,
    match: PaperSearchResult,
    *,
    subquestion: str,
    query_used: str,
) -> tuple[EvidencePackItem | None, dict[str, Any]]:
    manifest = build_canonical_block_manifest(
        papers_dir,
        doi=match.doi,
        safe_doi=match.safe_doi,
    )
    if manifest is None and match.doi:
        manifest = build_canonical_block_manifest(papers_dir, doi=match.doi)
    trace: dict[str, Any] = {
        "source": "search_saved_papers",
        "query_used": query_used,
        "doi": match.doi,
        "safe_doi": match.safe_doi,
        "section_name": match.section_name,
        "paragraph_start": match.paragraph_start,
        "paragraph_count": match.paragraph_count,
        "score": match.score,
        "dense_score": match.dense_score,
        "lexical_score": match.lexical_score,
    }
    if manifest is None:
        trace["status"] = "missing_paper"
        return None, trace
    block = find_block_for_paragraph_window(
        manifest,
        start_paragraph=match.paragraph_start,
        paragraph_count=match.paragraph_count,
    )
    if block is None:
        trace["status"] = "block_missing"
        return None, trace
    trace["status"] = "materialized"
    item_trace = {
        **trace,
        "paper_record": load_paper_record(
            papers_dir,
            doi=match.doi,
            safe_doi=manifest.safe_doi,
        ),
    }
    return _block_to_item(block, subquestion=subquestion, query_used=query_used, trace=item_trace), trace


def _fallback_scoped_item(
    papers_dir: Path,
    doi: str,
    *,
    subquestion: str,
    query_used: str,
    reason: str,
) -> tuple[EvidencePackItem | None, dict[str, Any]]:
    manifest = build_canonical_block_manifest(papers_dir, doi=doi)
    trace: dict[str, Any] = {
        "source": "canonical_block_registry",
        "query_used": query_used,
        "doi": doi,
        "reason": reason,
    }
    if manifest is None or not manifest.blocks:
        trace["status"] = "missing_paper"
        return None, trace
    trace["status"] = "materialized"
    item_trace = {
        **trace,
        "paper_record": load_paper_record(papers_dir, doi=doi, safe_doi=manifest.safe_doi),
    }
    return _block_to_item(
        manifest.blocks[0],
        subquestion=subquestion,
        query_used=query_used,
        trace=item_trace,
    ), trace


def _candidate_matches(
    chroma_dir: Path,
    papers_dir: Path,
    *,
    query_text: str,
    scoped_dois: list[str],
    max_windows: int,
) -> tuple[list[PaperSearchResult], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    matches: list[PaperSearchResult] = []
    if scoped_dois:
        for scoped_doi in scoped_dois[:max_windows]:
            try:
                scoped_matches = search_papers(
                    chroma_dir,
                    query_text,
                    limit=1,
                    papers_dir=papers_dir,
                    doi=scoped_doi,
                    use_reranking=True,
                )
            except Exception as exc:
                trace.append(
                    {
                        "source": "search_saved_papers",
                        "query_used": query_text,
                        "doi": scoped_doi,
                        "status": "search_failed",
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
                continue
            matches.extend(scoped_matches[:1])
        return matches, trace

    try:
        return (
            search_papers(
                chroma_dir,
                query_text,
                limit=max_windows,
                papers_dir=papers_dir,
                use_reranking=True,
            ),
            trace,
        )
    except Exception as exc:
        trace.append(
            {
                "source": "search_saved_papers",
                "query_used": query_text,
                "status": "search_failed",
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        )
        return [], trace


def prepare_evidence_pack(
    chroma_dir: Path,
    db_path: Path,
    *,
    topic: str,
    subquestions: list[str] | None = None,
    scoped_dois: list[str] | None = None,
    max_windows: int = 8,
) -> dict[str, Any]:
    """Retrieve candidate anchors, reread canonical blocks, and persist a pack."""
    papers_dir = resolve_papers_dir(chroma_dir)
    if papers_dir is None:
        raise ValueError("Cannot resolve papers directory for evidence pack preparation.")

    normalized_topic = topic.strip()
    resolved_subquestions = [
        question.strip() for question in (subquestions or []) if question.strip()
    ] or [normalized_topic]
    resolved_dois = [doi.strip() for doi in (scoped_dois or []) if doi.strip()]
    max_windows = max(1, min(max_windows, 25))
    evidence_items: list[EvidencePackItem] = []
    retrieval_trace: list[dict[str, Any]] = []
    insufficient_evidence: list[str] = []
    seen_blocks: set[tuple[str, str]] = set()

    for subquestion in resolved_subquestions:
        query_text = subquestion or normalized_topic
        matches, trace = _candidate_matches(
            chroma_dir,
            papers_dir,
            query_text=query_text,
            scoped_dois=resolved_dois,
            max_windows=max_windows,
        )
        retrieval_trace.extend(trace)
        covered_for_subquestion = 0

        for match in matches[:max_windows]:
            item, item_trace = _candidate_to_item(
                papers_dir,
                match,
                subquestion=subquestion,
                query_used=query_text,
            )
            retrieval_trace.append(item_trace)
            if item is None:
                continue
            covered_for_subquestion += 1
            key = (item.safe_doi, item.block_id)
            if key in seen_blocks:
                continue
            seen_blocks.add(key)
            evidence_items.append(item)

        if covered_for_subquestion == 0 and resolved_dois:
            for scoped_doi in resolved_dois[:max_windows]:
                item, item_trace = _fallback_scoped_item(
                    papers_dir,
                    scoped_doi,
                    subquestion=subquestion,
                    query_used=query_text,
                    reason="no_search_candidate",
                )
                retrieval_trace.append(item_trace)
                if item is None:
                    continue
                covered_for_subquestion += 1
                key = (item.safe_doi, item.block_id)
                if key not in seen_blocks:
                    seen_blocks.add(key)
                    evidence_items.append(item)
                break

        if covered_for_subquestion == 0:
            insufficient_evidence.append(subquestion)

    pack = _build_pack(
        topic=normalized_topic,
        query=normalized_topic,
        subquestions=resolved_subquestions,
        evidence_items=evidence_items,
        insufficient_evidence=insufficient_evidence,
        retrieval_trace=retrieval_trace,
    )
    return save_evidence_pack(db_path, pack)


def save_evidence_pack(db_path: Path, pack: EvidencePack | dict[str, Any]) -> dict[str, Any]:
    """Persist an evidence pack through the research_artifacts table."""
    pack_payload = evidence_pack_to_dict(pack) if isinstance(pack, EvidencePack) else dict(pack)
    pack_payload["pack_sha256"] = compute_pack_sha256(pack_payload)
    pack_obj = evidence_pack_from_dict(pack_payload)
    unique_dois = [item.doi for item in pack_obj.evidence_items if item.doi]
    source_doi = unique_dois[0] if len(set(unique_dois)) == 1 and unique_dois else ""
    receipt = save_research_artifact(
        db_path,
        kind=EVIDENCE_PACK_KIND,
        title=f"Evidence pack: {pack_obj.topic or pack_obj.pack_id}",
        content=evidence_pack_to_dict(pack_obj),
        source_doi=source_doi,
        metadata={
            "pack_id": pack_obj.pack_id,
            "pack_sha256": pack_obj.pack_sha256,
            "evidence_count": len(pack_obj.evidence_items),
        },
    )
    return {
        "pack_id": pack_obj.pack_id,
        "pack_sha256": pack_obj.pack_sha256,
        "artifact_id": receipt["artifact_id"],
        "kind": EVIDENCE_PACK_KIND,
        "answerable": pack_obj.answerable,
        "insufficient_evidence": list(pack_obj.insufficient_evidence),
        "evidence_count": len(pack_obj.evidence_items),
        "created_at": pack_obj.created_at,
        "preview": receipt.get("preview", ""),
    }


def _find_pack_artifact(db_path: Path, pack_id: str) -> dict[str, Any] | None:
    exact = query_research_artifacts(
        db_path,
        artifact_id=pack_id,
        kind=EVIDENCE_PACK_KIND,
        detail=True,
        limit=1,
    )
    candidates = list(exact.get("items", []))
    if not candidates:
        queried = query_research_artifacts(
            db_path,
            kind=EVIDENCE_PACK_KIND,
            query=pack_id,
            detail=True,
            limit=100,
        )
        candidates = list(queried.get("items", []))

    for item in candidates:
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        if item.get("artifact_id") == pack_id or content.get("pack_id") == pack_id:
            return dict(item)
    return None


def read_evidence_pack(db_path: Path, *, pack_id: str) -> dict[str, Any]:
    """Read a persisted evidence pack by pack id or artifact id."""
    artifact = _find_pack_artifact(db_path, pack_id)
    if artifact is None:
        return {"found": False, "pack_id": pack_id, "error": "pack_not_found"}
    content = artifact.get("content")
    if not isinstance(content, dict):
        return {"found": False, "pack_id": pack_id, "error": "invalid_pack_content"}
    pack = evidence_pack_from_dict(content)
    return {
        "found": True,
        "artifact_id": artifact.get("artifact_id", ""),
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "pack": evidence_pack_to_dict(pack),
    }


def _manifest_lookup(manifest: CanonicalBlockManifest) -> tuple[dict[str, CanonicalBlock], dict[int, CanonicalBlock]]:
    by_id = {block.block_id: block for block in manifest.blocks}
    by_ordinal = {block.ordinal: block for block in manifest.blocks}
    return by_id, by_ordinal


def _item_ref(item: EvidencePackItem) -> dict[str, Any]:
    return {
        "safe_doi": item.safe_doi,
        "doi": item.doi,
        "block_id": item.block_id,
        "canonical_uri": item.canonical_uri,
    }


def _verify_item(papers_dir: Path, item: EvidencePackItem) -> dict[str, Any]:
    result: dict[str, Any] = {
        "item": _item_ref(item),
        "snapshot_valid": bool(item.text and item.text_sha256),
        "current_valid": False,
        "missing_paper": False,
        "doc_changed": False,
        "block_missing": False,
        "block_shifted_relocated": False,
        "hash_mismatch": False,
        "ambiguous_relocation": False,
    }
    manifest = build_canonical_block_manifest(
        papers_dir,
        doi=item.doi or None,
        safe_doi=item.safe_doi or item.paper_id or None,
    )
    if manifest is None and item.doi:
        manifest = build_canonical_block_manifest(papers_dir, doi=item.doi)
    if manifest is None:
        result["missing_paper"] = True
        return result

    result["doc_changed"] = bool(item.doc_sha256 and item.doc_sha256 != manifest.doc_sha256)
    by_id, by_ordinal = _manifest_lookup(manifest)
    current = by_id.get(item.block_id)
    if current is not None:
        if current.text_sha256 == item.text_sha256:
            result["current_valid"] = not result["doc_changed"]
            return result
        result["hash_mismatch"] = True
        return result

    ordinal = parse_block_ordinal(item.block_id)
    if ordinal is not None:
        same_ordinal = by_ordinal.get(ordinal)
        if same_ordinal is not None and same_ordinal.text_sha256 != item.text_sha256:
            result["hash_mismatch"] = True

    relocated = [block for block in manifest.blocks if block.text_sha256 == item.text_sha256]
    if len(relocated) == 1:
        result["block_shifted_relocated"] = True
        result["relocated_block_id"] = relocated[0].block_id
        result["relocated_canonical_uri"] = relocated[0].canonical_uri
    elif len(relocated) > 1:
        result["ambiguous_relocation"] = True
        result["candidate_block_ids"] = [block.block_id for block in relocated]
    elif not result["hash_mismatch"]:
        result["block_missing"] = True
    return result


def verify_evidence_pack(db_path: Path, papers_dir: Path, *, pack_id: str) -> dict[str, Any]:
    """Verify a pack against the current canonical `papers/*.md` state."""
    loaded = read_evidence_pack(db_path, pack_id=pack_id)
    if not loaded.get("found"):
        return {
            "ok": False,
            "pack_id": pack_id,
            "snapshot_valid": False,
            "current_valid": False,
            "error": loaded.get("error", "pack_not_found"),
            "missing_paper": [],
            "doc_changed": [],
            "block_missing": [],
            "block_shifted_relocated": [],
            "hash_mismatch": [],
            "ambiguous_relocation": [],
        }

    pack_payload = loaded["pack"]
    assert isinstance(pack_payload, dict)
    pack = evidence_pack_from_dict(pack_payload)
    expected_hash = compute_pack_sha256(pack_payload)
    snapshot_valid = bool(pack.pack_sha256) and expected_hash == pack.pack_sha256

    item_results = [_verify_item(papers_dir, item) for item in pack.evidence_items]
    buckets: dict[str, list[dict[str, Any]]] = {
        "missing_paper": [],
        "doc_changed": [],
        "block_missing": [],
        "block_shifted_relocated": [],
        "hash_mismatch": [],
        "ambiguous_relocation": [],
    }
    for item_result in item_results:
        for key in buckets:
            if item_result.get(key):
                buckets[key].append(item_result)

    current_valid = snapshot_valid and bool(item_results) and all(
        bool(item.get("current_valid")) for item in item_results
    )
    return {
        "ok": snapshot_valid and current_valid,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "computed_pack_sha256": expected_hash,
        "snapshot_valid": snapshot_valid,
        "current_valid": current_valid,
        "evidence_count": len(pack.evidence_items),
        "item_results": item_results,
        **buckets,
    }
