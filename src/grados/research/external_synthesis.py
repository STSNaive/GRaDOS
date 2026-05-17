"""Host-side ChatGPT Pro external synthesis packet helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from grados.research.evidence_pack import (
    EvidencePack,
    EvidencePackItem,
    evidence_pack_from_dict,
    read_evidence_pack,
    verify_evidence_pack,
)
from grados.research.pack_audit import audit_answer_against_pack
from grados.research_state import query_research_artifacts, save_research_artifact

__all__ = [
    "EXTERNAL_SYNTHESIS_PACKET_KIND",
    "EXTERNAL_SYNTHESIS_RESULT_KIND",
    "audit_external_synthesis_result",
    "prepare_external_synthesis_packet",
    "preview_external_synthesis_packet",
    "save_external_synthesis_result",
]

EXTERNAL_SYNTHESIS_PACKET_KIND = "external_synthesis_packet"
EXTERNAL_SYNTHESIS_RESULT_KIND = "external_synthesis_result"
EXTERNAL_SYNTHESIS_PROTOCOL_VERSION = "external-synthesis-v1"

ExternalSynthesisMode = Literal["review", "synthesize"]

_ANCHOR_ID_PATTERN = re.compile(r"\banchor_[0-9]{3,}\b")
_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


def _normalize_mode(mode: str | None) -> ExternalSynthesisMode:
    normalized = (mode or "review").strip().lower()
    if normalized not in {"review", "synthesize"}:
        raise ValueError("mode must be `review` or `synthesize`.")
    return normalized  # type: ignore[return-value]


def _load_pack(db_path: Path, pack_id: str) -> tuple[EvidencePack | None, dict[str, Any]]:
    loaded = read_evidence_pack(db_path, pack_id=pack_id)
    if not loaded.get("found"):
        return None, {
            "ok": False,
            "error": str(loaded.get("error", "pack_not_found")),
            "pack_id": pack_id,
            "loaded": loaded,
        }
    content = loaded.get("pack")
    if not isinstance(content, dict):
        return None, {
            "ok": False,
            "error": "invalid_pack_content",
            "pack_id": pack_id,
            "loaded": loaded,
        }
    return evidence_pack_from_dict(content), loaded


def _blocked_packet_result(
    *,
    pack_id: str,
    mode: ExternalSynthesisMode,
    verify_result: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "sendable": False,
        "saved": False,
        "mode": mode,
        "pack_id": pack_id,
        "error": error,
        "blocked_reasons": [error],
        "verify": verify_result,
    }


def _short_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.strip().split())
    max_chars = max(80, max_chars)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _first_sentence(text: str, max_chars: int) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return ""
    match = re.search(r"(?<=[.!?])\s+", normalized)
    if match:
        normalized = normalized[: match.start()].strip()
    return _short_text(normalized, max_chars)


def _anchor_id(index: int) -> str:
    return f"anchor_{index:03d}"


def _packet_item(
    item: EvidencePackItem,
    *,
    index: int,
    max_excerpt_chars: int,
) -> dict[str, Any]:
    limitations = [
        "Use only this canonical excerpt and its metadata; do not infer beyond it.",
        "Final citation requires canonical reread through read_saved_paper or a current-valid pack.",
    ]
    if item.subquestion:
        limitations.append(f"Prepared for subquestion: {item.subquestion}")
    return {
        "anchor_id": _anchor_id(index),
        "doi": item.doi,
        "safe_doi": item.safe_doi,
        "paper_id": item.paper_id,
        "canonical_uri": item.canonical_uri,
        "paragraph_start": item.source_paragraph_index,
        "paragraph_count": 1,
        "block_id": item.block_id,
        "block_type": item.block_type,
        "heading_path": list(item.heading_path),
        "title": item.title,
        "authors": list(item.authors),
        "year": item.year,
        "journal": item.journal,
        "short_excerpt": _short_text(item.text, max_excerpt_chars),
        "candidate_claim": _first_sentence(item.text, min(240, max_excerpt_chars)),
        "limitations": limitations,
    }


def _packet_prompt_body(packet_without_prompt: dict[str, Any]) -> str:
    packet_json = json.dumps(packet_without_prompt, ensure_ascii=False, indent=2, sort_keys=True)
    mode = str(packet_without_prompt.get("mode", "review"))
    return (
        "You are a host-side ChatGPT Pro reviewer/synthesizer for a GRaDOS evidence packet.\n\n"
        f"Mode: {mode}\n\n"
        "Hard rules:\n"
        "- Use only the provided evidence anchors and anchor_ids.\n"
        "- Do not add papers, DOIs, facts, citations, or web evidence that are not in the packet.\n"
        "- If evidence is missing, report it under missing_evidence instead of filling the gap.\n"
        "- Treat every excerpt as advisory until the host rereads canonical paragraphs in GRaDOS.\n\n"
        "Return structured Markdown or JSON-like text with:\n"
        "- claims: each item must include text, anchor_ids, confidence, and caveat.\n"
        "- missing_evidence: gaps or unsupported claims.\n"
        "- forbidden_or_outside_content: any temptation to use pack-external material.\n\n"
        "Evidence packet:\n"
        "```json\n"
        f"{packet_json}\n"
        "```"
    )


def _build_packet(
    pack: EvidencePack,
    verify_result: dict[str, Any],
    *,
    mode: ExternalSynthesisMode,
    max_items: int,
    max_excerpt_chars: int,
) -> dict[str, Any]:
    max_items = max(1, min(max_items, 50))
    max_excerpt_chars = max(120, min(max_excerpt_chars, 2000))
    packet_items = [
        _packet_item(item, index=index, max_excerpt_chars=max_excerpt_chars)
        for index, item in enumerate(pack.evidence_items[:max_items], 1)
    ]
    payload: dict[str, Any] = {
        "schema_version": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "kind": EXTERNAL_SYNTHESIS_PACKET_KIND,
        "mode": mode,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "topic": pack.topic,
        "query": pack.query,
        "answerable": pack.answerable,
        "source": "verified_evidence_pack",
        "verify": {
            "current_valid": bool(verify_result.get("current_valid")),
            "snapshot_valid": bool(verify_result.get("snapshot_valid")),
            "evidence_count": int(verify_result.get("evidence_count") or 0),
        },
        "items": packet_items,
        "item_count": len(packet_items),
        "omitted_evidence_count": max(0, len(pack.evidence_items) - len(packet_items)),
        "insufficient_evidence": list(pack.insufficient_evidence),
        "forbidden": [
            "Do not introduce pack-external papers, DOIs, facts, citations, or web evidence.",
            "Do not treat ChatGPT Pro output as final citation evidence.",
            "Do not cite without a later GRaDOS canonical reread or current-valid evidence pack.",
        ],
    }
    host_prompt = _packet_prompt_body(payload)
    payload["host_prompt"] = host_prompt
    payload["prompt_hash"] = hashlib.sha256(host_prompt.encode("utf-8")).hexdigest()
    payload["estimated_chars"] = len(host_prompt)
    payload["estimated_tokens"] = max(1, len(host_prompt) // 4)
    return payload


def _packet_preview(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "sendable": True,
        "saved": False,
        "mode": packet["mode"],
        "pack_id": packet["pack_id"],
        "pack_sha256": packet["pack_sha256"],
        "prompt_hash": packet["prompt_hash"],
        "packet_item_count": packet["item_count"],
        "omitted_evidence_count": packet["omitted_evidence_count"],
        "estimated_chars": packet["estimated_chars"],
        "estimated_tokens": packet["estimated_tokens"],
        "verify": packet["verify"],
        "blocked_reasons": [],
        "prompt_skeleton": (
            "Send the packet to ChatGPT Pro only after the gate is enabled. Ask for claims, "
            "anchor_ids, confidence, caveats, missing_evidence, and forbidden_or_outside_content."
        ),
        "host_guidance": [
            "Use one ChatGPT Pro conversation per GRaDOS workflow.",
            "Save the Pro response with save_external_synthesis_result before using it.",
            "Run audit_external_synthesis_result and reread canonical windows before final citation.",
        ],
    }


def preview_external_synthesis_packet(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    mode: str = "review",
    max_items: int = 25,
    max_excerpt_chars: int = 700,
) -> dict[str, Any]:
    """Preview a ChatGPT Pro packet without saving or contacting external services."""
    resolved_mode = _normalize_mode(mode)
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return _blocked_packet_result(
            pack_id=pack_id,
            mode=resolved_mode,
            verify_result=dict(loaded),
            error=str(loaded.get("error", "pack_not_found")),
        )
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    if not verify_result.get("current_valid"):
        return _blocked_packet_result(
            pack_id=pack.pack_id,
            mode=resolved_mode,
            verify_result=verify_result,
            error="evidence_pack_not_current_valid",
        )
    packet = _build_packet(
        pack,
        verify_result,
        mode=resolved_mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
    )
    return _packet_preview(packet)


def prepare_external_synthesis_packet(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    mode: str = "review",
    max_items: int = 25,
    max_excerpt_chars: int = 700,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a current-valid evidence packet for host-side ChatGPT Pro use."""
    resolved_mode = _normalize_mode(mode)
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return _blocked_packet_result(
            pack_id=pack_id,
            mode=resolved_mode,
            verify_result=dict(loaded),
            error=str(loaded.get("error", "pack_not_found")),
        )
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    if not verify_result.get("current_valid"):
        return _blocked_packet_result(
            pack_id=pack.pack_id,
            mode=resolved_mode,
            verify_result=verify_result,
            error="evidence_pack_not_current_valid",
        )
    packet = _build_packet(
        pack,
        verify_result,
        mode=resolved_mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
    )
    artifact_metadata = {
        **(metadata or {}),
        "protocol": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "prompt_hash": packet["prompt_hash"],
        "mode": resolved_mode,
        "evidence_count": packet["item_count"],
    }
    receipt = save_research_artifact(
        db_path,
        kind=EXTERNAL_SYNTHESIS_PACKET_KIND,
        title=f"External synthesis packet: {pack.topic or pack.pack_id}",
        content=packet,
        metadata=artifact_metadata,
    )
    return {
        **_packet_preview(packet),
        "saved": True,
        "artifact_id": receipt["artifact_id"],
        "kind": EXTERNAL_SYNTHESIS_PACKET_KIND,
        "metadata": receipt["metadata"],
        "packet": packet,
        "host_prompt": packet["host_prompt"],
    }


