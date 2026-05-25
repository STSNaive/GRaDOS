"""Library and paper-management MCP tools/resources."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import stat
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from grados.config import GRaDOSConfig, GRaDOSPaths, IndexingConfig
from grados.http_limits import SizeLimitError, ensure_byte_limit
from grados.local_files import LocalFileReadError, read_bounded_local_file
from grados.publisher.common import PublisherMetadata, normalize_publisher_metadata, safe_doi_filename
from grados.server_tools.shared import (
    format_paper_index_resource,
    format_paper_overview_resource,
    get_paths_and_config,
    missing_paper_selector_message,
)
from grados.storage.assets import AssetLimits
from grados.workflows.library import (
    build_library_document_artifact,
    materialize_library_pdf,
    merge_library_diagnostics,
    persist_reviewed_library_document,
    plan_duplicate_library_pdf_cleanup,
    review_library_document,
)

__all__ = [
    "extract_paper_full_text",
    "get_saved_paper_structure",
    "ingest_codex_downloaded_pdf",
    "import_local_pdf_library",
    "paper_overview_resource",
    "papers_index_resource",
    "parse_pdf_file",
    "plan_library_pdf_cleanup",
    "read_paper_asset",
    "read_saved_paper",
    "register_library_tools",
]

_CODEX_HANDOFF_NEXT_ACTION = "download_with_chrome_extension_then_call_ingest_codex_downloaded_pdf"
_CODEX_INGEST_RECOVERY_NEXT_ACTION = (
    "call_ingest_codex_downloaded_pdf_with_downloaded_file_path_or_call_parse_pdf_file_with_known_pdf_path"
)
_CODEX_HANDOFF_DEFAULT_WATCH_DIR = "~/Downloads"
_PARSE_ATTEMPT_FUTURES: dict[str, concurrent.futures.Future[str]] = {}
_PARSE_ATTEMPT_LOCK = threading.Lock()


def _format_asset_hint_lines(asset_hints: Sequence[Mapping[str, object]]) -> list[str]:
    lines: list[str] = []
    for hint in asset_hints:
        label = str(hint.get("label", "")).strip() or str(hint.get("kind", "")).strip() or "asset_hint"
        target = str(hint.get("url", "")).strip() or str(hint.get("value", "")).strip()
        if target:
            lines.append(f"- {label}: {target}")
        else:
            lines.append(f"- {label}")
    return lines


def _asset_limits_from_config(config: object) -> AssetLimits:
    assets = getattr(getattr(config, "extract", object()), "assets", object())
    return AssetLimits(
        max_asset_file_bytes=int(getattr(assets, "max_asset_file_bytes", 32 * 1024 * 1024)),
        max_asset_total_bytes=int(getattr(assets, "max_asset_total_bytes", 512 * 1024 * 1024)),
        max_asset_inline_bytes=int(getattr(assets, "max_asset_inline_bytes", 8 * 1024 * 1024)),
        max_asset_count=int(getattr(assets, "max_asset_count", 3000)),
    )


def _metadata_only_receipt(
    doi: str,
    *,
    source: str,
    metadata: PublisherMetadata | None,
    asset_hints: list[dict[str, str]],
    warnings: list[str],
) -> str:
    lines = [
        "## Paper Located but Full Text Unavailable",
        "",
        f"- **DOI:** {doi}",
        f"- **Paper ID:** {safe_doi_filename(doi)}",
        f"- **Safe DOI:** {safe_doi_filename(doi)}",
        f"- **Source:** {source or 'Unknown'}",
        "- **Outcome:** metadata_only",
        "- **Fetch Status:** metadata_only",
        "- **Has Fulltext:** false",
        "- **Canonical Save:** not_written",
        "- **Index Status:** not_requested",
    ]

    if metadata is not None:
        if metadata.title:
            lines.append(f"- **Title:** {metadata.title}")
        if metadata.authors:
            lines.append(f"- **Authors:** {', '.join(metadata.authors[:8])}")
        if metadata.year:
            lines.append(f"- **Year:** {metadata.year}")
        if metadata.journal:
            lines.append(f"- **Journal:** {metadata.journal}")
        if metadata.publisher:
            lines.append(f"- **Publisher:** {metadata.publisher}")

    lines.extend(
        [
            "",
            "### Next Step",
            "- No canonical markdown was saved because no full text was obtained.",
            (
                "- Use the metadata and asset hints to decide whether to retry with another route "
                "or save the citation only."
            ),
        ]
    )

    if asset_hints:
        lines.extend(["", "### Asset Hints", *_format_asset_hint_lines(asset_hints)])
    if warnings:
        lines.extend(["", "### Warnings", *[f"- {warning}" for warning in warnings]])

    return "\n".join(lines)


def _already_saved_receipt(doi: str, record: object, papers_dir: Path) -> str:
    section_headings = list(getattr(record, "section_headings", []) or [])
    safe_doi = str(getattr(record, "safe_doi", "") or "")
    file_path = str((papers_dir / f"{safe_doi}.md").resolve()) if safe_doi else ""
    result = "## Paper Already Saved\n\n"
    result += f"- **DOI:** {getattr(record, 'doi', '') or doi}\n"
    result += f"- **Paper ID:** {safe_doi}\n"
    result += f"- **Safe DOI:** {safe_doi}\n"
    result += f"- **URI:** {getattr(record, 'canonical_uri', '')}\n"
    result += f"- **File:** {file_path}\n"
    result += f"- **Words:** {int(getattr(record, 'word_count', 0) or 0):,}\n"
    result += f"- **Characters:** {int(getattr(record, 'char_count', 0) or 0):,}\n"
    if getattr(record, "title", ""):
        result += f"- **Title:** {getattr(record, 'title')}\n"
    if getattr(record, "source", ""):
        result += f"- **Source:** {getattr(record, 'source')}\n"
    if getattr(record, "fetch_outcome", ""):
        result += f"- **Outcome:** {getattr(record, 'fetch_outcome')}\n"
    result += "- **Fetch Status:** fulltext\n"
    result += "- **Has Fulltext:** true\n"
    result += "- **Index Status:** existing\n"
    result += "- **Next Action:** read_saved_paper\n"
    result += "- **Refresh:** call `extract_paper_full_text` with `force_refresh=true` to refetch/reparse.\n"
    if section_headings:
        result += "\n### Sections\n" + "\n".join(f"- {heading}" for heading in section_headings)
    return result


def _parse_attempt_config_signature(config: object) -> dict[str, object]:
    parsing = getattr(getattr(config, "extract", object()), "parsing", object())
    return {
        "order": list(getattr(parsing, "order", []) or []),
        "enabled": dict(getattr(parsing, "enabled", {}) or {}),
        "marker_timeout": int(getattr(parsing, "marker_timeout", 120000)),
        "mineru_timeout": int(getattr(parsing, "mineru_timeout", 300000)),
        "mineru_poll_interval": float(getattr(parsing, "mineru_poll_interval", 3.0)),
        "mineru_model_version": str(getattr(parsing, "mineru_model_version", "")),
        "mineru_language": str(getattr(parsing, "mineru_language", "")),
        "mineru_enable_formula": bool(getattr(parsing, "mineru_enable_formula", True)),
        "mineru_enable_table": bool(getattr(parsing, "mineru_enable_table", True)),
        "mineru_is_ocr": bool(getattr(parsing, "mineru_is_ocr", False)),
    }


def _parse_attempt_is_stale(record: object, *, stale_seconds: float) -> bool:
    updated_at = str(getattr(record, "updated_at", "") or "")
    if not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).total_seconds() >= stale_seconds


def _parse_attempt_failure_is_terminal(record: object) -> bool:
    return str(getattr(record, "failure_reason", "") or "") == "pdf_materialization_conflict"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_attempt_completion_metadata(paths: object, doi: str) -> dict[str, str]:
    from grados.storage.papers import load_paper_record

    record = load_paper_record(getattr(paths, "papers"), doi=doi)
    metadata = {
        "canonical_uri": "",
        "paper_path": "",
        "canonical_pdf_path": "",
        "canonical_pdf_hash": "",
    }
    if record is not None:
        metadata["canonical_uri"] = record.canonical_uri
        metadata["paper_path"] = str((getattr(paths, "papers") / f"{record.safe_doi}.md").resolve())
        pdf_path = getattr(paths, "downloads") / f"{record.safe_doi}.pdf"
        if pdf_path.is_file():
            metadata["canonical_pdf_path"] = str(pdf_path.resolve())
            metadata["canonical_pdf_hash"] = _hash_file(pdf_path)
    return metadata


def _receipt_field(receipt: str, label: str) -> str:
    prefix = f"- **{label}:** "
    for line in receipt.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _parse_in_progress_receipt(
    *,
    doi: str,
    attempt: object,
    foreground_wait_seconds: float,
    attempt_stale_seconds: float,
    existing_worker: bool,
) -> str:
    action = "already_running" if existing_worker else "accepted"
    lines = [
        "## PDF Parse Accepted",
        "",
        f"- **DOI:** {doi}",
        "- **Status:** in_progress",
        "- **Outcome:** parse_in_progress",
        f"- **Attempt ID:** {getattr(attempt, 'attempt_id', '')}",
        f"- **Input PDF:** {getattr(attempt, 'input_pdf_path', '')}",
        f"- **Input PDF Name:** {getattr(attempt, 'input_pdf_name', '')}",
        f"- **Source PDF Hash:** {getattr(attempt, 'input_pdf_hash', '')}",
        f"- **Action:** {action}",
        f"- **Foreground Wait Seconds:** {foreground_wait_seconds:g}",
        f"- **Attempt Stale Seconds:** {attempt_stale_seconds:g}",
        "- **Next Action:** retry_parse_pdf_file_or_ingest_codex_downloaded_pdf_with_same_file_path",
        "",
        (
            "GRaDOS is still parsing this local PDF in a durable DOI-bound attempt. "
            "Do not download the PDF again; retry later with the same DOI and file path, or read the saved paper "
            "if canonical materialization has completed."
        ),
    ]
    return "\n".join(lines)


def _parse_retry_wait_receipt(
    *,
    doi: str,
    attempt: object,
    attempt_stale_seconds: float,
) -> str:
    lines = [
        "## PDF Parse Accepted",
        "",
        f"- **DOI:** {doi}",
        "- **Status:** retry_wait",
        "- **Outcome:** parse_retry_wait",
        f"- **Attempt ID:** {getattr(attempt, 'attempt_id', '')}",
        f"- **Input PDF:** {getattr(attempt, 'input_pdf_path', '')}",
        f"- **Input PDF Name:** {getattr(attempt, 'input_pdf_name', '')}",
        f"- **Source PDF Hash:** {getattr(attempt, 'input_pdf_hash', '')}",
        f"- **Previous Failure Reason:** {getattr(attempt, 'failure_reason', '')}",
        f"- **Previous Error:** {getattr(attempt, 'error_message', '')}",
        f"- **Attempt Stale Seconds:** {attempt_stale_seconds:g}",
        "- **Next Action:** retry_parse_pdf_file_or_ingest_codex_downloaded_pdf_with_same_file_path_after_stale_window",
        "",
        (
            "GRaDOS recorded a previous retryable parse failure for this DOI and PDF hash. "
            "Do not download the PDF again; retry later with the same DOI and file path. "
            "After the stale window expires, GRaDOS will restart the parse attempt from the same local PDF."
        ),
    ]
    return "\n".join(lines)


def _append_remote_metadata_warning(result: str, warning: str | None) -> str:
    if not warning:
        return result
    return result + f"\n\n### Remote Metadata\n- {warning}"


def _fetch_browser_capture(fetch_result: object) -> dict[str, object]:
    for trace in list(getattr(fetch_result, "trace", []) or []):
        if not isinstance(trace, dict):
            continue
        capture = trace.get("capture")
        if isinstance(capture, dict) and capture.get("source"):
            return capture
    return {}


def _append_manual_resume_receipt(result: str, fetch_result: object) -> str:
    manual = bool(getattr(fetch_result, "manual", False))
    resume = getattr(fetch_result, "resume", {}) or {}
    if not manual or not isinstance(resume, dict) or not resume:
        return result

    kind = str(resume.get("kind", "") or "")
    if kind == "codex":
        doi = str(resume.get("doi", "") or "")
        browser = str(resume.get("browser", "") or "Google Chrome")
        start_url = str(resume.get("start_url", "") or (f"https://doi.org/{doi}" if doi else ""))
        start_url_source = str(resume.get("start_url_source", "") or "")
        issued_at = str(resume.get("issued_at", "") or "")
        download_watch_dir = str(resume.get("download_watch_dir", "") or "")
        download_max_age_seconds = str(resume.get("download_max_age_seconds", "") or "")
        next_action = str(resume.get("next_action", "") or _CODEX_HANDOFF_NEXT_ACTION)
        required_host_plugin = str(resume.get("required_host_plugin", "") or "@chrome")
        required_host_backend = str(
            resume.get("required_host_backend", "") or "Codex Chrome plugin extension backend"
        )
        requested_route = str(resume.get("requested_route", "") or "codex_chrome_plugin_extension")
        documentation_url = str(resume.get("documentation_url", "") or "")
        result += "\n\n### Codex Chrome Extension Download\n"
        result += f"- **Browser:** {browser}\n"
        result += f"- **Required Host Plugin:** {required_host_plugin}\n"
        result += f"- **Required Host Backend:** {required_host_backend}\n"
        result += f"- **Requested Route:** {requested_route}\n"
        if documentation_url:
            result += f"- **Setup:** {documentation_url}\n"
        if start_url:
            result += f"- **Start URL:** {start_url}\n"
        if start_url_source:
            result += f"- **Start URL Source:** {start_url_source}\n"
        if issued_at:
            result += f"- **Issued At:** {issued_at}\n"
        if download_watch_dir:
            result += f"- **Download Watch Dir:** {download_watch_dir}\n"
        if download_max_age_seconds:
            result += f"- **Download Max Age Seconds:** {download_max_age_seconds}\n"
        if next_action:
            result += f"- **Next Action:** {next_action}\n"
        result += (
            "- **Next:** use the Codex `@chrome` plugin / Chrome extension backend to download the PDF, then call "
            "`ingest_codex_downloaded_pdf(doi=..., downloaded_file_path=...)` when the absolute PDF path is known. "
            "If you want to bypass handoff-state recovery entirely, call "
            "`parse_pdf_file(file_path=..., doi=..., copy_to_library=true, acquisition_via=\"codex\")`.\n"
        )
        return result.rstrip()

    host = str(getattr(fetch_result, "host", "") or resume.get("host", "") or "")
    url = str(resume.get("url", "") or "")
    profile_dir = str(resume.get("profile_dir", "") or "")
    action = str(resume.get("action", "complete_publisher_verification_then_retry") or "")
    result += "\n\n### Manual Browser Resume\n"
    if host:
        result += f"- **Host:** {host}\n"
    if url:
        result += f"- **URL:** {url}\n"
    if profile_dir:
        result += f"- **Profile:** {profile_dir}\n"
    if action:
        result += f"- **Action:** {action}\n"
    result += "- **Retry:** call `extract_paper_full_text` with `resume_browser=true` after verification.\n"
    return result.rstrip()


def _remote_metadata_dir(paths: object) -> Path:
    metadata_dir = getattr(paths, "database_remote_metadata", None)
    if isinstance(metadata_dir, Path):
        return metadata_dir
    chroma_dir = getattr(paths, "database_chroma")
    if isinstance(chroma_dir, Path):
        return chroma_dir
    return Path(chroma_dir)


def _load_browser_resume(paths: object, doi: str) -> dict[str, str] | None:
    from grados.storage.remote_metadata import get_remote_metadata_by_doi

    metadata_dirs = [_remote_metadata_dir(paths)]
    legacy_dir = getattr(paths, "database_chroma", None)
    if legacy_dir is not None and legacy_dir != metadata_dirs[0]:
        metadata_dirs.append(legacy_dir)

    record = None
    for metadata_dir in metadata_dirs:
        record = get_remote_metadata_by_doi(metadata_dir, doi)
        if record is not None:
            break
    if record is None or record.fetch_status != "challenge" or not record.fetch_manual:
        return None
    if not record.fetch_resume:
        return None
    try:
        loaded = json.loads(record.fetch_resume)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    resume = {str(key): str(value) for key, value in loaded.items() if str(value)}
    return resume or None


def _infer_remote_fetch_status(outcome: str, state: str, warnings: list[str]) -> str:
    if outcome == "metadata_only":
        return "metadata_only"
    if outcome == "host_action_required" or state == "host_action_required":
        return "host_action_required"
    if outcome in {"native_full_text", "pdf_obtained"}:
        return "fulltext"
    if state == "challenge":
        return "challenge"

    warning_text = " ".join(warnings).lower()
    challenge_markers = (
        "publisher_challenge",
        "challenge",
        "captcha",
        "are you a robot",
        "just a moment",
    )
    if any(marker in warning_text for marker in challenge_markers):
        return "challenge"
    return "failed"


def _canonical_fetch_strategy_name(name: str) -> str:
    normalized = "".join(ch for ch in name.lower() if ch.isalnum())
    aliases = {
        "api": "api",
        "tdm": "api",
        "browser": "browser",
        "headless": "browser",
        "codex": "codex",
        "scihub": "scihub",
    }
    return aliases.get(normalized, normalized)


def _remaining_fetch_order_after(order: Sequence[str], via: str) -> list[str]:
    canonical_order: list[str] = []
    seen: set[str] = set()
    for item in order:
        canonical = _canonical_fetch_strategy_name(str(item))
        if canonical and canonical not in seen:
            canonical_order.append(canonical)
            seen.add(canonical)
    if not canonical_order:
        canonical_order = ["api", "browser", "codex", "scihub"]

    canonical_via = _canonical_fetch_strategy_name(via)
    if canonical_via not in canonical_order:
        return []
    return canonical_order[canonical_order.index(canonical_via) + 1 :]


def _append_unique_diagnostics(target: list[str], values: Sequence[str]) -> None:
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in target:
            target.append(normalized)


def _publisher_metadata_signal_score(metadata: PublisherMetadata | None) -> int:
    if metadata is None:
        return 0
    return sum(
        1
        for value in [
            metadata.doi.strip(),
            metadata.title.strip(),
            metadata.abstract.strip(),
            metadata.year.strip(),
            metadata.journal.strip(),
            metadata.publisher.strip(),
            metadata.pii.strip(),
            metadata.eid.strip(),
            metadata.scidir_url.strip(),
            metadata.html_url.strip(),
            metadata.pdf_url.strip(),
        ]
        if value
    ) + len(metadata.authors)


def _prefer_publisher_metadata(
    current: PublisherMetadata | None,
    candidate: PublisherMetadata | None,
) -> PublisherMetadata | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    if _publisher_metadata_signal_score(candidate) > _publisher_metadata_signal_score(current):
        return candidate
    return current


def _record_remote_metadata_update(
    *,
    metadata_dir: Path,
    doi: str,
    fetch_status: str,
    has_fulltext: bool,
    source: str,
    title: str,
    metadata: PublisherMetadata | None,
    fetch_via: str = "",
    fetch_state: str = "",
    fetch_host: str = "",
    fetch_resume: dict[str, str] | None = None,
    fetch_manual: bool = False,
    fetch_trace: list[dict[str, Any]] | None = None,
    indexing_config: IndexingConfig | None = None,
) -> str | None:
    from grados.storage.remote_metadata import record_remote_fetch_result

    try:
        record_remote_fetch_result(
            metadata_dir,
            doi=doi,
            fetch_status=fetch_status,
            has_fulltext=has_fulltext,
            source=source,
            title=title,
            metadata=metadata,
            fetch_via=fetch_via,
            fetch_state=fetch_state,
            fetch_host=fetch_host,
            fetch_resume=fetch_resume,
            fetch_manual=fetch_manual,
            fetch_trace=fetch_trace,
            indexing_config=indexing_config,
        )
    except Exception as exc:
        return f"Remote metadata cache update failed: {exc.__class__.__name__}: {exc}"
    return None


def papers_index_resource() -> str:
    """Low-token index of saved papers."""
    from grados.storage.papers import list_saved_papers

    paths, _ = get_paths_and_config()
    papers = list_saved_papers(paths.papers, chroma_dir=paths.database_chroma)
    return format_paper_index_resource(papers)


def paper_overview_resource(safe_doi: str) -> str:
    """Overview card for a saved paper resource."""
    from grados.storage.papers import get_paper_structure

    paths, _ = get_paths_and_config()
    structure = get_paper_structure(
        papers_dir=paths.papers,
        safe_doi=safe_doi,
    )
    if not structure:
        return f"# Paper Not Found\n\nCould not resolve grados://papers/{safe_doi}"

    return format_paper_overview_resource(structure)


async def extract_paper_full_text(
    doi: Annotated[str, Field(min_length=1, description="Paper DOI to fetch and save.")],
    publisher: Annotated[
        str | None,
        Field(
            description=(
                "Optional publisher hint for reporting only. "
                "This does not change fetch routing; GRaDOS still follows the configured strategy order."
            )
        ),
    ] = None,
    expected_title: Annotated[
        str | None,
        Field(description="Optional title hint used for QA validation and save metadata only."),
    ] = None,
    resume_browser: Annotated[
        bool,
        Field(
            description=(
                "Resume a previous browser challenge for this DOI. When true, GRaDOS starts at the browser "
                "strategy and uses the saved publisher URL/profile instead of rerunning the full api-first chain."
            )
        ),
    ] = False,
    force_refresh: Annotated[
        bool,
        Field(
            description=(
                "When false, return the existing saved-paper receipt if canonical Markdown is already present. "
                "Set force_refresh=true to refetch/reparse and overwrite local full text."
            )
        ),
    ] = False,
) -> str:
    """Fetch, parse, and save one paper's canonical full text by DOI."""
    from grados.extract.fetch import fetch_paper
    from grados.extract.parse import normalize_document_text_with_diagnostics, parse_pdf_with_diagnostics
    from grados.extract.qa import is_valid_paper_content
    from grados.storage.papers import load_paper_record

    paths, config = get_paths_and_config()
    indexing_config = getattr(config, "indexing", None)
    api_keys = {k: v for k, v in config.api_keys.model_dump().items() if v}
    metadata_dir = _remote_metadata_dir(paths)
    if not force_refresh:
        record = load_paper_record(paths.papers, doi=doi)
        if record is not None and record.content_markdown.strip():
            return _already_saved_receipt(doi, record, paths.papers)

    browser_resume = _load_browser_resume(paths, doi) if resume_browser else None
    if resume_browser and browser_resume is None:
        browser_resume = {}
    configured_fetch_order = list(config.extract.fetch_strategy.order)
    repair_warnings: list[str] = []

    async def run_fetch_attempt(
        *,
        fetch_order: list[str],
        browser_resume_override: dict[str, str] | None,
    ) -> Any:
        return await fetch_paper(
            doi=doi,
            api_keys=api_keys,
            etiquette_email=config.academic_etiquette_email,
            fetch_order=fetch_order,
            fetch_enabled=config.extract.fetch_strategy.enabled,
            tdm_order=config.extract.tdm.order,
            tdm_enabled=config.extract.tdm.enabled,
            sci_hub_config=config.extract.sci_hub.model_dump(),
            headless_config=config.extract.headless_browser,
            paths=paths,
            browser_resume=browser_resume_override,
            codex_handoff_config=config.extract.codex_handoff,
            unpaywall_enabled=bool(getattr(config.extract.unpaywall, "enabled", True)),
            max_remote_pdf_bytes=config.extract.security.max_remote_pdf_bytes,
            max_remote_text_bytes=config.extract.security.max_remote_text_bytes,
            max_browser_capture_bytes=config.extract.security.max_browser_capture_bytes,
        )

    fetch_result = await run_fetch_attempt(
        fetch_order=configured_fetch_order,
        browser_resume_override=browser_resume if resume_browser else None,
    )

    def normalized_fetch_metadata(fetch_candidate: Any) -> PublisherMetadata | None:
        candidate_metadata = normalize_publisher_metadata(fetch_candidate.metadata)
        if candidate_metadata is not None and expected_title and not candidate_metadata.title:
            candidate_metadata = candidate_metadata.model_copy(update={"title": expected_title})
        return candidate_metadata

    metadata = normalized_fetch_metadata(fetch_result)

    if fetch_result.outcome == "metadata_only":
        remote_warning = _record_remote_metadata_update(
            metadata_dir=metadata_dir,
            doi=doi,
            fetch_status="metadata_only",
            has_fulltext=False,
            source=fetch_result.source,
            title=expected_title or (metadata.title if metadata is not None else ""),
            metadata=metadata,
            fetch_via=fetch_result.via,
            fetch_state=fetch_result.state,
            fetch_host=fetch_result.host,
            fetch_resume=fetch_result.resume,
            fetch_manual=fetch_result.manual,
            fetch_trace=fetch_result.trace,
            indexing_config=indexing_config,
        )
        return _append_remote_metadata_warning(
            _metadata_only_receipt(
                doi,
                source=fetch_result.source,
                metadata=metadata,
                asset_hints=fetch_result.asset_hints,
                warnings=fetch_result.warnings,
            ),
            remote_warning,
        )

    if fetch_result.outcome not in ("native_full_text", "pdf_obtained"):
        remote_warning = _record_remote_metadata_update(
            metadata_dir=metadata_dir,
            doi=doi,
            fetch_status=_infer_remote_fetch_status(fetch_result.outcome, fetch_result.state, fetch_result.warnings),
            has_fulltext=False,
            source=fetch_result.source,
            title=expected_title or (metadata.title if metadata is not None else ""),
            metadata=metadata,
            fetch_via=fetch_result.via,
            fetch_state=fetch_result.state,
            fetch_host=fetch_result.host,
            fetch_resume=fetch_result.resume,
            fetch_manual=fetch_result.manual,
            fetch_trace=fetch_result.trace,
            indexing_config=indexing_config,
        )
        failed_result = _append_manual_resume_receipt(
            f"Failed to fetch paper: {doi}\nWarnings: " + "; ".join(fetch_result.warnings or []),
            fetch_result,
        )
        return _append_remote_metadata_warning(
            failed_result,
            remote_warning,
        )

    async def build_artifact_for_fetch(candidate: Any) -> Any:
        if candidate.outcome == "native_full_text":
            return await build_library_document_artifact(
                lambda: normalize_document_text_with_diagnostics(
                    candidate.text,
                    content_format=candidate.text_format or "text",
                    filename=f"{doi}.txt",
                )
            )
        return await build_library_document_artifact(
            lambda: parse_pdf_with_diagnostics(
                candidate.pdf_buffer,
                filename=f"{safe_doi_filename(doi)}.pdf",
                parse_order=config.extract.parsing.order,
                parse_enabled=config.extract.parsing.enabled,
                marker_timeout=config.extract.parsing.marker_timeout,
                mineru_api_key=config.api_keys.MINERU_API_KEY,
                mineru_model_version=config.extract.parsing.mineru_model_version,
                mineru_language=config.extract.parsing.mineru_language,
                mineru_timeout=config.extract.parsing.mineru_timeout,
                mineru_poll_interval=config.extract.parsing.mineru_poll_interval,
                mineru_enable_formula=config.extract.parsing.mineru_enable_formula,
                mineru_enable_table=config.extract.parsing.mineru_enable_table,
                mineru_is_ocr=config.extract.parsing.mineru_is_ocr,
                mineru_max_zip_bytes=config.extract.security.max_mineru_zip_bytes,
                mineru_max_full_md_bytes=config.extract.security.max_mineru_full_md_bytes,
                asset_mode=config.extract.assets.mode,
                docling_image_scale=config.extract.assets.docling_image_scale,
                max_asset_file_bytes=config.extract.assets.max_asset_file_bytes,
                max_asset_total_bytes=config.extract.assets.max_asset_total_bytes,
                max_asset_inline_bytes=config.extract.assets.max_asset_inline_bytes,
                max_asset_count=config.extract.assets.max_asset_count,
                qa_validator=is_valid_paper_content,
                qa_min_characters=config.extract.qa.min_characters,
                qa_expected_title=expected_title,
                continue_on_qa_failure=True,
            )
        )

    artifact = None
    review = None
    copied_pdf_path = ""
    pdf_materialization = None

    while True:
        artifact = await build_artifact_for_fetch(fetch_result)
        if not artifact.markdown:
            remaining_order = _remaining_fetch_order_after(configured_fetch_order, fetch_result.via)
            if remaining_order and not resume_browser:
                repair_warnings.append(
                    f"{fetch_result.source or fetch_result.via} returned no usable Markdown; "
                    f"trying next fetch routes: {', '.join(remaining_order)}."
                )
                previous_fetch_result = fetch_result
                next_fetch = await run_fetch_attempt(
                    fetch_order=remaining_order,
                    browser_resume_override=None,
                )
                if next_fetch.outcome in ("native_full_text", "pdf_obtained"):
                    fetch_result = next_fetch
                    metadata = _prefer_publisher_metadata(metadata, normalized_fetch_metadata(fetch_result))
                    continue
                _append_unique_diagnostics(repair_warnings, list(getattr(next_fetch, "warnings", []) or []))
                repair_warnings.append(
                    f"Next fetch route ended with outcome={next_fetch.outcome or 'failed'}; keeping parse failure."
                )
                fetch_result = previous_fetch_result
            warnings, parser_debug = merge_library_diagnostics(
                artifact,
                base_warnings=[*repair_warnings, *list(fetch_result.warnings)],
            )
            warning_block = "\n".join(f"- {warning}" for warning in warnings) if warnings else "- Unknown parse failure"
            debug_block = "\n".join(f"- {entry}" for entry in parser_debug)
            result = f"Failed to parse PDF for {doi}\n\nWarnings:\n{warning_block}"
            if debug_block:
                result += f"\n\nParser debug:\n{debug_block}"
            remote_warning = _record_remote_metadata_update(
                metadata_dir=metadata_dir,
                doi=doi,
                fetch_status="failed",
                has_fulltext=True,
                source=fetch_result.source,
                title=expected_title or (metadata.title if metadata is not None else ""),
                metadata=metadata,
                fetch_via=fetch_result.via,
                fetch_state=fetch_result.state,
                fetch_host=fetch_result.host,
                fetch_resume=fetch_result.resume,
                fetch_manual=fetch_result.manual,
                fetch_trace=fetch_result.trace,
                indexing_config=indexing_config,
            )
            return _append_remote_metadata_warning(result, remote_warning)

        review = review_library_document(
            artifact,
            qa_validator=is_valid_paper_content,
            qa_min_characters=config.extract.qa.min_characters,
            qa_expected_title=expected_title,
            qa_warning_message="QA validation failed — saved content may be incomplete.",
            base_warnings=[*repair_warnings, *list(fetch_result.warnings)],
        )
        if review.qa_passed:
            break

        remaining_order = _remaining_fetch_order_after(configured_fetch_order, fetch_result.via)
        if not remaining_order or resume_browser:
            repair_warnings.append(
                "QA repair waterfall exhausted; saving this candidate as partial_success with QA warnings."
            )
            break
        repair_warnings.append(
            f"QA rejected {fetch_result.source or fetch_result.via}; "
            f"trying next fetch routes: {', '.join(remaining_order)}."
        )
        next_fetch = await run_fetch_attempt(
            fetch_order=remaining_order,
            browser_resume_override=None,
        )
        if next_fetch.outcome not in ("native_full_text", "pdf_obtained"):
            _append_unique_diagnostics(repair_warnings, list(getattr(next_fetch, "warnings", []) or []))
            repair_warnings.append(
                f"Next fetch route ended with outcome={next_fetch.outcome or 'failed'}; "
                "keeping best QA-failed candidate."
            )
            break
        fetch_result = next_fetch
        metadata = _prefer_publisher_metadata(metadata, normalized_fetch_metadata(fetch_result))

    title = ""
    authors: list[str] = []
    year = ""
    journal = ""
    publisher_name = ""
    extra_frontmatter: dict[str, str] = {}

    if metadata is not None:
        dump = metadata.model_dump()
        title = str(dump.get("title", ""))
        authors = [str(value) for value in dump.get("authors", []) if str(value)]
        year = str(dump.get("year", ""))
        journal = str(dump.get("journal", ""))
        publisher_name = str(dump.get("publisher", ""))

    if expected_title and not title:
        title = expected_title

    if review is None:
        raise RuntimeError("Internal error: reviewed library document was not prepared")
    if fetch_result.outcome == "pdf_obtained":
        pdf_materialization = materialize_library_pdf(
            doi=doi,
            paths=paths,
            input_path=None,
            pdf_bytes=fetch_result.pdf_buffer,
            copy_to_library=True,
        )
        if pdf_materialization.outcome == "conflict":
            remote_warning = _record_remote_metadata_update(
                metadata_dir=metadata_dir,
                doi=doi,
                fetch_status="failed",
                has_fulltext=True,
                source=fetch_result.source,
                title=title,
                metadata=metadata,
                fetch_via=fetch_result.via,
                fetch_state="pdf_materialization_conflict",
                fetch_host=fetch_result.host,
                fetch_resume=fetch_result.resume,
                fetch_manual=fetch_result.manual,
                fetch_trace=fetch_result.trace,
                indexing_config=indexing_config,
            )
            return _append_remote_metadata_warning(
                _pdf_materialization_conflict_receipt(doi, pdf_materialization),
                remote_warning,
            )
        copied_pdf_path = pdf_materialization.copied_pdf_path

    persisted = persist_reviewed_library_document(
        review,
        paths=paths,
        doi=doi,
        title=title,
        source=fetch_result.source,
        publisher=publisher or publisher_name,
        fetch_outcome=fetch_result.outcome,
        extra_frontmatter=extra_frontmatter or None,
        authors=authors,
        year=year,
        journal=journal,
        asset_hints=fetch_result.asset_hints,
        asset_mode=config.extract.assets.mode,
        asset_limits=_asset_limits_from_config(config),
        copied_pdf_path=copied_pdf_path,
        pdf_materialization=pdf_materialization,
        index_warning_message=(
            "Search index refresh failed — canonical markdown was saved to papers/ only. "
            "Error: {index_error}"
        ),
        indexing_config=indexing_config,
    )
    fetch_status = "partial_success" if persisted.index_warning_added or not persisted.qa_passed else "fulltext"
    remote_warning = _record_remote_metadata_update(
        metadata_dir=metadata_dir,
        doi=doi,
        fetch_status=fetch_status,
        has_fulltext=True,
        source=fetch_result.source,
        title=title,
        metadata=metadata,
        fetch_via=fetch_result.via,
        fetch_state=fetch_result.state,
        fetch_host=fetch_result.host,
        fetch_resume=fetch_result.resume,
        fetch_manual=fetch_result.manual,
        fetch_trace=fetch_result.trace,
        indexing_config=indexing_config,
    )

    result = (
        "## Paper Extracted with Partial Success\n\n"
        if fetch_status == "partial_success"
        else "## Paper Extracted Successfully\n\n"
    )
    result += f"- **DOI:** {doi}\n"
    result += f"- **Paper ID:** {persisted.summary.safe_doi}\n"
    result += f"- **Safe DOI:** {persisted.summary.safe_doi}\n"
    result += f"- **URI:** {persisted.summary.uri}\n"
    result += f"- **File:** {persisted.summary.file_path}\n"
    result += f"- **Words:** {persisted.summary.word_count:,}\n"
    result += f"- **Characters:** {persisted.summary.char_count:,}\n"
    result += f"- **Source:** {fetch_result.source}\n"
    if fetch_result.via:
        result += f"- **Via:** {fetch_result.via}\n"
    browser_capture = _fetch_browser_capture(fetch_result)
    if browser_capture:
        result += f"- **Browser Capture Source:** {browser_capture.get('source', '')}\n"
        if browser_capture.get("url"):
            result += f"- **Browser Capture URL:** {browser_capture.get('url', '')}\n"
        if browser_capture.get("content_type"):
            result += f"- **Browser Capture Content Type:** {browser_capture.get('content_type', '')}\n"
        if browser_capture.get("bytes"):
            result += f"- **Browser Capture Bytes:** {browser_capture.get('bytes', '')}\n"
    result += f"- **Outcome:** {fetch_result.outcome}\n"
    if fetch_result.state:
        result += f"- **State:** {fetch_result.state}\n"
    result += f"- **Fetch Status:** {fetch_status}\n"
    result += "- **Has Fulltext:** true\n"
    result += f"- **QA Status:** {'passed' if persisted.qa_passed else 'failed'}\n"
    result += f"- **Index Status:** {persisted.summary.index_status}\n"
    if persisted.artifact.parser_used:
        result += f"- **Parser Used:** {persisted.artifact.parser_used}\n"
    if persisted.asset_manifest_path:
        result += f"- **Assets Manifest:** {persisted.asset_manifest_path}\n"
    if persisted.summary.section_headings:
        result += "\n### Sections\n" + "\n".join(f"- {heading}" for heading in persisted.summary.section_headings)
    if persisted.warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {warning}" for warning in persisted.warnings)
    if persisted.debug:
        result += "\n\n### Parser Debug\n" + "\n".join(f"- {entry}" for entry in persisted.debug)
    return _append_remote_metadata_warning(result, remote_warning)


