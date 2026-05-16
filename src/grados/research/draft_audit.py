"""Draft-audit helpers for evidence support and attribution checks."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TypedDict

from grados.research.models import (
    AuditCitationMarker,
    AuditedClaim,
    AuditEvidenceItem,
    ClaimMapEntry,
    DraftAuditResult,
)
from grados.storage.paths import resolve_papers_dir
from grados.storage.retrieval import PaperSearchResult
from grados.storage.vector import search_papers

VERDICT_VERIFIED = "verified"
VERDICT_MINOR_DISTORTION = "minor_distortion"
VERDICT_MAJOR_DISTORTION = "major_distortion"
VERDICT_UNVERIFIABLE = "unverifiable"
VERDICT_UNVERIFIABLE_ACCESS = "unverifiable_access"


class DraftVerdictPayload(TypedDict):
    verdict: str
    severity: str
    issue_type: str
    revision_action: str
    mismatch_detail: str
    confidence: float
    requires_canonical_reread: bool


def _split_claims(draft_text: str) -> list[str]:
    claims: list[str] = []
    for block in re.split(r"\n{2,}", draft_text.strip()):
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        sentences = re.split(r"(?<=[。！？])|(?<=[.!?])\s+", block)
        for sentence in sentences:
            candidate = sentence.strip()
            if len(candidate) >= 20:
                claims.append(candidate)
    return claims


def _normalize_citation_piece(piece: str) -> str:
    normalized = piece.translate(str.maketrans("（），；【】", "(),;[]"))
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_citation_markers(text: str, citation_style: str) -> list[AuditCitationMarker]:
    markers: list[AuditCitationMarker] = []
    bracket_chunks = re.findall(r"[\[【]([^\]】]+)[\]】]", text)
    paren_chunks = re.findall(r"[\(（]([^\)）]+)[\)）]", text) if citation_style == "author_year" else []
    for chunk in bracket_chunks + paren_chunks:
        normalized_chunk = _normalize_citation_piece(chunk)
        if citation_style == "numeric":
            if re.search(r"\d", normalized_chunk):
                markers.append(AuditCitationMarker(style="numeric", marker=normalized_chunk))
            continue
        for piece in re.split(r"[;；]", normalized_chunk):
            normalized_piece = _normalize_citation_piece(piece)
            if not normalized_piece:
                continue
            match = re.search(
                r"([A-Z][A-Za-z'`-]+|[\u3400-\u9fff]{1,8}).*?(\d{4})",
                normalized_piece,
            )
            if match:
                author = re.sub(r"(等|等人)$", "", match.group(1).lower())
                markers.append(
                    AuditCitationMarker(
                        style="author_year",
                        author=author,
                        year=match.group(2),
                        marker=normalized_piece,
                    )
                )
    return markers


def _strip_citations(text: str) -> str:
    stripped = re.sub(r"[\[【][^\]】]+[\]】]", "", text)
    stripped = re.sub(r"[\(（][^\)）]+\d{4}[^\)）]*[\)）]", "", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _audit_query_cache_key(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip().lower()


def _canonical_uri(safe_doi: str) -> str:
    return f"grados://papers/{safe_doi}" if safe_doi else ""


def _citation_matches_result(marker: AuditCitationMarker, result: PaperSearchResult) -> bool:
    if marker.style != "author_year":
        return True
    authors = [str(value).lower() for value in result.authors]
    year = result.year
    author = marker.author
    return bool(authors) and any(author in candidate for candidate in authors) and year == marker.year


def _citation_style_supports_attribution(citation_style: str) -> bool:
    return citation_style == "author_year"


def _has_canonical_anchor(result: PaperSearchResult) -> bool:
    safe_doi = str(getattr(result, "safe_doi", "") or "")
    paragraph_count = int(getattr(result, "paragraph_count", 0) or 0)
    return bool(safe_doi) and paragraph_count > 0


def _draft_verdict(
    *,
    evidence: list[PaperSearchResult],
    markers: list[AuditCitationMarker],
    citation_style: str,
    strictness: str,
) -> DraftVerdictPayload:
    top_score = evidence[0].score if evidence else 0.0
    confidence = round(float(top_score), 6)
    if not evidence or top_score < 0.55:
        return {
            "verdict": VERDICT_UNVERIFIABLE,
            "severity": "blocking",
            "issue_type": "no_supporting_passage",
            "revision_action": "search_and_prepare_evidence_pack",
            "mismatch_detail": "No local evidence candidate reached the first-pass support threshold.",
            "confidence": confidence,
            "requires_canonical_reread": True,
        }

    top_has_anchor = _has_canonical_anchor(evidence[0])
    if top_score >= 1.1 and not top_has_anchor:
        return {
            "verdict": VERDICT_UNVERIFIABLE_ACCESS,
            "severity": "access",
            "issue_type": "missing_canonical_anchor",
            "revision_action": "reacquire_full_text_or_switch_parser",
            "mismatch_detail": "A high-scoring candidate exists, but no canonical paragraph window is available.",
            "confidence": confidence,
            "requires_canonical_reread": True,
        }

    if markers and evidence and _citation_style_supports_attribution(citation_style):
        marker_matched = any(
            _citation_matches_result(marker, result)
            for marker in markers
            for result in evidence
        )
        if not marker_matched:
            if strictness == "strict":
                return {
                    "verdict": VERDICT_MAJOR_DISTORTION,
                    "severity": "major",
                    "issue_type": "citation_mismatch",
                    "revision_action": "rewrite_or_replace_citation",
                    "mismatch_detail": (
                        "The claim retrieved evidence, but not from the resolvable cited author-year source."
                    ),
                    "confidence": confidence,
                    "requires_canonical_reread": True,
                }
            return {
                "verdict": VERDICT_MINOR_DISTORTION,
                "severity": "minor",
                "issue_type": "citation_mismatch",
                "revision_action": "revise_wording_or_add_locator",
                "mismatch_detail": "Balanced mode keeps the citation mismatch as a revision note.",
                "confidence": confidence,
                "requires_canonical_reread": True,
            }

    if strictness == "strict" and not markers:
        return {
            "verdict": VERDICT_MINOR_DISTORTION,
            "severity": "minor",
            "issue_type": "missing_citation",
            "revision_action": "add_citation_or_locator",
            "mismatch_detail": "The claim has relevant local evidence but no citation marker in strict audit mode.",
            "confidence": confidence,
            "requires_canonical_reread": True,
        }

    if top_score >= 1.1:
        return {
            "verdict": VERDICT_VERIFIED,
            "severity": "none",
            "issue_type": "",
            "revision_action": "keep",
            "mismatch_detail": "",
            "confidence": confidence,
            "requires_canonical_reread": False,
        }

    return {
        "verdict": VERDICT_MINOR_DISTORTION,
        "severity": "minor",
        "issue_type": "low_confidence_support",
        "revision_action": "revise_wording_or_add_locator",
        "mismatch_detail": "Relevant evidence exists, but first-pass support is below the verified threshold.",
        "confidence": confidence,
        "requires_canonical_reread": True,
    }


def audit_draft_support(
    chroma_dir: Path,
    *,
    draft_text: str,
    citation_style: str = "author_year",
    strictness: str = "strict",
    candidate_limit: int = 3,
    return_claim_map: bool = True,
) -> DraftAuditResult:
    """Audit draft claims against the local evidence store."""
    papers_dir = resolve_papers_dir(chroma_dir)
    claims = _split_claims(draft_text)
    candidate_limit = max(1, candidate_limit)
    audited_claims: list[AuditedClaim] = []
    verdict_counts: Counter[str] = Counter()
    evidence_cache: dict[str, list[PaperSearchResult]] = {}

    for index, claim in enumerate(claims, 1):
        markers = _extract_citation_markers(claim, citation_style)
        search_query = _strip_citations(claim)
        evidence: list[PaperSearchResult] = []
        if search_query:
            cache_key = _audit_query_cache_key(search_query)
            cached = evidence_cache.get(cache_key)
            if cached is None:
                cached = search_papers(
                    chroma_dir,
                    search_query,
                    limit=candidate_limit,
                    papers_dir=papers_dir,
                    use_reranking=True,
                )
                evidence_cache[cache_key] = cached
            evidence = cached
        verdict_payload = _draft_verdict(
            evidence=evidence,
            markers=markers,
            citation_style=citation_style,
            strictness=strictness,
        )

        entry = AuditedClaim(
            claim_id=f"claim_{index}",
            text=claim,
            query_text=search_query,
            verdict=str(verdict_payload["verdict"]),
            citation_marker_present=bool(markers),
            citations=markers,
            evidence=[
                AuditEvidenceItem(
                    doi=item.doi,
                    safe_doi=item.safe_doi,
                    canonical_uri=_canonical_uri(item.safe_doi),
                    title=item.title,
                    year=item.year,
                    section_name=item.section_name,
                    paragraph_start=item.paragraph_start if item.paragraph_count > 0 else None,
                    paragraph_count=item.paragraph_count if item.paragraph_count > 0 else None,
                    snippet=item.snippet,
                    score=item.score,
                    dense_score=item.dense_score,
                    lexical_score=item.lexical_score,
                )
                for item in evidence
            ],
            severity=str(verdict_payload["severity"]),
            issue_type=str(verdict_payload["issue_type"]),
            revision_action=str(verdict_payload["revision_action"]),
            mismatch_detail=str(verdict_payload["mismatch_detail"]),
            confidence=float(verdict_payload["confidence"]),
            requires_canonical_reread=bool(verdict_payload["requires_canonical_reread"]),
        )
        audited_claims.append(entry)
        verdict_counts[entry.verdict] += 1

    claim_map: list[ClaimMapEntry] = []
    if return_claim_map:
        claim_map = [
            ClaimMapEntry(
                claim_id=item.claim_id,
                verdict=item.verdict,
                evidence_dois=[evidence.doi for evidence in item.evidence],
                issue_type=item.issue_type,
                revision_action=item.revision_action,
            )
            for item in audited_claims
        ]
    return DraftAuditResult(
        claims_checked=len(audited_claims),
        verdict_counts=dict(verdict_counts),
        claims=audited_claims,
        claim_map=claim_map,
    )
