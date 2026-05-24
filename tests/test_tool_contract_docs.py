from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path
from typing import Any

from grados.server import mcp

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_TABLE_PATTERN = re.compile(r"\| `grados:([^`/][^`]*)` \|")
TOOL_TIER_SECTION_PATTERN = re.compile(
    r"## Tool Tiers\n(?P<section>.*?)\n## GRaDOS Server Tools",
    re.DOTALL,
)
INLINE_TOOL_PATTERN = re.compile(r"`grados:([^`]+)`")


def _live_tools() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _tool_property(tools: dict[str, Any], tool_name: str, property_name: str) -> dict[str, Any]:
    properties = tools[tool_name].parameters["properties"]
    value = properties[property_name]
    assert isinstance(value, dict)
    return value


def test_skill_tool_reference_matches_live_mcp_tool_names() -> None:
    live_tool_names = set(_live_tools())
    tools_reference = _read("skills/grados/references/tools.md")
    documented_tool_names = set(TOOL_TABLE_PATTERN.findall(tools_reference))

    assert documented_tool_names == live_tool_names


def test_skill_tool_tiers_cover_each_live_mcp_tool_once() -> None:
    live_tool_names = set(_live_tools())
    tools_reference = _read("skills/grados/references/tools.md")
    tier_section_match = TOOL_TIER_SECTION_PATTERN.search(tools_reference)

    assert tier_section_match is not None
    tier_table = "\n".join(
        line for line in tier_section_match.group("section").splitlines() if line.startswith("|")
    )
    tier_tool_names = INLINE_TOOL_PATTERN.findall(tier_table)
    tier_counts = Counter(tier_tool_names)

    assert set(tier_counts) == live_tool_names
    assert {name: count for name, count in tier_counts.items() if count != 1} == {}


def test_readmes_keep_light_coverage_of_live_mcp_tool_names() -> None:
    live_tool_names = sorted(_live_tools())

    for docs_path in ["README.md", "README.zh-CN.md"]:
        text = _read(docs_path)
        missing = [tool_name for tool_name in live_tool_names if f"`{tool_name}`" not in text]
        assert missing == []


def test_skill_tool_reference_mirrors_selected_live_schema_guardrails() -> None:
    tools = _live_tools()
    tools_reference = _read("skills/grados/references/tools.md")

    schema_checks = [
        ("search_academic_papers", "query", "minLength", 1, "`query` minLength=1"),
        ("search_academic_papers", "limit", "minimum", 1, "`limit` range 1-50"),
        ("search_academic_papers", "limit", "maximum", 50, "`limit` range 1-50"),
        ("extract_paper_full_text", "force_refresh", "default", False, "`force_refresh` defaults to false"),
        ("search_saved_papers", "limit", "maximum", 25, "`limit` range 1-25"),
        ("read_saved_paper", "start_paragraph", "minimum", 0, "`start_paragraph` minimum 0"),
        ("read_saved_paper", "max_paragraphs", "maximum", 100, "`max_paragraphs` range 1-100"),
        ("read_paper_asset", "limit", "maximum", 100, "list-mode `limit` range 1-100"),
        ("read_paper_asset", "offset", "minimum", 0, "`offset` minimum 0"),
        ("query_research_artifacts", "limit", "maximum", 50, "`limit` range 1-50"),
        ("get_papers_full_context", "dois", "minItems", 1, "`dois` minItems=1"),
        ("get_papers_full_context", "max_total_tokens", "maximum", 128000, "`max_total_tokens` range 1000-128000"),
        ("build_evidence_grid", "max_papers", "minimum", 1, "`max_papers` range 1-12"),
        ("build_evidence_grid", "max_papers", "maximum", 12, "`max_papers` range 1-12"),
        ("audit_draft_support", "draft_text", "minLength", 1, "`draft_text` minLength=1"),
        ("audit_draft_support", "candidate_limit", "maximum", 25, "`candidate_limit` range 1-25"),
        (
            "audit_answer_against_pack",
            "include_suggestions",
            "default",
            False,
            "`include_suggestions` defaults to false",
        ),
        ("audit_answer_against_pack", "max_suggestions", "minimum", 1, "`max_suggestions` range 1-25"),
        ("audit_answer_against_pack", "max_suggestions", "maximum", 25, "`max_suggestions` range 1-25"),
        ("save_external_synthesis_result", "audit", "default", True, "`audit` defaults to true"),
    ]
    for tool_name, property_name, schema_key, expected, doc_fragment in schema_checks:
        assert _tool_property(tools, tool_name, property_name)[schema_key] == expected
        assert doc_fragment in tools_reference

    enum_checks = [
        ("get_papers_full_context", "mode", ["estimate", "full"], "`mode` enum `estimate` / `full`"),
        (
            "audit_draft_support",
            "citation_style",
            ["author_year", "numeric"],
            "`citation_style` enum `author_year` / `numeric`",
        ),
        ("audit_draft_support", "strictness", ["strict", "balanced"], "`strictness` enum `strict` / `balanced`"),
    ]
    for tool_name, property_name, expected_enum, doc_fragment in enum_checks:
        assert _tool_property(tools, tool_name, property_name)["enum"] == expected_enum
        assert doc_fragment in tools_reference


def test_external_synthesis_tool_description_mentions_packet_scope() -> None:
    description = _live_tools()["audit_external_synthesis_result"].description or ""

    assert "linked packet" in description
    assert "source evidence pack" in description
    assert "structured claim anchor ids" in description


def test_external_synthesis_has_topic_to_packet_route_and_default_save_audit() -> None:
    tools = _live_tools()
    run_description = tools["run_external_synthesis"].description or ""
    prepare_description = tools["prepare_external_synthesis_from_topic"].description or ""
    save_description = tools["save_external_synthesis_result"].description or ""

    assert "GRaDOS-native ChatGPT Pro browser synthesis route" in run_description
    assert "private GRaDOS ChatGPT Chrome profile" in run_description
    assert "GRaDOS-validated Pro model and Pro Extended thinking route" in run_description
    assert "fresh evidence pack from a topic" in prepare_description
    assert "run_external_synthesis" in prepare_description
    assert "By default, immediately audits" in save_description
    assert _tool_property(tools, "save_external_synthesis_result", "audit")["default"] is True


def test_read_evidence_pack_tool_description_is_inspection_only() -> None:
    description = _live_tools()["read_evidence_pack"].description or ""

    assert "Inspect or recover" in description
    assert "read the pack internally" in description
    assert "before drafting" not in description
    assert "before pack-scoped auditing" not in description


def test_full_context_schema_avoids_low_fixed_paper_cap() -> None:
    tools = _live_tools()
    description = tools["get_papers_full_context"].description or ""
    dois_description = _tool_property(tools, "get_papers_full_context", "dois")["description"]

    assert "1-8" not in description
    assert "1-8" not in dois_description
    assert "context-budgeted" in description
    assert "multiple calls" in dois_description


def test_docs_do_not_claim_removed_project_id_parameter() -> None:
    tools = _live_tools()
    assert "project_id" not in tools["query_research_artifacts"].parameters["properties"]

    for docs_path in [
        "README.md",
        "README.zh-CN.md",
        "skills/grados/references/tools.md",
        "plugins/grados/skills/grados/references/tools.md",
    ]:
        assert "project_id" not in _read(docs_path)