async def read_saved_paper(
    doi: Annotated[str | None, Field(description="Paper DOI. Provide this, safe_doi, or uri.")] = None,
    safe_doi: Annotated[
        str | None,
        Field(description="Opaque GRaDOS paper ID such as `10_1234_demo__51facb5bc98d`. Provide this, doi, or uri."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo__51facb5bc98d`."),
    ] = None,
    start_paragraph: Annotated[
        int,
        Field(ge=0, description="Zero-based paragraph offset for manual windowing."),
    ] = 0,
    max_paragraphs: Annotated[
        int,
        Field(ge=1, le=100, description="Paragraphs to return in this window."),
    ] = 20,
    section_query: Annotated[
        str | None,
        Field(description="Optional section name or substring to jump near before windowing."),
    ] = None,
    include_front_matter: Annotated[
        bool,
        Field(description="Include YAML front matter when reading from canonical markdown."),
    ] = False,
    include_asset_refs: Annotated[
        bool,
        Field(description="Append compact asset references found in this paragraph window; images are not inlined."),
    ] = True,
) -> str:
    """Read a previously saved paper with paragraph windowing."""
    from grados.storage.papers import read_paper

    selector_error = missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return selector_error

    paths, _ = get_paths_and_config()
    result = read_paper(
        papers_dir=paths.papers,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
        start_paragraph=start_paragraph,
        max_paragraphs=max_paragraphs,
        section_query=section_query,
        include_front_matter=include_front_matter,
    )

    if not result:
        return f"Paper not found. doi={doi}, safe_doi={safe_doi}, uri={uri}"

    header = f"## Reading: {result.doi}\n\n"
    header += f"Paragraphs {result.start_paragraph + 1}–{result.start_paragraph + result.paragraph_count}"
    header += f" of {result.total_paragraphs}"
    if result.truncated:
        header += " (truncated — increase max_paragraphs or use start_paragraph to continue)"
    header += "\n\n---\n\n"

    footer = ""
    if result.section_headings:
        footer = "\n\n---\n### Available Sections\n" + "\n".join(f"- {heading}" for heading in result.section_headings)

    asset_block = ""
    if include_asset_refs and result.assets_manifest_path:
        from grados.storage.assets import load_asset_manifest, matching_asset_refs_for_text

        manifest = load_asset_manifest(paths.papers, result.assets_manifest_path)
        refs = matching_asset_refs_for_text(manifest, result.text)
        if refs:
            asset_lines = [
                f"- `{ref['asset_id']}` ({ref['kind']}, page {ref['page'] or 'unknown'}): "
                f"{ref['caption'] or ref['uri']}"
                for ref in refs
            ]
            asset_block = "\n\n---\n### Asset References\n" + "\n".join(asset_lines)

    return header + result.text + asset_block + footer


async def get_saved_paper_structure(
    doi: Annotated[str | None, Field(description="Paper DOI. Provide this, safe_doi, or uri.")] = None,
    safe_doi: Annotated[
        str | None,
        Field(description="Opaque GRaDOS paper ID such as `10_1234_demo__51facb5bc98d`. Provide this, doi, or uri."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo__51facb5bc98d`."),
    ] = None,
) -> dict[str, object]:
    """Return a compact structure card for a saved paper."""
    from grados.storage.papers import get_paper_structure

    selector_error = missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return {"found": False, "message": selector_error}

    paths, _ = get_paths_and_config()
    structure = get_paper_structure(
        papers_dir=paths.papers,
        doi=doi,
        safe_doi=safe_doi,
        uri=uri,
    )
    if not structure:
        return {
            "found": False,
            "message": f"Paper not found. doi={doi}, safe_doi={safe_doi}, uri={uri}",
        }

    payload = asdict(structure)
    payload["found"] = True
    return payload


def _parse_handoff_timestamp(value: object) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return None


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _resolve_codex_watch_dir(raw_watch_dir: str) -> Path:
    value = raw_watch_dir.strip() or _CODEX_HANDOFF_DEFAULT_WATCH_DIR
    return Path(value).expanduser()


def _is_default_codex_watch_dir(raw_watch_dir: str) -> bool:
    return (raw_watch_dir.strip() or _CODEX_HANDOFF_DEFAULT_WATCH_DIR) == _CODEX_HANDOFF_DEFAULT_WATCH_DIR


def _validate_codex_watch_dir(watch_dir: Path, raw_watch_dir: str) -> tuple[bool, str, str]:
    if _is_default_codex_watch_dir(raw_watch_dir) and sys.platform != "darwin":
        return (
            False,
            "config_required",
            "Default Codex handoff watch dir is macOS Chrome semantics; "
            "configure extract.codex_handoff.download_watch_dir.",
        )
    if not watch_dir.exists():
        reason = "config_required" if _is_default_codex_watch_dir(raw_watch_dir) else "watch_dir_missing"
        return False, reason, f"Codex handoff watch dir does not exist: {watch_dir}"
    if not watch_dir.is_dir():
        return False, "watch_dir_not_directory", f"Codex handoff watch dir is not a directory: {watch_dir}"
    if not os.access(watch_dir, os.R_OK | os.X_OK):
        return False, "permission_error", f"Codex handoff watch dir is not readable: {watch_dir}"
    return True, "", ""


def _load_pending_codex_handoff(paths: object, doi: str) -> tuple[object | None, dict[str, str], str]:
    from grados.storage.remote_metadata import get_remote_metadata_by_doi

    metadata_dirs = [_remote_metadata_dir(paths)]
    legacy_dir = getattr(paths, "database_chroma", None)
    if legacy_dir is not None and legacy_dir != metadata_dirs[0]:
        metadata_dirs.append(legacy_dir)

    record = None
    for metadata_dir in metadata_dirs:
        record = get_remote_metadata_by_doi(metadata_dir, doi)
        if record is not None:
            break
    if record is None:
        return None, {}, "no_pending_handoff"

    fetch_via = str(getattr(record, "fetch_via", "") or "")
    fetch_status = str(getattr(record, "fetch_status", "") or "")
    fetch_state = str(getattr(record, "fetch_state", "") or "")
    fetch_manual = bool(getattr(record, "fetch_manual", False))
    if fetch_via != "codex" or not fetch_manual or "host_action_required" not in {fetch_status, fetch_state}:
        return record, {}, "no_pending_handoff"

    raw_resume = str(getattr(record, "fetch_resume", "") or "")
    if not raw_resume:
        return record, {}, "missing_fetch_resume"
    try:
        loaded = json.loads(raw_resume)
    except json.JSONDecodeError:
        return record, {}, "invalid_fetch_resume"
    if not isinstance(loaded, dict):
        return record, {}, "invalid_fetch_resume"
    resume = {str(key): str(value) for key, value in loaded.items() if str(value)}
    return record, resume, ""


def _record_codex_ingest_receipt(
    paths: object,
    *,
    doi: str,
    status: str,
    failure_reason: str,
    message: str,
    context: dict[str, object],
) -> dict[str, object]:
    from grados.research_state import manage_failure_cases, save_research_artifact

    if hasattr(paths, "ensure_directories"):
        paths.ensure_directories()

    failure_type = "parse" if failure_reason == "parse_failed" else "fetch"
    failure_case = manage_failure_cases(
        getattr(paths, "database_state"),
        mode="record",
        failure_type=failure_type,
        doi=doi,
        source="codex",
        error_message=message or failure_reason,
        context={"status": status, "failure_reason": failure_reason, **context},
    )
    artifact = save_research_artifact(
        getattr(paths, "database_state"),
        kind="extraction_receipt",
        title=f"Codex PDF handoff {status}: {doi}",
        source_doi=doi,
        content={"status": status, "failure_reason": failure_reason, "message": message, **context},
        metadata={"source": "codex", "failure_reason": failure_reason},
    )
    return {"failure_case": failure_case, "research_artifact": artifact}


def _codex_ingest_failure(
    paths: object,
    *,
    doi: str,
    failure_reason: str,
    message: str,
    watch_dir: Path | None = None,
    candidates: list[dict[str, object]] | None = None,
    rejected_candidates: list[dict[str, object]] | None = None,
    next_action: str = _CODEX_INGEST_RECOVERY_NEXT_ACTION,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "watch_dir": str(watch_dir) if watch_dir is not None else "",
        "candidates": candidates or [],
        "rejected_candidates": rejected_candidates or [],
        "next_action": next_action,
    }
    if extra:
        context.update(extra)
    recorded = _record_codex_ingest_receipt(
        paths,
        doi=doi,
        status="failed",
        failure_reason=failure_reason,
        message=message,
        context=context,
    )
    return {
        "status": "failed",
        "doi": doi,
        "failure_reason": failure_reason,
        "message": message,
        **context,
        **recorded,
    }


def _pdf_materialization_conflict_receipt(doi: str, materialization: object) -> str:
    next_action = "review_pdf_conflict_then_force_refresh_or_parse_as_new_version"
    lines = [
        "## PDF Materialization Conflict",
        "",
        f"- **DOI:** {doi}",
        "- **Status:** conflict",
        f"- **Existing Canonical PDF:** {getattr(materialization, 'conflict_existing_path', '')}",
        f"- **Existing Canonical PDF Hash:** {getattr(materialization, 'conflict_existing_hash', '')}",
        "- **Candidate PDF:** "
        f"{getattr(materialization, 'conflict_candidate_path', '') or getattr(materialization, 'input_pdf_path', '')}",
        "- **Candidate PDF Hash:** "
        f"{getattr(materialization, 'conflict_candidate_hash', '') or getattr(materialization, 'input_pdf_hash', '')}",
        f"- **Next Action:** {next_action}",
        "",
        "GRaDOS did not overwrite the existing canonical PDF. Review both files, then force-refresh intentionally "
        "or parse the candidate as a new version/identifier.",
    ]
    warnings = list(getattr(materialization, "warnings", []) or [])
    if warnings:
        lines.extend(["", "### Warnings", *[f"- {warning}" for warning in warnings]])
    return "\n".join(lines)


def _candidate_payload(path: Path, file_stat: os.stat_result) -> dict[str, object]:
    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": file_stat.st_size,
        "mtime": _format_timestamp(file_stat.st_mtime),
    }


