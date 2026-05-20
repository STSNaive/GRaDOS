"""Stage B research MCP tools."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from grados.server_tools.shared import get_paths_and_config

__all__ = [
    "audit_answer_against_pack",
    "audit_draft_support",
    "build_evidence_grid",
    "compare_papers",
    "get_citation_graph",
    "get_papers_full_context",
    "manage_failure_cases",
    "prepare_evidence_pack",
    "prepare_external_synthesis_from_topic",
    "prepare_external_synthesis_packet",
    "preview_external_synthesis_packet",
    "query_research_artifacts",
    "read_evidence_pack",
    "register_research_tools_api",
    "run_external_synthesis",
    "save_external_synthesis_result",
    "save_research_artifact",
    "suggest_missing_evidence",
    "audit_external_synthesis_result",
    "verify_evidence_pack",
]


def _external_synthesis_disabled_response() -> dict[str, object]:
    return {
        "ok": False,
        "sendable": False,
        "saved": False,
        "enabled": False,
        "error": "external_synthesis_disabled",
        "next_action": (
            "Set research.external_synthesis.enabled=true and verify with "
            "`grados external-synthesis is-enabled --quiet` before using the "
            "GRaDOS-native ChatGPT browser synthesis route."
        ),
    }


async def save_research_artifact(
    kind: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Artifact kind such as `search_snapshot`, "
                "`extraction_receipt`, `evidence_grid`, `evidence_checkpoint`, "
                "or `research_run_manifest`."
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
        Field(
            description=(
                "Optional structured metadata such as query terms, filters, or audit settings. "
                "Set `research_run_id` to link this artifact into a run manifest."
            )
        ),
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


async def prepare_evidence_pack(
    topic: Annotated[
        str,
        Field(min_length=1, description="Research topic or question the evidence pack should cover."),
    ],
    subquestions: Annotated[
        list[str] | None,
        Field(description="Optional focused subquestions. If omitted, the topic is used as one question."),
    ] = None,
    scoped_dois: Annotated[
        list[str] | None,
        Field(description="Optional saved-paper DOI scope used only for candidate selection."),
    ] = None,
    max_windows: Annotated[
        int,
        Field(ge=1, le=25, description="Maximum candidate windows to materialize per subquestion."),
    ] = 8,
) -> dict[str, object]:
    """Prepare and persist a canonical evidence pack."""
    from grados.research_tools import prepare_evidence_pack as run_prepare

    paths, _ = get_paths_and_config()
    return run_prepare(
        paths.database_chroma,
        paths.database_state,
        topic=topic,
        subquestions=subquestions,
        scoped_dois=scoped_dois,
        max_windows=max_windows,
    )


async def read_evidence_pack(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id returned by `prepare_evidence_pack`."),
    ],
) -> dict[str, object]:
    """Read a persisted evidence pack by id."""
    from grados.research_tools import read_evidence_pack as run_read

    paths, _ = get_paths_and_config()
    return run_read(paths.database_state, pack_id=pack_id)


async def verify_evidence_pack(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id returned by `prepare_evidence_pack`."),
    ],
) -> dict[str, object]:
    """Verify an evidence pack against current canonical paper Markdown."""
    from grados.research_tools import verify_evidence_pack as run_verify

    paths, _ = get_paths_and_config()
    return run_verify(paths.database_state, paths.papers, pack_id=pack_id)


async def run_external_synthesis(
    topic: Annotated[
        str | None,
        Field(description="Research topic to prepare into a fresh evidence pack and browser synthesis packet."),
    ] = None,
    pack_id: Annotated[
        str | None,
        Field(description="Existing current-valid evidence pack id to send through browser synthesis."),
    ] = None,
    subquestions: Annotated[
        list[str] | None,
        Field(description="Optional focused subquestions when topic is provided."),
    ] = None,
    scoped_dois: Annotated[
        list[str] | None,
        Field(description="Optional saved-paper DOI scope when topic is provided."),
    ] = None,
    evidence_max_windows: Annotated[
        int,
        Field(ge=1, le=25, description="Maximum candidate windows per evidence subquestion."),
    ] = 8,
    mode: Annotated[
        Literal["review", "synthesize"],
        Field(description="External synthesis mode."),
    ] = "review",
    max_items: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum verified evidence anchors to include."),
    ] = 25,
    max_excerpt_chars: Annotated[
        int,
        Field(ge=120, le=2000, description="Maximum characters per canonical excerpt."),
    ] = 700,
    metadata: Annotated[
        dict[str, object] | None,
        Field(description="Optional metadata such as research_run_id for manifest linking."),
    ] = None,
    recover_session_id: Annotated[
        str | None,
        Field(description="Optional saved ChatGPT browser session id to recover without resending."),
    ] = None,
) -> dict[str, object]:
    """Run the default GRaDOS-native ChatGPT Pro browser synthesis route."""
    from grados.research_tools import run_external_synthesis as run_browser_synthesis

    paths, config = get_paths_and_config()
    if not config.research.external_synthesis.enabled:
        return _external_synthesis_disabled_response()
    return await run_browser_synthesis(
        paths.database_chroma,
        paths.database_state,
        paths.papers,
        paths,
        topic=topic or "",
        pack_id=pack_id or "",
        subquestions=subquestions,
        scoped_dois=scoped_dois,
        evidence_max_windows=evidence_max_windows,
        mode=mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
        metadata=metadata,
        recover_session_id=recover_session_id or "",
        browser_config=config.extract.headless_browser,
    )


async def preview_external_synthesis_packet(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id returned by `prepare_evidence_pack`."),
    ],
    mode: Annotated[
        Literal["review", "synthesize"],
        Field(description="External ChatGPT Pro packet mode."),
    ] = "review",
    max_items: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum verified evidence anchors to include."),
    ] = 25,
    max_excerpt_chars: Annotated[
        int,
        Field(ge=120, le=2000, description="Maximum characters per canonical excerpt."),
    ] = 700,
) -> dict[str, object]:
    """Preview a ChatGPT Pro packet without saving or contacting external services."""
    from grados.research_tools import preview_external_synthesis_packet as run_preview

    paths, config = get_paths_and_config()
    if not config.research.external_synthesis.enabled:
        return _external_synthesis_disabled_response()
    return run_preview(
        paths.database_state,
        paths.papers,
        pack_id=pack_id,
        mode=mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
    )


async def prepare_external_synthesis_packet(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id returned by `prepare_evidence_pack`."),
    ],
    mode: Annotated[
        Literal["review", "synthesize"],
        Field(description="External ChatGPT Pro packet mode."),
    ] = "review",
    max_items: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum verified evidence anchors to include."),
    ] = 25,
    max_excerpt_chars: Annotated[
        int,
        Field(ge=120, le=2000, description="Maximum characters per canonical excerpt."),
    ] = 700,
    metadata: Annotated[
        dict[str, object] | None,
        Field(description="Optional metadata such as research_run_id for manifest linking."),
    ] = None,
) -> dict[str, object]:
    """Persist a current-valid packet for host-side ChatGPT Pro review or synthesis."""
    from grados.research_tools import prepare_external_synthesis_packet as run_prepare

    paths, config = get_paths_and_config()
    if not config.research.external_synthesis.enabled:
        return _external_synthesis_disabled_response()
    return run_prepare(
        paths.database_state,
        paths.papers,
        pack_id=pack_id,
        mode=mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
        metadata=metadata,
    )


async def prepare_external_synthesis_from_topic(
    topic: Annotated[
        str,
        Field(min_length=1, description="Research topic or question to turn into an external synthesis packet."),
    ],
    subquestions: Annotated[
        list[str] | None,
        Field(description="Optional focused subquestions for evidence pack preparation."),
    ] = None,
    scoped_dois: Annotated[
        list[str] | None,
        Field(description="Optional saved-paper DOI scope for evidence pack candidate selection."),
    ] = None,
    evidence_max_windows: Annotated[
        int,
        Field(ge=1, le=25, description="Maximum candidate windows to materialize per evidence subquestion."),
    ] = 8,
    mode: Annotated[
        Literal["review", "synthesize"],
        Field(description="External ChatGPT Pro packet mode."),
    ] = "review",
    max_items: Annotated[
        int,
        Field(ge=1, le=50, description="Maximum verified evidence anchors to include in the packet."),
    ] = 25,
    max_excerpt_chars: Annotated[
        int,
        Field(ge=120, le=2000, description="Maximum characters per canonical excerpt."),
    ] = 700,
    metadata: Annotated[
        dict[str, object] | None,
        Field(description="Optional metadata such as research_run_id for manifest linking."),
    ] = None,
) -> dict[str, object]:
    """Prepare a fresh evidence pack and persist a current-valid external synthesis packet."""
    from grados.research_tools import prepare_external_synthesis_from_topic as run_prepare_from_topic

    paths, config = get_paths_and_config()
    if not config.research.external_synthesis.enabled:
        return _external_synthesis_disabled_response()
    return run_prepare_from_topic(
        paths.database_chroma,
        paths.database_state,
        paths.papers,
        topic=topic,
        subquestions=subquestions,
        scoped_dois=scoped_dois,
        evidence_max_windows=evidence_max_windows,
        mode=mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
        metadata=metadata,
    )


async def save_external_synthesis_result(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id used for the external synthesis packet."),
    ],
    response: Annotated[
        dict[str, object] | str,
        Field(description="Raw ChatGPT Pro response as text or structured JSON-like content."),
    ],
    packet_artifact_id: Annotated[
        str | None,
        Field(description="Optional external_synthesis_packet artifact id returned by prepare."),
    ] = None,
    prompt_hash: Annotated[
        str | None,
        Field(description="Optional host prompt hash when no packet artifact id is available."),
    ] = None,
    conversation_url: Annotated[
        str | None,
        Field(description="Optional ChatGPT conversation URL or external session locator."),
    ] = None,
    model_label: Annotated[
        str | None,
        Field(description="Host-observed ChatGPT model label. Metadata only; not config."),
    ] = None,
    thinking_label: Annotated[
        str | None,
        Field(description="Host-observed thinking level label. Metadata only; not config."),
    ] = None,
    mode: Annotated[
        Literal["review", "synthesize"],
        Field(description="External ChatGPT Pro result mode."),
    ] = "review",
    claims: Annotated[
        list[dict[str, object]] | None,
        Field(description="Optional structured claims parsed or copied from ChatGPT Pro output."),
    ] = None,
    gaps: Annotated[
        list[str] | None,
        Field(description="Optional missing-evidence or gap list copied from ChatGPT Pro output."),
    ] = None,
    metadata: Annotated[
        dict[str, object] | None,
        Field(description="Optional metadata such as research_run_id for manifest linking."),
    ] = None,
    audit: Annotated[
        bool,
        Field(description="When true, immediately audit the saved advisory result before returning."),
    ] = True,
) -> dict[str, object]:
    """Save a host-provided ChatGPT Pro response as advisory research state."""
    from grados.research_tools import save_external_synthesis_result as run_save

    paths, config = get_paths_and_config()
    if not config.research.external_synthesis.enabled:
        return _external_synthesis_disabled_response()
    return run_save(
        paths.database_state,
        paths.papers,
        pack_id=pack_id,
        response=response,
        packet_artifact_id=packet_artifact_id or "",
        prompt_hash=prompt_hash or "",
        conversation_url=conversation_url or "",
        model_label=model_label or "",
        thinking_label=thinking_label or "",
        mode=mode,
        claims=claims,
        gaps=gaps,
        metadata=metadata,
        audit=audit,
    )


async def audit_external_synthesis_result(
    result_id: Annotated[
        str,
        Field(min_length=1, description="external_synthesis_result artifact id to audit."),
    ],
    strict: Annotated[
        bool,
        Field(description="When true, use only current-valid pack evidence."),
    ] = True,
    citation_style: Annotated[
        Literal["author_year", "numeric"],
        Field(description="Citation style used in the saved external response."),
    ] = "author_year",
) -> dict[str, object]:
    """Audit a saved ChatGPT Pro response against its linked packet or source pack."""
    from grados.research_tools import audit_external_synthesis_result as run_audit

    paths, config = get_paths_and_config()
    if not config.research.external_synthesis.enabled:
        return _external_synthesis_disabled_response()
    return run_audit(
        paths.database_state,
        paths.papers,
        result_id=result_id,
        strict=strict,
        citation_style=citation_style,
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
        Field(
            min_length=1,
            description=(
                "Saved-paper DOI list for a context-budgeted reading batch. "
                "Use `mode=estimate` and multiple calls for broad paper sets."
            ),
        ),
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
    """Return full-context material for a context-budgeted saved-paper batch."""
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
        Field(
            ge=1,
            le=12,
            description=(
                "Per-call paper hits to consider per subquestion; run more calls or scoped batches "
                "for broader evidence maps."
            ),
        ),
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
        Field(
            description=(
                "Citation style used in the draft. "
                "`author_year` supports attribution checks; `numeric` is currently support-only until "
                "citation numbers can be mapped back to bibliography entries."
            )
        ),
    ] = "author_year",
    strictness: Annotated[
        Literal["strict", "balanced"],
        Field(
            description=(
                "Strict mode treats mismatched resolvable citations as "
                "`major_distortion`; balanced mode softens that to `minor_distortion`. "
                "Numeric citations stay support-only until bibliography mapping exists."
            )
        ),
    ] = "strict",
    candidate_limit: Annotated[
        int,
        Field(
            ge=1,
            le=25,
            description=(
                "Maximum candidate evidence items to retrieve per claim. "
                "Use a larger value when the host agent will rerank evidence before judgment."
            ),
        ),
    ] = 3,
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
            candidate_limit=candidate_limit,
            return_claim_map=return_claim_map,
        )
    )


async def audit_answer_against_pack(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id returned by `prepare_evidence_pack`."),
    ],
    draft: Annotated[
        str,
        Field(min_length=1, description="Markdown or plain-text draft to audit claim by claim."),
    ],
    strict: Annotated[
        bool,
        Field(description="When true, use only current-valid pack evidence and do not soften missing citations."),
    ] = True,
    citation_style: Annotated[
        Literal["author_year", "numeric"],
        Field(description="Citation style used in the draft for marker extraction."),
    ] = "author_year",
    return_claim_map: Annotated[
        bool,
        Field(description="Include a compact claim-to-pack-evidence map."),
    ] = True,
    include_suggestions: Annotated[
        bool,
        Field(description="Include suggestion-only follow-up work for non-verified claims."),
    ] = False,
    max_suggestions: Annotated[
        int,
        Field(ge=1, le=25, description="Maximum suggestion-only follow-up items when include_suggestions=true."),
    ] = 8,
) -> dict[str, object]:
    """Audit a draft strictly against one materialized evidence pack."""
    from grados.research_tools import audit_answer_against_pack as run_audit_pack

    paths, _ = get_paths_and_config()
    return run_audit_pack(
        paths.database_state,
        paths.papers,
        pack_id=pack_id,
        draft=draft,
        strict=strict,
        citation_style=citation_style,
        return_claim_map=return_claim_map,
        include_suggestions=include_suggestions,
        max_suggestions=max_suggestions,
    )


async def suggest_missing_evidence(
    pack_id: Annotated[
        str,
        Field(min_length=1, description="Evidence pack id returned by `prepare_evidence_pack`."),
    ],
    draft: Annotated[
        str,
        Field(
            min_length=1,
            description="Draft whose non-verified pack-audit claims need follow-up evidence or revision.",
        ),
    ],
    max_suggestions: Annotated[
        int,
        Field(ge=1, le=25, description="Maximum follow-up evidence suggestions to return."),
    ] = 8,
) -> dict[str, object]:
    """Suggest follow-up evidence work without changing strict pack-audit verdicts."""
    from grados.research_tools import suggest_missing_evidence as run_suggest

    paths, _ = get_paths_and_config()
    return run_suggest(
        paths.database_state,
        paths.papers,
        pack_id=pack_id,
        draft=draft,
        max_suggestions=max_suggestions,
    )


def register_research_tools_api(mcp: FastMCP) -> None:
    mcp.tool(
        description=(
            "Save a structured research artifact produced during search, extraction, reading, or writing. "
            "Use this for reusable intermediate outputs such as search snapshots, "
            "extraction receipts, evidence grids, compression-safe evidence checkpoints, "
            "and run-linked artifacts."
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
            "Prepare a citation-grade evidence pack by materializing retrieved candidate anchors "
            "back into canonical paragraph blocks from `papers/*.md`. Returns a compact receipt, "
            "`pack_id`, `pack_sha256`, and answerability flags."
        )
    )(prepare_evidence_pack)

    mcp.tool(
        description=(
            "Inspect or recover a previously saved evidence pack snapshot from research artifacts. "
            "For current-valid status or pack-scoped auditing, use the verify/audit tools directly; "
            "they read the pack internally."
        )
    )(read_evidence_pack)

    mcp.tool(
        description=(
            "Verify an evidence pack by rereading current `papers/*.md` canonical blocks. "
            "Does not use Chroma, FTS, or retrieval scores for current-valid evidence."
        )
    )(verify_evidence_pack)

    mcp.tool(
        description=(
            "Run the default GRaDOS-native ChatGPT Pro browser synthesis route. "
            "When external synthesis is enabled, this prepares or verifies a current-valid "
            "evidence pack, creates a packet, uses the private GRaDOS ChatGPT Chrome profile, "
            "verifies Oracle's current Pro model and Pro Extended thinking route before sending, "
            "captures the response, saves it as advisory output, and audits it before "
            "returning the canonical reread next action."
        )
    )(run_external_synthesis)

    mcp.tool(
        description=(
            "Preview the compact host-side ChatGPT Pro packet for a current-valid evidence pack. "
            "This never opens Chrome, calls ChatGPT, or saves an artifact."
        )
    )(preview_external_synthesis_packet)

    mcp.tool(
        description=(
            "Persist a compact external_synthesis_packet built only from current-valid evidence pack "
            "anchors. This is a lower-level recovery route; the default enabled route is "
            "run_external_synthesis, which sends the packet through GRaDOS's private ChatGPT "
            "browser mode."
        )
    )(prepare_external_synthesis_packet)

    mcp.tool(
        description=(
            "Prepare a fresh evidence pack from a topic and persist a verified external_synthesis_packet "
            "in one deterministic lower-level route. Use run_external_synthesis for the default "
            "browser send/save/audit workflow when no pack id already exists."
        )
    )(prepare_external_synthesis_from_topic)

    mcp.tool(
        description=(
            "Save a host-provided ChatGPT Pro response as an advisory external_synthesis_result "
            "artifact linked to the source evidence pack and optional packet/session metadata. "
            "By default, immediately audits the saved result before returning."
        )
    )(save_external_synthesis_result)

    mcp.tool(
        description=(
            "Audit a saved external_synthesis_result against its linked packet when present, "
            "otherwise its source evidence pack, using structured claim anchor ids while "
            "flagging unknown anchors, locators, outside DOIs, stale packs, and non-verified "
            "claims."
        )
    )(audit_external_synthesis_result)

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
            "Return structured full-context material for a context-budgeted batch of saved papers. "
            "Use `mode=estimate` to budget context first, then `mode=full` when the batch fits; "
            "run additional batches for broad reading."
        )
    )(get_papers_full_context)

    mcp.tool(
        description=(
            "Build an evidence grid for a research topic or subquestions. "
            "Returns aligned paper-section-snippet rows and reread anchors so the host agent can rerank "
            "evidence before drafting prose."
        )
    )(build_evidence_grid)

    mcp.tool(
        description=(
            "Extract parallel comparison material across saved papers. "
            "It aligns methods, results, or full-text excerpts into a table "
            "or bullet view with reread anchors, leaving higher-level comparison reasoning to "
            "the host agent."
        )
    )(compare_papers)

    mcp.tool(
        description=(
            "Audit draft claims against the local paper library. "
            "Returns claim-level `verified`, `minor_distortion`, `major_distortion`, "
            "`unverifiable`, or `unverifiable_access` verdicts plus candidate evidence snippets, "
            "issue types, revision actions, and reread anchors."
        )
    )(audit_draft_support)

    mcp.tool(
        description=(
            "Audit draft claims against one evidence pack only. Strict mode does not search the full "
            "library for replacement evidence; non-verified claims stay visible until a separate "
            "evidence-gathering or revision step extends or prepares a pack. Set include_suggestions=true "
            "to attach suggestion-only follow-up work in the same response."
        )
    )(audit_answer_against_pack)

    mcp.tool(
        description=(
            "Suggest follow-up evidence or revision work for non-verified pack-audit claims. "
            "This is suggestion-only and does not alter strict audit verdicts."
        )
    )(suggest_missing_evidence)