def _read_artifact(db_path: Path, artifact_id: str) -> dict[str, Any] | None:
    result = query_research_artifacts(db_path, artifact_id=artifact_id, detail=True, limit=1)
    items = result.get("items", [])
    if not isinstance(items, list) or not items:
        return None
    item = items[0]
    return dict(item) if isinstance(item, dict) else None


def _response_text(raw_response: str | dict[str, Any]) -> str:
    if isinstance(raw_response, str):
        return raw_response
    return json.dumps(raw_response, ensure_ascii=False, indent=2, sort_keys=True)


def save_external_synthesis_result(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    response: str | dict[str, Any],
    packet_artifact_id: str = "",
    prompt_hash: str = "",
    conversation_url: str = "",
    model_label: str = "",
    thinking_label: str = "",
    mode: str = "review",
    claims: list[dict[str, Any]] | None = None,
    gaps: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a host-provided ChatGPT Pro response as advisory research state."""
    resolved_mode = _normalize_mode(mode)
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return {
            "ok": False,
            "saved": False,
            "pack_id": pack_id,
            "error": str(loaded.get("error", "pack_not_found")),
        }
    packet_content: dict[str, Any] | None = None
    if packet_artifact_id:
        packet_artifact = _read_artifact(db_path, packet_artifact_id)
        if packet_artifact is None or packet_artifact.get("kind") != EXTERNAL_SYNTHESIS_PACKET_KIND:
            return {
                "ok": False,
                "saved": False,
                "pack_id": pack.pack_id,
                "error": "packet_artifact_not_found",
            }
        content = packet_artifact.get("content")
        if not isinstance(content, dict) or content.get("pack_id") != pack.pack_id:
            return {
                "ok": False,
                "saved": False,
                "pack_id": pack.pack_id,
                "error": "packet_artifact_pack_mismatch",
            }
        packet_content = content
        prompt_hash = prompt_hash or str(content.get("prompt_hash", "") or "")
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    text = _response_text(response)
    response_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    content_payload: dict[str, Any] = {
        "schema_version": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "kind": EXTERNAL_SYNTHESIS_RESULT_KIND,
        "mode": resolved_mode,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "conversation_url": conversation_url,
        "model_label": model_label,
        "thinking_label": thinking_label,
        "raw_response": response,
        "response_text": text,
        "response_sha256": response_hash,
        "claims": claims or [],
        "gaps": gaps or [],
        "verify_at_save": verify_result,
        "advisory_only": True,
        "next_action": "audit_external_synthesis_result",
    }
    if packet_content is not None:
        content_payload["packet"] = {
            "artifact_id": packet_artifact_id,
            "prompt_hash": prompt_hash,
            "item_count": packet_content.get("item_count"),
        }
    artifact_metadata = {
        **(metadata or {}),
        "protocol": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "response_sha256": response_hash,
        "mode": resolved_mode,
        "model_label": model_label,
        "thinking_label": thinking_label,
        "conversation_url": conversation_url,
    }
    receipt = save_research_artifact(
        db_path,
        kind=EXTERNAL_SYNTHESIS_RESULT_KIND,
        title=f"External synthesis result: {pack.topic or pack.pack_id}",
        content=content_payload,
        metadata=artifact_metadata,
    )
    return {
        "ok": True,
        "saved": True,
        "artifact_id": receipt["artifact_id"],
        "kind": EXTERNAL_SYNTHESIS_RESULT_KIND,
        "pack_id": pack.pack_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "response_sha256": response_hash,
        "verify": verify_result,
        "next_action": "audit_external_synthesis_result",
        "metadata": receipt["metadata"],
    }


def _allowed_refs_from_pack(pack: EvidencePack) -> dict[str, set[str]]:
    anchor_ids = {_anchor_id(index) for index, _ in enumerate(pack.evidence_items, 1)}
    return {
        "anchor_ids": anchor_ids,
        "block_ids": {item.block_id for item in pack.evidence_items if item.block_id},
        "canonical_uris": {item.canonical_uri for item in pack.evidence_items if item.canonical_uri},
        "dois": {item.doi.lower() for item in pack.evidence_items if item.doi},
    }


def _collect_anchor_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(match.group(0) for match in _ANCHOR_ID_PATTERN.finditer(value))
    elif isinstance(value, dict):
        for nested in value.values():
            refs.update(_collect_anchor_refs(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.update(_collect_anchor_refs(nested))
    return refs


def _extract_dois(text: str) -> set[str]:
    dois: set[str] = set()
    for match in _DOI_PATTERN.finditer(text):
        doi = match.group(0).rstrip(".,;:)]}>\"'").lower()
        dois.add(doi)
    return dois


def audit_external_synthesis_result(
    db_path: Path,
    papers_dir: Path,
    *,
    result_id: str,
    strict: bool = True,
    citation_style: str = "author_year",
) -> dict[str, Any]:
    """Audit a saved ChatGPT Pro result against its source evidence pack."""
    artifact = _read_artifact(db_path, result_id)
    if artifact is None:
        return {"ok": False, "result_id": result_id, "error": "result_not_found"}
    if artifact.get("kind") != EXTERNAL_SYNTHESIS_RESULT_KIND:
        return {
            "ok": False,
            "result_id": result_id,
            "error": "artifact_is_not_external_synthesis_result",
            "kind": artifact.get("kind", ""),
        }
    content = artifact.get("content")
    if not isinstance(content, dict):
        return {"ok": False, "result_id": result_id, "error": "invalid_result_content"}
    pack_id = str(content.get("pack_id") or "")
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return {
            "ok": False,
            "result_id": result_id,
            "pack_id": pack_id,
            "error": str(loaded.get("error", "pack_not_found")),
        }
    response_text = str(content.get("response_text") or _response_text(content.get("raw_response", "")))
    allowed = _allowed_refs_from_pack(pack)
    referenced_anchor_ids = _collect_anchor_refs(content)
    unknown_anchor_ids = sorted(referenced_anchor_ids - allowed["anchor_ids"])
    referenced_dois = _extract_dois(response_text)
    outside_dois = sorted(referenced_dois - allowed["dois"])
    pack_audit = audit_answer_against_pack(
        db_path,
        papers_dir,
        pack_id=pack.pack_id,
        draft=response_text,
        strict=strict,
        citation_style=citation_style,
        return_claim_map=True,
    )
    claims = [
        claim
        for claim in pack_audit.get("claims", [])
        if isinstance(claim, dict)
    ]
    usable_claim_ids = [
        str(claim.get("claim_id"))
        for claim in claims
        if claim.get("verdict") == "verified"
    ]
    claims_requiring_revision = [
        {
            "claim_id": str(claim.get("claim_id")),
            "verdict": str(claim.get("verdict")),
            "issue_type": str(claim.get("issue_type")),
            "revision_action": str(claim.get("revision_action")),
        }
        for claim in claims
        if claim.get("verdict") != "verified"
    ]
    verdict_counts = pack_audit.get("verdict_counts", {})
    non_verified = sum(
        int(count)
        for verdict, count in (verdict_counts.items() if isinstance(verdict_counts, dict) else [])
        if verdict != "verified"
    )
    verify_result = pack_audit.get("verify", {})
    ready_for_canonical_reread = (
        bool(isinstance(verify_result, dict) and verify_result.get("current_valid"))
        and not unknown_anchor_ids
        and not outside_dois
        and non_verified == 0
    )
    return {
        "ok": ready_for_canonical_reread,
        "result_id": result_id,
        "pack_id": pack.pack_id,
        "advisory_only": True,
        "ready_for_canonical_reread": ready_for_canonical_reread,
        "referenced_anchor_ids": sorted(referenced_anchor_ids),
        "unknown_anchor_ids": unknown_anchor_ids,
        "pack_outside_dois": outside_dois,
        "usable_claim_ids": usable_claim_ids,
        "claims_requiring_revision": claims_requiring_revision,
        "audit": pack_audit,
        "next_action": (
            "Reread verified canonical windows with read_saved_paper before final citation."
            if ready_for_canonical_reread
            else "Revise or gather evidence before using this external synthesis."
        ),
    }