def _identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        int(file_stat.st_mode),
        int(file_stat.st_size),
        int(file_stat.st_mtime_ns),
    )


def _iter_codex_watch_dir(watch_dir: Path, *, recursive: bool) -> tuple[list[Path], str]:
    try:
        iterator = watch_dir.rglob("*") if recursive else watch_dir.iterdir()
        return list(iterator), ""
    except OSError as exc:
        return [], f"{exc.__class__.__name__}: {exc}"


def _wait_for_stable_candidate(
    path: Path,
    *,
    settle_seconds: float,
    settle_max_wait_seconds: float,
) -> tuple[os.stat_result | None, str]:
    try:
        previous = path.stat(follow_symlinks=False)
    except OSError as exc:
        return None, f"permission_error:{exc.__class__.__name__}: {exc}"
    if settle_seconds <= 0:
        return previous, ""

    deadline = time.monotonic() + settle_max_wait_seconds
    while True:
        if time.time() - previous.st_mtime >= settle_seconds:
            return previous, ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, "unstable_download"
        time.sleep(min(settle_seconds, remaining))
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            return None, f"permission_error:{exc.__class__.__name__}: {exc}"
        if _identity(current) != _identity(previous):
            previous = current


def _read_candidate_pdf_hash(path: Path, *, max_bytes: int) -> tuple[str, bytes, os.stat_result | None, str]:
    try:
        before = path.stat(follow_symlinks=False)
        if path.is_symlink():
            return "", b"", before, "symlink_rejected"
        if not stat.S_ISREG(before.st_mode):
            return "", b"", before, "non_regular"
        try:
            ensure_byte_limit(before.st_size, max_bytes=max_bytes, label=f"Codex handoff PDF {path}")
        except SizeLimitError as exc:
            return "", b"", before, f"too_large:{exc}"
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        return "", b"", None, f"permission_error:{exc.__class__.__name__}: {exc}"
    try:
        ensure_byte_limit(len(data), max_bytes=max_bytes, label=f"Codex handoff PDF {path}")
    except SizeLimitError as exc:
        return "", b"", after, f"too_large:{exc}"
    if _identity(before) != _identity(after):
        return "", b"", after, "hash_changed"
    if not stat.S_ISREG(after.st_mode):
        return "", b"", after, "non_regular"
    if data[:5] != b"%PDF-":
        return "", b"", after, "not_pdf"
    return hashlib.sha256(data).hexdigest(), data, after, ""


