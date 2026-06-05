"""Host-side ChatGPT Pro external consult packet helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from grados.browser.chatgpt.urls import is_recoverable_chatgpt_conversation_url
from grados.config import (
    DEFAULT_EXTERNAL_CONSULT_RESPONSE_WAIT_TOTAL_SECONDS,
    ExternalConsultConfig,
    GRaDOSPaths,
    HeadlessBrowserConfig,
)
from grados.research.draft_audit import (
    VERDICT_MAJOR_DISTORTION,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
)
from grados.research.evidence_eligibility import classify_evidence_rejection
from grados.research.evidence_pack import (
    EvidencePack,
    EvidencePackItem,
    evidence_pack_from_dict,
    prepare_evidence_pack,
    read_evidence_pack,
    verify_evidence_pack,
)
from grados.research.pack_audit import audit_answer_against_pack
from grados.research_state import query_research_artifacts, save_research_artifact

__all__ = [
    "EXTERNAL_CONSULT_PACKET_KIND",
    "EXTERNAL_CONSULT_RESULT_KIND",
    "audit_external_consult_result",
    "consult_chatgpt_pro",
    "prepare_external_consult_from_topic",
    "prepare_external_consult_packet",
    "preview_external_consult_packet",
    "get_external_consult_operation_status",
    "run_external_consult",
    "save_external_consult_result",
]

EXTERNAL_CONSULT_PACKET_KIND = "external_consult_packet"
EXTERNAL_CONSULT_RESULT_KIND = "external_consult_result"
CHATGPT_PRO_CONSULT_RESULT_KIND = "chatgpt_pro_consult_result"
EXTERNAL_CONSULT_PROTOCOL_VERSION = "external-consult-v1"
CHATGPT_PRO_CONSULT_PROTOCOL_VERSION = "chatgpt-pro-consult-v1"
DEFAULT_EXTERNAL_CONSULT_FOREGROUND_WAIT_SECONDS = 75.0
DEFAULT_CHATGPT_PRO_CONSULT_REATTACH_SETTLE_SECONDS = 2.0
DEFAULT_CHATGPT_PRO_STATUS_RECOVERY_PROBE_SECONDS = 30.0

ExternalConsultMode = Literal["review", "synthesize"]
ChatGPTProConsultMode = Literal["ask", "review", "synthesize", "critique"]
ChatGPTProModelStrategy = Literal["select", "current", "ignore"]
ChatGPTProThinkingStrategy = Literal["highest", "current", "ignore"]
ChatGPTProWaitPolicy = Literal["auto", "return_pending"]

_ANCHOR_ID_PATTERN = re.compile(r"\banchor_[0-9]{3,}\b")
_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_BLOCK_ID_PATTERN = re.compile(
    r"\b(?:md_p\d{5}_[0-9a-f]{12}|paragraph-\d{6}-[0-9a-f]{12}|[A-Za-z0-9_.-]+::p\d+:\d+:\d+)\b",
    re.IGNORECASE,
)
_CANONICAL_URI_PATTERN = re.compile(r"\bgrados://papers/[^\s,;)\]}>\"']+")
_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]{2,}", re.IGNORECASE)


@dataclass(frozen=True)
class _ChatGPTProConsultWaitSettings:
    response_wait_total_seconds: float
    per_attempt_wait_seconds: float
    max_browser_wait_attempts: int
    initial_wait_seconds: float
    max_reattach_after_initial: int


def _resolve_chatgpt_pro_consult_wait_settings(
    external_consult_config: ExternalConsultConfig | None,
) -> _ChatGPTProConsultWaitSettings:
    total_seconds = float(
        getattr(
            external_consult_config,
            "response_wait_total_seconds",
            DEFAULT_EXTERNAL_CONSULT_RESPONSE_WAIT_TOTAL_SECONDS,
        )
        or DEFAULT_EXTERNAL_CONSULT_RESPONSE_WAIT_TOTAL_SECONDS
    )
    if total_seconds < 1.0:
        total_seconds = DEFAULT_EXTERNAL_CONSULT_RESPONSE_WAIT_TOTAL_SECONDS
    per_attempt_seconds = min(DEFAULT_EXTERNAL_CONSULT_FOREGROUND_WAIT_SECONDS, total_seconds)
    max_browser_attempts = max(
        1,
        int(math.ceil(total_seconds / DEFAULT_EXTERNAL_CONSULT_FOREGROUND_WAIT_SECONDS)),
    )
    return _ChatGPTProConsultWaitSettings(
        response_wait_total_seconds=total_seconds,
        per_attempt_wait_seconds=per_attempt_seconds,
        max_browser_wait_attempts=max_browser_attempts,
        initial_wait_seconds=per_attempt_seconds,
        max_reattach_after_initial=max(0, max_browser_attempts - 1),
    )


def _status_recovery_probe_wait_settings(
    wait_settings: _ChatGPTProConsultWaitSettings,
) -> _ChatGPTProConsultWaitSettings:
    probe_seconds = min(
        wait_settings.response_wait_total_seconds,
        wait_settings.per_attempt_wait_seconds,
        DEFAULT_CHATGPT_PRO_STATUS_RECOVERY_PROBE_SECONDS,
    )
    probe_seconds = max(1.0, probe_seconds)
    return _ChatGPTProConsultWaitSettings(
        response_wait_total_seconds=probe_seconds,
        per_attempt_wait_seconds=probe_seconds,
        max_browser_wait_attempts=1,
        initial_wait_seconds=probe_seconds,
        max_reattach_after_initial=0,
    )


def _normalize_mode(mode: str | None) -> ExternalConsultMode:
    normalized = (mode or "review").strip().lower()
    if normalized not in {"review", "synthesize"}:
        raise ValueError("mode must be `review` or `synthesize`.")
    return normalized  # type: ignore[return-value]


def _normalize_consult_mode(mode: str | None) -> ChatGPTProConsultMode:
    normalized = (mode or "ask").strip().lower()
    if normalized not in {"ask", "review", "synthesize", "critique"}:
        raise ValueError("mode must be `ask`, `review`, `synthesize`, or `critique`.")
    return normalized  # type: ignore[return-value]


def _normalize_model_strategy(strategy: str | None) -> ChatGPTProModelStrategy:
    normalized = (strategy or "select").strip().lower()
    if normalized not in {"select", "current", "ignore"}:
        raise ValueError("model_strategy must be `select`, `current`, or `ignore`.")
    return normalized  # type: ignore[return-value]


def _normalize_thinking_strategy(strategy: str | None) -> ChatGPTProThinkingStrategy:
    normalized = (strategy or "highest").strip().lower()
    if normalized not in {"highest", "current", "ignore"}:
        raise ValueError("thinking_strategy must be `highest`, `current`, or `ignore`.")
    return normalized  # type: ignore[return-value]


def _normalize_wait_policy(policy: str | None) -> ChatGPTProWaitPolicy:
    normalized = (policy or "auto").strip().lower()
    if normalized not in {"auto", "return_pending"}:
        raise ValueError("wait_policy must be `auto` or `return_pending`.")
    return normalized  # type: ignore[return-value]


def _external_result_mode(mode: str | None) -> ExternalConsultMode:
    normalized = (mode or "review").strip().lower()
    return "synthesize" if normalized == "synthesize" else "review"


def _load_pack(db_path: Path, pack_id: str) -> tuple[EvidencePack | None, dict[str, Any]]:
    loaded = read_evidence_pack(db_path, pack_id=pack_id)
    if not loaded.get("found"):
        return None, {
            "ok": False,
            "error": str(loaded.get("error", "pack_not_found")),
            "pack_id": pack_id,
            "loaded": loaded,
        }
    content = loaded.get("pack")
    if not isinstance(content, dict):
        return None, {
            "ok": False,
            "error": "invalid_pack_content",
            "pack_id": pack_id,
            "loaded": loaded,
        }
    return evidence_pack_from_dict(content), loaded


def _blocked_packet_result(
    *,
    pack_id: str,
    mode: ExternalConsultMode,
    verify_result: dict[str, Any],
    error: str,
    blocked_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "sendable": False,
        "saved": False,
        "mode": mode,
        "pack_id": pack_id,
        "error": error,
        "blocked_reasons": [error],
        "blocked_items": blocked_items or [],
        "verify": verify_result,
    }


def _short_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.strip().split())
    max_chars = max(80, max_chars)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _first_sentence(text: str, max_chars: int) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return ""
    match = re.search(r"(?<=[.!?])\s+", normalized)
    if match:
        normalized = normalized[: match.start()].strip()
    return _short_text(normalized, max_chars)


def _anchor_id(index: int) -> str:
    return f"anchor_{index:03d}"


def _packet_item(
    item: EvidencePackItem,
    *,
    index: int,
    max_excerpt_chars: int,
) -> dict[str, Any]:
    limitations = [
        "Use only this canonical excerpt and its metadata; do not infer beyond it.",
        "Final citation requires canonical reread through read_saved_paper or a current-valid pack.",
    ]
    if item.subquestion:
        limitations.append(f"Prepared for subquestion: {item.subquestion}")
    return {
        "anchor_id": _anchor_id(index),
        "doi": item.doi,
        "safe_doi": item.safe_doi,
        "paper_id": item.paper_id,
        "canonical_uri": item.canonical_uri,
        "paragraph_start": item.source_paragraph_index,
        "paragraph_count": 1,
        "block_id": item.block_id,
        "block_type": item.block_type,
        "heading_path": list(item.heading_path),
        "title": item.title,
        "authors": list(item.authors),
        "year": item.year,
        "journal": item.journal,
        "short_excerpt": _short_text(item.text, max_excerpt_chars),
        "candidate_claim": _first_sentence(item.text, min(240, max_excerpt_chars)),
        "limitations": limitations,
    }


def _packet_prompt_body(packet_without_prompt: dict[str, Any]) -> str:
    packet_json = json.dumps(packet_without_prompt, ensure_ascii=False, indent=2, sort_keys=True)
    mode = str(packet_without_prompt.get("mode", "review"))
    return (
        "You are ChatGPT Pro acting as an external reviewer/synthesizer for a GRaDOS "
        "evidence packet.\n\n"
        "Outcome:\n"
        "Return advisory claims and evidence gaps that GRaDOS can audit. Do not produce "
        "final citation evidence.\n\n"
        f"Mode: {mode}\n\n"
        "Evidence rules:\n"
        "- Use only the provided evidence anchors and anchor_ids.\n"
        "- Do not add papers, DOIs, facts, citations, or web evidence that are not in the packet.\n"
        "- If evidence is missing, report it under missing_evidence instead of filling the gap.\n"
        "- Treat every excerpt as advisory until GRaDOS rereads canonical paragraphs.\n"
        "- Every claim must cite packet anchor_ids.\n\n"
        "Return Markdown with one JSON block containing:\n"
        "{\n"
        '  "claims": [\n'
        '    {"text": "...", "anchor_ids": ["anchor_001"], "confidence": "low|medium|high", "caveat": "..."}\n'
        "  ],\n"
        '  "missing_evidence": [],\n'
        '  "forbidden_or_outside_content": [],\n'
        '  "notes_for_grados_audit": []\n'
        "}\n\n"
        "Evidence packet:\n"
        "```json\n"
        f"{packet_json}\n"
        "```"
    )


def _packet_prompt_payload(packet: dict[str, Any]) -> dict[str, Any]:
    payload = dict(packet)
    payload.pop("host_prompt", None)
    payload.pop("prompt_hash", None)
    payload.pop("estimated_chars", None)
    payload.pop("estimated_tokens", None)
    return payload


def _render_host_prompt(packet: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    host_prompt = _packet_prompt_body(_packet_prompt_payload(packet))
    return host_prompt, {
        "prompt_hash": hashlib.sha256(host_prompt.encode("utf-8")).hexdigest(),
        "estimated_chars": len(host_prompt),
        "estimated_tokens": max(1, len(host_prompt) // 4),
    }


def _build_packet(
    pack: EvidencePack,
    verify_result: dict[str, Any],
    *,
    mode: ExternalConsultMode,
    max_items: int,
    max_excerpt_chars: int,
) -> dict[str, Any]:
    max_items = max(1, min(max_items, 50))
    max_excerpt_chars = max(120, min(max_excerpt_chars, 2000))
    packet_items = [
        _packet_item(item, index=index, max_excerpt_chars=max_excerpt_chars)
        for index, item in enumerate(pack.evidence_items[:max_items], 1)
    ]
    payload: dict[str, Any] = {
        "schema_version": EXTERNAL_CONSULT_PROTOCOL_VERSION,
        "kind": EXTERNAL_CONSULT_PACKET_KIND,
        "mode": mode,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "topic": pack.topic,
        "query": pack.query,
        "answerable": pack.answerable,
        "source": "verified_evidence_pack",
        "verify": {
            "current_valid": bool(verify_result.get("current_valid")),
            "snapshot_valid": bool(verify_result.get("snapshot_valid")),
            "evidence_count": int(verify_result.get("evidence_count") or 0),
        },
        "items": packet_items,
        "item_count": len(packet_items),
        "omitted_evidence_count": max(0, len(pack.evidence_items) - len(packet_items)),
        "insufficient_evidence": list(pack.insufficient_evidence),
        "requested_scoped_dois": list(pack.requested_scoped_dois),
        "covered_dois": list(pack.covered_dois),
        "missing_scoped_dois": list(pack.missing_scoped_dois),
        "missing_reasons": dict(pack.missing_reasons),
        "forbidden": [
            "Do not introduce pack-external papers, DOIs, facts, citations, or web evidence.",
            "Do not treat ChatGPT Pro output as final citation evidence.",
            "Do not cite without a later GRaDOS canonical reread or current-valid evidence pack.",
        ],
    }
    _, prompt_metadata = _render_host_prompt(payload)
    payload.update(prompt_metadata)
    return payload


def _packet_section_name(item: dict[str, Any]) -> str:
    heading_path = item.get("heading_path")
    if isinstance(heading_path, list) and heading_path:
        return str(heading_path[-1])
    return ""


def _validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    blocked_items: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    warning_items: list[dict[str, Any]] = []
    for item in packet.get("items", []):
        if not isinstance(item, dict):
            continue
        anchor_id = str(item.get("anchor_id") or "")
        section_name = _packet_section_name(item)
        known_title = str(item.get("title") or "")
        excerpt_reason = classify_evidence_rejection(
            section_name,
            str(item.get("short_excerpt") or ""),
            known_title=known_title,
        )
        claim_reason = classify_evidence_rejection(
            section_name,
            str(item.get("candidate_claim") or ""),
            known_title=known_title,
        )
        if claim_reason is not None:
            warning_items.append(
                {
                    "anchor_id": anchor_id,
                    "doi": str(item.get("doi") or ""),
                    "section_name": section_name,
                    "reason": claim_reason,
                    "field": "candidate_claim",
                }
            )
        if excerpt_reason is not None:
            blocked_items.append(
                {
                    "anchor_id": anchor_id,
                    "doi": str(item.get("doi") or ""),
                    "section_name": section_name,
                    "reason": excerpt_reason,
                    "excerpt_rejection_reason": excerpt_reason,
                    "candidate_claim_rejection_reason": claim_reason or "",
                }
            )
            blocked_reasons.append(f"{anchor_id or 'anchor'}:{excerpt_reason}:{section_name or 'unknown_section'}")

    missing_reasons = packet.get("missing_reasons")
    missing_reason_map = missing_reasons if isinstance(missing_reasons, dict) else {}
    missing_scoped_dois = [str(doi) for doi in packet.get("missing_scoped_dois", []) if str(doi).strip()]
    if missing_scoped_dois:
        blocked_reasons.append("missing_scoped_doi_coverage")
        blocked_items.extend(
            {
                "doi": doi,
                "reason": str(missing_reason_map.get(doi) or "not_covered"),
            }
            for doi in missing_scoped_dois
        )

    if not packet.get("items"):
        blocked_reasons.append("empty_evidence_packet")

    return {
        "sendable": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "blocked_items": blocked_items,
        "warning_items": warning_items,
    }


def _packet_preview(
    packet: dict[str, Any],
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation = validation or _validate_packet(packet)
    sendable = bool(validation.get("sendable"))
    return {
        "ok": sendable,
        "sendable": sendable,
        "saved": False,
        "mode": packet["mode"],
        "pack_id": packet["pack_id"],
        "pack_sha256": packet["pack_sha256"],
        "prompt_hash": packet["prompt_hash"],
        "packet_item_count": packet["item_count"],
        "omitted_evidence_count": packet["omitted_evidence_count"],
        "estimated_chars": packet["estimated_chars"],
        "estimated_tokens": packet["estimated_tokens"],
        "verify": packet["verify"],
        "blocked_reasons": list(validation.get("blocked_reasons", [])),
        "blocked_items": list(validation.get("blocked_items", [])),
        "warning_items": list(validation.get("warning_items", [])),
        "prompt_skeleton": (
            "Send the packet to ChatGPT Pro only after the gate is enabled. Ask for claims, "
            "anchor_ids, confidence, caveats, missing_evidence, and forbidden_or_outside_content."
        ),
        "host_guidance": [
            "Use one ChatGPT Pro conversation per GRaDOS workflow.",
            "Save the Pro response with save_external_consult_result before using it.",
            "Run audit_external_consult_result and reread canonical windows before final citation.",
        ],
    }


def preview_external_consult_packet(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    mode: str = "review",
    max_items: int = 25,
    max_excerpt_chars: int = 700,
) -> dict[str, Any]:
    """Preview a ChatGPT Pro packet without saving or contacting external services."""
    resolved_mode = _normalize_mode(mode)
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return _blocked_packet_result(
            pack_id=pack_id,
            mode=resolved_mode,
            verify_result=dict(loaded),
            error=str(loaded.get("error", "pack_not_found")),
        )
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    if not verify_result.get("current_valid"):
        return _blocked_packet_result(
            pack_id=pack.pack_id,
            mode=resolved_mode,
            verify_result=verify_result,
            error="evidence_pack_not_current_valid",
        )
    packet = _build_packet(
        pack,
        verify_result,
        mode=resolved_mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
    )
    return _packet_preview(packet, _validate_packet(packet))


def prepare_external_consult_packet(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    mode: str = "review",
    max_items: int = 25,
    max_excerpt_chars: int = 700,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a current-valid evidence packet for host-side ChatGPT Pro use."""
    resolved_mode = _normalize_mode(mode)
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return _blocked_packet_result(
            pack_id=pack_id,
            mode=resolved_mode,
            verify_result=dict(loaded),
            error=str(loaded.get("error", "pack_not_found")),
        )
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    if not verify_result.get("current_valid"):
        return _blocked_packet_result(
            pack_id=pack.pack_id,
            mode=resolved_mode,
            verify_result=verify_result,
            error="evidence_pack_not_current_valid",
        )
    packet = _build_packet(
        pack,
        verify_result,
        mode=resolved_mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
    )
    validation = _validate_packet(packet)
    preview = _packet_preview(packet, validation)
    if not preview["sendable"]:
        return preview
    host_prompt, _ = _render_host_prompt(packet)
    artifact_metadata = {
        **(metadata or {}),
        "protocol": EXTERNAL_CONSULT_PROTOCOL_VERSION,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "prompt_hash": packet["prompt_hash"],
        "mode": resolved_mode,
        "evidence_count": packet["item_count"],
    }
    receipt = save_research_artifact(
        db_path,
        kind=EXTERNAL_CONSULT_PACKET_KIND,
        title=f"External consult packet: {pack.topic or pack.pack_id}",
        content=packet,
        metadata=artifact_metadata,
    )
    return {
        **preview,
        "saved": True,
        "artifact_id": receipt["artifact_id"],
        "kind": EXTERNAL_CONSULT_PACKET_KIND,
        "metadata": receipt["metadata"],
        "packet": packet,
        "host_prompt": host_prompt,
    }


