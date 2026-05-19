"""Pack-scoped draft audit helpers."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from grados.research.draft_audit import (
    VERDICT_MAJOR_DISTORTION,
    VERDICT_MINOR_DISTORTION,
    VERDICT_UNVERIFIABLE,
    VERDICT_UNVERIFIABLE_ACCESS,
    VERDICT_VERIFIED,
    _extract_citation_markers,
    _split_claims,
    _strip_citations,
)
from grados.research.evidence_pack import (
    EvidencePack,
    EvidencePackItem,
    evidence_pack_from_dict,
    read_evidence_pack,
    verify_evidence_pack,
)

__all__ = [
    "audit_answer_against_pack",
    "suggest_missing_evidence",
]

_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]{2,}", re.IGNORECASE)
_GENERALIZER_PATTERN = re.compile(
    r"\b(all|always|never|every|entirely|fully|proves?|guarantees?)\b|"
    r"(所有|全部|总是|从不|完全|证明|保证)"
)


def _normalize_token(token: str) -> str:
    token = token.lower().strip()
    for suffix in ("ingly", "edly", "ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _normalize_token(match.group(0))
        for match in _WORD_PATTERN.finditer(text.lower())
        if len(match.group(0)) >= 2
    }


def _overlap_score(claim_text: str, evidence_text: str) -> float:
    claim_tokens = _tokens(claim_text)
    evidence_tokens = _tokens(evidence_text)
    if not claim_tokens or not evidence_tokens:
        return 0.0
    overlap = len(claim_tokens & evidence_tokens)
    return overlap / math.sqrt(len(claim_tokens) * len(evidence_tokens))


def _citation_matches_item(marker: Any, item: EvidencePackItem) -> bool:
    if getattr(marker, "style", "") != "author_year":
        return True
    marker_author = str(getattr(marker, "author", "") or "").lower()
    marker_year = str(getattr(marker, "year", "") or "")
    if not marker_author or not marker_year:
        return False
    item_authors = [author.lower() for author in item.authors]
    return item.year == marker_year and any(marker_author in author for author in item_authors)


def _claim_verdict(
    claim_text: str,
    markers: list[Any],
    ranked: list[tuple[EvidencePackItem, float]],
    *,
    strict: bool,
) -> dict[str, Any]:
    if not ranked:
        return {
            "verdict": VERDICT_UNVERIFIABLE,
            "severity": "blocking",
            "issue_type": "no_supporting_passage",
            "revision_action": "search_and_prepare_evidence_pack",
            "mismatch_detail": "The evidence pack contains no candidate passage for this claim.",
            "confidence": 0.0,
            "requires_canonical_reread": True,
        }
    top_score = ranked[0][1]
    if top_score < 0.18:
        return {
            "verdict": VERDICT_UNVERIFIABLE,
            "severity": "blocking",
            "issue_type": "no_supporting_passage",
            "revision_action": "search_and_prepare_evidence_pack",
            "mismatch_detail": "The best pack passage is below the support threshold.",
            "confidence": round(float(top_score), 6),
            "requires_canonical_reread": True,
        }
    if top_score < 0.32:
        return {
            "verdict": VERDICT_MINOR_DISTORTION,
            "severity": "minor",
            "issue_type": "low_confidence_support",
            "revision_action": "revise_wording_or_add_locator",
            "mismatch_detail": "A related pack passage exists, but the overlap is too weak for verification.",
            "confidence": round(float(top_score), 6),
            "requires_canonical_reread": True,
        }
    if strict and _GENERALIZER_PATTERN.search(claim_text) and top_score < 0.62:
        return {
            "verdict": VERDICT_MINOR_DISTORTION,
            "severity": "minor",
            "issue_type": "scope_overreach",
            "revision_action": "revise_wording_or_add_locator",
            "mismatch_detail": "The claim uses broad language that the pack evidence does not fully cover.",
            "confidence": round(float(top_score), 6),
            "requires_canonical_reread": True,
        }
    if markers and any(getattr(marker, "style", "") == "author_year" for marker in markers):
        if not any(_citation_matches_item(marker, item) for item, _ in ranked[:3] for marker in markers):
            return {
                "verdict": VERDICT_MAJOR_DISTORTION,
                "severity": "major",
                "issue_type": "citation_mismatch",
                "revision_action": "rewrite_or_replace_citation",
                "mismatch_detail": "The pack has related evidence, but not from the cited author-year source.",
                "confidence": round(float(top_score), 6),
                "requires_canonical_reread": True,
            }
    if strict and not markers:
        return {
            "verdict": VERDICT_UNVERIFIABLE,
            "severity": "blocking",
            "issue_type": "missing_citation",
            "revision_action": "add_citation_or_locator",
            "mismatch_detail": "Strict pack audit requires a citation marker for factual claims.",
            "confidence": round(float(top_score), 6),
            "requires_canonical_reread": True,
        }
    return {
        "verdict": VERDICT_VERIFIED,
        "severity": "none",
        "issue_type": "",
        "revision_action": "keep",
        "mismatch_detail": "",
        "confidence": round(float(top_score), 6),
        "requires_canonical_reread": False,
    }


def _access_verdict(reason: str) -> dict[str, Any]:
    return {
        "verdict": VERDICT_UNVERIFIABLE_ACCESS,
        "severity": "access",
        "issue_type": reason,
        "revision_action": "reacquire_full_text_or_switch_parser",
        "mismatch_detail": "The evidence pack cannot be treated as current canonical full-text evidence.",
        "confidence": 0.0,
        "requires_canonical_reread": True,
    }


def _rank_evidence(claim_text: str, items: list[EvidencePackItem]) -> list[tuple[EvidencePackItem, float]]:
    ranked = [(item, _overlap_score(claim_text, item.text)) for item in items]
    return [(item, score) for item, score in sorted(ranked, key=lambda pair: pair[1], reverse=True) if score > 0]


def _load_pack(db_path: Path, pack_id: str) -> tuple[EvidencePack | None, dict[str, Any]]:
    loaded = read_evidence_pack(db_path, pack_id=pack_id)
    if not loaded.get("found"):
        return None, loaded
    pack_payload = loaded.get("pack")
    if not isinstance(pack_payload, dict):
        return None, {"found": False, "pack_id": pack_id, "error": "invalid_pack_content"}
    return evidence_pack_from_dict(pack_payload), loaded


def audit_answer_against_pack(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    draft: str,
    strict: bool = True,
    citation_style: str = "author_year",
    return_claim_map: bool = True,
    include_suggestions: bool = False,
    max_suggestions: int = 8,
) -> dict[str, Any]:
    """Audit draft claims using only materialized evidence items from one pack."""
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return {
            "pack_id": pack_id,
            "strict": strict,
            "search_scope": "pack_only",
            "claims_checked": 0,
            "verdict_counts": {},
            "claims": [],
            "claim_map": [],
            "error": loaded.get("error", "pack_not_found"),
        }

    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    pack_is_current = bool(verify_result.get("current_valid"))
    claims = _split_claims(draft)
    audited_claims: list[dict[str, Any]] = []
    verdict_counts: Counter[str] = Counter()

    for index, claim in enumerate(claims, 1):
        query_text = _strip_citations(claim)
        markers = _extract_citation_markers(claim, citation_style)
        ranked = _rank_evidence(query_text, pack.evidence_items)
        if strict and not pack_is_current:
            verdict_payload = _access_verdict("stale_pack")
        else:
            verdict_payload = _claim_verdict(query_text, markers, ranked, strict=strict)
        evidence = [
            {
                "canonical_uri": item.canonical_uri,
                "paper_id": item.paper_id,
                "safe_doi": item.safe_doi,
                "doi": item.doi,
                "block_id": item.block_id,
                "block_type": item.block_type,
                "text_sha256": item.text_sha256,
                "score": score,
                "snippet": item.text[:320],
            }
            for item, score in ranked[:5]
        ]
        audited = {
            "claim_id": f"claim_{index}",
            "text": claim,
            "query_text": query_text,
            "verdict": str(verdict_payload["verdict"]),
            "citation_marker_present": bool(markers),
            "citations": [asdict(marker) for marker in markers],
            "evidence": evidence,
            "severity": str(verdict_payload["severity"]),
            "issue_type": str(verdict_payload["issue_type"]),
            "revision_action": str(verdict_payload["revision_action"]),
            "mismatch_detail": str(verdict_payload["mismatch_detail"]),
            "confidence": float(verdict_payload["confidence"]),
            "requires_canonical_reread": bool(verdict_payload["requires_canonical_reread"]),
        }
        audited_claims.append(audited)
        verdict_counts[str(verdict_payload["verdict"])] += 1

    claim_map: list[dict[str, Any]] = []
    if return_claim_map:
        claim_map = [
            {
                "claim_id": claim["claim_id"],
                "verdict": claim["verdict"],
                "issue_type": claim["issue_type"],
                "revision_action": claim["revision_action"],
                "evidence_block_ids": [
                    str(item["block_id"]) for item in claim["evidence"] if item.get("block_id")
                ],
            }
            for claim in audited_claims
        ]

    result: dict[str, Any] = {
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "strict": strict,
        "search_scope": "pack_only",
        "claims_checked": len(audited_claims),
        "verdict_counts": dict(verdict_counts),
        "claims": audited_claims,
        "claim_map": claim_map,
        "verify": verify_result,
    }
    if include_suggestions:
        result["suggestions"] = suggest_missing_evidence(
            db_path,
            papers_dir,
            pack_id=pack.pack_id,
            draft=draft,
            max_suggestions=max_suggestions,
        )
    return result


def suggest_missing_evidence(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    draft: str,
    max_suggestions: int = 8,
) -> dict[str, Any]:
    """Suggest follow-up evidence queries without changing strict audit results."""
    audit = audit_answer_against_pack(
        db_path,
        papers_dir,
        pack_id=pack_id,
        draft=draft,
        strict=True,
        return_claim_map=False,
    )
    actionable_verdicts = {
        VERDICT_MINOR_DISTORTION,
        VERDICT_MAJOR_DISTORTION,
        VERDICT_UNVERIFIABLE,
        VERDICT_UNVERIFIABLE_ACCESS,
    }
    next_actions = {
        VERDICT_MINOR_DISTORTION: "revise_wording_or_add_locator",
        VERDICT_MAJOR_DISTORTION: "rewrite_or_replace_citation",
        VERDICT_UNVERIFIABLE: "search_and_prepare_evidence_pack",
        VERDICT_UNVERIFIABLE_ACCESS: "reacquire_full_text_or_switch_parser",
    }
    suggestions: list[dict[str, Any]] = []
    for claim in audit.get("claims", []):
        if len(suggestions) >= max(1, max_suggestions):
            break
        verdict = str(claim.get("verdict", ""))
        if verdict not in actionable_verdicts:
            continue
        suggestions.append(
            {
                "claim_id": claim.get("claim_id", ""),
                "verdict": verdict,
                "issue_type": claim.get("issue_type", ""),
                "suggested_query": claim.get("query_text", claim.get("text", "")),
                "reason": claim.get("issue_type") or "pack_evidence_insufficient",
                "next_action": next_actions.get(verdict, "search_and_prepare_evidence_pack"),
            }
        )

    return {
        "pack_id": pack_id,
        "mode": "suggestion_only",
        "search_scope": "none",
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    }