def _explicit_codex_download_candidate(
    downloaded_file_path: str,
    *,
    settle_seconds: float,
    settle_max_wait_seconds: float,
    max_pdf_bytes: int,
) -> tuple[dict[str, object] | None, list[dict[str, object]], str, str]:
    path = Path(downloaded_file_path).expanduser()
    rejected_path = str(path)
    if not path.is_absolute():
        return None, [{"path": rejected_path, "name": path.name, "reason": "path_not_absolute", "detail": ""}], "", (
            "downloaded_file_path must be an absolute path."
        )
    if not path.exists():
        return None, [{"path": rejected_path, "name": path.name, "reason": "file_not_found", "detail": ""}], "", (
            f"downloaded_file_path does not exist: {path}"
        )
    stable_stat, stable_error = _wait_for_stable_candidate(
        path,
        settle_seconds=settle_seconds,
        settle_max_wait_seconds=settle_max_wait_seconds,
    )
    if stable_error or stable_stat is None:
        reason, _, detail = stable_error.partition(":")
        return None, [
            {"path": rejected_path, "name": path.name, "reason": reason or "unstable_download", "detail": detail}
        ], reason, "downloaded_file_path did not settle before validation."
    source_hash, _, after_read_stat, hash_error = _read_candidate_pdf_hash(path, max_bytes=max_pdf_bytes)
    if hash_error or after_read_stat is None:
        reason, _, detail = hash_error.partition(":")
        return None, [
            {
                **_candidate_payload(path, stable_stat),
                "reason": reason or "hash_changed",
                "detail": detail,
            }
        ], reason, "downloaded_file_path failed PDF validation."
    return {
        **_candidate_payload(path, after_read_stat),
        "source_pdf_hash": source_hash,
        "candidate_source": "downloaded_file_path",
    }, [], "", ""


