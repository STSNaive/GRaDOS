"""Draft-audit helpers for evidence support and attribution checks."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

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
    status_counts: Counter[str] = Counter()
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
        top_score = evidence[0].score if evidence else 0.0
        status = "unsupported"
        if top_score >= 1.1:
            status = "supported"
        elif top_score >= 0.55:
            status = "weak"

        if markers and evidence and _citation_style_supports_attribution(citation_style):
            marker_matched = any(
                _citation_matches_result(marker, result)
                for marker in markers
                for result in evidence
            )
            if not marker_matched:
                status = "misattributed" if strictness == "strict" else "weak"

        entry = AuditedClaim(
            claim_id=f"claim_{index}",
            text=claim,
            query_text=search_query,
            status=status,
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
        )
        audited_claims.append(entry)
        status_counts[status] += 1

    claim_map: list[ClaimMapEntry] = []
    if return_claim_map:
        claim_map = [
            ClaimMapEntry(
                claim_id=item.claim_id,
                status=item.status,
                evidence_dois=[evidence.doi for evidence in item.evidence],
            )
            for item in audited_claims
        ]
    return DraftAuditResult(
        claims_checked=len(audited_claims),
        status_counts=dict(status_counts),
        claims=audited_claims,
        claim_map=claim_map,
    )
