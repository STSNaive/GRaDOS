"""Host-side ChatGPT Pro external synthesis packet helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.research.draft_audit import (
    VERDICT_MAJOR_DISTORTION,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
)
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
    "EXTERNAL_SYNTHESIS_PACKET_KIND",
    "EXTERNAL_SYNTHESIS_RESULT_KIND",
    "audit_external_synthesis_result",
    "prepare_external_synthesis_from_topic",
    "prepare_external_synthesis_packet",
    "preview_external_synthesis_packet",
    "run_external_synthesis",
    "save_external_synthesis_result",
]

EXTERNAL_SYNTHESIS_PACKET_KIND = "external_synthesis_packet"
EXTERNAL_SYNTHESIS_RESULT_KIND = "external_synthesis_result"
EXTERNAL_SYNTHESIS_PROTOCOL_VERSION = "external-synthesis-v1"

ExternalSynthesisMode = Literal["review", "synthesize"]

_ANCHOR_ID_PATTERN = re.compile(r"\banchor_[0-9]{3,}\b")
_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_BLOCK_ID_PATTERN = re.compile(
    r"\b(?:md_p\d{5}_[0-9a-f]{12}|paragraph-\d{6}-[0-9a-f]{12}|[A-Za-z0-9_.-]+::p\d+:\d+:\d+)\b",
    re.IGNORECASE,
)
_CANONICAL_URI_PATTERN = re.compile(r"\bgrados://papers/[^\s,;)\]}>\"']+")
_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]{2,}", re.IGNORECASE)


def _normalize_mode(mode: str | None) -> ExternalSynthesisMode:
    normalized = (mode or "review").strip().lower()
    if normalized not in {"review", "synthesize"}:
        raise ValueError("mode must be `review` or `synthesize`.")
    return normalized  # type: ignore[return-value]


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
    mode: ExternalSynthesisMode,
    verify_result: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "sendable": False,
        "saved": False,
        "mode": mode,
        "pack_id": pack_id,
        "error": error,
        "blocked_reasons": [error],
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
    mode: ExternalSynthesisMode,
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
        "schema_version": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "kind": EXTERNAL_SYNTHESIS_PACKET_KIND,
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
        "forbidden": [
            "Do not introduce pack-external papers, DOIs, facts, citations, or web evidence.",
            "Do not treat ChatGPT Pro output as final citation evidence.",
            "Do not cite without a later GRaDOS canonical reread or current-valid evidence pack.",
        ],
    }
    _, prompt_metadata = _render_host_prompt(payload)
    payload.update(prompt_metadata)
    return payload


def _packet_preview(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "sendable": True,
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
        "blocked_reasons": [],
        "prompt_skeleton": (
            "Send the packet to ChatGPT Pro only after the gate is enabled. Ask for claims, "
            "anchor_ids, confidence, caveats, missing_evidence, and forbidden_or_outside_content."
        ),
        "host_guidance": [
            "Use one ChatGPT Pro conversation per GRaDOS workflow.",
            "Save the Pro response with save_external_synthesis_result before using it.",
            "Run audit_external_synthesis_result and reread canonical windows before final citation.",
        ],
    }


def preview_external_synthesis_packet(
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
    return _packet_preview(packet)


def prepare_external_synthesis_packet(
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
    host_prompt, _ = _render_host_prompt(packet)
    artifact_metadata = {
        **(metadata or {}),
        "protocol": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "pack_id": pack.pack_id,
        "pack_sha256": pack.pack_sha256,
        "prompt_hash": packet["prompt_hash"],
        "mode": resolved_mode,
        "evidence_count": packet["item_count"],
    }
    receipt = save_research_artifact(
        db_path,
        kind=EXTERNAL_SYNTHESIS_PACKET_KIND,
        title=f"External synthesis packet: {pack.topic or pack.pack_id}",
        content=packet,
        metadata=artifact_metadata,
    )
    return {
        **_packet_preview(packet),
        "saved": True,
        "artifact_id": receipt["artifact_id"],
        "kind": EXTERNAL_SYNTHESIS_PACKET_KIND,
        "metadata": receipt["metadata"],
        "packet": packet,
        "host_prompt": host_prompt,
    }


def prepare_external_synthesis_from_topic(
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
        "source": "prepare_external_synthesis_from_topic",
        "evidence_pack_artifact_id": str(pack_receipt.get("artifact_id") or ""),
    }
    packet = prepare_external_synthesis_packet(
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
        "route": "prepare_external_synthesis_from_topic",
        "pack_id": pack_id,
        "pack_artifact_id": str(pack_receipt.get("artifact_id") or ""),
        "evidence_pack": pack_receipt,
    }


async def run_external_synthesis(
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
) -> dict[str, Any]:
    """Run the default GRaDOS-native ChatGPT Pro browser synthesis route."""
    from grados.browser.chatgpt.runtime import run_chatgpt_browser_session

    resolved_mode = _normalize_mode(mode)
    packet: dict[str, Any] = {}
    host_prompt = ""
    source_pack_id = pack_id.strip()
    packet_artifact_id = ""
    prompt_hash = ""

    if recover_session_id:
        browser_result = await run_chatgpt_browser_session(
            paths,
            browser_config or HeadlessBrowserConfig(),
            prompt="",
            pack_id=source_pack_id,
            packet_artifact_id="",
            prompt_hash="",
            mode=resolved_mode,
            metadata=metadata,
            recover_session_id=recover_session_id,
        )
    else:
        if bool(topic.strip()) == bool(source_pack_id):
            return {
                "ok": False,
                "sendable": False,
                "saved": False,
                "error": "invalid_external_synthesis_input",
                "message": "Provide exactly one of topic or pack_id.",
            }
        if topic.strip():
            packet = prepare_external_synthesis_from_topic(
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
            packet = prepare_external_synthesis_packet(
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
        source_pack_id = str(packet.get("pack_id") or source_pack_id)
        packet_artifact_id = str(packet.get("artifact_id") or "")
        prompt_hash = str(packet.get("prompt_hash") or "")
        host_prompt = str(packet.get("host_prompt") or "")
        browser_result = await run_chatgpt_browser_session(
            paths,
            browser_config or HeadlessBrowserConfig(),
            prompt=host_prompt,
            pack_id=source_pack_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=prompt_hash,
            mode=resolved_mode,
            metadata={
                **(metadata or {}),
                "packet_artifact_id": packet_artifact_id,
                "prompt_hash": prompt_hash,
            },
        )

    browser_payload = browser_result.to_dict()
    if not browser_result.ok:
        return {
            "ok": False,
            "sendable": False,
            "saved": False,
            "error": browser_result.error_code or "chatgpt_browser_failed",
            "message": browser_result.error,
            "browser": browser_payload,
            "packet": packet,
        }

    source_pack_id = str(browser_result.metadata.get("pack_id") or source_pack_id)
    packet_artifact_id = str(browser_result.metadata.get("packet_artifact_id") or packet_artifact_id)
    prompt_hash = str(browser_result.metadata.get("prompt_hash") or prompt_hash)
    if not source_pack_id:
        return {
            "ok": False,
            "sendable": False,
            "saved": False,
            "error": "browser_session_pack_missing",
            "message": "Captured ChatGPT browser session has no linked evidence pack id.",
            "browser": browser_payload,
        }

    save_metadata = {
        **(metadata or {}),
        "runtime": "grados_chatgpt_browser",
        "browser_mode_version": browser_payload.get("browser_mode_version"),
        "browser_session_id": browser_result.session_id,
        "browser_session_record": browser_result.session_record_path,
        "model_selection": browser_payload.get("model"),
        "thinking_selection": browser_payload.get("thinking"),
        "capture": browser_payload.get("capture"),
    }
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
    saved = save_external_synthesis_result(
        db_path,
        papers_dir,
        pack_id=source_pack_id,
        response=browser_result.response_text,
        packet_artifact_id=packet_artifact_id,
        prompt_hash=prompt_hash,
        conversation_url=browser_result.conversation_url,
        model_label=browser_result.model_label,
        thinking_label=browser_result.thinking_label,
        mode=resolved_mode,
        claims=structured_claims,
        gaps=structured_gaps,
        metadata=save_metadata,
        audit=True,
    )
    return {
        "ok": bool(saved.get("ok")),
        "sendable": True,
        "saved": bool(saved.get("saved")),
        "audited": bool(saved.get("audited")),
        "artifact_id": saved.get("artifact_id", ""),
        "pack_id": source_pack_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "browser_session_id": browser_result.session_id,
        "conversation_url": browser_result.conversation_url,
        "model_label": browser_result.model_label,
        "thinking_label": browser_result.thinking_label,
        "browser": browser_payload,
        "packet": packet,
        "result": saved,
        "audit": saved.get("audit"),
        "ready_for_canonical_reread": bool(saved.get("ready_for_canonical_reread")),
        "next_action": saved.get("next_action", "audit_external_synthesis_result"),
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


def save_external_synthesis_result(
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
        if packet_artifact is None or packet_artifact.get("kind") != EXTERNAL_SYNTHESIS_PACKET_KIND:
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
    structured_claims = claims
    if structured_claims is None and isinstance(response, dict):
        response_claims = response.get("claims")
        if isinstance(response_claims, list):
            structured_claims = [dict(item) for item in response_claims if isinstance(item, dict)]
    structured_gaps = gaps
    if structured_gaps is None and isinstance(response, dict):
        structured_gaps = _coerce_string_list(
            response.get("gaps") or response.get("missing_evidence")
        )
    content_payload: dict[str, Any] = {
        "schema_version": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
        "kind": EXTERNAL_SYNTHESIS_RESULT_KIND,
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
        "next_action": "audit_external_synthesis_result",
    }
    if packet_content is not None:
        content_payload["packet"] = {
            "artifact_id": packet_artifact_id,
            "prompt_hash": prompt_hash,
            "item_count": packet_content.get("item_count"),
        }
    artifact_metadata = {
        **(metadata or {}),
        "protocol": EXTERNAL_SYNTHESIS_PROTOCOL_VERSION,
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
    receipt = save_research_artifact(
        db_path,
        kind=EXTERNAL_SYNTHESIS_RESULT_KIND,
        title=f"External synthesis result: {pack.topic or pack.pack_id}",
        content=content_payload,
        metadata=artifact_metadata,
    )
    result: dict[str, Any] = {
        "ok": True,
        "saved": True,
        "audited": False,
        "artifact_id": receipt["artifact_id"],
        "kind": EXTERNAL_SYNTHESIS_RESULT_KIND,
        "pack_id": pack.pack_id,
        "packet_artifact_id": packet_artifact_id,
        "prompt_hash": prompt_hash,
        "response_sha256": response_hash,
        "verify": verify_result,
        "next_action": "audit_external_synthesis_result",
        "metadata": receipt["metadata"],
    }
    if audit:
        audit_result = audit_external_synthesis_result(
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
        _packet_item(item, index=index, max_excerpt_chars=2000)
        for index, item in enumerate(pack.evidence_items, 1)
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
        "canonical_uris": {
            str(item.get("canonical_uri")) for item in items if item.get("canonical_uri")
        },
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
        _normalize_token(match.group(0))
        for match in _WORD_PATTERN.finditer(text.lower())
        if len(match.group(0)) >= 2
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
    items_by_anchor = {
        str(item.get("anchor_id")): item
        for item in source_items
        if item.get("anchor_id")
    }
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
                str(item.get(key) or "")
                for key in ("candidate_claim", "short_excerpt", "title", "heading_path")
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


def audit_external_synthesis_result(
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
    if artifact.get("kind") != EXTERNAL_SYNTHESIS_RESULT_KIND:
        return {
            "ok": False,
            "result_id": result_id,
            "error": "artifact_is_not_external_synthesis_result",
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
        if packet_artifact is None or packet_artifact.get("kind") != EXTERNAL_SYNTHESIS_PACKET_KIND:
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
    claims = [
        claim
        for claim in pack_audit.get("claims", [])
        if isinstance(claim, dict)
    ]
    prose_usable_claim_ids = [
        str(claim.get("claim_id"))
        for claim in claims
        if claim.get("verdict") == "verified"
    ]
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
        str(claim.get("claim_id"))
        for claim in structured_claims
        if claim.get("verdict") == VERDICT_VERIFIED
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
        and (
            not structured_claims_requiring_revision
            if has_structured_claims
            else prose_non_verified == 0
        )
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
            structured_claims_requiring_revision
            if has_structured_claims
            else prose_claims_requiring_revision
        ),
        "audit": pack_audit,
        "next_action": (
            "Reread verified canonical windows with read_saved_paper before final citation."
            if ready_for_canonical_reread
            else "Revise or gather evidence before using this external synthesis."
        ),
    }