def _codex_scan_excluded_paths(paths: GRaDOSPaths, *, doi: str) -> set[Path]:
    from grados.storage.papers import list_saved_papers

    excluded = {paths.downloads / f"{safe_doi_filename(doi)}.pdf"}
    try:
        for paper in list_saved_papers(paths.papers, chroma_dir=paths.database_chroma):
            if paper.safe_doi:
                excluded.add(paths.downloads / f"{paper.safe_doi}.pdf")
    except Exception:
        pass
    return excluded


def _scan_codex_handoff_candidates(
    *,
    watch_dir: Path,
    raw_watch_dir: str,
    recursive: bool,
    file_name_hint: str,
    issued_at_ts: float | None,
    downloaded_at_ts: float | None,
    max_age_seconds: float,
    settle_seconds: float,
    settle_max_wait_seconds: float,
    max_pdf_bytes: int,
    excluded_paths: set[Path] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
    ok, reason, message = _validate_codex_watch_dir(watch_dir, raw_watch_dir)
    if not ok:
        return [], [], f"{reason}:{message}"

    paths, iter_error = _iter_codex_watch_dir(watch_dir, recursive=recursive)
    if iter_error:
        return [], [], f"permission_error:{iter_error}"

    now = time.time()
    recent_cutoff = now - max_age_seconds
    basename_hint = Path(file_name_hint).name if file_name_hint else ""
    excluded_resolved = {path.resolve() for path in excluded_paths or set()}
    candidates: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []

    for path in paths:
        reason = ""
        detail = ""
        try:
            file_stat = path.stat(follow_symlinks=False)
        except OSError as exc:
            rejected.append({"path": str(path), "name": path.name, "reason": "permission_error", "detail": str(exc)})
            continue

        if path.resolve() in excluded_resolved:
            reason = "canonical_pdf_excluded"
        elif path.name.startswith("."):
            reason = "temporary_file"
        elif path.name.endswith(".crdownload"):
            reason = "temporary_file"
        elif path.is_symlink():
            reason = "symlink_rejected"
        elif not stat.S_ISREG(file_stat.st_mode):
            reason = "non_regular"
        elif path.suffix.lower() != ".pdf":
            reason = "not_pdf"
        elif basename_hint and path.name != basename_hint:
            reason = "file_name_hint_mismatch"
        elif file_stat.st_mtime < recent_cutoff:
            reason = "too_old"
        elif issued_at_ts is not None and file_stat.st_mtime < issued_at_ts:
            reason = "too_old"
        elif downloaded_at_ts is not None and abs(file_stat.st_mtime - downloaded_at_ts) > max(max_age_seconds, 60.0):
            reason = "downloaded_at_mismatch"

        if reason:
            rejected.append({**_candidate_payload(path, file_stat), "reason": reason, "detail": detail})
            continue

        try:
            ensure_byte_limit(
                file_stat.st_size,
                max_bytes=max_pdf_bytes,
                label=f"Codex handoff PDF {path}",
            )
        except SizeLimitError as exc:
            rejected.append({**_candidate_payload(path, file_stat), "reason": "too_large", "detail": str(exc)})
            continue

        stable_stat, stable_error = _wait_for_stable_candidate(
            path,
            settle_seconds=settle_seconds,
            settle_max_wait_seconds=settle_max_wait_seconds,
        )
        if stable_error or stable_stat is None:
            stable_reason, _, stable_detail = stable_error.partition(":")
            rejected.append({
                **_candidate_payload(path, file_stat),
                "reason": stable_reason or "unstable_download",
                "detail": stable_detail,
            })
            continue

        source_hash, _, after_read_stat, hash_error = _read_candidate_pdf_hash(path, max_bytes=max_pdf_bytes)
        if hash_error or after_read_stat is None:
            hash_reason, _, hash_detail = hash_error.partition(":")
            rejected.append({
                **_candidate_payload(path, stable_stat),
                "reason": hash_reason or "hash_changed",
                "detail": hash_detail,
            })
            continue

        candidates.append({
            **_candidate_payload(path, after_read_stat),
            "source_pdf_hash": source_hash,
            "candidate_source": str(watch_dir),
        })

    return candidates, rejected, ""


def _dominant_codex_failure_reason(rejected_candidates: Sequence[Mapping[str, object]]) -> str:
    priority = [
        "hash_changed",
        "unstable_download",
        "permission_error",
        "symlink_rejected",
        "non_regular",
        "too_large",
        "not_pdf",
        "too_old",
        "temporary_file",
    ]
    reasons = {str(candidate.get("reason") or "") for candidate in rejected_candidates}
    for reason in priority:
        if reason in reasons:
            return reason
    return "no_candidate"


async def ingest_codex_downloaded_pdf(
    doi: Annotated[str, Field(min_length=1, description="DOI for the pending Codex Chrome Extension handoff.")],
    expected_title: Annotated[
        str | None,
        Field(description="Optional title used for QA validation only."),
    ] = None,
    file_name_hint: Annotated[
        str | None,
        Field(description="Optional downloaded filename hint. It narrows candidates but does not bypass validation."),
    ] = None,
    downloaded_at: Annotated[
        str | None,
        Field(description="Optional ISO timestamp or epoch seconds from the host-agent download event."),
    ] = None,
    downloaded_file_path: Annotated[
        str | None,
        Field(description="Optional absolute path to the PDF already downloaded by the host agent."),
    ] = None,
) -> dict[str, object]:
    """Ingest the single PDF produced by a pending Codex Chrome Extension handoff."""
    from grados.storage.papers import load_paper_record

    paths, config = get_paths_and_config()
    if hasattr(paths, "ensure_directories"):
        paths.ensure_directories()

    existing_record = load_paper_record(paths.papers, doi=doi)
    if existing_record is not None and existing_record.content_markdown.strip():
        return {
            "status": "success",
            "outcome": "already_saved",
            "doi": doi,
            "safe_doi": existing_record.safe_doi,
            "uri": existing_record.canonical_uri,
            "file": str((paths.papers / f"{existing_record.safe_doi}.md").resolve()),
            "message": "Canonical paper is already saved; no Codex handoff ingest is needed.",
            "next_action": "read_saved_paper_or_get_saved_paper_structure",
        }

    explicit_path = (downloaded_file_path or "").strip()
    resume: dict[str, str] = {}
    pending_error = ""
    if not explicit_path:
        _, resume, pending_error = _load_pending_codex_handoff(paths, doi)
    if pending_error:
        return _codex_ingest_failure(
            paths,
            doi=doi,
            failure_reason=pending_error,
            message="No pending Codex Chrome Extension handoff was found for this DOI.",
            extra={
                "next_action": _CODEX_INGEST_RECOVERY_NEXT_ACTION,
                "recovery_hint": (
                    "If Chrome already downloaded the PDF, pass downloaded_file_path here or call "
                    "parse_pdf_file(file_path=..., doi=..., copy_to_library=true, acquisition_via=\"codex\")."
                ),
            },
        )

    codex_config = config.extract.codex_handoff
    raw_watch_dir = codex_config.download_watch_dir
    watch_dir = _resolve_codex_watch_dir(raw_watch_dir)
    issued_at_ts = _parse_handoff_timestamp(resume.get("issued_at"))
    downloaded_at_ts = _parse_handoff_timestamp(downloaded_at)
    max_age_seconds = float(codex_config.download_max_age_seconds)
    settle_seconds = float(codex_config.download_settle_seconds)
    settle_max_wait_seconds = float(codex_config.download_settle_max_wait_seconds)
    excluded_candidate_paths = _codex_scan_excluded_paths(paths, doi=doi)

    if explicit_path:
        candidate, rejected, explicit_reason, explicit_message = _explicit_codex_download_candidate(
            explicit_path,
            settle_seconds=settle_seconds,
            settle_max_wait_seconds=settle_max_wait_seconds,
            max_pdf_bytes=int(config.extract.security.max_local_pdf_bytes),
        )
        if candidate is None:
            return _codex_ingest_failure(
                paths,
                doi=doi,
                failure_reason=explicit_reason or _dominant_codex_failure_reason(rejected),
                message=explicit_message or "downloaded_file_path failed validation.",
                watch_dir=watch_dir,
                rejected_candidates=rejected,
                extra={
                    "downloaded_file_path": explicit_path,
                    "next_action": "call_parse_pdf_file_with_known_pdf_path_after_fixing_file",
                },
            )
        candidates = [candidate]
        rejected = []
        scan_error = ""
    else:
        candidates, rejected, scan_error = _scan_codex_handoff_candidates(
            watch_dir=watch_dir,
            raw_watch_dir=raw_watch_dir,
            recursive=bool(codex_config.download_scan_recursive),
            file_name_hint=file_name_hint or "",
            issued_at_ts=issued_at_ts,
            downloaded_at_ts=downloaded_at_ts,
            max_age_seconds=max_age_seconds,
            settle_seconds=settle_seconds,
            settle_max_wait_seconds=settle_max_wait_seconds,
            max_pdf_bytes=int(config.extract.security.max_local_pdf_bytes),
            excluded_paths=excluded_candidate_paths,
        )
        if scan_error:
            reason, _, message = scan_error.partition(":")
            return _codex_ingest_failure(
                paths,
                doi=doi,
                failure_reason=reason or "watch_dir_error",
                message=message or scan_error,
                watch_dir=watch_dir,
                rejected_candidates=rejected,
            )

        fallback_candidates: list[dict[str, object]] = []
        fallback_rejected: list[dict[str, object]] = []
        if not candidates and paths.downloads.resolve() != watch_dir.resolve():
            fallback_candidates, fallback_rejected, _ = _scan_codex_handoff_candidates(
                watch_dir=paths.downloads,
                raw_watch_dir=str(paths.downloads),
                recursive=False,
                file_name_hint=file_name_hint or "",
                issued_at_ts=issued_at_ts,
                downloaded_at_ts=downloaded_at_ts,
                max_age_seconds=max_age_seconds,
                settle_seconds=settle_seconds,
                settle_max_wait_seconds=settle_max_wait_seconds,
                max_pdf_bytes=int(config.extract.security.max_local_pdf_bytes),
                excluded_paths=excluded_candidate_paths,
            )
            candidates = fallback_candidates
            rejected.extend(fallback_rejected)

    if not candidates:
        failure_reason = _dominant_codex_failure_reason(rejected)
        return _codex_ingest_failure(
            paths,
            doi=doi,
            failure_reason=failure_reason,
            message="No acceptable Codex handoff PDF candidate was found.",
            watch_dir=watch_dir,
            rejected_candidates=rejected,
            extra={
                "next_action": _CODEX_INGEST_RECOVERY_NEXT_ACTION,
                "download_watch_dir_semantics": "scan_only",
                "recovery_hint": (
                    "download_watch_dir only tells GRaDOS where to scan. If Chrome saved the PDF elsewhere, pass "
                    "downloaded_file_path or call parse_pdf_file(file_path=..., doi=..., copy_to_library=true)."
                ),
            },
        )

    if len(candidates) > 1:
        token_input = json.dumps(
            {"doi": doi, "candidates": [candidate["path"] for candidate in candidates]},
            sort_keys=True,
            ensure_ascii=False,
        )
        disambiguation_token = hashlib.sha1(token_input.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        recorded = _record_codex_ingest_receipt(
            paths,
            doi=doi,
            status="needs_disambiguation",
            failure_reason="multiple_candidates",
            message="Multiple acceptable Codex handoff PDF candidates were found; no DOI guess was made.",
            context={
                "watch_dir": str(watch_dir),
                "candidates": candidates,
                "rejected_candidates": rejected,
                "disambiguation_token": disambiguation_token,
                "next_action": "call_ingest_codex_downloaded_pdf_with_file_name_hint_or_downloaded_file_path",
            },
        )
        return {
            "status": "needs_disambiguation",
            "doi": doi,
            "failure_reason": "multiple_candidates",
            "message": "Multiple acceptable Codex handoff PDF candidates were found; no DOI guess was made.",
            "watch_dir": str(watch_dir),
            "candidates": candidates,
            "rejected_candidates": rejected,
            "disambiguation_token": disambiguation_token,
            "next_action": "call_ingest_codex_downloaded_pdf_with_file_name_hint_or_downloaded_file_path",
            **recorded,
        }

    candidate = candidates[0]
    source_path = Path(str(candidate["path"]))
    source_hash = str(candidate["source_pdf_hash"])
    verify_hash, _, _, verify_error = _read_candidate_pdf_hash(
        source_path,
        max_bytes=int(config.extract.security.max_local_pdf_bytes),
    )
    if verify_error or verify_hash != source_hash:
        return _codex_ingest_failure(
            paths,
            doi=doi,
            failure_reason="hash_changed",
            message="Codex handoff PDF changed before parse/save.",
            watch_dir=watch_dir,
            candidates=[candidate],
            rejected_candidates=rejected,
            extra={"source_path": str(source_path), "source_pdf_hash": source_hash},
        )

    parse_receipt = await parse_pdf_file(
        file_path=str(source_path),
        expected_title=expected_title,
        doi=doi,
        copy_to_library=True,
        acquisition_via="codex",
    )
    if parse_receipt.startswith("## PDF Parse Accepted"):
        parse_outcome = _receipt_field(parse_receipt, "Outcome") or "parse_in_progress"
        return {
            "status": "in_progress",
            "outcome": parse_outcome,
            "doi": doi,
            "source_path": str(source_path),
            "source_pdf_hash": source_hash,
            "attempt_id": _receipt_field(parse_receipt, "Attempt ID"),
            "watch_dir": str(watch_dir),
            "candidate": candidate,
            "rejected_candidates": rejected,
            "parse_receipt": parse_receipt,
            "next_action": (
                "retry_ingest_codex_downloaded_pdf_with_same_downloaded_file_path"
                "_or_read_saved_paper_if_completed"
            ),
        }
    if parse_receipt.startswith("## Paper Already Saved"):
        completed_record = load_paper_record(paths.papers, doi=doi)
        return {
            "status": "success",
            "outcome": "background_completed" if completed_record is not None else "already_saved",
            "doi": doi,
            "safe_doi": completed_record.safe_doi if completed_record is not None else "",
            "uri": completed_record.canonical_uri if completed_record is not None else "",
            "source_path": str(source_path),
            "source_pdf_hash": source_hash,
            "watch_dir": str(watch_dir),
            "candidate": candidate,
            "rejected_candidates": rejected,
            "parse_receipt": parse_receipt,
            "next_action": "read_saved_paper_or_get_saved_paper_structure",
        }
    if not parse_receipt.startswith("## PDF Parsed & Saved"):
        completed_record = load_paper_record(paths.papers, doi=doi)
        if completed_record is not None and completed_record.content_markdown.strip():
            return {
                "status": "success",
                "outcome": "background_completed",
                "doi": doi,
                "safe_doi": completed_record.safe_doi,
                "uri": completed_record.canonical_uri,
                "file": str((paths.papers / f"{completed_record.safe_doi}.md").resolve()),
                "source_path": str(source_path),
                "source_pdf_hash": source_hash,
                "watch_dir": str(watch_dir),
                "candidate": candidate,
                "rejected_candidates": rejected,
                "parse_receipt": parse_receipt,
                "next_action": "read_saved_paper_or_get_saved_paper_structure",
            }
        if parse_receipt.startswith("## PDF Materialization Conflict"):
            recorded = _record_codex_ingest_receipt(
                paths,
                doi=doi,
                status="conflict",
                failure_reason="pdf_materialization_conflict",
                message="Codex handoff PDF conflicts with the existing canonical library PDF.",
                context={
                    "watch_dir": str(watch_dir),
                    "candidates": [candidate],
                    "rejected_candidates": rejected,
                    "source_path": str(source_path),
                    "source_pdf_hash": source_hash,
                    "parse_receipt": parse_receipt,
                    "next_action": "review_pdf_conflict_then_force_refresh_or_parse_as_new_version",
                },
            )
            return {
                "status": "conflict",
                "outcome": "pdf_materialization_conflict",
                "doi": doi,
                "source_path": str(source_path),
                "source_pdf_hash": source_hash,
                "watch_dir": str(watch_dir),
                "candidate": candidate,
                "rejected_candidates": rejected,
                "parse_receipt": parse_receipt,
                "next_action": "review_pdf_conflict_then_force_refresh_or_parse_as_new_version",
                **recorded,
            }
        return _codex_ingest_failure(
            paths,
            doi=doi,
            failure_reason="parse_failed",
            message="Codex handoff PDF candidate was found but parsing or canonical save failed.",
            watch_dir=watch_dir,
            candidates=[candidate],
            rejected_candidates=rejected,
            extra={
                "source_path": str(source_path),
                "source_pdf_hash": source_hash,
                "parse_receipt": parse_receipt,
            },
        )

    archived_pdf_path = paths.downloads / f"{safe_doi_filename(doi)}.pdf"
    return {
        "status": "success",
        "outcome": "saved",
        "doi": doi,
        "source_path": str(source_path),
        "archived_pdf_path": str(archived_pdf_path),
        "source_pdf_hash": source_hash,
        "watch_dir": str(watch_dir),
        "candidate": candidate,
        "rejected_candidates": rejected,
        "parse_receipt": parse_receipt,
        "fetch_status": "fulltext" if "Fetch Status:** fulltext" in parse_receipt else "partial_success",
        "next_action": "read_saved_paper_or_get_saved_paper_structure",
    }


async def read_paper_asset(
    doi: Annotated[str | None, Field(description="Paper DOI. Provide this, safe_doi, uri, or asset_uri.")] = None,
    safe_doi: Annotated[
        str | None,
        Field(description="Opaque GRaDOS paper ID such as `10_1234_demo__51facb5bc98d`."),
    ] = None,
    uri: Annotated[
        str | None,
        Field(description="Canonical paper URI such as `grados://papers/10_1234_demo__51facb5bc98d`."),
    ] = None,
    asset_id: Annotated[
        str | None,
        Field(description="Asset id from get_saved_paper_structure/read_saved_paper, such as `fig_001`."),
    ] = None,
    asset_uri: Annotated[
        str | None,
        Field(description="Asset URI such as `grados://papers/{safe_doi}/assets/{asset_id}`."),
    ] = None,
    kind: Annotated[
        str | None,
        Field(description="Optional list-mode kind filter: figure, table, formula, page, debug, or object."),
    ] = None,
    role: Annotated[
        str | None,
        Field(description="Optional list-mode role filter: content, page, debug, source, or supporting."),
    ] = None,
    offset: Annotated[int, Field(ge=0, description="List-mode offset.")] = 0,
    limit: Annotated[int, Field(ge=1, le=100, description="List-mode result limit.")] = 20,
    include_pages: Annotated[bool, Field(description="Include page image assets in list mode.")] = False,
    include_debug: Annotated[bool, Field(description="Include source/debug assets in list mode.")] = False,
    include_image: Annotated[
        bool,
        Field(
            description=(
                "Return image content inline when reading one image asset and it is under the inline byte limit."
            )
        ),
    ] = False,
) -> object:
    """List or read assets for a saved paper."""
    from grados.storage.assets import (
        is_image_asset,
        load_asset_manifest,
        manifest_assets,
        parse_asset_uri,
        resolve_asset_path,
        resolve_manifest_relative_path,
    )
    from grados.storage.papers import load_paper_record

    parsed_asset = parse_asset_uri(asset_uri or "") if asset_uri else None
    if parsed_asset:
        parsed_safe_doi, parsed_asset_id = parsed_asset
        if safe_doi and safe_doi != parsed_safe_doi:
            return {
                "found": False,
                "message": f"asset_uri safe_doi does not match safe_doi parameter: {parsed_safe_doi} != {safe_doi}",
            }
        safe_doi = safe_doi or parsed_safe_doi
        asset_id = asset_id or parsed_asset_id
    elif asset_uri:
        return {"found": False, "message": f"Invalid asset_uri: {asset_uri}"}

    selector_error = missing_paper_selector_message(doi=doi, safe_doi=safe_doi, uri=uri)
    if selector_error:
        return {"found": False, "message": selector_error}

    paths, config = get_paths_and_config()
    record = load_paper_record(paths.papers, doi=doi, safe_doi=safe_doi, uri=uri)
    if record is None:
        return {"found": False, "message": f"Paper not found. doi={doi}, safe_doi={safe_doi}, uri={uri}"}
    if not record.assets_manifest_path:
        return {
            "found": False,
            "doi": record.doi,
            "safe_doi": record.safe_doi,
            "message": "Paper has no asset manifest. Re-extract or re-import the PDF to generate assets.",
        }

    manifest = load_asset_manifest(paths.papers, record.assets_manifest_path)
    assets = manifest_assets(manifest)
    if manifest is None or not assets:
        return {
            "found": False,
            "doi": record.doi,
            "safe_doi": record.safe_doi,
            "manifest_path": record.assets_manifest_path,
            "message": "Asset manifest was missing or empty.",
        }

    if not asset_id:
        filtered = _filter_asset_list(
            assets,
            kind=kind,
            role=role,
            include_pages=include_pages,
            include_debug=include_debug,
        )
        selected = filtered[offset : offset + limit]
        return {
            "found": True,
            "mode": "list",
            "doi": record.doi,
            "safe_doi": record.safe_doi,
            "manifest_path": record.assets_manifest_path,
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "truncated": offset + limit < len(filtered),
            "assets": [_compact_server_asset(asset) for asset in selected],
        }

    asset = next((item for item in assets if str(item.get("asset_id", "")) == asset_id), None)
    if asset is None:
        return {
            "found": False,
            "doi": record.doi,
            "safe_doi": record.safe_doi,
            "manifest_path": record.assets_manifest_path,
            "message": f"Asset not found: {asset_id}",
        }

    primary_path = resolve_asset_path(paths.papers, record.assets_manifest_path, asset)
    payload: dict[str, Any] = {
        "found": True,
        "mode": "read",
        "doi": record.doi,
        "safe_doi": record.safe_doi,
        "manifest_path": record.assets_manifest_path,
        "asset": dict(asset),
        "absolute_path": str(primary_path) if primary_path is not None else "",
        "table_html": _read_manifest_text(
            paths.papers,
            record.assets_manifest_path,
            str(asset.get("html_path") or ""),
        ),
        "table_csv": _read_manifest_text(
            paths.papers,
            record.assets_manifest_path,
            str(asset.get("csv_path") or ""),
        ),
        "table_markdown": _read_manifest_text(
            paths.papers,
            record.assets_manifest_path,
            str(asset.get("markdown_path") or ""),
        ),
    }

    if include_image:
        if primary_path is None or not is_image_asset(asset):
            payload["image_warning"] = "Asset has no readable image file."
        else:
            size = primary_path.stat().st_size
            max_inline = config.extract.assets.max_asset_inline_bytes
            if size > max_inline:
                payload["image_warning"] = (
                    f"Image is larger than max_asset_inline_bytes ({size} > {max_inline}); returning path only."
                )
            else:
                from fastmcp.tools import ToolResult
                from fastmcp.utilities.types import Image
                from mcp.types import TextContent

                return ToolResult(
                    content=[
                        TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2)),
                        Image(path=str(primary_path)),
                    ],
                    structured_content=payload,
                )

    # Touch structured paths through the resolver so invalid manifest values never leak arbitrary files.
    for path_key in ("html_path", "csv_path", "markdown_path"):
        rel = str(asset.get(path_key) or "")
        if rel:
            resolved = resolve_manifest_relative_path(paths.papers, record.assets_manifest_path, rel)
            payload[f"absolute_{path_key}"] = str(resolved) if resolved else ""
    return payload


