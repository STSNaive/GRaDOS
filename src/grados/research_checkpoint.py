"""Research checkpoint and paper-summary artifacts for indepth workflows."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grados.storage.chunking import split_paragraphs
from grados.storage.papers import PaperRecord, load_paper_record

CHECKPOINT_SCHEMA_VERSION = 1
PAPER_SUMMARY_SCHEMA_VERSION = 1
SUMMARY_PROMPT_VERSION = "paper-summary-extractive-v1"
SUMMARY_MODEL = "grados-extractive-v1"

SummaryStatus = Literal["valid", "missing", "stale", "not_applicable"]


class EvidenceAnchor(BaseModel):
    """A rereadable claim-to-paragraph pointer."""

    model_config = ConfigDict(extra="ignore")

    doi: str = ""
    safe_doi: str = ""
    canonical_uri: str = ""
    section_name: str = ""
    paragraph_start: int | None = None
    paragraph_count: int | None = None
    claim: str = ""
    support_reason: str = ""


class ResearchCheckpointPaper(BaseModel):
    """Per-paper state stored inside a multi-paper research checkpoint."""

    model_config = ConfigDict(extra="ignore")

    doi: str = ""
    safe_doi: str = ""
    paper_id: str = ""
    title: str = ""
    screening_status: str = "candidate"
    fetch_status: str = "metadata_only"
    paper_uri: str = ""
    paper_summary_id: str = ""
    index_status: str = ""
    failure_reason: str = ""


class ResearchCheckpoint(BaseModel):
    """Durable workflow state for one GRaDOS research conversation."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    conversation_id: str
    user_question: str
    search_queries: list[str] = Field(default_factory=list)
    papers: list[ResearchCheckpointPaper] = Field(default_factory=list)
    current_findings: list[str] = Field(default_factory=list)
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: str
    updated_at: str