def prepare_external_consult_from_topic(
    chroma_dir: Path,
    db_path: Path,
    papers_dir: Path,
    *,
    topic: str,
    subquestions: list[str] | None = None,
    scoped_dois: list[str] | None = None,
    evidence_max_windows: int = 8,
    mode: str = "review",
    max_items: int = 25,
    max_excerpt_chars: int = 700,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare a pack, verify it through packet preparation, and persist a sendable packet."""
    pack_receipt = prepare_evidence_pack(
        chroma_dir,
        db_path,
        topic=topic,
        subquestions=subquestions,
        scoped_dois=scoped_dois,
        max_windows=evidence_max_windows,
    )
    pack_id = str(pack_receipt.get("pack_id") or "")
    if not pack_id:
        return {
            "ok": False,
            "sendable": False,
            "saved": False,
            "error": "evidence_pack_not_prepared",
            "evidence_pack": pack_receipt,
        }

    packet_metadata = {
        **(metadata or {}),
        "source": "prepare_external_consult_from_topic",
        "evidence_pack_artifact_id": str(pack_receipt.get("artifact_id") or ""),
    }
    packet = prepare_external_consult_packet(
        db_path,
        papers_dir,
        pack_id=pack_id,
        mode=mode,
        max_items=max_items,
        max_excerpt_chars=max_excerpt_chars,
        metadata=packet_metadata,
    )
    return {
        **packet,
        "route": "prepare_external_consult_from_topic",
        "pack_id": pack_id,
        "pack_artifact_id": str(pack_receipt.get("artifact_id") or ""),
        "evidence_pack": pack_receipt,
    }


def _selection_label(record: dict[str, Any], key: str) -> str:
    raw = record.get(key)
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("resolved_label") or "")


def _recoverable_conversation_url(url: Any) -> str:
    observed_url = str(url or "").strip()
    return observed_url if is_recoverable_chatgpt_conversation_url(observed_url) else ""


def _last_observed_conversation_url(record: dict[str, Any]) -> str:
    conversation_url = str(record.get("conversation_url") or "").strip()
    return str(record.get("last_observed_url") or conversation_url).strip()


def _external_recovery_metadata(
    *,
    recover_session_id: str,
    packet_artifact_id: str,
    prompt_hash: str,
    browser_session_record: str = "",
    conversation_url: str = "",
    last_observed_url: str = "",
    next_action: str = "",
) -> dict[str, Any]:
    recoverable_url = _recoverable_conversation_url(conversation_url)
    payload = {
        "recover_session_id": recover_session_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "conversation_url": recoverable_url,
    }
    if browser_session_record:
        payload["browser_session_record"] = browser_session_record
    if last_observed_url:
        payload["last_observed_url"] = last_observed_url
    if next_action:
        payload["next_action"] = next_action
    return payload


def _external_operation_error_message(error: str, record: dict[str, Any]) -> str:
    if not error:
        return ""
    if error in {"assistant_timeout", "capture_failed"}:
        return (
            "The ChatGPT prompt was submitted once, but capture is incomplete after a bounded browser wait. "
            "This usually reflects host timeout/context compression interacting with durable recovery, not a "
            "single ChatGPT Pro route failure. Retry get_operation_status(detail=true) later; use manual_response "
            "only if the answer is visible and automatic capture still cannot save it."
        )
    if error == "conversation_url_missing_or_not_recoverable":
        return "Saved ChatGPT browser session has no recoverable /c/ conversation URL."
    record_error = record.get("error")
    if isinstance(record_error, dict):
        return str(record_error.get("message") or "")
    return ""


def _operation_lookup_sha256(operation_id: str) -> str:
    value = operation_id.strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ensure_external_operation(
    db_path: Path,
    *,
    operation_id: str,
    pack_id: str,
    packet_artifact_id: str,
    prompt_hash: str,
    mode: str,
    status: str,
    stage: str,
    recovery: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    from grados.storage.operations import create_operation, update_operation

    if not operation_id:
        return
    create_operation(
        db_path,
        operation_id=operation_id,
        kind="external_consult",
        status=status,
        stage=stage,
        idempotency_key=prompt_hash,
        input_data={
            "pack_id": pack_id,
            "packet_artifact_id": packet_artifact_id,
            "prompt_hash": prompt_hash,
            "mode": mode,
        },
        progress={"response_captured": bool(result and result.get("response_path")), "result_saved": False},
        recovery=recovery,
        result=result,
        error=error,
    )
    update_operation(
        db_path,
        operation_id,
        status=status,
        stage=stage,
        recovery=recovery,
        result=result,
        error=error,
        event_type="external_consult_status",
        event_payload={"status": status, "stage": stage, "pack_id": pack_id},
    )


def _find_external_result_for_session(
    db_path: Path,
    *,
    session_id: str,
    prompt_hash: str = "",
    allow_prompt_hash_fallback: bool = False,
) -> dict[str, Any] | None:
    for kind in (EXTERNAL_CONSULT_RESULT_KIND, CHATGPT_PRO_CONSULT_RESULT_KIND):
        artifacts = query_research_artifacts(
            db_path,
            kind=kind,
            detail=True,
            limit=100,
        )
        for artifact in artifacts.get("items", []):
            if not isinstance(artifact, dict):
                continue
            metadata = artifact.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            content = artifact.get("content")
            if not isinstance(content, dict):
                content = {}
            if session_id and str(metadata.get("browser_session_id") or "") == session_id:
                return artifact
            session_hash = _operation_lookup_sha256(session_id)
            if session_hash and session_hash in {
                str(metadata.get("operation_lookup_sha256") or ""),
                str(content.get("operation_lookup_sha256") or ""),
            }:
                return artifact
            candidate_prompt_hash = str(
                content.get("prompt_hash")
                or content.get("rendered_prompt_hash")
                or metadata.get("prompt_hash")
                or metadata.get("rendered_prompt_hash")
                or ""
            )
            if allow_prompt_hash_fallback and prompt_hash and candidate_prompt_hash == prompt_hash:
                if str(metadata.get("runtime") or "") == "grados_chatgpt_browser":
                    return artifact
    return None


def _external_operation_status_payload(
    *,
    operation_id: str,
    record: dict[str, Any],
    result_artifact: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    session_status = str(record.get("status") or "unknown")
    completed = result_artifact is not None
    failed = session_status == "failed" and not completed
    operation_status = "completed" if completed else "failed" if failed else "pending"
    prompt_hash = str(record.get("prompt_hash") or "")
    packet_artifact_id = str(record.get("packet_artifact_id") or "")
    conversation_url = _recoverable_conversation_url(record.get("conversation_url"))
    last_observed_url = _last_observed_conversation_url(record)
    recovery_metadata = _external_recovery_metadata(
        recover_session_id=operation_id,
        packet_artifact_id=packet_artifact_id,
        prompt_hash=prompt_hash,
        browser_session_record=str(record.get("session_record") or ""),
        conversation_url=conversation_url,
        last_observed_url=last_observed_url,
    )
    return {
        "found": True,
        "operation_id": operation_id,
        "kind": "external_consult",
        "status": operation_status,
        "stage": session_status,
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "progress": {
            "stage": session_status,
            "conversation_url_available": bool(conversation_url),
            "response_captured": bool(record.get("response_text") or record.get("response_path")),
            "result_saved": completed,
        },
        "next_action": (
            "read_saved_external_consult_result_or_audit"
            if completed
            else "call_get_operation_status_with_detail_true_after_chatgpt_finishes"
            if operation_status == "pending"
            else "inspect_browser_session_record_and_retry_after_fixing_error"
        ),
        "result_artifact_id": str(result_artifact.get("artifact_id") or "") if result_artifact else "",
        "result_path": str(record.get("response_path") or ""),
        "pack_id": str(record.get("pack_id") or ""),
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "conversation_url": conversation_url,
        "last_observed_url": last_observed_url,
        "browser_session_id": operation_id,
        "browser_session_record": str(record.get("session_record") or ""),
        "recovery_metadata": recovery_metadata,
        "auto_reattach_attempts": list(record.get("auto_reattach_attempts") or []),
        "error": error,
        "error_message": _external_operation_error_message(error, record),
    }


def _save_captured_session_result(
    db_path: Path,
    papers_dir: Path,
    *,
    operation_id: str,
    record: dict[str, Any],
    session_record_path: str,
) -> dict[str, Any]:
    response_text = str(record.get("response_text") or "")
    response_path = str(record.get("response_path") or "")
    if not response_text and response_path:
        try:
            response_text = Path(response_path).read_text(encoding="utf-8")
        except OSError:
            response_text = ""
    if not response_text:
        return {
            "ok": False,
            "saved": False,
            "error": "chatgpt_response_not_captured",
            "message": "The ChatGPT session has no captured response text yet.",
        }

    pack_id = str(record.get("pack_id") or "")
    if not pack_id:
        raw_metadata_value = record.get("metadata")
        raw_metadata: dict[str, Any] = raw_metadata_value if isinstance(raw_metadata_value, dict) else {}
        raw_context_manifest = raw_metadata.get("context_manifest")
        context_manifest = raw_context_manifest if isinstance(raw_context_manifest, dict) else {}
        prompt_text = ""
        try:
            prompt_text = Path(session_record_path).with_name("prompt.txt").read_text(encoding="utf-8")
        except OSError:
            prompt_text = str(raw_metadata.get("prompt") or "")
        return _save_chatgpt_pro_consult_result(
            db_path,
            prompt=prompt_text,
            rendered_prompt_hash=str(record.get("prompt_hash") or ""),
            response=response_text,
            mode=str(record.get("mode") or raw_metadata.get("consult_mode") or "ask"),
            context_manifest=context_manifest,
            conversation_url=_recoverable_conversation_url(record.get("conversation_url")),
            model_label=_selection_label(record, "model_selection"),
            thinking_label=_selection_label(record, "thinking_selection"),
            metadata={
                **raw_metadata,
                "runtime": "grados_chatgpt_browser",
                "browser_session_id": operation_id,
                "browser_session_record": session_record_path,
                "capture": {
                    "method": str(record.get("capture_method") or ""),
                    "warnings": list(record.get("capture_warnings") or []),
                },
            },
        )

    structured_response = _structured_response_from_text(response_text)
    structured_claims = None
    structured_gaps = None
    if isinstance(structured_response, dict):
        raw_claims = structured_response.get("claims")
        if isinstance(raw_claims, list):
            structured_claims = [dict(item) for item in raw_claims if isinstance(item, dict)]
        structured_gaps = _coerce_string_list(
            structured_response.get("missing_evidence") or structured_response.get("gaps")
        )
    record_metadata_value = record.get("metadata")
    record_metadata: dict[str, Any] = record_metadata_value if isinstance(record_metadata_value, dict) else {}
    return save_external_consult_result(
        db_path,
        papers_dir,
        pack_id=pack_id,
        response=response_text,
        packet_artifact_id=str(record.get("packet_artifact_id") or ""),
        prompt_hash=str(record.get("prompt_hash") or ""),
        conversation_url=_recoverable_conversation_url(record.get("conversation_url")),
        model_label=_selection_label(record, "model_selection"),
        thinking_label=_selection_label(record, "thinking_selection"),
        mode=_external_result_mode(str(record.get("mode") or "review")),
        claims=structured_claims,
        gaps=structured_gaps,
        metadata={
            **record_metadata,
            "runtime": "grados_chatgpt_browser",
            "browser_session_id": operation_id,
            "browser_session_record": session_record_path,
            "capture": {
                "method": str(record.get("capture_method") or ""),
                "warnings": list(record.get("capture_warnings") or []),
            },
        },
        audit=False,
    )


def _save_manual_chatgpt_pro_response(
    db_path: Path,
    papers_dir: Path,
    paths: GRaDOSPaths,
    *,
    session_id: str,
    response_text: str,
) -> dict[str, Any]:
    from grados.browser.chatgpt.session_store import ChatGPTSessionStore
    from grados.storage.operations import complete_operation, fail_operation

    pasted_response = response_text.strip()
    if not pasted_response:
        return {
            "ok": False,
            "saved": False,
            "error": "manual_response_required",
            "message": "`manual_response` must contain the pasted ChatGPT response.",
        }

    store = ChatGPTSessionStore(paths.chatgpt_browser_sessions)
    record = store.read(session_id)
    if record is None:
        return {
            "ok": False,
            "saved": False,
            "error": "chatgpt_session_not_found",
            "message": "The requested ChatGPT browser session record was not found.",
        }

    prompt_hash = str(record.get("prompt_hash") or "")
    existing = _find_external_result_for_session(
        db_path,
        session_id=session_id,
        prompt_hash=prompt_hash,
        allow_prompt_hash_fallback=False,
    )
    if existing is not None:
        return {
            "ok": True,
            "saved": True,
            "already_saved": True,
            "artifact_id": str(existing.get("artifact_id") or ""),
            "result_artifact_id": str(existing.get("artifact_id") or ""),
            "kind": str(existing.get("kind") or ""),
            "next_action": "read_existing_chatgpt_pro_consult_result",
        }

    response_path = store.save_response(session_id, pasted_response)
    warnings = ["manual_copy_fallback"]
    artifact_paths = store.save_capture_artifacts(
        session_id,
        response_text=pasted_response,
        capture_method="manual_copy",
        capture_warnings=warnings,
        snapshot={
            "source": "manual_copy_fallback",
            "manual": True,
            "conversationUrl": _recoverable_conversation_url(record.get("conversation_url")),
        },
        min_turn_index=int(record.get("min_turn_index") or 0) or None,
    )
    updated = store.update(
        session_id,
        status="captured",
        response_text=pasted_response,
        response_path=response_path,
        transcript_path=artifact_paths["transcript_path"],
        assistant_snapshot_path=artifact_paths["assistant_snapshot_path"],
        capture_method="manual_copy",
        capture_warnings=warnings,
        manual_copy_fallback=True,
    )
    session_record_path = str(store.session_json(session_id))
    saved = _save_captured_session_result(
        db_path,
        papers_dir,
        operation_id=session_id,
        record=updated,
        session_record_path=session_record_path,
    )
    operation_result = {
        "artifact_id": str(saved.get("artifact_id") or ""),
        "result_artifact_id": str(saved.get("artifact_id") or ""),
        "result_path": response_path,
        "pack_id": str(updated.get("pack_id") or ""),
        "packet_artifact_id": str(updated.get("packet_artifact_id") or ""),
        "prompt_hash": prompt_hash,
        "next_action": saved.get("next_action", "verify_any_claims_with_canonical_grados_reads"),
        "capture_method": "manual_copy",
    }
    _ensure_external_operation(
        db_path,
        operation_id=session_id,
        pack_id=str(updated.get("pack_id") or ""),
        packet_artifact_id=str(updated.get("packet_artifact_id") or ""),
        prompt_hash=prompt_hash,
        mode=str(updated.get("mode") or "ask"),
        status="pending",
        stage="manual_copy_captured",
        recovery=_external_recovery_metadata(
            recover_session_id=session_id,
            packet_artifact_id=str(updated.get("packet_artifact_id") or ""),
            prompt_hash=prompt_hash,
            browser_session_record=session_record_path,
            conversation_url=_recoverable_conversation_url(updated.get("conversation_url")),
            last_observed_url=_last_observed_conversation_url(updated),
        ),
        result=operation_result,
    )
    if saved.get("saved"):
        complete_operation(
            db_path,
            session_id,
            stage="chatgpt_pro_consult_saved",
            progress={"response_captured": True, "result_saved": True},
            result=operation_result,
        )
    else:
        fail_operation(
            db_path,
            session_id,
            stage="manual_chatgpt_pro_response_save_failed",
            result=operation_result,
            error={"message": str(saved.get("error") or "manual_chatgpt_pro_response_save_failed")},
        )
    return {
        "ok": bool(saved.get("ok")),
        "saved": bool(saved.get("saved")),
        "audited": False,
        "tool_name": "consult_chatgpt_pro",
        "kind": "chatgpt_pro_consult",
        "operation_id": session_id,
        "status": "completed" if saved.get("saved") else "failed",
        "stage": "manual_copy_captured",
        "artifact_id": saved.get("artifact_id", ""),
        "result_artifact_id": saved.get("artifact_id", ""),
        "result_path": response_path,
        "pack_id": str(updated.get("pack_id") or ""),
        "packet_artifact_id": str(updated.get("packet_artifact_id") or ""),
        "prompt_hash": prompt_hash,
        "browser_session_id": session_id,
        "browser_session_record": session_record_path,
        "conversation_url": _recoverable_conversation_url(updated.get("conversation_url")),
        "capture": {"method": "manual_copy", "warnings": warnings},
        "transcript_path": artifact_paths["transcript_path"],
        "assistant_snapshot_path": artifact_paths["assistant_snapshot_path"],
        "result": saved,
        "advisory_only": True,
        "next_action": saved.get("next_action", "verify_any_claims_with_canonical_grados_reads"),
    }


def _mark_external_operation_saved(
    db_path: Path,
    *,
    operation_id: str,
    saved: dict[str, Any],
    record: dict[str, Any],
) -> None:
    from grados.storage.operations import complete_operation

    if not operation_id or not saved.get("saved"):
        return
    complete_operation(
        db_path,
        operation_id,
        stage="external_consult_saved",
        progress={"response_captured": True, "result_saved": True},
        result={
            "artifact_id": str(saved.get("artifact_id") or ""),
            "result_artifact_id": str(saved.get("artifact_id") or ""),
            "result_path": str(record.get("response_path") or ""),
            "pack_id": str(record.get("pack_id") or ""),
            "packet_artifact_id": str(record.get("packet_artifact_id") or ""),
            "prompt_hash": str(record.get("prompt_hash") or ""),
            "next_action": saved.get("next_action", "audit_external_consult_result"),
        },
    )


def _prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _truncate_context_text(text: str, *, max_chars: int = 12_000) -> str:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 80].rstrip() + "\n\n[...truncated by GRaDOS consult context guard...]"


def _artifact_context_section(db_path: Path, artifact_id: str) -> tuple[str, dict[str, Any] | None, str]:
    artifact = _read_artifact(db_path, artifact_id)
    if artifact is None:
        return "", None, f"artifact_not_found:{artifact_id}"
    content = artifact.get("content")
    if isinstance(content, str):
        body = content
    else:
        body = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
    manifest: dict[str, Any] = {
        "artifact_id": artifact_id,
        "kind": str(artifact.get("kind") or ""),
        "title": str(artifact.get("title") or ""),
        "metadata": artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {},
    }
    section = f"Artifact context `{artifact_id}` ({manifest['kind']}):\n{_truncate_context_text(body)}"
    return section, manifest, ""


def _path_context_section(path_value: str) -> tuple[str, dict[str, Any] | None, str]:
    path = Path(path_value).expanduser()
    try:
        resolved = path.resolve()
        if not resolved.is_file():
            return "", None, f"context_path_not_file:{path_value}"
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return "", None, f"context_path_unreadable:{path_value}:{exc.__class__.__name__}"
    manifest = {"path": str(resolved), "sha256": _prompt_sha256(text), "chars": len(text)}
    return f"File context `{resolved}`:\n{_truncate_context_text(text)}", manifest, ""


def _pack_context_section(
    db_path: Path,
    papers_dir: Path,
    pack_id: str,
) -> tuple[str, dict[str, Any] | None, str]:
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return "", None, f"pack_not_found:{pack_id}:{loaded.get('error')}"
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    items = _source_items_from_pack(pack)
    body = {
        "pack_id": pack.pack_id,
        "topic": pack.topic,
        "pack_sha256": pack.pack_sha256,
        "current_valid": bool(verify_result.get("current_valid")),
        "items": items,
    }
    manifest = {
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "current_valid": bool(verify_result.get("current_valid")),
        "item_count": len(items),
    }
    return (
        "GRaDOS evidence pack context:\n"
        + _truncate_context_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)),
        manifest,
        "",
    )


def _packet_context_section(db_path: Path, packet_id: str) -> tuple[str, dict[str, Any] | None, str]:
    artifact = _read_artifact(db_path, packet_id)
    if artifact is None or artifact.get("kind") != EXTERNAL_CONSULT_PACKET_KIND:
        return "", None, f"packet_artifact_not_found:{packet_id}"
    content = artifact.get("content")
    if not isinstance(content, dict):
        return "", None, f"packet_artifact_invalid:{packet_id}"
    manifest = {
        "packet_artifact_id": packet_id,
        "pack_id": str(content.get("pack_id") or ""),
        "prompt_hash": str(content.get("prompt_hash") or ""),
        "item_count": int(content.get("item_count") or 0),
    }
    return (
        "GRaDOS evidence packet context:\n"
        + _truncate_context_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)),
        manifest,
        "",
    )


def _build_consult_context(
    db_path: Path,
    papers_dir: Path,
    *,
    context_artifact_ids: list[str] | None = None,
    context_paths: list[str] | None = None,
    pack_id: str = "",
    packet_id: str = "",
) -> tuple[list[str], dict[str, Any]]:
    sections: list[str] = []
    warnings: list[str] = []
    artifacts: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    packs: list[dict[str, Any]] = []
    packets: list[dict[str, Any]] = []

    for artifact_id in context_artifact_ids or []:
        section, manifest, warning = _artifact_context_section(db_path, str(artifact_id))
        if section:
            sections.append(section)
        if manifest:
            artifacts.append(manifest)
        if warning:
            warnings.append(warning)

    for context_path in context_paths or []:
        section, manifest, warning = _path_context_section(str(context_path))
        if section:
            sections.append(section)
        if manifest:
            paths.append(manifest)
        if warning:
            warnings.append(warning)

    if packet_id:
        section, manifest, warning = _packet_context_section(db_path, packet_id)
        if section:
            sections.append(section)
        if manifest:
            packets.append(manifest)
        if warning:
            warnings.append(warning)

    if pack_id and not packet_id:
        section, manifest, warning = _pack_context_section(db_path, papers_dir, pack_id)
        if section:
            sections.append(section)
        if manifest:
            packs.append(manifest)
        if warning:
            warnings.append(warning)

    manifest = {
        "context_artifact_ids": [item["artifact_id"] for item in artifacts],
        "context_paths": paths,
        "pack_ids": [item["pack_id"] for item in packs],
        "packet_artifact_ids": [item["packet_artifact_id"] for item in packets],
        "artifacts": artifacts,
        "packs": packs,
        "packets": packets,
        "warnings": warnings,
    }
    return sections, manifest


def _render_consult_prompt(
    *,
    prompt: str,
    mode: str,
    context_sections: list[str],
) -> str:
    parts = [
        "System / protocol guardrails:",
        "- You are consulted by GRaDOS through a private ChatGPT Pro browser session.",
        "- Treat all output as advisory review material, not citation evidence.",
        "- Do not invent papers, DOIs, sources, browser state, downloads, or facts outside the supplied context.",
        "- Any claim used later must be verified again through GRaDOS canonical paper reads "
        "or current-valid evidence packs.",
        "",
        f"Consult mode: {mode}",
        "",
        "User prompt:",
        prompt.strip(),
    ]
    if context_sections:
        parts.extend(["", "Optional GRaDOS context:"])
        parts.extend(context_sections)
    parts.extend(
        [
            "",
            "Output request:",
            "Answer the user prompt directly. Mark uncertainty, assumptions, and follow-up verification needs.",
        ]
    )
    return "\n".join(parts).strip() + "\n"


def _save_chatgpt_pro_consult_result(
    db_path: Path,
    *,
    prompt: str,
    rendered_prompt_hash: str,
    response: str | dict[str, Any],
    mode: str,
    context_manifest: dict[str, Any],
    conversation_url: str = "",
    model_label: str = "",
    thinking_label: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _response_text(response)
    response_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    conversation_url = _recoverable_conversation_url(conversation_url)
    raw_metadata = metadata or {}
    content_payload = {
        "schema_version": CHATGPT_PRO_CONSULT_PROTOCOL_VERSION,
        "kind": CHATGPT_PRO_CONSULT_RESULT_KIND,
        "mode": mode,
        "prompt": prompt,
        "prompt_sha256": _prompt_sha256(prompt),
        "rendered_prompt_hash": rendered_prompt_hash,
        "context_manifest": context_manifest,
        "conversation_url": conversation_url,
        "model_label": model_label,
        "thinking_label": thinking_label,
        "raw_response": response,
        "response_text": text,
        "response_sha256": response_hash,
        "advisory_only": True,
        "next_action": "verify_any_claims_with_canonical_grados_reads",
    }
    operation_lookup_sha256 = _operation_lookup_sha256(
        str(raw_metadata.get("browser_session_id") or raw_metadata.get("recover_session_id") or "")
    )
    if operation_lookup_sha256:
        content_payload["operation_lookup_sha256"] = operation_lookup_sha256
    artifact_metadata = {
        **raw_metadata,
        "protocol": CHATGPT_PRO_CONSULT_PROTOCOL_VERSION,
        "rendered_prompt_hash": rendered_prompt_hash,
        "prompt_sha256": _prompt_sha256(prompt),
        "response_sha256": response_hash,
        "mode": mode,
        "model_label": model_label,
        "thinking_label": thinking_label,
        "conversation_url": conversation_url,
    }
    if operation_lookup_sha256:
        artifact_metadata["operation_lookup_sha256"] = operation_lookup_sha256
    receipt = save_research_artifact(
        db_path,
        kind=CHATGPT_PRO_CONSULT_RESULT_KIND,
        title="ChatGPT Pro consult result",
        content=content_payload,
        metadata=artifact_metadata,
    )
    return {
        "ok": True,
        "saved": True,
        "audited": False,
        "artifact_id": receipt["artifact_id"],
        "kind": CHATGPT_PRO_CONSULT_RESULT_KIND,
        "response_sha256": response_hash,
        "advisory_only": True,
        "metadata": receipt["metadata"],
        "next_action": "verify_any_claims_with_canonical_grados_reads",
    }


def _recoverable_browser_result(result: Any) -> bool:
    error_code = str(getattr(result, "error_code", "") or "")
    if error_code == "chatgpt_login_required":
        return _browser_result_has_recovery_handle(result)
    return bool(
        getattr(result, "status", "") == "incomplete_capture" or error_code in {"assistant_timeout", "capture_failed"}
    )


def _browser_result_has_recovery_handle(result: Any) -> bool:
    metadata = getattr(result, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    for candidate in (
        getattr(result, "conversation_url", ""),
        metadata.get("conversation_url"),
        metadata.get("last_observed_url"),
    ):
        if _recoverable_conversation_url(candidate):
            return True
    min_turn_index = metadata.get("min_turn_index")
    if min_turn_index is None:
        return False
    try:
        return int(min_turn_index) >= 0
    except (TypeError, ValueError):
        return False


def _browser_failure_next_action(result: Any) -> str:
    if _recoverable_browser_result(result):
        return "call get_operation_status with detail=true after ChatGPT finishes"
    error_code = str(getattr(result, "error_code", "") or "")
    if error_code == "chatgpt_login_required":
        return "rerun external-consult doctor --live or setup-browser, then retry consult_chatgpt_pro"
    if _chatgpt_model_route_error(error_code):
        return "rerun external-consult doctor --live, inspect model route diagnostics, then retry consult_chatgpt_pro"
    return "inspect browser error and retry when fixed"


def _chatgpt_model_route_error(error_code: str) -> bool:
    return error_code in {"model_picker_unavailable", "model_unavailable", "model_unconfirmed"}


def _chatgpt_session_error(record: dict[str, Any]) -> dict[str, Any]:
    error = record.get("error")
    return error if isinstance(error, dict) else {}


def _chatgpt_session_error_code(record: dict[str, Any]) -> str:
    error = _chatgpt_session_error(record)
    return str(error.get("error") or error.get("code") or "")


def _chatgpt_session_error_stage(record: dict[str, Any]) -> str:
    error = _chatgpt_session_error(record)
    return str(error.get("stage") or "")


def _chatgpt_nonrecoverable_status_error(record: dict[str, Any]) -> tuple[str, str]:
    error_code = _chatgpt_session_error_code(record)
    stage = _chatgpt_session_error_stage(record)
    if _chatgpt_model_route_error(error_code) or stage == "model-selection":
        return (
            "model_route_unavailable",
            "The requested ChatGPT model route was unavailable before prompt submission.",
        )
    return (
        "conversation_url_missing_or_not_recoverable",
        "Saved ChatGPT browser session has no recoverable ChatGPT conversation URL.",
    )


async def _auto_reattach_chatgpt_session(
    paths: GRaDOSPaths,
    browser_config: HeadlessBrowserConfig,
    *,
    deadline: float,
    wait_settings: _ChatGPTProConsultWaitSettings,
    max_reattach_attempts: int,
    session_id: str,
    pack_id: str,
    packet_artifact_id: str,
    prompt_hash: str,
    mode: str,
    metadata: dict[str, Any],
    model_strategy: str,
    thinking_strategy: str,
) -> tuple[Any, list[dict[str, Any]]]:
    from grados.browser.chatgpt.runtime import run_chatgpt_browser_session

    attempts: list[dict[str, Any]] = []
    last_result: Any = None
    for attempt_index in range(1, max_reattach_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait_seconds = min(wait_settings.per_attempt_wait_seconds, remaining)
        browser_result = await run_chatgpt_browser_session(
            paths,
            browser_config,
            prompt="",
            pack_id=pack_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=prompt_hash,
            mode=mode,
            metadata={
                **metadata,
                "auto_reattach_attempt": attempt_index,
                "auto_reattach_max_attempts": max_reattach_attempts,
                "response_wait_total_seconds": wait_settings.response_wait_total_seconds,
                "per_attempt_wait_seconds": wait_settings.per_attempt_wait_seconds,
                "max_browser_wait_attempts": wait_settings.max_browser_wait_attempts,
            },
            recover_session_id=session_id,
            assistant_timeout_seconds=wait_seconds,
            model_strategy=model_strategy,
            thinking_strategy=thinking_strategy,
        )
        attempts.append(
            {
                "attempt": attempt_index,
                "ok": bool(browser_result.ok),
                "status": browser_result.status,
                "error": browser_result.error_code or browser_result.error,
                "conversation_url": browser_result.conversation_url,
                "wait_seconds": wait_seconds,
            }
        )
        last_result = browser_result
        if browser_result.ok or not _recoverable_browser_result(browser_result):
            return browser_result, attempts
        if attempt_index < max_reattach_attempts:
            await asyncio.sleep(DEFAULT_CHATGPT_PRO_CONSULT_REATTACH_SETTLE_SECONDS)
    return last_result, attempts


async def get_external_consult_operation_status(
    db_path: Path,
    papers_dir: Path,
    paths: GRaDOSPaths,
    *,
    operation_id: str,
    detail: bool = False,
    browser_config: HeadlessBrowserConfig | None = None,
    external_consult_config: ExternalConsultConfig | None = None,
) -> dict[str, Any]:
    """Inspect or recover one durable ChatGPT external-consult operation."""
    from grados.browser.chatgpt.session_store import ChatGPTSessionStore, is_valid_chatgpt_session_id

    if not is_valid_chatgpt_session_id(operation_id):
        return {
            "found": False,
            "operation_id": operation_id,
            "kind": "external_consult",
            "status": "not_found",
            "error": "invalid_browser_session_id",
        }
    store = ChatGPTSessionStore(paths.chatgpt_browser_sessions)
    record = store.read(operation_id)
    if not record:
        return {
            "found": False,
            "operation_id": operation_id,
            "kind": "external_consult",
            "status": "not_found",
            "error": "browser_session_not_found",
        }
    session_record_path = str(store.session_json(operation_id))
    record["session_record"] = session_record_path
    prompt_hash = str(record.get("prompt_hash") or "")
    packet_artifact_id = str(record.get("packet_artifact_id") or "")
    existing = _find_external_result_for_session(
        db_path,
        session_id=operation_id,
        prompt_hash=prompt_hash,
        allow_prompt_hash_fallback=False,
    )
    if existing is not None:
        return _external_operation_status_payload(
            operation_id=operation_id,
            record=record,
            result_artifact=existing,
        )

    if detail and str(record.get("status") or "") == "captured":
        saved = _save_captured_session_result(
            db_path,
            papers_dir,
            operation_id=operation_id,
            record=record,
            session_record_path=session_record_path,
        )
        if saved.get("saved"):
            _mark_external_operation_saved(db_path, operation_id=operation_id, saved=saved, record=record)
            existing = query_research_artifacts(
                db_path,
                artifact_id=str(saved.get("artifact_id") or ""),
                detail=True,
                limit=1,
            ).get("items", [None])[0]
            if isinstance(existing, dict):
                return _external_operation_status_payload(
                    operation_id=operation_id,
                    record=store.read(operation_id) or record,
                    result_artifact=existing,
                )
        return _external_operation_status_payload(
            operation_id=operation_id,
            record=record,
            error=str(saved.get("error") or "external_consult_save_failed"),
        )

    conversation_url = _recoverable_conversation_url(record.get("conversation_url"))
    if detail and conversation_url:
        wait_settings = _resolve_chatgpt_pro_consult_wait_settings(external_consult_config)
        probe_wait_settings = _status_recovery_probe_wait_settings(wait_settings)
        record_metadata_value = record.get("metadata")
        record_metadata: dict[str, Any] = record_metadata_value if isinstance(record_metadata_value, dict) else {}
        browser_result, reattach_attempts = await _auto_reattach_chatgpt_session(
            paths,
            browser_config or HeadlessBrowserConfig(),
            deadline=time.monotonic() + probe_wait_settings.response_wait_total_seconds,
            wait_settings=probe_wait_settings,
            max_reattach_attempts=probe_wait_settings.max_browser_wait_attempts,
            session_id=operation_id,
            pack_id=str(record.get("pack_id") or ""),
            packet_artifact_id=str(record.get("packet_artifact_id") or ""),
            prompt_hash=prompt_hash,
            mode=str(record.get("mode") or "review"),
            metadata={
                **record_metadata,
                "recovery_source": "get_operation_status_detail_true",
                "recovery_probe": "short_no_prompt_resend",
                "configured_response_wait_total_seconds": wait_settings.response_wait_total_seconds,
            },
            model_strategy=_normalize_model_strategy(str(record_metadata.get("model_strategy") or "select")),
            thinking_strategy=_normalize_thinking_strategy(str(record_metadata.get("thinking_strategy") or "highest")),
        )
        refreshed = store.read(operation_id) or record
        refreshed["session_record"] = session_record_path
        refreshed["auto_reattach_attempts"] = reattach_attempts
        if browser_result.ok:
            saved = _save_captured_session_result(
                db_path,
                papers_dir,
                operation_id=operation_id,
                record=refreshed,
                session_record_path=session_record_path,
            )
            if saved.get("saved"):
                _mark_external_operation_saved(db_path, operation_id=operation_id, saved=saved, record=refreshed)
                existing = query_research_artifacts(
                    db_path,
                    artifact_id=str(saved.get("artifact_id") or ""),
                    detail=True,
                    limit=1,
                ).get("items", [None])[0]
                if isinstance(existing, dict):
                    return _external_operation_status_payload(
                        operation_id=operation_id,
                        record=refreshed,
                        result_artifact=existing,
                    )
            return _external_operation_status_payload(
                operation_id=operation_id,
                record=refreshed,
                error=str(saved.get("error") or "external_consult_save_failed"),
            )
        return _external_operation_status_payload(
            operation_id=operation_id,
            record=refreshed,
            error=browser_result.error_code or browser_result.error,
        )

    if detail:
        from grados.storage.operations import update_operation

        error_code, error_message = _chatgpt_nonrecoverable_status_error(record)
        recovery_metadata = _external_recovery_metadata(
            recover_session_id=operation_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=prompt_hash,
            browser_session_record=session_record_path,
            conversation_url="",
            last_observed_url=_last_observed_conversation_url(record),
            next_action=(
                "rerun_external_consult_doctor_live_after_model_route_fix"
                if error_code == "model_route_unavailable"
                else "inspect_browser_session_record_and_retry_after_fixing_error"
            ),
        )
        session_status = str(record.get("status") or "unknown")
        update_operation(
            db_path,
            operation_id,
            status="failed" if session_status == "failed" else "pending",
            stage=session_status,
            recovery=recovery_metadata,
            error={
                "error": error_code,
                "message": error_message,
            },
            event_type="external_consult_recovery_unavailable",
            event_payload=recovery_metadata,
        )
        return _external_operation_status_payload(
            operation_id=operation_id,
            record=record,
            error=error_code,
        )

    return _external_operation_status_payload(operation_id=operation_id, record=record)


async def consult_chatgpt_pro(
    db_path: Path,
    papers_dir: Path,
    paths: GRaDOSPaths,
    *,
    prompt: str,
    context_artifact_ids: list[str] | None = None,
    context_paths: list[str] | None = None,
    pack_id: str = "",
    packet_id: str = "",
    mode: str = "ask",
    model_strategy: str = "select",
    thinking_strategy: str = "highest",
    wait_policy: str = "auto",
    manual_response: str = "",
    metadata: dict[str, Any] | None = None,
    recover_session_id: str = "",
    browser_config: HeadlessBrowserConfig | None = None,
    external_consult_config: ExternalConsultConfig | None = None,
) -> dict[str, Any]:
    """Consult ChatGPT Pro through the GRaDOS-managed private browser profile."""
    from grados.browser.chatgpt.runtime import run_chatgpt_browser_session
    from grados.storage.operations import complete_operation, fail_operation

    resolved_mode = _normalize_consult_mode(mode)
    resolved_model_strategy = _normalize_model_strategy(model_strategy)
    resolved_thinking_strategy = _normalize_thinking_strategy(thinking_strategy)
    resolved_wait_policy = _normalize_wait_policy(wait_policy)
    wait_settings = _resolve_chatgpt_pro_consult_wait_settings(external_consult_config)
    source_pack_id = pack_id.strip()
    packet_artifact_id = packet_id.strip()
    auto_reattach_attempts: list[dict[str, Any]] = []

    if not recover_session_id and not prompt.strip():
        return {
            "ok": False,
            "saved": False,
            "sendable": False,
            "error": "consult_prompt_required",
            "message": "`prompt` is required for consult_chatgpt_pro.",
        }

    if manual_response.strip():
        if not recover_session_id:
            return {
                "ok": False,
                "saved": False,
                "sendable": False,
                "error": "manual_response_requires_recover_session_id",
                "message": "`manual_response` must be paired with a ChatGPT `recover_session_id`.",
            }
        return _save_manual_chatgpt_pro_response(
            db_path,
            papers_dir,
            paths,
            session_id=recover_session_id,
            response_text=manual_response,
        )

    context_sections: list[str] = []
    context_manifest: dict[str, Any] = {}
    rendered_prompt = ""
    rendered_prompt_hash = ""
    metadata_payload = dict(metadata or {})
    browser_result: Any = None
    if recover_session_id:
        rendered_prompt_hash = str(metadata_payload.get("rendered_prompt_hash") or "")
        context_manifest = dict(metadata_payload.get("context_manifest") or {})
        browser_result, auto_reattach_attempts = await _auto_reattach_chatgpt_session(
            paths,
            browser_config or HeadlessBrowserConfig(),
            deadline=time.monotonic() + wait_settings.response_wait_total_seconds,
            wait_settings=wait_settings,
            max_reattach_attempts=wait_settings.max_browser_wait_attempts,
            session_id=recover_session_id,
            pack_id=source_pack_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=rendered_prompt_hash,
            mode=resolved_mode,
            metadata={
                **metadata_payload,
                "tool_name": "consult_chatgpt_pro",
                "recovery_source": "consult_chatgpt_pro",
            },
            model_strategy=resolved_model_strategy,
            thinking_strategy=resolved_thinking_strategy,
        )
    else:
        context_sections, context_manifest = _build_consult_context(
            db_path,
            papers_dir,
            context_artifact_ids=context_artifact_ids,
            context_paths=context_paths,
            pack_id=source_pack_id,
            packet_id=packet_artifact_id,
        )
        if not source_pack_id and context_manifest.get("packets"):
            first_packet = context_manifest["packets"][0]
            if isinstance(first_packet, dict):
                source_pack_id = str(first_packet.get("pack_id") or "")
        rendered_prompt = _render_consult_prompt(
            prompt=prompt,
            mode=resolved_mode,
            context_sections=context_sections,
        )
        rendered_prompt_hash = _prompt_sha256(rendered_prompt)
        metadata_payload = {
            **metadata_payload,
            "tool_name": "consult_chatgpt_pro",
            "consult_mode": resolved_mode,
            "prompt_sha256": _prompt_sha256(prompt),
            "rendered_prompt_hash": rendered_prompt_hash,
            "context_manifest": context_manifest,
            "model_strategy": resolved_model_strategy,
            "thinking_strategy": resolved_thinking_strategy,
            "wait_policy": resolved_wait_policy,
            "response_wait_total_seconds": wait_settings.response_wait_total_seconds,
            "per_attempt_wait_seconds": wait_settings.per_attempt_wait_seconds,
            "max_browser_wait_attempts": wait_settings.max_browser_wait_attempts,
            "max_reattach_attempts": wait_settings.max_reattach_after_initial,
        }
        response_wait_deadline = time.monotonic() + wait_settings.response_wait_total_seconds
        browser_result = await run_chatgpt_browser_session(
            paths,
            browser_config or HeadlessBrowserConfig(),
            prompt=rendered_prompt,
            pack_id=source_pack_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=rendered_prompt_hash,
            mode=resolved_mode,
            metadata=metadata_payload,
            assistant_timeout_seconds=wait_settings.initial_wait_seconds,
            model_strategy=resolved_model_strategy,
            thinking_strategy=resolved_thinking_strategy,
        )
        if (
            resolved_wait_policy == "auto"
            and not browser_result.ok
            and _recoverable_browser_result(browser_result)
            and wait_settings.max_reattach_after_initial > 0
        ):
            reattached_result, auto_reattach_attempts = await _auto_reattach_chatgpt_session(
                paths,
                browser_config or HeadlessBrowserConfig(),
                deadline=response_wait_deadline,
                wait_settings=wait_settings,
                max_reattach_attempts=wait_settings.max_reattach_after_initial,
                session_id=browser_result.session_id,
                pack_id=source_pack_id,
                packet_artifact_id=packet_artifact_id,
                prompt_hash=rendered_prompt_hash,
                mode=resolved_mode,
                metadata=metadata_payload,
                model_strategy=resolved_model_strategy,
                thinking_strategy=resolved_thinking_strategy,
            )
            if reattached_result is not None:
                browser_result = reattached_result

    if browser_result is None:
        return {
            "ok": False,
            "saved": False,
            "sendable": False,
            "error": "chatgpt_consult_recovery_unavailable",
        }

    browser_payload = browser_result.to_dict()
    source_pack_id = str(browser_result.metadata.get("pack_id") or source_pack_id)
    packet_artifact_id = str(browser_result.metadata.get("packet_artifact_id") or packet_artifact_id)
    rendered_prompt_hash = str(browser_result.metadata.get("prompt_hash") or rendered_prompt_hash)
    last_observed_url = str(browser_payload.get("metadata", {}).get("last_observed_url") or "")
    failure_next_action = _browser_failure_next_action(browser_result)
    recovery_metadata = _external_recovery_metadata(
        recover_session_id=browser_result.session_id,
        packet_artifact_id=packet_artifact_id,
        prompt_hash=rendered_prompt_hash,
        browser_session_record=browser_result.session_record_path,
        conversation_url=browser_result.conversation_url,
        last_observed_url=last_observed_url,
        next_action=failure_next_action,
    )

    if not browser_result.ok:
        recoverable = _recoverable_browser_result(browser_result)
        operation_status = "pending" if recoverable else "failed"
        failure_kind = (
            "model_route_unavailable"
            if _chatgpt_model_route_error(browser_result.error_code)
            else "pre_submit_failure"
            if browser_result.status == "failed" and not _browser_result_has_recovery_handle(browser_result)
            else "browser_failure"
        )
        manual_copy_available = recoverable and _browser_result_has_recovery_handle(browser_result)
        _ensure_external_operation(
            db_path,
            operation_id=browser_result.session_id,
            pack_id=source_pack_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=rendered_prompt_hash,
            mode=resolved_mode,
            status=operation_status,
            stage=browser_result.status,
            recovery=recovery_metadata,
            result={"result_path": "", "result_artifact_id": "", "next_action": failure_next_action},
            error={"error": browser_result.error_code or "chatgpt_browser_failed", "message": browser_result.error},
        )
        return {
            "ok": False,
            "sendable": True,
            "saved": False,
            "audited": False,
            "tool_name": "consult_chatgpt_pro",
            "kind": "chatgpt_pro_consult",
            "operation_id": browser_result.session_id,
            "status": operation_status,
            "stage": browser_result.status,
            "failure_kind": failure_kind,
            "pre_submit_failure": failure_kind in {"model_route_unavailable", "pre_submit_failure"},
            "recoverable": recoverable,
            "browser_session_id": browser_result.session_id,
            "browser_session_record": browser_result.session_record_path,
            "conversation_url": browser_result.conversation_url,
            "last_observed_url": last_observed_url,
            "packet_artifact_id": packet_artifact_id,
            "pack_id": source_pack_id,
            "prompt_hash": rendered_prompt_hash,
            "context_manifest": context_manifest,
            "auto_reattach_attempts": auto_reattach_attempts,
            "error": browser_result.error_code or "chatgpt_browser_failed",
            "message": browser_result.error,
            "next_action": failure_next_action,
            "recovery_metadata": recovery_metadata,
            "manual_copy_fallback": {
                "available": manual_copy_available,
                "recover_session_id": browser_result.session_id,
                "browser_session_record": browser_result.session_record_path,
                "conversation_url": browser_result.conversation_url,
                "last_observed_url": last_observed_url,
                "save_entry": "consult_chatgpt_pro",
                "response_field": "manual_response",
                "next_action": (
                    "copy the final assistant response manually, then call consult_chatgpt_pro "
                    "with recover_session_id and manual_response"
                ),
            },
            "browser": browser_payload,
        }

    save_metadata = {
        **metadata_payload,
        "runtime": "grados_chatgpt_browser",
        "tool_name": "consult_chatgpt_pro",
        "browser_mode_version": browser_payload.get("browser_mode_version"),
        "browser_session_id": browser_result.session_id,
        "browser_session_record": browser_result.session_record_path,
        "conversation_url": browser_result.conversation_url,
        "model_strategy": resolved_model_strategy,
        "model_selection": browser_payload.get("model"),
        "thinking_strategy": resolved_thinking_strategy,
        "thinking_selection": browser_payload.get("thinking"),
        "capture": browser_payload.get("capture"),
        "auto_reattach_attempts": auto_reattach_attempts,
    }
    if source_pack_id:
        structured_response = _structured_response_from_text(browser_result.response_text)
        structured_claims = None
        structured_gaps = None
        if isinstance(structured_response, dict):
            raw_claims = structured_response.get("claims")
            if isinstance(raw_claims, list):
                structured_claims = [dict(item) for item in raw_claims if isinstance(item, dict)]
            structured_gaps = _coerce_string_list(
                structured_response.get("missing_evidence") or structured_response.get("gaps")
            )
        saved = save_external_consult_result(
            db_path,
            papers_dir,
            pack_id=source_pack_id,
            response=browser_result.response_text,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=rendered_prompt_hash,
            conversation_url=browser_result.conversation_url,
            model_label=browser_result.model_label,
            thinking_label=browser_result.thinking_label,
            mode=_external_result_mode(resolved_mode),
            claims=structured_claims,
            gaps=structured_gaps,
            metadata=save_metadata,
            audit=False,
        )
    else:
        saved = _save_chatgpt_pro_consult_result(
            db_path,
            prompt=prompt,
            rendered_prompt_hash=rendered_prompt_hash,
            response=browser_result.response_text,
            mode=resolved_mode,
            context_manifest=context_manifest,
            conversation_url=browser_result.conversation_url,
            model_label=browser_result.model_label,
            thinking_label=browser_result.thinking_label,
            metadata=save_metadata,
        )

    operation_result = {
        "artifact_id": str(saved.get("artifact_id") or ""),
        "result_artifact_id": str(saved.get("artifact_id") or ""),
        "result_path": str(browser_payload.get("metadata", {}).get("response_path", "")),
        "pack_id": source_pack_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": rendered_prompt_hash,
        "next_action": saved.get("next_action", "verify_any_claims_with_canonical_grados_reads"),
    }
    _ensure_external_operation(
        db_path,
        operation_id=browser_result.session_id,
        pack_id=source_pack_id,
        packet_artifact_id=packet_artifact_id,
        prompt_hash=rendered_prompt_hash,
        mode=resolved_mode,
        status="pending",
        stage="captured",
        recovery=recovery_metadata,
        result=operation_result,
    )
    if saved.get("saved"):
        complete_operation(
            db_path,
            browser_result.session_id,
            stage="chatgpt_pro_consult_saved",
            progress={"response_captured": True, "result_saved": True},
            result=operation_result,
        )
    else:
        fail_operation(
            db_path,
            browser_result.session_id,
            stage="chatgpt_pro_consult_save_failed",
            result=operation_result,
            error={"message": str(saved.get("error") or "chatgpt_pro_consult_save_failed")},
        )
    return {
        "ok": bool(saved.get("ok")),
        "sendable": True,
        "saved": bool(saved.get("saved")),
        "audited": False,
        "tool_name": "consult_chatgpt_pro",
        "kind": "chatgpt_pro_consult",
        "operation_id": browser_result.session_id,
        "status": "completed" if saved.get("saved") else "failed",
        "stage": "captured",
        "artifact_id": saved.get("artifact_id", ""),
        "result_artifact_id": saved.get("artifact_id", ""),
        "result_path": str(browser_payload.get("metadata", {}).get("response_path", "")),
        "pack_id": source_pack_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": rendered_prompt_hash,
        "context_manifest": context_manifest,
        "browser_session_id": browser_result.session_id,
        "conversation_url": browser_result.conversation_url,
        "model_label": browser_result.model_label,
        "thinking_label": browser_result.thinking_label,
        "auto_reattach_attempts": auto_reattach_attempts,
        "browser": browser_payload,
        "result": saved,
        "advisory_only": True,
        "next_action": saved.get("next_action", "verify_any_claims_with_canonical_grados_reads"),
    }


async def run_external_consult(
    chroma_dir: Path,
    db_path: Path,
    papers_dir: Path,
    paths: GRaDOSPaths,
    *,
    topic: str = "",
    pack_id: str = "",
    subquestions: list[str] | None = None,
    scoped_dois: list[str] | None = None,
    evidence_max_windows: int = 8,
    mode: str = "review",
    max_items: int = 25,
    max_excerpt_chars: int = 700,
    metadata: dict[str, Any] | None = None,
    recover_session_id: str = "",
    browser_config: HeadlessBrowserConfig | None = None,
    external_consult_config: ExternalConsultConfig | None = None,
) -> dict[str, Any]:
    """Prepare topic/pack packet context, then consult ChatGPT Pro."""
    resolved_mode = _normalize_mode(mode)
    source_pack_id = pack_id.strip()
    if recover_session_id:
        result = await consult_chatgpt_pro(
            db_path,
            papers_dir,
            paths,
            prompt="",
            pack_id=source_pack_id,
            mode=resolved_mode,
            metadata={**(metadata or {}), "route": "run_external_consult"},
            recover_session_id=recover_session_id,
            browser_config=browser_config,
            external_consult_config=external_consult_config,
        )
        return {
            **result,
            "route": "run_external_consult",
            "browser_route": "consult_chatgpt_pro",
        }

    if bool(topic.strip()) == bool(source_pack_id):
        return {
            "ok": False,
            "sendable": False,
            "saved": False,
            "error": "invalid_external_consult_input",
            "message": "Provide exactly one of topic or pack_id.",
        }
    if topic.strip():
        packet = prepare_external_consult_from_topic(
            chroma_dir,
            db_path,
            papers_dir,
            topic=topic,
            subquestions=subquestions,
            scoped_dois=scoped_dois,
            evidence_max_windows=evidence_max_windows,
            mode=resolved_mode,
            max_items=max_items,
            max_excerpt_chars=max_excerpt_chars,
            metadata=metadata,
        )
    else:
        packet = prepare_external_consult_packet(
            db_path,
            papers_dir,
            pack_id=source_pack_id,
            mode=resolved_mode,
            max_items=max_items,
            max_excerpt_chars=max_excerpt_chars,
            metadata=metadata,
        )
    if not packet.get("sendable"):
        return packet
    packet_artifact_id = str(packet.get("artifact_id") or "")
    result = await consult_chatgpt_pro(
        db_path,
        papers_dir,
        paths,
        prompt=(
            "Review this GRaDOS evidence packet as advisory material. "
            "Focus on gaps, distortions, unsupported claims, and canonical reread needs."
            if resolved_mode == "review"
            else "Synthesize this GRaDOS evidence packet as advisory material and mark verification needs."
        ),
        pack_id=str(packet.get("pack_id") or source_pack_id),
        packet_id=packet_artifact_id,
        mode=resolved_mode,
        metadata={
            **(metadata or {}),
            "route": "run_external_consult",
            "packet_artifact_id": packet_artifact_id,
            "packet_prompt_hash": str(packet.get("prompt_hash") or ""),
        },
        browser_config=browser_config,
        external_consult_config=external_consult_config,
    )
    return {
        **result,
        "packet_sendable": bool(packet.get("sendable")),
        "packet": packet,
        "route": "run_external_consult",
        "browser_route": "consult_chatgpt_pro",
    }


def _read_artifact(db_path: Path, artifact_id: str) -> dict[str, Any] | None:
    result = query_research_artifacts(db_path, artifact_id=artifact_id, detail=True, limit=1)
    items = result.get("items", [])
    if not isinstance(items, list) or not items:
        return None
    item = items[0]
    return dict(item) if isinstance(item, dict) else None


def _response_text(raw_response: str | dict[str, Any]) -> str:
    if isinstance(raw_response, str):
        return raw_response
    return json.dumps(raw_response, ensure_ascii=False, indent=2, sort_keys=True)


def _structured_response_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    candidates = [stripped]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def save_external_consult_result(
    db_path: Path,
    papers_dir: Path,
    *,
    pack_id: str,
    response: str | dict[str, Any],
    packet_artifact_id: str = "",
    prompt_hash: str = "",
    conversation_url: str = "",
    model_label: str = "",
    thinking_label: str = "",
    mode: str = "review",
    claims: list[dict[str, Any]] | None = None,
    gaps: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    audit: bool = True,
) -> dict[str, Any]:
    """Persist a host-provided ChatGPT Pro response as advisory research state."""
    resolved_mode = _normalize_mode(mode)
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return {
            "ok": False,
            "saved": False,
            "pack_id": pack_id,
            "error": str(loaded.get("error", "pack_not_found")),
        }
    packet_content: dict[str, Any] | None = None
    if packet_artifact_id:
        packet_artifact = _read_artifact(db_path, packet_artifact_id)
        if packet_artifact is None or packet_artifact.get("kind") != EXTERNAL_CONSULT_PACKET_KIND:
            return {
                "ok": False,
                "saved": False,
                "pack_id": pack.pack_id,
                "error": "packet_artifact_not_found",
            }
        content = packet_artifact.get("content")
        if not isinstance(content, dict) or content.get("pack_id") != pack.pack_id:
            return {
                "ok": False,
                "saved": False,
                "pack_id": pack.pack_id,
                "error": "packet_artifact_pack_mismatch",
            }
        packet_content = content
        prompt_hash = prompt_hash or str(content.get("prompt_hash", "") or "")
    verify_result = verify_evidence_pack(db_path, papers_dir, pack_id=pack.pack_id)
    text = _response_text(response)
    response_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    conversation_url = _recoverable_conversation_url(conversation_url)
    raw_metadata = metadata or {}
    operation_lookup_sha256 = _operation_lookup_sha256(
        str(raw_metadata.get("browser_session_id") or raw_metadata.get("recover_session_id") or "")
    )
    structured_claims = claims
    if structured_claims is None and isinstance(response, dict):
        response_claims = response.get("claims")
        if isinstance(response_claims, list):
            structured_claims = [dict(item) for item in response_claims if isinstance(item, dict)]
    structured_gaps = gaps
    if structured_gaps is None and isinstance(response, dict):
        structured_gaps = _coerce_string_list(response.get("gaps") or response.get("missing_evidence"))
    content_payload: dict[str, Any] = {
        "schema_version": EXTERNAL_CONSULT_PROTOCOL_VERSION,
        "kind": EXTERNAL_CONSULT_RESULT_KIND,
        "mode": resolved_mode,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "conversation_url": conversation_url,
        "model_label": model_label,
        "thinking_label": thinking_label,
        "raw_response": response,
        "response_text": text,
        "response_sha256": response_hash,
        "claims": structured_claims or [],
        "gaps": structured_gaps or [],
        "verify_at_save": verify_result,
        "advisory_only": True,
        "next_action": "audit_external_consult_result",
    }
    if operation_lookup_sha256:
        content_payload["operation_lookup_sha256"] = operation_lookup_sha256
    if packet_content is not None:
        content_payload["packet"] = {
            "artifact_id": packet_artifact_id,
            "prompt_hash": prompt_hash,
            "item_count": packet_content.get("item_count"),
        }
    artifact_metadata = {
        **raw_metadata,
        "protocol": EXTERNAL_CONSULT_PROTOCOL_VERSION,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "response_sha256": response_hash,
        "mode": resolved_mode,
        "model_label": model_label,
        "thinking_label": thinking_label,
        "conversation_url": conversation_url,
    }
    if operation_lookup_sha256:
        artifact_metadata["operation_lookup_sha256"] = operation_lookup_sha256
    receipt = save_research_artifact(
        db_path,
        kind=EXTERNAL_CONSULT_RESULT_KIND,
        title=f"External consult result: {pack.topic or pack.pack_id}",
        content=content_payload,
        metadata=artifact_metadata,
    )
    result: dict[str, Any] = {
        "ok": True,
        "saved": True,
        "audited": False,
        "artifact_id": receipt["artifact_id"],
        "kind": EXTERNAL_CONSULT_RESULT_KIND,
        "pack_id": pack.pack_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "response_sha256": response_hash,
        "verify": verify_result,
        "next_action": "audit_external_consult_result",
        "metadata": receipt["metadata"],
    }
    if audit:
        audit_result = audit_external_consult_result(
            db_path,
            papers_dir,
            result_id=str(receipt["artifact_id"]),
        )
        result["audited"] = True
        result["audit"] = audit_result
        result["ready_for_canonical_reread"] = bool(audit_result.get("ready_for_canonical_reread"))
        result["next_action"] = str(audit_result.get("next_action") or result["next_action"])
    return result


def _allowed_refs_from_pack(pack: EvidencePack) -> dict[str, set[str]]:
    anchor_ids = {_anchor_id(index) for index, _ in enumerate(pack.evidence_items, 1)}
    return {
        "anchor_ids": anchor_ids,
        "block_ids": {item.block_id for item in pack.evidence_items if item.block_id},
        "canonical_uris": {item.canonical_uri for item in pack.evidence_items if item.canonical_uri},
        "dois": {item.doi.lower() for item in pack.evidence_items if item.doi},
    }


def _source_items_from_pack(pack: EvidencePack) -> list[dict[str, Any]]:
    return [
        _packet_item(item, index=index, max_excerpt_chars=2000) for index, item in enumerate(pack.evidence_items, 1)
    ]


def _packet_items(packet_content: dict[str, Any]) -> list[dict[str, Any]]:
    items = packet_content.get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _allowed_refs_from_items(items: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {
        "anchor_ids": {str(item.get("anchor_id")) for item in items if item.get("anchor_id")},
        "block_ids": {str(item.get("block_id")) for item in items if item.get("block_id")},
        "canonical_uris": {str(item.get("canonical_uri")) for item in items if item.get("canonical_uri")},
        "dois": {str(item.get("doi")).lower() for item in items if item.get("doi")},
    }


def _response_payload_for_audit(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw_response": content.get("raw_response"),
        "response_text": content.get("response_text"),
        "claims": content.get("claims"),
        "gaps": content.get("gaps"),
    }


def _collect_anchor_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(match.group(0) for match in _ANCHOR_ID_PATTERN.finditer(value))
    elif isinstance(value, dict):
        for nested in value.values():
            refs.update(_collect_anchor_refs(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.update(_collect_anchor_refs(nested))
    return refs


def _collect_pattern_refs(value: Any, pattern: re.Pattern[str]) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(match.group(0).rstrip(".,;:)]}>\"'") for match in pattern.finditer(value))
    elif isinstance(value, dict):
        for nested in value.values():
            refs.update(_collect_pattern_refs(nested, pattern))
    elif isinstance(value, list):
        for nested in value:
            refs.update(_collect_pattern_refs(nested, pattern))
    return {ref for ref in refs if ref}


def _extract_dois(text: str) -> set[str]:
    dois: set[str] = set()
    for match in _DOI_PATTERN.finditer(text):
        doi = match.group(0).rstrip(".,;:)]}>\"'").lower()
        dois.add(doi)
    return dois


def _collect_dois(value: Any) -> set[str]:
    return {doi.lower() for doi in _collect_pattern_refs(value, _DOI_PATTERN)}


def _normalize_token(token: str) -> str:
    token = token.lower().strip()
    for suffix in ("ingly", "edly", "ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _normalize_token(match.group(0)) for match in _WORD_PATTERN.finditer(text.lower()) if len(match.group(0)) >= 2
    }


def _overlap_score(claim_text: str, evidence_text: str) -> float:
    claim_tokens = _tokens(claim_text)
    evidence_tokens = _tokens(evidence_text)
    if not claim_tokens or not evidence_tokens:
        return 0.0
    overlap = len(claim_tokens & evidence_tokens)
    return overlap / math.sqrt(len(claim_tokens) * len(evidence_tokens))


def _anchor_ids_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(sorted(_collect_anchor_refs(item)))
            if isinstance(item, str) and _ANCHOR_ID_PATTERN.fullmatch(item.strip()):
                refs.append(item.strip())
        return sorted(set(refs))
    if isinstance(value, str):
        return sorted(_collect_anchor_refs(value))
    return []


def _structured_claim_inputs(content: dict[str, Any]) -> list[dict[str, Any]]:
    claims = content.get("claims")
    if (not isinstance(claims, list) or not claims) and isinstance(content.get("raw_response"), dict):
        raw_claims = content["raw_response"].get("claims")
        if isinstance(raw_claims, list):
            claims = raw_claims
    if not isinstance(claims, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in claims:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("claim") or "").strip()
            anchor_ids = _anchor_ids_from_value(item.get("anchor_ids"))
            normalized.append({"text": text, "anchor_ids": anchor_ids, "raw": dict(item)})
        elif isinstance(item, str):
            normalized.append({"text": item.strip(), "anchor_ids": [], "raw": item})
    return normalized


def _audit_structured_claims(
    content: dict[str, Any],
    *,
    allowed: dict[str, set[str]],
    source_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items_by_anchor = {str(item.get("anchor_id")): item for item in source_items if item.get("anchor_id")}
    audited: list[dict[str, Any]] = []
    for index, claim in enumerate(_structured_claim_inputs(content), 1):
        text = str(claim.get("text") or "").strip()
        anchor_ids = [str(anchor_id) for anchor_id in claim.get("anchor_ids", [])]
        unknown_anchor_ids = sorted(set(anchor_ids) - allowed["anchor_ids"])
        scored: list[tuple[str, float]] = []
        for anchor_id in sorted(set(anchor_ids) - set(unknown_anchor_ids)):
            item = items_by_anchor.get(anchor_id)
            if not item:
                continue
            support_text = " ".join(
                str(item.get(key) or "") for key in ("candidate_claim", "short_excerpt", "title", "heading_path")
            )
            scored.append((anchor_id, _overlap_score(text, support_text)))
        best_anchor_id = ""
        best_score = 0.0
        if scored:
            best_anchor_id, best_score = max(scored, key=lambda pair: pair[1])
        supporting_anchor_ids = sorted(anchor_id for anchor_id, score in scored if score >= 0.18)

        if not text:
            verdict = VERDICT_UNVERIFIABLE
            severity = "blocking"
            issue_type = "missing_claim_text"
            revision_action = "copy_structured_claim_text"
            mismatch_detail = "The structured claim has anchor ids but no claim text to audit."
        elif not anchor_ids:
            verdict = VERDICT_UNVERIFIABLE
            severity = "blocking"
            issue_type = "missing_anchor_ids"
            revision_action = "add_packet_anchor_ids"
            mismatch_detail = "Structured claims must carry anchor_ids from the saved packet."
        elif unknown_anchor_ids:
            verdict = VERDICT_MAJOR_DISTORTION
            severity = "major"
            issue_type = "unknown_anchor_ids"
            revision_action = "remove_or_replace_unknown_anchors"
            mismatch_detail = "The claim cites anchors outside the audit reference scope."
        elif not scored:
            verdict = VERDICT_UNVERIFIABLE
            severity = "blocking"
            issue_type = "no_supporting_packet_item"
            revision_action = "reprepare_packet_or_add_locator"
            mismatch_detail = "The cited anchors could not be resolved to packet items."
        elif not supporting_anchor_ids:
            verdict = VERDICT_UNVERIFIABLE
            severity = "blocking"
            issue_type = "anchor_text_mismatch"
            revision_action = "revise_claim_or_anchor_ids"
            mismatch_detail = "The claim text has too little overlap with its cited packet anchors."
        else:
            verdict = VERDICT_VERIFIED
            severity = "none"
            issue_type = ""
            revision_action = "reread_canonical_window"
            mismatch_detail = ""

        audited.append(
            {
                "claim_id": f"external_claim_{index}",
                "text": text,
                "anchor_ids": anchor_ids,
                "unknown_anchor_ids": unknown_anchor_ids,
                "supporting_anchor_ids": supporting_anchor_ids,
                "best_anchor_id": best_anchor_id,
                "verdict": verdict,
                "severity": severity,
                "issue_type": issue_type,
                "revision_action": revision_action,
                "mismatch_detail": mismatch_detail,
                "confidence": round(float(best_score), 6),
                "requires_canonical_reread": True,
            }
        )
    return audited


def audit_external_consult_result(
    db_path: Path,
    papers_dir: Path,
    *,
    result_id: str,
    strict: bool = True,
    citation_style: str = "author_year",
) -> dict[str, Any]:
    """Audit a saved ChatGPT Pro result against its linked packet or source pack."""
    artifact = _read_artifact(db_path, result_id)
    if artifact is None:
        return {"ok": False, "result_id": result_id, "error": "result_not_found"}
    if artifact.get("kind") != EXTERNAL_CONSULT_RESULT_KIND:
        return {
            "ok": False,
            "result_id": result_id,
            "error": "artifact_is_not_external_consult_result",
            "kind": artifact.get("kind", ""),
        }
    content = artifact.get("content")
    if not isinstance(content, dict):
        return {"ok": False, "result_id": result_id, "error": "invalid_result_content"}
    pack_id = str(content.get("pack_id") or "")
    pack, loaded = _load_pack(db_path, pack_id)
    if pack is None:
        return {
            "ok": False,
            "result_id": result_id,
            "pack_id": pack_id,
            "error": str(loaded.get("error", "pack_not_found")),
        }
    response_text = str(content.get("response_text") or _response_text(content.get("raw_response", "")))
    packet_artifact_id = str(content.get("packet_artifact_id") or "")
    packet_content: dict[str, Any] | None = None
    if packet_artifact_id:
        packet_artifact = _read_artifact(db_path, packet_artifact_id)
        if packet_artifact is None or packet_artifact.get("kind") != EXTERNAL_CONSULT_PACKET_KIND:
            return {
                "ok": False,
                "result_id": result_id,
                "pack_id": pack.pack_id,
                "packet_artifact_id": packet_artifact_id,
                "error": "packet_artifact_not_found",
                "ready_for_canonical_reread": False,
            }
        loaded_packet_content = packet_artifact.get("content")
        if not isinstance(loaded_packet_content, dict) or loaded_packet_content.get("pack_id") != pack.pack_id:
            return {
                "ok": False,
                "result_id": result_id,
                "pack_id": pack.pack_id,
                "packet_artifact_id": packet_artifact_id,
                "error": "packet_artifact_pack_mismatch",
                "ready_for_canonical_reread": False,
            }
        packet_pack_sha = str(loaded_packet_content.get("pack_sha256") or "")
        if packet_pack_sha and packet_pack_sha != pack.pack_sha256:
            return {
                "ok": False,
                "result_id": result_id,
                "pack_id": pack.pack_id,
                "packet_artifact_id": packet_artifact_id,
                "error": "packet_artifact_pack_sha_mismatch",
                "ready_for_canonical_reread": False,
            }
        packet_content = loaded_packet_content

    if packet_content is not None:
        source_items = _packet_items(packet_content)
        allowed = _allowed_refs_from_items(source_items)
        reference_scope = "packet"
    else:
        source_items = _source_items_from_pack(pack)
        allowed = _allowed_refs_from_pack(pack)
        reference_scope = "pack"

    response_payload = _response_payload_for_audit(content)
    referenced_anchor_ids = _collect_anchor_refs(response_payload)
    unknown_anchor_ids = sorted(referenced_anchor_ids - allowed["anchor_ids"])
    referenced_block_ids = _collect_pattern_refs(response_payload, _BLOCK_ID_PATTERN)
    unknown_block_ids = sorted(referenced_block_ids - allowed["block_ids"])
    referenced_canonical_uris = _collect_pattern_refs(response_payload, _CANONICAL_URI_PATTERN)
    unknown_canonical_uris = sorted(referenced_canonical_uris - allowed["canonical_uris"])
    referenced_dois = _collect_dois(response_payload)
    outside_dois = sorted(referenced_dois - allowed["dois"])
    structured_claims = _audit_structured_claims(
        content,
        allowed=allowed,
        source_items=source_items,
    )
    pack_audit = audit_answer_against_pack(
        db_path,
        papers_dir,
        pack_id=pack.pack_id,
        draft=response_text,
        strict=strict,
        citation_style=citation_style,
        return_claim_map=True,
    )
    claims = [claim for claim in pack_audit.get("claims", []) if isinstance(claim, dict)]
    prose_usable_claim_ids = [str(claim.get("claim_id")) for claim in claims if claim.get("verdict") == "verified"]
    prose_claims_requiring_revision = [
        {
            "claim_id": str(claim.get("claim_id")),
            "verdict": str(claim.get("verdict")),
            "issue_type": str(claim.get("issue_type")),
            "revision_action": str(claim.get("revision_action")),
        }
        for claim in claims
        if claim.get("verdict") != "verified"
    ]
    structured_claims_requiring_revision = [
        {
            "claim_id": str(claim.get("claim_id")),
            "verdict": str(claim.get("verdict")),
            "issue_type": str(claim.get("issue_type")),
            "revision_action": str(claim.get("revision_action")),
        }
        for claim in structured_claims
        if claim.get("verdict") != VERDICT_VERIFIED
    ]
    structured_usable_claim_ids = [
        str(claim.get("claim_id")) for claim in structured_claims if claim.get("verdict") == VERDICT_VERIFIED
    ]
    verdict_counts = pack_audit.get("verdict_counts", {})
    prose_non_verified = sum(
        int(count)
        for verdict, count in (verdict_counts.items() if isinstance(verdict_counts, dict) else [])
        if verdict != "verified"
    )
    verify_result = pack_audit.get("verify", {})
    has_structured_claims = bool(structured_claims)
    ready_for_canonical_reread = (
        bool(isinstance(verify_result, dict) and verify_result.get("current_valid"))
        and not unknown_anchor_ids
        and not unknown_block_ids
        and not unknown_canonical_uris
        and not outside_dois
        and (not structured_claims_requiring_revision if has_structured_claims else prose_non_verified == 0)
    )
    return {
        "ok": ready_for_canonical_reread,
        "result_id": result_id,
        "pack_id": pack.pack_id,
        "packet_artifact_id": packet_artifact_id,
        "allowed_reference_scope": reference_scope,
        "advisory_only": True,
        "ready_for_canonical_reread": ready_for_canonical_reread,
        "referenced_anchor_ids": sorted(referenced_anchor_ids),
        "unknown_anchor_ids": unknown_anchor_ids,
        "referenced_block_ids": sorted(referenced_block_ids),
        "unknown_block_ids": unknown_block_ids,
        "referenced_canonical_uris": sorted(referenced_canonical_uris),
        "unknown_canonical_uris": unknown_canonical_uris,
        "pack_outside_dois": outside_dois,
        "structured_claims": structured_claims,
        "structured_claims_checked": len(structured_claims),
        "prose_claims_requiring_revision": prose_claims_requiring_revision,
        "usable_claim_ids": structured_usable_claim_ids if has_structured_claims else prose_usable_claim_ids,
        "claims_requiring_revision": (
            structured_claims_requiring_revision if has_structured_claims else prose_claims_requiring_revision
        ),
        "audit": pack_audit,
        "next_action": (
            "Reread verified canonical windows with read_saved_paper before final citation."
            if ready_for_canonical_reread
            else "Revise or gather evidence before using this external consult."
        ),
    }