def _filter_asset_list(
    assets: list[dict[str, Any]],
    *,
    kind: str | None,
    role: str | None,
    include_pages: bool,
    include_debug: bool,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    kind_filter = (kind or "").strip().lower()
    role_filter = (role or "").strip().lower()
    for asset in assets:
        asset_kind = str(asset.get("kind") or "").lower()
        asset_role = str(asset.get("role") or "").lower()
        if kind_filter and asset_kind != kind_filter:
            continue
        if role_filter and asset_role != role_filter:
            continue
        if asset_kind == "page" and not include_pages:
            continue
        if (asset_kind == "debug" or asset_role in {"debug", "source"}) and not include_debug:
            continue
        filtered.append(asset)
    return filtered


def _compact_server_asset(asset: dict[str, Any]) -> dict[str, Any]:
    caption = str(asset.get("caption") or asset.get("text") or asset.get("latex") or "")
    return {
        "asset_id": asset.get("asset_id", ""),
        "kind": asset.get("kind", ""),
        "role": asset.get("role", ""),
        "uri": asset.get("uri", ""),
        "page": asset.get("page"),
        "caption": caption[:240],
        "relative_path": asset.get("relative_path", ""),
        "mime_type": asset.get("mime_type", ""),
        "bytes": asset.get("bytes", 0),
    }


def _read_manifest_text(papers_dir: Path, manifest_path: str, relative_path: str, *, max_chars: int = 20000) -> str:
    if not relative_path:
        return ""
    from grados.storage.assets import resolve_manifest_relative_path

    path = resolve_manifest_relative_path(papers_dir, manifest_path, relative_path)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


async def import_local_pdf_library(
    source_path: Annotated[
        str,
        Field(min_length=1, description="Local path to a PDF file or a directory containing PDFs."),
    ],
    recursive: Annotated[bool, Field(description="Recursively scan subdirectories for matching PDFs.")] = False,
    glob_pattern: Annotated[
        str,
        Field(min_length=1, description="Glob pattern used to discover PDFs inside the source directory."),
    ] = "*.pdf",
    copy_to_library: Annotated[
        bool,
        Field(description="Copy raw PDFs into the managed downloads archive before parsing."),
    ] = True,
) -> dict[str, object]:
    """Import a local PDF library into GRaDOS canonical storage."""
    from grados.importing import import_local_pdf_library as run_import

    paths, _ = get_paths_and_config()
    result = await run_import(
        source_path=Path(source_path),
        paths=paths,
        recursive=recursive,
        glob_pattern=glob_pattern,
        copy_to_library=copy_to_library,
    )

    item_limit = 25
    items = [
        {
            "source_path": item.source_path,
            "status": item.status,
            "doi": item.doi,
            "safe_doi": item.safe_doi,
            "title": item.title,
            "detail": item.detail,
            "copied_pdf_path": item.copied_pdf_path,
            "warnings": item.warnings,
            "debug": item.debug,
        }
        for item in result.items[:item_limit]
    ]
    return {
        "source_path": result.source_path,
        "scanned": result.scanned,
        "imported": result.imported,
        "skipped": result.skipped,
        "failed": result.failed,
        "warnings": result.warnings,
        "items": items,
        "truncated_items": max(0, len(result.items) - item_limit),
    }


async def plan_library_pdf_cleanup() -> dict[str, object]:
    """Return a dry-run report for duplicate noncanonical PDFs in downloads/."""
    paths, _ = get_paths_and_config()
    return plan_duplicate_library_pdf_cleanup(paths)


async def _parse_pdf_file_core(
    *,
    path: Path,
    pdf_buffer: bytes,
    pdf_hash: str,
    expected_title: str | None,
    doi: str | None,
    copy_to_library: bool,
    acquisition_via: str | None,
    paths: GRaDOSPaths,
    config: GRaDOSConfig,
) -> str:
    from grados.extract.parse import parse_pdf_with_diagnostics
    from grados.extract.qa import is_valid_paper_content

    artifact = await build_library_document_artifact(
        lambda: parse_pdf_with_diagnostics(
            pdf_buffer,
            filename=path.name,
            parse_order=config.extract.parsing.order,
            parse_enabled=config.extract.parsing.enabled,
            marker_timeout=config.extract.parsing.marker_timeout,
            mineru_api_key=config.api_keys.MINERU_API_KEY,
            mineru_model_version=config.extract.parsing.mineru_model_version,
            mineru_language=config.extract.parsing.mineru_language,
            mineru_timeout=config.extract.parsing.mineru_timeout,
            mineru_poll_interval=config.extract.parsing.mineru_poll_interval,
            mineru_enable_formula=config.extract.parsing.mineru_enable_formula,
            mineru_enable_table=config.extract.parsing.mineru_enable_table,
            mineru_is_ocr=config.extract.parsing.mineru_is_ocr,
            mineru_max_zip_bytes=config.extract.security.max_mineru_zip_bytes,
            mineru_max_full_md_bytes=config.extract.security.max_mineru_full_md_bytes,
            asset_mode=config.extract.assets.mode,
            docling_image_scale=config.extract.assets.docling_image_scale,
            max_asset_file_bytes=config.extract.assets.max_asset_file_bytes,
            max_asset_total_bytes=config.extract.assets.max_asset_total_bytes,
            max_asset_inline_bytes=config.extract.assets.max_asset_inline_bytes,
            max_asset_count=config.extract.assets.max_asset_count,
        )
    )
    if not artifact.markdown:
        warnings, parser_debug = merge_library_diagnostics(artifact)
        result = f"All parsers failed for: {path}"
        if warnings:
            result += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)
        if parser_debug:
            result += "\n\nParser debug:\n" + "\n".join(f"- {entry}" for entry in parser_debug)
        return result

    review = review_library_document(
        artifact,
        qa_validator=is_valid_paper_content,
        qa_min_characters=config.extract.qa.min_characters,
        qa_expected_title=expected_title,
        qa_warning_message="QA validation failed — content may be incomplete.",
    )
    markdown = artifact.markdown

    if doi:
        normalized_acquisition = (acquisition_via or "").strip()
        source = "Codex Chrome Extension" if normalized_acquisition == "codex" else "Local PDF"
        pdf_materialization = materialize_library_pdf(
            doi=doi,
            paths=paths,
            input_path=path,
            pdf_bytes=pdf_buffer,
            copy_to_library=copy_to_library,
        )
        if pdf_materialization.outcome == "conflict":
            return _pdf_materialization_conflict_receipt(doi, pdf_materialization)
        persisted = persist_reviewed_library_document(
            review,
            paths=paths,
            doi=doi,
            title=expected_title or "",
            source=source,
            fetch_outcome="local_parse",
            extra_frontmatter=None,
            asset_mode=config.extract.assets.mode,
            asset_limits=_asset_limits_from_config(config),
            copied_pdf_path=pdf_materialization.copied_pdf_path if pdf_materialization else "",
            pdf_materialization=pdf_materialization,
            index_warning_message=(
                "Search index refresh failed — canonical markdown was saved to papers/ only. "
                "Error: {index_error}"
            ),
            indexing_config=config.indexing,
        )
        fetch_status = "partial_success" if persisted.index_warning_added else "fulltext"
        remote_warning = _record_remote_metadata_update(
            metadata_dir=_remote_metadata_dir(paths),
            doi=doi,
            fetch_status=fetch_status,
            has_fulltext=True,
            source=source,
            title=expected_title or "",
            metadata=None,
            fetch_via=normalized_acquisition or "local_pdf",
            fetch_state="ok",
            indexing_config=config.indexing,
        )
        result = (
            "## PDF Parsed & Saved with Partial Success\n\n"
            if persisted.index_warning_added
            else "## PDF Parsed & Saved\n\n"
        )
        result += f"- **DOI:** {doi}\n"
        result += f"- **URI:** {persisted.summary.uri}\n"
        result += f"- **File:** {persisted.summary.file_path}\n"
        if persisted.copied_pdf_path:
            result += f"- **Canonical PDF:** {persisted.copied_pdf_path}\n"
        if pdf_materialization:
            result += f"- **PDF Materialization:** {pdf_materialization.action}\n"
        result += f"- **Source PDF Hash:** {pdf_hash}\n"
        if normalized_acquisition:
            result += f"- **Acquisition Via:** {normalized_acquisition}\n"
        result += f"- **Fetch Status:** {fetch_status}\n"
        result += f"- **Words:** {persisted.summary.word_count:,}\n"
        result += f"- **Index Status:** {persisted.summary.index_status}\n"
        if persisted.artifact.parser_used:
            result += f"- **Parser Used:** {persisted.artifact.parser_used}\n"
        if persisted.asset_manifest_path:
            result += f"- **Assets Manifest:** {persisted.asset_manifest_path}\n"
        warnings = persisted.warnings
        parser_debug = persisted.debug
        if remote_warning:
            warnings = [*warnings, remote_warning]
    else:
        result = "## PDF Parsed\n\n"
        if review.artifact.parser_used:
            result += f"- **Parser Used:** {review.artifact.parser_used}\n"
        result += f"- **Words:** {len(markdown.split()):,}\n"
        result += f"- **Characters:** {len(markdown):,}\n"
        result += f"\n---\n\n{markdown[:3000]}"
        if len(markdown) > 3000:
            result += f"\n\n... (truncated, {len(markdown):,} total chars)"
        warnings = review.warnings
        parser_debug = review.debug

    if warnings:
        result += "\n\n### Warnings\n" + "\n".join(f"- {warning}" for warning in warnings)
    if parser_debug:
        result += "\n\n### Parser Debug\n" + "\n".join(f"- {entry}" for entry in parser_debug)

    return result


