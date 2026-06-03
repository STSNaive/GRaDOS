"""GRaDOS MCP server entrypoint and domain registration."""

from __future__ import annotations

from fastmcp import FastMCP

from grados import __version__
from grados.server_tools.admin_tools import register_admin_tools, save_paper_to_zotero
from grados.server_tools.library_tools import (
    extract_paper_full_text,
    get_saved_paper_structure,
    import_local_pdf_library,
    paper_overview_resource,
    papers_index_resource,
    parse_pdf_file,
    plan_library_pdf_cleanup,
    read_paper_asset,
    read_saved_paper,
    register_library_tools,
)
from grados.server_tools.research_tools_api import (
    audit_draft_support,
    audit_external_consult_result,
    build_evidence_grid,
    compare_papers,
    consult_chatgpt_pro,
    get_citation_graph,
    get_operation_status,
    get_papers_full_context,
    manage_failure_cases,
    prepare_external_consult_from_topic,
    prepare_external_consult_packet,
    preview_external_consult_packet,
    query_research_artifacts,
    register_research_tools_api,
    run_external_consult,
    save_external_consult_result,
    save_research_artifact,
)
from grados.server_tools.search_tools import register_search_tools, search_academic_papers, search_saved_papers
from grados.server_tools.toolsets import ToolsetPolicy, resolve_toolset_policy

__all__ = [
    "audit_draft_support",
    "audit_external_consult_result",
    "build_evidence_grid",
    "compare_papers",
    "consult_chatgpt_pro",
    "create_mcp",
    "extract_paper_full_text",
    "get_citation_graph",
    "get_operation_status",
    "get_papers_full_context",
    "get_saved_paper_structure",
    "import_local_pdf_library",
    "manage_failure_cases",
    "mcp",
    "paper_overview_resource",
    "papers_index_resource",
    "parse_pdf_file",
    "plan_library_pdf_cleanup",
    "prepare_external_consult_from_topic",
    "prepare_external_consult_packet",
    "preview_external_consult_packet",
    "query_research_artifacts",
    "read_paper_asset",
    "read_saved_paper",
    "run_server",
    "run_external_consult",
    "save_external_consult_result",
    "save_paper_to_zotero",
    "save_research_artifact",
    "search_academic_papers",
    "search_saved_papers",
]

def create_mcp(policy: ToolsetPolicy | None = None) -> FastMCP:
    """Create a GRaDOS MCP server with the configured tool exposure policy."""
    server = FastMCP(
        "GRaDOS",
        version=__version__,
        instructions="Academic research MCP server — search, extract, and manage papers",
    )
    active_policy = policy or resolve_toolset_policy()
    register_search_tools(server, active_policy)
    register_library_tools(server, active_policy)
    register_research_tools_api(server, active_policy)
    register_admin_tools(server, active_policy)
    return server


mcp = create_mcp()


def run_server() -> None:
    """Start the MCP stdio server."""
    mcp.run()
