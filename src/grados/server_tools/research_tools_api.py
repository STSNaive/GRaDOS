"""Stage B research MCP tools."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from grados.server_tools.shared import get_paths_and_config

__all__ = [
    "audit_draft_support",
    "build_evidence_grid",
    "compare_papers",
    "get_citation_graph",
    "get_papers_full_context",
    "manage_failure_cases",
    "query_research_artifacts",
    "register_research_tools_api",
    "save_research_artifact",
]


async def save_research_artifact(
    kind: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Artifact kind such as `search_snapshot`, "
                "`extraction_receipt`, or `evidence_table`."
            ),
        ),
    ],
    content: Annotated[
        dict[str, object] | str,
        Field(description="Structured JSON-like content or markdown text for the artifact body."),
    ],
    title: Annotated[
        str | None,
        Field(description="Optional short label. If omitted, GRaDOS derives one from the artifact kind."),
    ] = None,
    source_doi: Annotated[
        str | None,
        Field(description="Optional DOI most directly associated with this artifact."),
    ] = None,
    metadata: Annotated[
        dict[str, object] | None,
        Field(description="Optional structured metadata such as query terms, filters, or audit settings."),
    ] = None,
) -> dict[str, object]:
    """Persist a reusable research artifact in the local state database."""
    from grados.research_state import save_research_artifact as persist_artifact

    paths, _ = get_paths_and_config()
    return persist_artifact(
        paths.database_state,
        kind=kind,
        title=title or "",
        content=content,
        source_doi=source_doi or "",
        metadata=metadata,
    )


async def query_research_artifacts(
    artifact_id: Annotated[
        str | None,
        Field(description="Optional exact artifact id returned by `save_research_artifact`."),
    ] = None,
    kind: Annotated[
        str | None,
        Field(description="Optional artifact kind filter."),
    ] = None,
    query: Annotated[
        str | None,
        Field(description="Optional keyword query over artifact titles and stored content."),
    ] = None,
    detail: Annotated[
        bool,
        Field(description="Return full artifact content instead of previews."),
    ] = False,
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum artifacts to return."),
    ] = 20,
) -> dict[str, object]:
    """Query local research artifacts."""
    from grados.research_state import query_research_artifacts as run_query

    paths, _ = get_paths_and_config()
    return run_query(
        paths.database_state,
        artifact_id=artifact_id or "",
        kind=kind or "",
        query=query or "",
        detail=detail,
        limit=limit,
    )


async def manage_failure_cases(
    mode: Annotated[
        Literal["record", "query", "suggest_retry"],
        Field(description="Whether to record a failure, list failures, or request retry guidance."),
    ],
    failure_type: Annotated[
        str | None,
        Field(description="Optional failure family such as `fetch`, `parse`, `search`, or `citation`."),
    ] = None,
    doi: Annotated[
        str | None,
        Field(description="Optional DOI associated with the failure."),
    ] = None,
    query_text: Annotated[
        str | None,
        Field(description="Optional search query or draft-claim text associated with the failure."),
    ] = None,
    source: Annotated[
        str | None,
        Field(description="Optional backend or publisher label associated with the failure."),
    ] = None,
    error_message: Annotated[
        str | None,
        Field(description="Optional raw error message. Especially useful with `mode=record` and `mode=suggest_retry`."),
    ] = None,
    context: Annotated[
        dict[str, object] | None,
        Field(description="Optional structured failure context such as filters, parser order, or citation style."),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum failure cases to return for query or retry analysis."),
    ] = 20,
) -> dict[str, object]:
    """Manage local failure memory."""
    from grados.research_state import manage_failure_cases as run_failure_memory

    paths, _ = get_paths_and_config()
    return run_failure_memory(
        paths.database_state,
        mode=mode,
        failure_type=failure_type or "",
        doi=doi or "",
        query_text=query_text or "",
        source=source or "",
        error_message=error_message or "",
        context=context,
        limit=limit,
    )


async def get_citation_graph(
    mode: Annotated[
        Literal["neighbors", "common_references", "citing_papers"],
        Field(description="Which citation subquery to run."),
    ] = "neighbors",
    doi: Annotated[
        str | None,
        Field(description="Optional primary DOI. Use this for single-paper neighbor or citing-paper queries."),
    ] = None,
    dois: Annotated[
        list[str] | None,
        Field(description="Optional DOI list for multi-paper citation analysis such as common references."),
    ] = None,
    max_hops: Annotated[
        int,
        Field(ge=1, le=3, description="Only used by `neighbors`; expands local citation hops conservatively."),
    ] = 1,
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum relationship items to return."),
    ] = 20,
) -> dict[str, object]:
    """Return lightweight local citation graph data."""
    from grados.research_tools import get_citation_graph as run_citation_graph

    paths, _ = get_paths_and_config()
    return asdict(
        run_citation_graph(
            paths.database_chroma,
            mode=mode,
            doi=doi or "",
            dois=dois,
            max_hops=max_hops,
            limit=limit,
        )
    )


async def get_papers_full_context(
    dois: Annotated[
        list[str],
        Field(min_length=1, description="Saved-paper DOI list. Best for 1-8 papers you intend to read closely."),
    ],
    section_filter: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional section names to scope the returned context, "
                "such as `Abstract`, `Methods`, or `Results`."
            )
        ),
    ] = None,
    mode: Annotated[
        Literal["estimate", "full"],
        Field(description="Use `estimate` for token budgeting and `full` for actual section content."),
    ] = "estimate",
    max_total_tokens: Annotated[
        int,
        Field(ge=1000, le=128000, description="Approximate token budget across all returned papers when `mode=full`."),
    ] = 32000,
) -> dict[str, object]:
    """Return full-context material for a small saved-paper set."""
    from grados.research_tools import get_papers_full_context as run_full_context

    paths, _ = get_paths_and_config()
    return asdict(
        run_full_context(
            paths.database_chroma,
            dois=dois,
            section_filter=section_filter,
            mode=mode,
            max_total_tokens=max_total_tokens,
        )
    )


async def build_evidence_grid(
    topic: Annotated[
        str,
        Field(min_length=1, description="Research topic or question that the evidence grid should organize."),
    ],
    subquestions: Annotated[
        list[str] | None,
        Field(description="Optional focused subquestions. If omitted, the topic itself is used as one query."),
    ] = None,
    dois: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional saved-paper DOI scope. When provided, GRaDOS "
                "only mines evidence from these papers."
            )
        ),
    ] = None,
    section_filter: Annotated[
        list[str] | None,
        Field(description="Optional section names to prefer while gathering evidence."),
    ] = None,
    max_papers: Annotated[
        int,
        Field(ge=1, le=12, description="Maximum paper hits to consider per subquestion."),
    ] = 8,
) -> dict[str, object]:
    """Construct an evidence grid for writing preparation."""
    from grados.research_tools import build_evidence_grid as run_evidence_grid

    paths, _ = get_paths_and_config()
    return asdict(
        run_evidence_grid(
            paths.database_chroma,
            topic=topic,
            subquestions=subquestions,
            dois=dois,
            section_filter=section_filter,
            max_papers=max_papers,
        )
    )


async def compare_papers(
    dois: Annotated[
        list[str],
        Field(min_length=2, description="Saved-paper DOI list to compare side by side."),
    ],
    focus: Annotated[
        Literal["methods", "results", "full_text"],
        Field(description="Which paper aspect to align for comparison."),
    ] = "methods",
    comparison_axes: Annotated[
        list[str] | None,
        Field(description="Optional comparison axes such as dataset, metric, limitation, or objective."),
    ] = None,
    output_format: Annotated[
        Literal["table", "bullets"],
        Field(description="Preferred presentation for the aligned comparison payload."),
    ] = "table",
) -> dict[str, object]:
    """Compare saved papers without collapsing them into one narrative."""
    from grados.research_tools import compare_papers as run_compare_papers

    paths, _ = get_paths_and_config()
    return asdict(
        run_compare_papers(
            paths.database_chroma,
            dois=dois,
            focus=focus,
            comparison_axes=comparison_axes,
            output_format=output_format,
        )
    )


async def audit_draft_support(
    draft_text: Annotated[
        str,
        Field(min_length=1, description="Markdown or plain-text draft to audit claim by claim."),
    ],
    citation_style: Annotated[
        Literal["author_year", "numeric"],
        Field(description="Citation style used in the draft so GRaDOS can parse citation markers more accurately."),
    ] = "author_year",
    strictness: Annotated[
        Literal["strict", "balanced"],
        Field(
            description=(
                "Strict mode treats mismatched citations as "
                "`misattributed`; balanced mode softens that to `weak`."
            )
        ),
    ] = "strict",
    return_claim_map: Annotated[
        bool,
        Field(description="Include a compact claim-to-evidence map in addition to the full claim audit."),
    ] = True,
) -> dict[str, object]:
    """Audit whether a draft is supported by the local evidence store."""
    from grados.research_tools import audit_draft_support as run_audit

    paths, _ = get_paths_and_config()
    return asdict(
        run_audit(
            paths.database_chroma,
            draft_text=draft_text,
            citation_style=citation_style,
            strictness=strictness,
            return_claim_map=return_claim_map,
        )
    )


def register_research_tools_api(mcp: FastMCP) -> None:
    mcp.tool(
        description=(
            "Save a structured research artifact produced during search, extraction, reading, or writing. "
            "Use this for reusable intermediate outputs such as search snapshots, "
            "extraction receipts, and evidence tables."
        )
    )(save_research_artifact)

    mcp.tool(
        description=(
            "Query previously saved research artifacts by id, kind, or keyword. "
            "Set `detail=true` to load the full stored content."
        )
    )(query_research_artifacts)

    mcp.tool(
        description=(
            "Record, inspect, and summarize failed fetch/parse/search/citation attempts. "
            "Use `mode=suggest_retry` to get conservative next-step guidance from the local failure memory."
        )
    )(manage_failure_cases)

    mcp.tool(
        description=(
            "Return local citation relationships among saved papers. "
            "Supports paper neighborhoods, common references, and reverse "
            "citing-paper lookups without generating prose conclusions."
        )
    )(get_citation_graph)

    mcp.tool(
        description=(
            "Return structured full-context material for a small set of saved papers. "
            "Use `mode=estimate` to budget context first, then `mode=full` "
            "when you are ready to enter a CAG-style deep-reading pass."
        )
    )(get_papers_full_context)

    mcp.tool(
        description=(
            "Build an evidence grid for a research topic or subquestions. "
            "Returns aligned paper-section-snippet rows so the agent can plan writing before drafting prose."
        )
    )(build_evidence_grid)

    mcp.tool(
        description=(
            "Extract parallel comparison material across saved papers. "
            "It aligns methods, results, or full-text excerpts into a table "
            "or bullet view, leaving higher-level comparison reasoning to "
            "the agent."
        )
    )(compare_papers)

    mcp.tool(
        description=(
            "Audit draft claims against the local paper library. "
            "Returns claim-level `supported`, `weak`, `unsupported`, or "
            "`misattributed` statuses plus candidate evidence snippets."
        )
    )(audit_draft_support)