def _run_parse_pdf_file_attempt_sync(
    *,
    attempt_id: str,
    db_path: Path,
    path: Path,
    pdf_buffer: bytes,
    pdf_hash: str,
    expected_title: str | None,
    doi: str,
    copy_to_library: bool,
    acquisition_via: str | None,
    paths: GRaDOSPaths,
    config: GRaDOSConfig,
) -> str:
    from grados.storage.parse_attempts import complete_parse_attempt, fail_parse_attempt

    try:
        receipt = asyncio.run(
            _parse_pdf_file_core(
                path=path,
                pdf_buffer=pdf_buffer,
                pdf_hash=pdf_hash,
                expected_title=expected_title,
                doi=doi,
                copy_to_library=copy_to_library,
                acquisition_via=acquisition_via,
                paths=paths,
                config=config,
            )
        )
    except Exception as exc:
        receipt = f"PDF parse attempt failed for {path}: {exc.__class__.__name__}: {exc}"
        fail_parse_attempt(
            db_path,
            attempt_id,
            receipt_text=receipt,
            failure_reason="parse_failed",
            error_message=f"{exc.__class__.__name__}: {exc}",
        )
        return receipt

    if receipt.startswith("## PDF Parsed & Saved") or receipt.startswith("## Paper Already Saved"):
        complete_parse_attempt(
            db_path,
            attempt_id,
            receipt_text=receipt,
            **_parse_attempt_completion_metadata(paths, doi),
        )
    elif receipt.startswith("## PDF Materialization Conflict"):
        fail_parse_attempt(
            db_path,
            attempt_id,
            receipt_text=receipt,
            failure_reason="pdf_materialization_conflict",
            error_message="PDF materialization conflict",
        )
    else:
        fail_parse_attempt(
            db_path,
            attempt_id,
            receipt_text=receipt,
            failure_reason="parse_failed",
            error_message=receipt.splitlines()[0] if receipt else "parse failed",
        )
    return receipt


