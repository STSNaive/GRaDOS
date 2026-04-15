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
    read_saved_paper,
    register_library_tools,
)
from grados.server_tools.research_tools_api import (
    audit_draft_support,
    build_evidence_grid,
    compare_papers,
    get_citation_graph,
    get_papers_full_context,
    manage_failure_cases,
    query_research_artifacts,
    register_research_tools_api,
    save_research_artifact,
)
from grados.server_tools.search_tools import register_search_tools, search_academic_papers, search_saved_papers

__all__ = [
    "audit_draft_support",
    "build_evidence_grid",
    "compare_papers",
    "extract_paper_full_text",
    "get_citation_graph",
    "get_papers_full_context",
    "get_saved_paper_structure",
    "import_local_pdf_library",
    "manage_failure_cases",
    "mcp",
    "paper_overview_resource",
    "papers_index_resource",
    "parse_pdf_file",
    "query_research_artifacts",
    "read_saved_paper",
    "run_server",
    "save_paper_to_zotero",
    "save_research_artifact",
    "search_academic_papers",
    "search_saved_papers",
]

mcp = FastMCP(
    "GRaDOS",
    version=__version__,
    instructions="Academic research MCP server — search, extract, and manage papers",
)

register_search_tools(mcp)
register_library_tools(mcp)
register_research_tools_api(mcp)
register_admin_tools(mcp)


def run_server() -> None:
    """Start the MCP stdio server."""
    mcp.run()
