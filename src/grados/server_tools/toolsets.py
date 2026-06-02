"""MCP tool exposure policy for GRaDOS server registration."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP

DEFAULT_TOOLSET_NAME = "research_default"

RESEARCH_DEFAULT_TOOLS: tuple[str, ...] = (
    "search_saved_papers",
    "search_academic_papers",
    "extract_paper_full_text",
    "get_saved_paper_structure",
    "read_saved_paper",
    "get_operation_status",
    "audit_draft_support",
    "prepare_evidence_pack",
    "verify_evidence_pack",
    "audit_answer_against_pack",
    "build_evidence_grid",
    "compare_papers",
    "run_external_synthesis",
    "save_research_artifact",
)

ALL_PUBLIC_TOOL_NAMES: tuple[str, ...] = (
    "audit_answer_against_pack",
    "audit_draft_support",
    "audit_external_synthesis_result",
    "build_evidence_grid",
    "compare_papers",
    "extract_paper_full_text",
    "get_citation_graph",
    "get_operation_status",
    "get_papers_full_context",
    "get_saved_paper_structure",
    "import_local_pdf_library",
    "ingest_codex_downloaded_pdf",
    "manage_failure_cases",
    "parse_pdf_file",
    "plan_library_pdf_cleanup",
    "prepare_evidence_pack",
    "prepare_external_synthesis_from_topic",
    "prepare_external_synthesis_packet",
    "preview_external_synthesis_packet",
    "query_research_artifacts",
    "read_evidence_pack",
    "read_paper_asset",
    "read_saved_paper",
    "run_external_synthesis",
    "save_external_synthesis_result",
    "save_paper_to_zotero",
    "save_research_artifact",
    "search_academic_papers",
    "search_saved_papers",
    "suggest_missing_evidence",
    "verify_evidence_pack",
)

PUBLIC_TOOL_NAMES = frozenset(ALL_PUBLIC_TOOL_NAMES)

TOOLSET_TOOLS: dict[str, tuple[str, ...]] = {
    "default": RESEARCH_DEFAULT_TOOLS,
    "research_default": RESEARCH_DEFAULT_TOOLS,
    "evidence_extra": ("suggest_missing_evidence",),
    "evidence_recovery": ("read_evidence_pack",),
    "analysis_extra": ("get_papers_full_context", "get_citation_graph"),
    "local_pdf": (
        "import_local_pdf_library",
        "parse_pdf_file",
        "ingest_codex_downloaded_pdf",
        "read_paper_asset",
    ),
    "external_recovery": (
        "preview_external_synthesis_packet",
        "prepare_external_synthesis_packet",
        "prepare_external_synthesis_from_topic",
        "save_external_synthesis_result",
        "audit_external_synthesis_result",
    ),
    "maintenance": (
        "query_research_artifacts",
        "manage_failure_cases",
        "plan_library_pdf_cleanup",
    ),
    "zotero": ("save_paper_to_zotero",),
    "all": ALL_PUBLIC_TOOL_NAMES,
    "full": ALL_PUBLIC_TOOL_NAMES,
}

TOOL_LABELS: dict[str, str] = {
    **{name: "[DEFAULT]" for name in RESEARCH_DEFAULT_TOOLS},
    "suggest_missing_evidence": "[EVIDENCE]",
    "read_evidence_pack": "[RECOVERY]",
    "get_papers_full_context": "[ANALYSIS]",
    "get_citation_graph": "[ANALYSIS]",
    "import_local_pdf_library": "[LOCAL_PDF]",
    "parse_pdf_file": "[LOCAL_PDF]",
    "ingest_codex_downloaded_pdf": "[LOCAL_PDF]",
    "read_paper_asset": "[LOCAL_PDF]",
    "preview_external_synthesis_packet": "[RECOVERY]",
    "prepare_external_synthesis_packet": "[RECOVERY]",
    "prepare_external_synthesis_from_topic": "[RECOVERY]",
    "save_external_synthesis_result": "[RECOVERY]",
    "audit_external_synthesis_result": "[RECOVERY]",
    "query_research_artifacts": "[MAINTENANCE]",
    "manage_failure_cases": "[MAINTENANCE]",
    "plan_library_pdf_cleanup": "[MAINTENANCE]",
    "save_paper_to_zotero": "[ZOTERO]",
}


@dataclass(frozen=True)
class ToolsetPolicy:
    """Resolved MCP tool exposure policy."""

    enabled_tools: frozenset[str]
    toolsets: tuple[str, ...]
    explicit_tools: tuple[str, ...]

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.enabled_tools

    def register_tool(self, mcp: FastMCP, func: Callable[..., Any], *, description: str) -> None:
        tool_name = func.__name__
        if self.allows(tool_name):
            mcp.tool(description=with_tool_label(tool_name, description))(func)


def with_tool_label(tool_name: str, description: str) -> str:
    label = TOOL_LABELS.get(tool_name)
    if label is None or description.startswith(label):
        return description
    return f"{label} {description}"


def resolve_toolset_policy(env: Mapping[str, str] | None = None) -> ToolsetPolicy:
    source = os.environ if env is None else env
    requested_toolsets = tuple(name.lower() for name in _split_env_list(source.get("GRADOS_MCP_TOOLSETS")))
    explicit_tools = _split_env_list(source.get("GRADOS_MCP_TOOLS"))

    if not requested_toolsets and not explicit_tools:
        requested_toolsets = (DEFAULT_TOOLSET_NAME,)

    enabled_tools: list[str] = []
    for toolset_name in requested_toolsets:
        if toolset_name not in TOOLSET_TOOLS:
            raise ValueError(
                "Unknown GRADOS_MCP_TOOLSETS entry "
                f"{toolset_name!r}; expected one of: {_format_choices(TOOLSET_TOOLS)}"
            )
        enabled_tools.extend(TOOLSET_TOOLS[toolset_name])

    for tool_name in explicit_tools:
        if tool_name not in PUBLIC_TOOL_NAMES:
            raise ValueError(
                "Unknown GRADOS_MCP_TOOLS entry "
                f"{tool_name!r}; expected one of: {_format_choices(PUBLIC_TOOL_NAMES)}"
            )
        enabled_tools.append(tool_name)

    return ToolsetPolicy(
        enabled_tools=frozenset(enabled_tools),
        toolsets=_unique(requested_toolsets),
        explicit_tools=_unique(explicit_tools),
    )


def _split_env_list(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _unique(names: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return tuple(result)


def _format_choices(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))


def _validate_toolsets() -> None:
    for toolset_name, tool_names in TOOLSET_TOOLS.items():
        unknown_tools = sorted(set(tool_names) - PUBLIC_TOOL_NAMES)
        if unknown_tools:
            raise RuntimeError(
                f"Toolset {toolset_name!r} references unknown tools: {', '.join(unknown_tools)}"
            )
    unlabeled_tools = sorted(PUBLIC_TOOL_NAMES - set(TOOL_LABELS))
    if unlabeled_tools:
        raise RuntimeError(f"MCP tools are missing exposure labels: {', '.join(unlabeled_tools)}")


_validate_toolsets()