def _start_parse_attempt_future(
    *,
    attempt_id: str,
    db_path: Path,
    path: Path,
    pdf_buffer: bytes,
    pdf_hash: str,
    expected_title: str | None,
    doi: str,
    copy_to_library: bool,
    acquisition_via: str | None,
    paths: GRaDOSPaths,
    config: GRaDOSConfig,
) -> concurrent.futures.Future[str]:
    with _PARSE_ATTEMPT_LOCK:
        future = _PARSE_ATTEMPT_FUTURES.get(attempt_id)
        if future is not None and not future.done():
            return future
        future = concurrent.futures.Future()
        _PARSE_ATTEMPT_FUTURES[attempt_id] = future

    def _forget(completed: concurrent.futures.Future[str]) -> None:
        with _PARSE_ATTEMPT_LOCK:
            if _PARSE_ATTEMPT_FUTURES.get(attempt_id) is completed:
                _PARSE_ATTEMPT_FUTURES.pop(attempt_id, None)

    future.add_done_callback(_forget)
    worker = threading.Thread(
        target=_run_parse_attempt_future_worker,
        kwargs={
            "future": future,
            "attempt_id": attempt_id,
            "db_path": db_path,
            "path": path,
            "pdf_buffer": pdf_buffer,
            "pdf_hash": pdf_hash,
            "expected_title": expected_title,
            "doi": doi,
            "copy_to_library": copy_to_library,
            "acquisition_via": acquisition_via,
            "paths": paths,
            "config": config,
        },
        name=f"grados-parse-attempt-{attempt_id[:8]}",
        daemon=True,
    )
    worker.start()
    return future


def _run_parse_attempt_future_worker(
    *,
    future: concurrent.futures.Future[str],
    attempt_id: str,
    db_path: Path,
    path: Path,
    pdf_buffer: bytes,
    pdf_hash: str,
    expected_title: str | None,
    doi: str,
    copy_to_library: bool,
    acquisition_via: str | None,
    paths: GRaDOSPaths,
    config: GRaDOSConfig,
) -> None:
    if not future.set_running_or_notify_cancel():
        return
    try:
        result = _run_parse_pdf_file_attempt_sync(
            attempt_id=attempt_id,
            db_path=db_path,
            path=path,
            pdf_buffer=pdf_buffer,
            pdf_hash=pdf_hash,
            expected_title=expected_title,
            doi=doi,
            copy_to_library=copy_to_library,
            acquisition_via=acquisition_via,
            paths=paths,
            config=config,
        )
    except BaseException as exc:
        future.set_exception(exc)
        return
    future.set_result(result)


async def _await_parse_attempt_future(
    future: concurrent.futures.Future[str],
    *,
    timeout_seconds: float,
) -> str | None:
    if future.done():
        return future.result()
    if timeout_seconds <= 0:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.shield(asyncio.wrap_future(future)),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return None


async def parse_pdf_file(
    file_path: Annotated[str, Field(min_length=1, description="Local path to the PDF file to parse.")],
    expected_title: Annotated[
        str | None,
        Field(description="Optional title used for QA validation only."),
    ] = None,
    doi: Annotated[
        str | None,
        Field(description="Optional DOI to bind the parsed PDF to canonical storage and save it to the paper library."),
    ] = None,
    copy_to_library: Annotated[
        bool,
        Field(description="Copy the raw PDF into the managed downloads archive when a DOI is provided."),
    ] = False,
    acquisition_via: Annotated[
        str | None,
        Field(description="Optional acquisition route label, such as `codex` or `local_pdf`."),
    ] = None,
) -> str:
    """Parse a local PDF file into markdown."""
    from grados.storage.papers import load_paper_record
    from grados.storage.parse_attempts import (
        build_parse_attempt_id,
        get_parse_attempt,
        mark_parse_attempt_interrupted,
        restart_parse_attempt,
        upsert_running_parse_attempt,
    )

    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return f"File not found: {file_path}"

    paths, config = get_paths_and_config()
    try:
        pdf_buffer = read_bounded_local_file(
            path,
            max_bytes=config.extract.security.max_local_pdf_bytes,
            label=f"Local PDF {path}",
        )
    except SizeLimitError as exc:
        return f"PDF file is too large: {exc}"
    except LocalFileReadError as exc:
        return f"Could not read PDF file: {exc}"

    if pdf_buffer[:5] != b"%PDF-":
        return f"Not a valid PDF file: {file_path}"
    pdf_hash = hashlib.sha256(pdf_buffer).hexdigest()

    if not doi:
        return await _parse_pdf_file_core(
            path=path,
            pdf_buffer=pdf_buffer,
            pdf_hash=pdf_hash,
            expected_title=expected_title,
            doi=None,
            copy_to_library=copy_to_library,
            acquisition_via=acquisition_via,
            paths=paths,
            config=config,
        )

    existing_record = load_paper_record(paths.papers, doi=doi)
    if existing_record is not None and existing_record.content_markdown.strip():
        return _already_saved_receipt(doi, existing_record, paths.papers)

    normalized_acquisition = (acquisition_via or "").strip()
    parser_config = _parse_attempt_config_signature(config)
    attempt_id = build_parse_attempt_id(
        doi=doi,
        input_pdf_hash=pdf_hash,
        copy_to_library=copy_to_library,
        acquisition_via=normalized_acquisition,
        parser_config=parser_config,
    )
    db_path = paths.database_state
    attempt, created = upsert_running_parse_attempt(
        db_path,
        attempt_id=attempt_id,
        doi=doi,
        input_pdf_path=str(path),
        input_pdf_name=path.name,
        input_pdf_hash=pdf_hash,
        copy_to_library=copy_to_library,
        acquisition_via=normalized_acquisition,
        expected_title=expected_title or "",
        parser_config=parser_config,
    )
    foreground_wait_seconds = float(getattr(config.extract.parsing, "foreground_wait_seconds", 90.0))
    attempt_stale_seconds = float(getattr(config.extract.parsing, "attempt_stale_seconds", 1800.0))
    with _PARSE_ATTEMPT_LOCK:
        active_future = _PARSE_ATTEMPT_FUTURES.get(attempt_id)
    if active_future is not None and active_future.done():
        refreshed = get_parse_attempt(db_path, attempt_id)
        if refreshed is not None:
            attempt = refreshed
        active_future = None

    if attempt.status == "completed" and attempt.receipt_text:
        return attempt.receipt_text
    should_start = created
    if attempt.status == "failed" and attempt.receipt_text:
        if _parse_attempt_failure_is_terminal(attempt):
            return attempt.receipt_text
        if not _parse_attempt_is_stale(attempt, stale_seconds=attempt_stale_seconds):
            return _parse_retry_wait_receipt(
                doi=doi,
                attempt=attempt,
                attempt_stale_seconds=attempt_stale_seconds,
            )
        restarted = restart_parse_attempt(db_path, attempt_id)
        if restarted is not None:
            attempt = restarted
            should_start = True

    if attempt.status == "interrupted":
        restarted = restart_parse_attempt(db_path, attempt_id)
        if restarted is not None:
            attempt = restarted
            should_start = True
    if attempt.status == "running" and active_future is None and not should_start:
        if _parse_attempt_is_stale(attempt, stale_seconds=attempt_stale_seconds):
            mark_parse_attempt_interrupted(
                db_path,
                attempt_id,
                error_message="running parse attempt had no in-process worker and exceeded attempt_stale_seconds",
            )
            restarted = restart_parse_attempt(db_path, attempt_id)
            if restarted is not None:
                attempt = restarted
            should_start = True
        else:
            return _parse_in_progress_receipt(
                doi=doi,
                attempt=attempt,
                foreground_wait_seconds=foreground_wait_seconds,
                attempt_stale_seconds=attempt_stale_seconds,
                existing_worker=False,
            )

    future = active_future
    if future is None or should_start:
        future = _start_parse_attempt_future(
            attempt_id=attempt_id,
            db_path=db_path,
            path=path,
            pdf_buffer=pdf_buffer,
            pdf_hash=pdf_hash,
            expected_title=expected_title,
            doi=doi,
            copy_to_library=copy_to_library,
            acquisition_via=normalized_acquisition,
            paths=paths,
            config=config,
        )

    completed_receipt = await _await_parse_attempt_future(
        future,
        timeout_seconds=foreground_wait_seconds,
    )
    if completed_receipt is not None:
        return completed_receipt

    refreshed = get_parse_attempt(db_path, attempt_id) or attempt
    return _parse_in_progress_receipt(
        doi=doi,
        attempt=refreshed,
        foreground_wait_seconds=foreground_wait_seconds,
        attempt_stale_seconds=attempt_stale_seconds,
        existing_worker=True,
    )


def register_library_tools(mcp: FastMCP) -> None:
    mcp.resource("grados://papers/index", mime_type="text/markdown")(papers_index_resource)
    mcp.resource("grados://papers/{safe_doi}", mime_type="text/markdown")(paper_overview_resource)

    mcp.tool(
        description=(
            "Fetch, parse, and save one paper's canonical full text by DOI. "
            "If canonical Markdown is already saved, returns an already-saved receipt unless `force_refresh=true`. "
            "Returns a compact save receipt with URI, file path, section headings, "
            "and warnings rather than the full paper text."
        )
    )(extract_paper_full_text)

    mcp.tool(
        description=(
            "Read a paragraph window from a previously saved paper for canonical deep reading "
            "and citation verification. "
            "Provide one of `doi`, `safe_doi`, or `uri`; use `section_query` to jump near a heading."
        )
    )(read_saved_paper)

    mcp.tool(
        description=(
            "Return a low-token structure card for one saved paper. "
            "Use this to screen a paper before calling `read_saved_paper`; it is not the full citation source."
        )
    )(get_saved_paper_structure)

    mcp.tool(
        description=(
            "List or read parser-generated paper assets such as figures, tables, formulas, page images, "
            "and debug/source files. Use `include_image=true` only for a specific image asset."
        )
    )(read_paper_asset)

    mcp.tool(
        description=(
            "Import a local PDF file or directory into GRaDOS canonical storage and the retrieval index. "
            "Returns a summary plus the first 25 item results."
        )
    )(import_local_pdf_library)

    mcp.tool(
        description=(
            "Dry-run scan for noncanonical PDFs in downloads/ that duplicate a DOI's managed "
            "downloads/{safe_doi}.pdf artifact. It reports candidates only and never deletes files."
        )
    )(plan_library_pdf_cleanup)

    mcp.tool(
        description=(
            "Complete a Codex Chrome Extension handoff by conservatively scanning the "
            "configured watch directory for one completed PDF, or by validating downloaded_file_path when the "
            "absolute PDF path is known, then save it through the same codex parse/canonical-storage path."
        )
    )(ingest_codex_downloaded_pdf)

    mcp.tool(
        description=(
            "Parse a local PDF into markdown. "
            "Without a DOI it returns a truncated preview; with a DOI it saves canonical markdown "
            "and returns a save receipt. Use copy_to_library=true for host-agent downloaded PDFs "
            "that should be archived under downloads/."
        )
    )(parse_pdf_file)
