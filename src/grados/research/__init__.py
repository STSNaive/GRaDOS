"""Research helper package for evidence, citation, and drafting workflows."""

from .citation_graph import get_citation_graph
from .compare import compare_papers
from .draft_audit import audit_draft_support
from .evidence_grid import build_evidence_grid
from .full_context import get_papers_full_context
from .models import (
    AuditCitationMarker,
    AuditedClaim,
    AuditEvidenceItem,
    CitationGraphEdge,
    CitationGraphItem,
    CitationGraphNode,
    CitationGraphResult,
    CitationGraphSummary,
    CitingPaperItem,
    ClaimMapEntry,
    CommonReferenceItem,
    DraftAuditResult,
    EvidenceGridBlock,
    EvidenceGridResult,
    EvidenceGridRow,
    FullContextPaper,
    FullContextResult,
    FullContextSection,
    PaperComparisonResult,
    PaperComparisonRow,
)

__all__ = [
    "AuditCitationMarker",
    "AuditEvidenceItem",
    "AuditedClaim",
    "CitationGraphEdge",
    "CitationGraphItem",
    "CitationGraphNode",
    "CitationGraphResult",
    "CitationGraphSummary",
    "ClaimMapEntry",
    "CitingPaperItem",
    "CommonReferenceItem",
    "DraftAuditResult",
    "EvidenceGridBlock",
    "EvidenceGridResult",
    "EvidenceGridRow",
    "FullContextPaper",
    "FullContextResult",
    "FullContextSection",
    "PaperComparisonResult",
    "PaperComparisonRow",
    "audit_draft_support",
    "build_evidence_grid",
    "compare_papers",
    "get_citation_graph",
    "get_papers_full_context",
]