class PaperSummary(BaseModel):
    """Query-independent, non-citable derived summary for one saved paper."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = PAPER_SUMMARY_SCHEMA_VERSION
    summary_id: str
    doi: str
    safe_doi: str
    paper_id: str
    paper_uri: str
    content_hash: str
    summary_prompt_version: str = SUMMARY_PROMPT_VERSION
    summary_model: str = SUMMARY_MODEL
    generated_at: str
    methods: list[str] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(value: str, *, fallback: str = "research", max_length: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or fallback)[:max_length].strip("-") or fallback


def checkpoint_folder_name(*, started_at: str, user_question: str, search_queries: list[str]) -> str:
    timestamp = re.sub(r"[^0-9T]", "", started_at.replace("+00:00", "Z"))[:16]
    slug_source = user_question or " ".join(search_queries)
    slug = slugify(slug_source)
    digest_input = json.dumps(
        {
            "started_at": started_at,
            "user_question": user_question,
            "search_queries": search_queries,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    short_hash = hashlib.sha1(digest_input.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{timestamp}_{slug}_{short_hash}"


def make_research_checkpoint(
    *,
    user_question: str,
    search_queries: list[str],
    papers: list[ResearchCheckpointPaper] | None = None,
    current_findings: list[str] | None = None,
    evidence_anchors: list[EvidenceAnchor] | None = None,
    open_questions: list[str] | None = None,
    next_actions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ResearchCheckpoint:
    started_at = utc_now()
    digest = hashlib.sha1(
        json.dumps(
            {"started_at": started_at, "user_question": user_question, "search_queries": search_queries},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:12]
    return ResearchCheckpoint(
        conversation_id=f"research_{digest}",
        user_question=user_question,
        search_queries=search_queries,
        papers=papers or [],
        current_findings=current_findings or [],
        evidence_anchors=evidence_anchors or [],
        open_questions=open_questions or [],
        next_actions=next_actions or [],
        warnings=warnings or [],
        started_at=started_at,
        updated_at=started_at,
    )


def render_checkpoint_markdown(checkpoint: ResearchCheckpoint) -> str:
    lines = [
        "# GRaDOS Research Checkpoint",
        "",
        f"- Conversation ID: `{checkpoint.conversation_id}`",
        f"- Started at: `{checkpoint.started_at}`",
        f"- Updated at: `{checkpoint.updated_at}`",
        f"- User question: {checkpoint.user_question}",
        "",
        "## Search Queries",
        "",
    ]
    lines.extend(f"- {query}" for query in checkpoint.search_queries)
    lines.extend(["", "## Papers", ""])
    if not checkpoint.papers:
        lines.append("- No papers recorded.")
    for paper in checkpoint.papers:
        lines.append(f"- **{paper.title or paper.doi or paper.paper_id}**")
        lines.append(f"  - DOI: `{paper.doi}`")
        lines.append(f"  - Safe DOI: `{paper.safe_doi}`")
        lines.append(f"  - Paper ID: `{paper.paper_id}`")
        lines.append(f"  - Screening: `{paper.screening_status}`")
        lines.append(f"  - Fetch: `{paper.fetch_status}`")
        if paper.paper_uri:
            lines.append(f"  - URI: `{paper.paper_uri}`")
        if paper.paper_summary_id:
            lines.append(f"  - Summary: `{paper.paper_summary_id}`")
        if paper.index_status:
            lines.append(f"  - Index: `{paper.index_status}`")
        if paper.failure_reason:
            lines.append(f"  - Failure: {paper.failure_reason}")

    _append_list_section(lines, "Current Findings", checkpoint.current_findings)
    _append_anchor_section(lines, checkpoint.evidence_anchors)
    _append_list_section(lines, "Open Questions", checkpoint.open_questions)
    _append_list_section(lines, "Next Actions", checkpoint.next_actions)
    _append_list_section(lines, "Warnings", checkpoint.warnings)
    lines.extend(
        [
            "",
            "## Evidence Discipline",
            "",
            (
                "Summary, checkpoint, search result, and snippet content is navigation material only. "
                "Final answers, citations, audits, and comparisons must reread canonical `papers/*.md` "
                "paragraph windows with `read_saved_paper`."
            ),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _append_list_section(lines: list[str], heading: str, values: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if values:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.append("- None recorded.")


def _append_anchor_section(lines: list[str], anchors: list[EvidenceAnchor]) -> None:
    lines.extend(["", "## Evidence Anchors", ""])
    if not anchors:
        lines.append("- None recorded.")
        return
    for anchor in anchors:
        coords = ""
        if anchor.paragraph_start is not None and anchor.paragraph_count is not None:
            coords = f" paragraphs {anchor.paragraph_start}-{anchor.paragraph_start + anchor.paragraph_count - 1}"
        lines.append(f"- `{anchor.doi}` {anchor.section_name}{coords}: {anchor.claim}")
        if anchor.support_reason:
            lines.append(f"  - Reason: {anchor.support_reason}")


def write_research_checkpoint(checkpoint_root: Path, checkpoint: ResearchCheckpoint) -> Path:
    folder = checkpoint_root / checkpoint_folder_name(
        started_at=checkpoint.started_at,
        user_question=checkpoint.user_question,
        search_queries=checkpoint.search_queries,
    )
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint.updated_at = utc_now()
    (folder / "checkpoint.json").write_text(
        checkpoint.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (folder / "checkpoint.md").write_text(render_checkpoint_markdown(checkpoint), encoding="utf-8")
    return folder


def paper_summary_path(summary_root: Path, safe_doi: str) -> Path:
    return summary_root / f"{safe_doi}.json"


def load_paper_summary(summary_root: Path, safe_doi: str) -> PaperSummary | None:
    path = paper_summary_path(summary_root, safe_doi)
    if not path.is_file():
        return None
    try:
        return PaperSummary.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def paper_summary_status(summary_root: Path, papers_dir: Path, *, doi: str = "", safe_doi: str = "") -> SummaryStatus:
    record = load_paper_record(papers_dir, doi=doi or None, safe_doi=safe_doi or None)
    if record is None:
        return "not_applicable"
    summary = load_paper_summary(summary_root, record.safe_doi)
    if summary is None:
        return "missing"
    if summary.summary_prompt_version != SUMMARY_PROMPT_VERSION:
        return "stale"
    if summary.content_hash != content_hash(record.content_markdown):
        return "stale"
    return "valid"


def generate_paper_summary(summary_root: Path, papers_dir: Path, *, doi: str = "", safe_doi: str = "") -> PaperSummary:
    record = load_paper_record(papers_dir, doi=doi or None, safe_doi=safe_doi or None)
    if record is None:
        raise FileNotFoundError(f"Saved paper not found for doi={doi!r} safe_doi={safe_doi!r}")

    summary_root.mkdir(parents=True, exist_ok=True)
    paper_hash = content_hash(record.content_markdown)
    summary = PaperSummary(
        summary_id=f"summary_{record.safe_doi}_{paper_hash[:12]}",
        doi=record.doi,
        safe_doi=record.safe_doi,
        paper_id=record.safe_doi,
        paper_uri=record.canonical_uri,
        content_hash=paper_hash,
        generated_at=utc_now(),
    )
    paragraphs = split_paragraphs(record.content_markdown, include_front_matter=False)
    methods, method_anchors = _summarize_section_group(record, paragraphs, ["method", "materials", "experiment"])
    findings, finding_anchors = _summarize_section_group(
        record,
        paragraphs,
        ["result", "finding", "discussion", "conclusion", "abstract"],
    )
    limitations, limitation_anchors = _summarize_section_group(record, paragraphs, ["limitation", "threat"])

    summary.methods = methods
    summary.key_findings = findings
    summary.limitations = limitations
    summary.evidence_anchors = [*method_anchors, *finding_anchors, *limitation_anchors]
    if not methods:
        summary.quality_flags.append("no_methods_section_detected")
    if not findings:
        summary.quality_flags.append("no_findings_section_detected")
    if not limitations:
        summary.quality_flags.append("no_limitations_section_detected")

    paper_summary_path(summary_root, record.safe_doi).write_text(
        summary.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _summarize_section_group(
    record: PaperRecord,
    paragraphs: list[str],
    needles: list[str],
    *,
    max_items: int = 3,
) -> tuple[list[str], list[EvidenceAnchor]]:
    items: list[str] = []
    anchors: list[EvidenceAnchor] = []
    lower_needles = [needle.lower() for needle in needles]
    for index, paragraph in enumerate(paragraphs):
        heading = _heading_text(paragraph)
        if not heading:
            continue
        normalized_heading = heading.lower()
        if not any(needle in normalized_heading for needle in lower_needles):
            continue
        window = _section_window(paragraphs, index, max_body_paragraphs=2)
        claim = _first_sentence(" ".join(window[1:] or window))
        if not claim:
            continue
        items.append(claim)
        anchors.append(
            EvidenceAnchor(
                doi=record.doi,
                safe_doi=record.safe_doi,
                canonical_uri=record.canonical_uri,
                section_name=heading,
                paragraph_start=index,
                paragraph_count=len(window),
                claim=claim,
                support_reason="Derived summary anchor; reread before citation.",
            )
        )
        if len(items) >= max_items:
            break
    return items, anchors


def _heading_text(paragraph: str) -> str:
    match = re.match(r"^#{1,6}\s+(.+)$", paragraph.strip())
    return match.group(1).strip() if match else ""


def _section_window(paragraphs: list[str], heading_index: int, *, max_body_paragraphs: int) -> list[str]:
    window = [paragraphs[heading_index]]
    for paragraph in paragraphs[heading_index + 1:]:
        if _heading_text(paragraph):
            break
        window.append(paragraph)
        if len(window) > max_body_paragraphs:
            break
    return window


def _first_sentence(text: str, *, max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    sentence_match = re.search(r"(.+?[.!?。！？])(?:\s|$)", text)
    sentence = sentence_match.group(1) if sentence_match else text
    if len(sentence) <= max_chars:
        return sentence
    return sentence[: max_chars - 3].rstrip() + "..."
