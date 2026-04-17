"""Shared helpers for MCP server tool/resource modules."""

from __future__ import annotations

from grados._retry import install_runtime_defaults
from grados.config import GRaDOSConfig, GRaDOSPaths, load_config
from grados.storage.papers import PaperListEntry, PaperStructureResult


def get_paths_and_config() -> tuple[GRaDOSPaths, GRaDOSConfig]:
    """Load GRaDOS config + install runtime retry/timeout defaults.

    We install the retry / timeout policy from the freshly-loaded config on
    every tool call. That guarantees "new-process semantics": when the user
    edits ~/GRaDOS/config.json and the MCP server restarts (or simply
    re-enters a tool), the updated timeouts and retry knobs take effect
    without reimporting any module. See ADR-008 / TODO P1-T5.2.

    This is intentionally cheap: validated Pydantic reads + a single module-
    level assignment in grados._retry.
    """
    paths = GRaDOSPaths()
    config = load_config(paths)
    install_runtime_defaults(config)
    return paths, config


def get_api_keys(config: GRaDOSConfig) -> dict[str, str]:
    keys = config.api_keys
    return {key: value for key, value in keys.model_dump().items() if value}


def missing_paper_selector_message(doi: str | None, safe_doi: str | None, uri: str | None) -> str | None:
    """Return a user-facing error when no paper selector was provided."""
    if doi or safe_doi or uri:
        return None
    return "Provide at least one of doi, safe_doi, or uri."


def format_paper_index_resource(papers: list[PaperListEntry]) -> str:
    lines = ["# GRaDOS Saved Papers Index", ""]
    if not papers:
        lines.append("No saved papers found.")
        return "\n".join(lines)

    lines.append(f"Total papers: {len(papers)}")
    lines.append("")
    for item in papers:
        title = item.title or "(untitled)"
        lines.append(f"## {title}")
        lines.append(f"- DOI: {item.doi}")
        lines.append(f"- URI: grados://papers/{item.safe_doi}")
        lines.append("")

    return "\n".join(lines).strip()


def format_paper_overview_resource(structure: PaperStructureResult) -> str:
    lines = [f"# {structure.title or structure.safe_doi}", ""]
    lines.append(f"- DOI: {structure.doi}")
    lines.append(f"- URI: {structure.canonical_uri}")
    if structure.year:
        lines.append(f"- Year: {structure.year}")
    if structure.journal:
        lines.append(f"- Journal: {structure.journal}")
    if structure.source:
        lines.append(f"- Source: {structure.source}")
    if structure.word_count:
        lines.append(f"- Word count: {structure.word_count}")
    if structure.paragraph_count:
        lines.append(f"- Paragraph count: {structure.paragraph_count}")

    if structure.preview_excerpt:
        lines.extend(["", "## Preview", "", structure.preview_excerpt])

    if structure.section_headings:
        lines.extend(["", "## Sections", ""])
        lines.extend(f"- {heading}" for heading in structure.section_headings)

    assets_summary = structure.assets_summary
    if assets_summary.has_assets:
        lines.extend(
            [
                "",
                "## Assets",
                "",
                f"- Manifest: {assets_summary.manifest_path}",
                f"- Figures: {assets_summary.figures}",
                f"- Tables: {assets_summary.tables}",
                f"- Objects: {assets_summary.objects}",
            ]
        )

    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "Use `read_saved_paper` for canonical deep reading and citation verification.",
        ]
    )
    return "\n".join(lines).strip()
