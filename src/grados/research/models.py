"""Typed result models for Stage B research helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from grados.storage.papers import PaperRecord


@dataclass(frozen=True)
class LocalCitationRecord:
    paper: PaperRecord
    cites: list[str]


@dataclass(frozen=True)
class _LocalCitationCacheEntry:
    signature: tuple[tuple[str, int, int], ...]
    records: tuple[LocalCitationRecord, ...]


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
    canonical_uri: str
    title: str
    year: str
    journal: str
    section_name: str
    paragraph_start: int | None
    paragraph_count: int | None
    snippet: str
    score: float
    support_strength: str
    dense_score: float = 0.0
    lexical_score: float = 0.0


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
    canonical_uri: str
    title: str
    year: str
    journal: str
    focus: str
    sections_used: list[str]
    comparisons: dict[str, str]
    evidence: list[ComparisonEvidenceItem] = field(default_factory=list)


@dataclass(frozen=True)
class ComparisonEvidenceItem:
    axis: str
    section_name: str
    excerpt: str
    canonical_uri: str
    paragraph_start: int | None = None
    paragraph_count: int | None = None
    warning: str = ""


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
    canonical_uri: str
    title: str
    year: str
    section_name: str
    paragraph_start: int | None
    paragraph_count: int | None
    snippet: str
    score: float
    dense_score: float = 0.0
    lexical_score: float = 0.0


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
