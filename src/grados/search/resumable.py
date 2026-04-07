"""Resumable search: continuation tokens, deduplication, multi-source orchestration."""

from __future__ import annotations

import base64
import dataclasses
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from grados.publisher.common import normalize_doi
from grados.search.academic import (
    CrossrefState,
    ElsevierState,
    PaperMetadata,
    PubMedState,
    SearchPageResult,
    SpringerState,
    WoSState,
    build_search_adapters,
)

MAX_PAGE_FETCHES_PER_SOURCE = 8
CROSSREF_CURSOR_TTL_SECONDS = 300  # 5 minutes


# ── Continuation token ───────────────────────────────────────────────────────


@dataclass
class ContinuationData:
    version: int = 1
    query: str = ""
    normalized_query: str = ""
    active_sources: list[str] = field(default_factory=list)
    source_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    exhausted_sources: list[str] = field(default_factory=list)
    seen_dois: list[str] = field(default_factory=list)
    issued_at: str = ""


def encode_token(data: ContinuationData) -> str:
    raw = json.dumps(asdict(data), separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_token(token: str) -> ContinuationData | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        obj = json.loads(raw)
        if obj.get("version") != 1:
            return None
        return ContinuationData(**{k: v for k, v in obj.items() if k in ContinuationData.__dataclass_fields__})
    except Exception:
        return None


# ── State serialization ──────────────────────────────────────────────────────

_STATE_CLASSES: dict[str, type] = {
    "Crossref": CrossrefState,
    "PubMed": PubMedState,
    "WebOfScience": WoSState,
    "Elsevier": ElsevierState,
    "Springer": SpringerState,
}


def _deserialize_state(source: str, raw: dict[str, Any]) -> Any:
    cls = _STATE_CLASSES.get(source)
    if cls and raw:
        return cls(**{k: v for k, v in raw.items() if k in {f.name for f in dataclasses.fields(cls)}})
    return None


def _serialize_state(state: Any) -> dict[str, Any]:
    return asdict(state) if state else {}


# ── Resumable search engine ──────────────────────────────────────────────────


@dataclass
class ResumableSearchResult:
    query: str
    limit: int
    results: list[PaperMetadata]
    has_more: bool
    exhausted_sources: list[str]
    next_continuation_token: str | None
    warnings: list[str]
    continuation_applied: bool


async def run_resumable_search(
    query: str,
    limit: int,
    continuation_token: str | None,
    search_order: list[str],
    api_keys: dict[str, str],
    etiquette_email: str,
) -> ResumableSearchResult:
    """Execute a resumable multi-source academic search."""
    adapters = build_search_adapters(api_keys, etiquette_email, limit)

    # Decode or initialize continuation
    continuation_applied = False
    cont: ContinuationData | None = None
    if continuation_token:
        cont = decode_token(continuation_token)
        if cont and cont.normalized_query == query.strip().lower():
            continuation_applied = True
        else:
            cont = None

    if not cont:
        cont = ContinuationData(
            query=query,
            normalized_query=query.strip().lower(),
            active_sources=[s for s in search_order if s in adapters],
            issued_at=datetime.now(UTC).isoformat(),
        )

    seen_dois: set[str] = set(cont.seen_dois)
    exhausted: set[str] = set(cont.exhausted_sources)
    batch: dict[str, PaperMetadata] = {}  # doi → paper
    all_warnings: list[str] = []

    # Initialize states for new sources
    source_states: dict[str, Any] = {}
    for src in cont.active_sources:
        if src in cont.source_states and cont.source_states[src]:
            source_states[src] = _deserialize_state(src, cont.source_states[src])
        elif src in adapters:
            source_states[src] = adapters[src]["init"]()

    async with httpx.AsyncClient() as client:
        for src in cont.active_sources:
            if src in exhausted or src not in adapters:
                continue
            if len(batch) >= limit:
                break

            adapter = adapters[src]
            state = source_states.get(src)
            if state is None:
                continue

            # Refresh expired Crossref cursor
            if src == "Crossref" and isinstance(state, CrossrefState) and state.cursor_issued_at:
                try:
                    issued = datetime.fromisoformat(state.cursor_issued_at)
                    age = (datetime.now(UTC) - issued).total_seconds()
                    if age > CROSSREF_CURSOR_TTL_SECONDS:
                        state.cursor = "*"
                        state.cursor_issued_at = datetime.now(UTC).isoformat()
                except Exception:
                    pass

            for _page_fetch in range(MAX_PAGE_FETCHES_PER_SOURCE):
                if len(batch) >= limit:
                    break

                result: SearchPageResult
                result, state = await adapter["fetch"](query, limit, state, client)
                source_states[src] = state
                all_warnings.extend(result.warnings)

                new_in_page = 0
                for paper in result.papers:
                    if not paper.doi:
                        continue
                    ndoi = normalize_doi(paper.doi)
                    if ndoi in seen_dois:
                        continue
                    if ndoi in batch:
                        # Update if we now have an abstract
                        if paper.abstract and not batch[ndoi].abstract:
                            batch[ndoi].abstract = paper.abstract
                        continue
                    seen_dois.add(ndoi)
                    batch[ndoi] = paper
                    new_in_page += 1

                if result.exhausted:
                    exhausted.add(src)
                    break
                if new_in_page == 0:
                    break

    # Build result
    results = list(batch.values())[:limit]
    has_more = any(s not in exhausted for s in cont.active_sources if s in adapters)

    # Update continuation
    cont.seen_dois = list(seen_dois)
    cont.exhausted_sources = list(exhausted)
    cont.source_states = {s: _serialize_state(st) for s, st in source_states.items()}

    next_token = encode_token(cont) if has_more else None

    return ResumableSearchResult(
        query=query,
        limit=limit,
        results=results,
        has_more=has_more,
        exhausted_sources=list(exhausted),
        next_continuation_token=next_token,
        warnings=all_warnings,
        continuation_applied=continuation_applied,
    )
