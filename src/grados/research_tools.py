"""Stage B research helpers for evidence, citations, and drafting."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grados.storage.chunking import extract_sections
from grados.storage.papers import PaperRecord, list_saved_papers, load_paper_record
from grados.storage.vector import PaperSearchResult, search_papers

__all__ = [
    "audit_draft_support",
    "build_evidence_grid",
    "compare_papers",
    "get_citation_graph",
    "get_papers_full_context",
]

_METHOD_SECTION_NAMES = {
    "methods",
    "materials and methods",
    "methodology",
    "experimental",
    "experiments",
    "materials",
}
_RESULT_SECTION_NAMES = {
    "results",
    "results and discussion",
    "findings",
    "evaluation",
    "experiments and results",
    "discussion",
}
_REFERENCE_SECTION_NAMES = {
    "references",
    "bibliography",
    "works cited",
    "literature cited",
    "参考文献",
}
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class LocalCitationRecord:
    paper: PaperRecord
    cites: list[str]


@dataclass(frozen=True)
class FullContextSection:
    name: str
    level: int
    token_estimate: int
    content: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class FullContextPaper:
    doi: str
    safe_doi: str
    title: str
    year: str
    journal: str
    available_sections: list[str]
    estimated_tokens: int
    returned_tokens: int
    truncated: bool
    sections: list[FullContextSection]


@dataclass(frozen=True)
class FullContextResult:
    mode: str
    requested_dois: list[str]
    found: int
    missing_dois: list[str]
    section_filter: list[str]
    estimated_total_tokens: int
    returned_total_tokens: int
    papers: list[FullContextPaper]


@dataclass(frozen=True)
class CitationGraphItem:
    doi: str
    title: str
    safe_doi: str


@dataclass(frozen=True)
class CitationGraphNode:
    doi: str
    title: str
    year: str
    safe_doi: str
    cites_local_count: int
    cited_by_local_count: int
    cites_external_count: int


@dataclass(frozen=True)
class CitationGraphEdge:
    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class CitationGraphSummary:
    cited_local: list[CitationGraphItem] = field(default_factory=list)
    cited_external: list[str] = field(default_factory=list)
    cited_by_local: list[CitationGraphItem] = field(default_factory=list)


@dataclass(frozen=True)
class CommonReferenceItem:
    doi: str
    title: str
    is_saved_locally: bool
    cited_by: list[str]


@dataclass(frozen=True)
class CitingPaperItem:
    target_doi: str
    doi: str
    title: str
    year: str
    safe_doi: str


@dataclass(frozen=True)
class CitationGraphResult:
    mode: str
    targets: list[str]
    nodes: list[CitationGraphNode] = field(default_factory=list)
    edges: list[CitationGraphEdge] = field(default_factory=list)
    summary: CitationGraphSummary | None = None
    common_references: list[CommonReferenceItem] = field(default_factory=list)
    count: int = 0
    items: list[CitingPaperItem] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class EvidenceGridRow:
    subquestion: str
    query_used: str
    doi: str
    safe_doi: str
    title: str
    year: str
    journal: str
    section_name: str
    snippet: str
    score: float
    support_strength: str


@dataclass(frozen=True)
class EvidenceGridBlock:
    subquestion: str
    rows: list[EvidenceGridRow]


@dataclass(frozen=True)
class EvidenceGridResult:
    topic: str
    subquestions: list[str]
    scoped_dois: list[str]
    section_filter: list[str]
    paper_coverage: dict[str, int]
    grids: list[EvidenceGridBlock]


@dataclass(frozen=True)
class PaperComparisonRow:
    doi: str
    safe_doi: str
    title: str
    year: str
    journal: str
    focus: str
    sections_used: list[str]
    comparisons: dict[str, str]


@dataclass(frozen=True)
class PaperComparisonResult:
    focus: str
    axes: list[str]
    missing_dois: list[str]
    papers: list[PaperComparisonRow]
    output_format: str
    rendered: str


@dataclass(frozen=True)
class AuditCitationMarker:
    style: str
    marker: str
    author: str = ""
    year: str = ""


@dataclass(frozen=True)
class AuditEvidenceItem:
    doi: str
    safe_doi: str
    title: str
    year: str
    section_name: str
    snippet: str
    score: float


@dataclass(frozen=True)
class AuditedClaim:
    claim_id: str
    text: str
    query_text: str
    status: str
    citation_marker_present: bool
    citations: list[AuditCitationMarker]
    evidence: list[AuditEvidenceItem]


@dataclass(frozen=True)
class ClaimMapEntry:
    claim_id: str
    status: str
    evidence_dois: list[str]


@dataclass(frozen=True)
class DraftAuditResult:
    claims_checked: int
    status_counts: dict[str, int]
    claims: list[AuditedClaim]
    claim_map: list[ClaimMapEntry] = field(default_factory=list)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_doi(value: str) -> str:
    return re.sub(r"[)\].,;:]+$", "", value.strip().lower())


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9]{3,}", query.lower()) if term]


def _section_matches(section_name: str, section_filter: list[str] | None) -> bool:
    if not section_filter:
        return True
    normalized = _normalize_text(section_name)
    candidates = {_normalize_text(value) for value in section_filter if value.strip()}
    return any(candidate in normalized or normalized in candidate for candidate in candidates)


def _papers_dir_from_chroma_dir(chroma_dir: Path) -> Path:
    if chroma_dir.name == "papers":
        return chroma_dir
    if chroma_dir.parent.name == "database":
        return chroma_dir.parent.parent / "papers"
    return chroma_dir.parent / "papers"


def _resolve_documents(chroma_dir: Path, dois: list[str]) -> tuple[list[PaperRecord], list[str]]:
    papers_dir = _papers_dir_from_chroma_dir(chroma_dir)
    resolved: list[PaperRecord] = []
    missing: list[str] = []
    for doi in dois:
        record = load_paper_record(papers_dir, doi=doi)
        if not record:
            missing.append(doi)
            continue
        resolved.append(record)
    return resolved, missing


def _extract_reference_dois(markdown: str) -> list[str]:
    sections = extract_sections(markdown)
    reference_sections = [
        section
        for section in sections
        if _normalize_text(str(section["name"])) in _REFERENCE_SECTION_NAMES
    ]
    search_space = (
        "\n\n".join(str(section["text"]) for section in reference_sections)
        if reference_sections
        else markdown
    )

    seen: set[str] = set()
    citations: list[str] = []
    for match in _DOI_PATTERN.findall(search_space):
        normalized = _normalize_doi(match)
        if normalized in seen:
            continue
        seen.add(normalized)
        citations.append(normalized)
    return citations


def _load_local_citation_records(chroma_dir: Path) -> list[LocalCitationRecord]:
    papers_dir = _papers_dir_from_chroma_dir(chroma_dir)
    records: list[LocalCitationRecord] = []
    for item in list_saved_papers(papers_dir):
        safe_doi = item.safe_doi.strip()
        if not safe_doi:
            continue
        record = load_paper_record(papers_dir, safe_doi=safe_doi)
        if not record or not record.doi.strip():
            continue
        records.append(LocalCitationRecord(paper=record, cites=_extract_reference_dois(record.content_markdown)))
    return records


def _select_sections(
    record: PaperRecord,
    *,
    section_filter: list[str] | None = None,
    focus: str = "full_text",
) -> list[dict[str, Any]]:
    markdown = record.content_markdown
    all_sections = extract_sections(markdown, fallback_title=record.title)
    if not all_sections:
        return []

    sections = all_sections
    if focus == "methods":
        sections = [
            section
            for section in all_sections
            if _normalize_text(str(section["name"])) in _METHOD_SECTION_NAMES
        ]
    elif focus == "results":
        sections = [
            section
            for section in all_sections
            if _normalize_text(str(section["name"])) in _RESULT_SECTION_NAMES
        ]
    elif focus == "references":
        sections = [
            section
            for section in all_sections
            if _normalize_text(str(section["name"])) in _REFERENCE_SECTION_NAMES
        ]

    if not sections:
        sections = all_sections

    selected = [section for section in sections if _section_matches(str(section["name"]), section_filter)]
    return selected or sections


def _excerpt_for_axis(text: str, axis: str, max_chars: int = 260) -> str:
    axis_terms = _query_terms(axis)
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    if not paragraphs:
        return ""

    best_paragraph = ""
    best_score = -1
    for paragraph in paragraphs:
        score = sum(paragraph.lower().count(term) for term in axis_terms)
        if score > best_score:
            best_score = score
            best_paragraph = paragraph
    excerpt = re.sub(r"\s+", " ", best_paragraph).strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[: max_chars - 3].rstrip() + "..."


def get_papers_full_context(
    chroma_dir: Path,
    *,
    dois: list[str],
    section_filter: list[str] | None = None,
    mode: str = "estimate",
    max_total_tokens: int = 32000,
) -> FullContextResult:
    """Return structured full-context material for a small paper set."""

    resolved, missing = _resolve_documents(chroma_dir, dois)
    papers: list[FullContextPaper] = []
    total_estimated = 0
    returned_tokens = 0

    for record in resolved:
        selected_sections = _select_sections(record, section_filter=section_filter)
        section_payloads: list[FullContextSection] = []
        paper_estimated = 0
        paper_returned = 0
        truncated = False

        for section in selected_sections:
            content = str(section["text"]).strip()
            token_estimate = _estimate_tokens(content)
            paper_estimated += token_estimate
            total_estimated += token_estimate

            content_value = ""
            section_truncated = False
            if mode == "full":
                remaining_budget = max_total_tokens - returned_tokens
                if remaining_budget <= 0:
                    truncated = True
                    continue
                if token_estimate <= remaining_budget:
                    content_value = content
                    paper_returned += token_estimate
                    returned_tokens += token_estimate
                else:
                    max_chars = max(0, remaining_budget * 4)
                    content_value = content[:max_chars].rstrip()
                    section_truncated = True
                    paper_returned += remaining_budget
                    returned_tokens += remaining_budget
                    truncated = True
                if returned_tokens >= max_total_tokens:
                    truncated = True
            section_payloads.append(
                FullContextSection(
                    name=str(section["name"]),
                    level=int(section["level"]),
                    token_estimate=token_estimate,
                    content=content_value,
                    truncated=section_truncated,
                )
            )

        papers.append(
            FullContextPaper(
                doi=record.doi,
                safe_doi=record.safe_doi,
                title=record.title,
                year=record.year,
                journal=record.journal,
                available_sections=list(record.section_headings),
                estimated_tokens=paper_estimated,
                returned_tokens=paper_returned,
                truncated=truncated,
                sections=section_payloads,
            )
        )

    return FullContextResult(
        mode=mode,
        requested_dois=dois,
        found=len(papers),
        missing_dois=missing,
        section_filter=section_filter or [],
        estimated_total_tokens=total_estimated,
        returned_total_tokens=returned_tokens,
        papers=papers,
    )


def get_citation_graph(
    chroma_dir: Path,
    *,
    mode: str,
    doi: str = "",
    dois: list[str] | None = None,
    max_hops: int = 1,
    limit: int = 20,
) -> CitationGraphResult:
    """Return lightweight local citation relationships."""

    documents = _load_local_citation_records(chroma_dir)
    if not documents:
        return CitationGraphResult(mode=mode, targets=[], message="No saved papers found.")

    doc_by_doi = {
        _normalize_doi(record.paper.doi): record
        for record in documents
        if record.paper.doi.strip()
    }
    outgoing = {
        key: [_normalize_doi(value) for value in record.cites]
        for key, record in doc_by_doi.items()
    }
    incoming: dict[str, list[str]] = defaultdict(list)
    for src, cited in outgoing.items():
        for target in cited:
            incoming[target].append(src)

    requested = [_normalize_doi(value) for value in ([doi] + (dois or [])) if value and value.strip()]
    requested = list(dict.fromkeys(requested))
    resolved_targets = [value for value in requested if value in doc_by_doi]

    if mode == "common_references":
        if len(resolved_targets) < 2:
            return CitationGraphResult(
                mode=mode,
                targets=resolved_targets,
                common_references=[],
                message="Provide at least two locally saved DOIs for common reference analysis.",
            )
        common = set(outgoing.get(resolved_targets[0], []))
        for target in resolved_targets[1:]:
            common &= set(outgoing.get(target, []))
        items: list[CommonReferenceItem] = []
        for ref in sorted(common)[: max(1, min(limit, 100))]:
            saved = doc_by_doi.get(ref)
            items.append(
                CommonReferenceItem(
                    doi=ref,
                    title=saved.paper.title if saved else "",
                    is_saved_locally=saved is not None,
                    cited_by=resolved_targets,
                )
            )
        return CitationGraphResult(
            mode=mode,
            targets=resolved_targets,
            common_references=items,
        )

    if mode == "citing_papers":
        citing_items: list[CitingPaperItem] = []
        for target in requested:
            for src in incoming.get(target, []):
                record = doc_by_doi.get(src)
                if not record:
                    continue
                citing_items.append(
                    CitingPaperItem(
                        target_doi=target,
                        doi=record.paper.doi,
                        title=record.paper.title,
                        year=record.paper.year,
                        safe_doi=record.paper.safe_doi,
                    )
                )
        return CitationGraphResult(
            mode=mode,
            targets=requested,
            count=len(citing_items),
            items=citing_items[: max(1, min(limit, 100))],
        )

    seed_targets = resolved_targets or requested[:1]
    visited = set(seed_targets)
    frontier = list(seed_targets)
    edges: list[CitationGraphEdge] = []
    hops = 0
    while frontier and hops < max(1, min(max_hops, 3)):
        next_frontier: list[str] = []
        for current in frontier:
            for neighbor in outgoing.get(current, []):
                edges.append(CitationGraphEdge(source=current, target=neighbor, relation="cites"))
                if neighbor in doc_by_doi and neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
            for neighbor in incoming.get(current, []):
                edges.append(CitationGraphEdge(source=neighbor, target=current, relation="cites"))
                if neighbor in doc_by_doi and neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
        hops += 1

    nodes: list[CitationGraphNode] = []
    for node_doi in sorted(visited):
        record = doc_by_doi.get(node_doi)
        if not record:
            continue
        nodes.append(
            CitationGraphNode(
                doi=record.paper.doi,
                title=record.paper.title,
                year=record.paper.year,
                safe_doi=record.paper.safe_doi,
                cites_local_count=len([value for value in outgoing.get(node_doi, []) if value in doc_by_doi]),
                cited_by_local_count=len(incoming.get(node_doi, [])),
                cites_external_count=len([value for value in outgoing.get(node_doi, []) if value not in doc_by_doi]),
            )
        )

    if seed_targets:
        target = seed_targets[0]
        cited_local = [doc_by_doi[value] for value in outgoing.get(target, []) if value in doc_by_doi]
        cited_external = [value for value in outgoing.get(target, []) if value not in doc_by_doi][:limit]
        cited_by = [doc_by_doi[value] for value in incoming.get(target, []) if value in doc_by_doi]
    else:
        cited_local = []
        cited_external = []
        cited_by = []

    return CitationGraphResult(
        mode="neighbors",
        targets=seed_targets,
        nodes=nodes[: max(1, min(limit * 2, 200))],
        edges=edges[: max(1, min(limit * 4, 400))],
        summary=CitationGraphSummary(
            cited_local=[
                CitationGraphItem(
                    doi=item.paper.doi,
                    title=item.paper.title,
                    safe_doi=item.paper.safe_doi,
                )
                for item in cited_local[:limit]
            ],
            cited_external=cited_external,
            cited_by_local=[
                CitationGraphItem(
                    doi=item.paper.doi,
                    title=item.paper.title,
                    safe_doi=item.paper.safe_doi,
                )
                for item in cited_by[:limit]
            ],
        ),
    )


def build_evidence_grid(
    chroma_dir: Path,
    *,
    topic: str,
    subquestions: list[str] | None = None,
    dois: list[str] | None = None,
    section_filter: list[str] | None = None,
    max_papers: int = 8,
) -> EvidenceGridResult:
    """Construct a compact evidence grid for a topic and subquestions."""

    papers_dir = _papers_dir_from_chroma_dir(chroma_dir)
    resolved_subquestions = [question.strip() for question in (subquestions or []) if question.strip()] or [topic]
    scoped_dois = [value.strip() for value in (dois or []) if value.strip()]
    grids: list[EvidenceGridBlock] = []
    paper_counter: Counter[str] = Counter()

    for subquestion in resolved_subquestions:
        rows: list[EvidenceGridRow] = []
        query_candidates = [subquestion]
        if topic.strip() and _normalize_text(topic) != _normalize_text(subquestion):
            query_candidates.append(topic)

        if scoped_dois:
            for query_text in query_candidates:
                for scoped_doi in scoped_dois[: max_papers]:
                    matches = search_papers(
                        chroma_dir,
                        query_text,
                        limit=1,
                        papers_dir=papers_dir,
                        doi=scoped_doi,
                        use_reranking=True,
                    )
                    if not matches:
                        continue
                    match = matches[0]
                    if section_filter and not _section_matches(match.section_name, section_filter):
                        continue
                    rows.append(
                        EvidenceGridRow(
                            subquestion=subquestion,
                            query_used=query_text,
                            doi=match.doi,
                            safe_doi=match.safe_doi,
                            title=match.title,
                            year=match.year,
                            journal=match.journal,
                            section_name=match.section_name,
                            snippet=match.snippet,
                            score=match.score,
                            support_strength=_support_strength(match.score),
                        )
                    )
                    paper_counter[match.doi] += 1
                if rows:
                    break
        else:
            for query_text in query_candidates:
                matches = search_papers(
                    chroma_dir,
                    query_text,
                    limit=max_papers,
                    papers_dir=papers_dir,
                    use_reranking=True,
                )
                if not matches:
                    continue
                for match in matches:
                    if section_filter and not _section_matches(match.section_name, section_filter):
                        continue
                    rows.append(
                        EvidenceGridRow(
                            subquestion=subquestion,
                            query_used=query_text,
                            doi=match.doi,
                            safe_doi=match.safe_doi,
                            title=match.title,
                            year=match.year,
                            journal=match.journal,
                            section_name=match.section_name,
                            snippet=match.snippet,
                            score=match.score,
                            support_strength=_support_strength(match.score),
                        )
                    )
                    paper_counter[match.doi] += 1
                if rows:
                    break
        grids.append(EvidenceGridBlock(subquestion=subquestion, rows=rows))

    return EvidenceGridResult(
        topic=topic,
        subquestions=resolved_subquestions,
        scoped_dois=scoped_dois,
        section_filter=section_filter or [],
        paper_coverage=dict(paper_counter),
        grids=grids,
    )


def _support_strength(score: float) -> str:
    if score >= 1.1:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def compare_papers(
    chroma_dir: Path,
    *,
    dois: list[str],
    focus: str = "methods",
    comparison_axes: list[str] | None = None,
    output_format: str = "table",
) -> PaperComparisonResult:
    """Return aligned, parallel paper comparisons for agent consumption."""

    resolved, missing = _resolve_documents(chroma_dir, dois)
    axes = [axis.strip() for axis in (comparison_axes or []) if axis.strip()]
    if not axes:
        if focus == "results":
            axes = ["dataset", "metric", "main finding", "limitation"]
        elif focus == "full_text":
            axes = ["objective", "approach", "key finding", "limitation"]
        else:
            axes = ["objective", "dataset", "method", "limitation"]

    paper_rows: list[PaperComparisonRow] = []
    for record in resolved:
        sections = _select_sections(record, focus=focus)
        joined_text = "\n\n".join(str(section["text"]).strip() for section in sections)
        comparisons = {
            axis: _excerpt_for_axis(joined_text, axis)
            for axis in axes
        }
        paper_rows.append(
            PaperComparisonRow(
                doi=record.doi,
                safe_doi=record.safe_doi,
                title=record.title,
                year=record.year,
                journal=record.journal,
                focus=focus,
                sections_used=[str(section["name"]) for section in sections],
                comparisons=comparisons,
            )
        )

    rendered = ""
    if output_format == "table" and paper_rows:
        header = "| Paper | " + " | ".join(axes) + " |"
        divider = "| --- | " + " | ".join("---" for _ in axes) + " |"
        rows = []
        for paper in paper_rows:
            label = f"{paper.title} ({paper.year})".strip()
            cells = [paper.comparisons.get(axis, "") for axis in axes]
            rows.append("| " + " | ".join([label, *cells]) + " |")
        rendered = "\n".join([header, divider, *rows])
    elif output_format == "bullets" and paper_rows:
        lines: list[str] = []
        for paper in paper_rows:
            lines.append(f"- {paper.title} ({paper.doi})")
            for axis in axes:
                lines.append(f"  - {axis}: {paper.comparisons.get(axis, '')}")
        rendered = "\n".join(lines)

    return PaperComparisonResult(
        focus=focus,
        axes=axes,
        missing_dois=missing,
        papers=paper_rows,
        output_format=output_format,
        rendered=rendered,
    )


def _split_claims(draft_text: str) -> list[str]:
    claims: list[str] = []
    for block in re.split(r"\n{2,}", draft_text.strip()):
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        sentences = re.split(r"(?<=[。！？.!?])\s+", block)
        for sentence in sentences:
            candidate = sentence.strip()
            if len(candidate) >= 20:
                claims.append(candidate)
    return claims


def _extract_citation_markers(text: str, citation_style: str) -> list[AuditCitationMarker]:
    markers: list[AuditCitationMarker] = []
    bracket_chunks = re.findall(r"\[([^\]]+)\]", text)
    paren_chunks = re.findall(r"\(([^)]+)\)", text) if citation_style == "author_year" else []
    for chunk in bracket_chunks + paren_chunks:
        if citation_style == "numeric":
            if re.search(r"\d", chunk):
                markers.append(AuditCitationMarker(style="numeric", marker=chunk.strip()))
            continue
        for piece in re.split(r";", chunk):
            match = re.search(r"([A-Z][A-Za-z'`-]+).*?(\d{4})", piece)
            if match:
                markers.append(
                    AuditCitationMarker(
                        style="author_year",
                        author=match.group(1).lower(),
                        year=match.group(2),
                        marker=piece.strip(),
                    )
                )
    return markers


def _strip_citations(text: str) -> str:
    stripped = re.sub(r"\[[^\]]+\]", "", text)
    stripped = re.sub(r"\([^)]+\d{4}[^)]*\)", "", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


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
    return_claim_map: bool = True,
) -> DraftAuditResult:
    """Audit draft claims against the local evidence store."""

    papers_dir = _papers_dir_from_chroma_dir(chroma_dir)
    claims = _split_claims(draft_text)
    audited_claims: list[AuditedClaim] = []
    status_counts: Counter[str] = Counter()

    for index, claim in enumerate(claims, 1):
        markers = _extract_citation_markers(claim, citation_style)
        search_query = _strip_citations(claim)
        evidence = (
            search_papers(
                chroma_dir,
                search_query,
                limit=3,
                papers_dir=papers_dir,
                use_reranking=True,
            )
            if search_query
            else []
        )
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
                    title=item.title,
                    year=item.year,
                    section_name=item.section_name,
                    snippet=item.snippet,
                    score=item.score,
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
