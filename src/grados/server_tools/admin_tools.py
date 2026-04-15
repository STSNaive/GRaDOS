"""Administrative / integration MCP tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from grados.server_tools.shared import get_paths_and_config

__all__ = ["register_admin_tools", "save_paper_to_zotero"]


async def save_paper_to_zotero(
    doi: Annotated[str, Field(min_length=1, description="Paper DOI.")],
    title: Annotated[str, Field(min_length=1, description="Paper title.")],
    authors: Annotated[list[str] | None, Field(description="Optional author display names.")] = None,
    abstract: Annotated[str | None, Field(description="Optional paper abstract.")] = None,
    journal: Annotated[str | None, Field(description="Optional journal name.")] = None,
    year: Annotated[str | None, Field(description="Optional publication year string.")] = None,
    url: Annotated[str | None, Field(description="Optional paper URL.")] = None,
    tags: Annotated[list[str] | None, Field(description="Optional Zotero tags.")] = None,
    collection_key: Annotated[
        str | None,
        Field(description="Optional Zotero collection key override."),
    ] = None,
) -> str:
    """Save a paper to your Zotero library via the Web API."""
    from grados.zotero import save_to_zotero

    _, config = get_paths_and_config()
    zotero_cfg = config.zotero
    api_key = config.api_keys.ZOTERO_API_KEY

    result = await save_to_zotero(
        doi=doi,
        title=title,
        library_id=zotero_cfg.library_id,
        library_type=zotero_cfg.library_type,
        api_key=api_key,
        authors=authors,
        abstract=abstract or "",
        journal=journal or "",
        year=year or "",
        url=url or "",
        tags=tags,
        collection_key=collection_key or zotero_cfg.default_collection_key,
    )

    if result.success:
        return f"## Saved to Zotero\n\n- **Item key:** {result.item_key}\n- **Title:** {title}\n- **DOI:** {doi}"
    return f"## Zotero Save Failed\n\n- **Error:** {result.message}"


def register_admin_tools(mcp: FastMCP) -> None:
    mcp.tool(
        description=(
            "Save one paper to Zotero via the Web API using the configured library settings. "
            "Best used for papers that actually support the final answer."
        )
    )(save_paper_to_zotero)
