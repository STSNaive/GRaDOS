"""PDF fetch waterfall: api -> browser -> oa -> scihub."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from grados._retry import current_fetch_timeout, current_pdf_timeout, http_retry
from grados.config import DEFAULT_SCI_HUB_ENDPOINT, GRaDOSPaths, HeadlessBrowserConfig
from grados.publisher.common import (
    PublisherMetadata,
    classify_pdf_content,
    detect_bot_challenge,
    normalize_publisher_metadata,
)
from grados.publisher.elsevier import ElsevierFetchResult, fetch_elsevier_article
from grados.publisher.springer import SpringerFetchResult, fetch_springer_article


@dataclass
class FetchResult:
    text: str = ""
    pdf_buffer: bytes = b""
    outcome: str = ""  # native_full_text | pdf_obtained | metadata_only | failed
    source: str = ""  # e.g. "Elsevier TDM", "Unpaywall OA", "Sci-Hub"
    via: str = ""  # api | browser | oa | scihub
    state: str = ""  # ok | partial | challenge | timeout | nobrowser | not_found | blocked | error
    text_format: str = ""  # markdown | text | html | xml
    metadata: PublisherMetadata | None = None
    asset_hints: list[dict[str, str]] = field(default_factory=list)
    manual: bool = False
    host: str = ""
    resume: dict[str, str] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FetchStrategyContext:
    doi: str
    api_keys: dict[str, str]
    etiquette_email: str
    client: httpx.AsyncClient
    tdm_order: list[str] | None
    tdm_enabled: dict[str, bool] | None
    sci_hub_config: dict[str, Any]
    headless_config: HeadlessBrowserConfig | None
    paths: GRaDOSPaths | None
    browser_resume: dict[str, str] | None


@dataclass(frozen=True)
class TDMProviderContext:
    doi: str
    api_keys: dict[str, str]
    client: httpx.AsyncClient


class FetchStrategy(Protocol):
    name: str

    async def run(self, context: FetchStrategyContext) -> FetchResult:
        ...


class TDMProvider(Protocol):
    name: str

    async def run(self, context: TDMProviderContext) -> FetchResult:
        ...


@dataclass(frozen=True)
class _FunctionFetchStrategy:
    name: str
    runner: Callable[[FetchStrategyContext], Awaitable[FetchResult]]

    async def run(self, context: FetchStrategyContext) -> FetchResult:
        return await self.runner(context)


@dataclass(frozen=True)
class _FunctionTDMProvider:
    name: str
    runner: Callable[[TDMProviderContext], Awaitable[FetchResult]]

    async def run(self, context: TDMProviderContext) -> FetchResult:
        return await self.runner(context)


async def _run_tdm_fetch_strategy(context: FetchStrategyContext) -> FetchResult:
    return await _fetch_tdm(
        context.doi,
        context.api_keys,
        context.client,
        context.tdm_order,
        context.tdm_enabled,
    )


async def _run_oa_fetch_strategy(context: FetchStrategyContext) -> FetchResult:
    return await _fetch_oa(context.doi, context.etiquette_email, context.client)


async def _run_scihub_fetch_strategy(context: FetchStrategyContext) -> FetchResult:
    return await _fetch_scihub(context.doi, context.client, context.sci_hub_config)


async def _run_browser_fetch_strategy(context: FetchStrategyContext) -> FetchResult:
    if not context.headless_config or not context.paths:
        return FetchResult(
            outcome="failed",
            via="browser",
            state="nobrowser",
            warnings=["Browser not configured."],
        )

    from grados.browser.generic import fetch_with_browser

    browser_result = await fetch_with_browser(
        context.doi,
        context.headless_config,
        context.paths,
        resume=context.browser_resume,
    )
    if browser_result.pdf_buffer:
        return FetchResult(
            pdf_buffer=browser_result.pdf_buffer,
            outcome="pdf_obtained",
            source=browser_result.source,
            via=browser_result.via,
            state=browser_result.state,
            manual=browser_result.manual,
            host=browser_result.host,
            resume=browser_result.resume,
            warnings=browser_result.warnings,
        )
    return FetchResult(
        outcome=browser_result.outcome or "failed",
        source=browser_result.source,
        via=browser_result.via,
        state=browser_result.state,
        manual=browser_result.manual,
        host=browser_result.host,
        resume=browser_result.resume,
        warnings=browser_result.warnings,
    )


async def _run_elsevier_tdm_provider(context: TDMProviderContext) -> FetchResult:
    key = context.api_keys.get("ELSEVIER_API_KEY", "")
    if not key:
        return FetchResult(outcome="failed", via="api", state="error")

    result: ElsevierFetchResult = await fetch_elsevier_article(context.doi, key, context.client)
    if result.outcome == "native_full_text":
        return FetchResult(
            text=result.text,
            outcome="native_full_text",
            source="Elsevier TDM",
            via="api",
            state="ok",
            text_format=result.text_format,
            metadata=normalize_publisher_metadata(result.metadata),
            asset_hints=result.asset_hints,
        )
    return FetchResult(
        outcome=result.outcome or "failed",
        source="Elsevier TDM",
        via="api",
        state="partial" if result.outcome == "metadata_only" else "error",
        metadata=normalize_publisher_metadata(result.metadata),
        asset_hints=result.asset_hints,
    )


async def _run_springer_tdm_provider(context: TDMProviderContext) -> FetchResult:
    meta_key = context.api_keys.get("SPRINGER_meta_API_KEY", "")
    oa_key = context.api_keys.get("SPRINGER_OA_API_KEY", "")
    if not meta_key:
        return FetchResult(outcome="failed", via="api", state="error")

    result: SpringerFetchResult = await fetch_springer_article(context.doi, meta_key, oa_key, context.client)
    if result.outcome == "native_full_text":
        return FetchResult(
            text=result.text,
            outcome="native_full_text",
            source="Springer TDM",
            via="api",
            state="ok",
            text_format=result.text_format,
            metadata=normalize_publisher_metadata(result.metadata),
            asset_hints=result.asset_hints,
        )
    if result.outcome == "pdf_obtained":
        return FetchResult(
            pdf_buffer=result.pdf_buffer,
            outcome="pdf_obtained",
            source="Springer TDM",
            via="api",
            state="ok",
            metadata=normalize_publisher_metadata(result.metadata),
            asset_hints=result.asset_hints,
        )
    return FetchResult(
        outcome=result.outcome or "failed",
        source="Springer TDM",
        via="api",
        state="partial" if result.outcome == "metadata_only" else "error",
        metadata=normalize_publisher_metadata(result.metadata),
        asset_hints=result.asset_hints,
    )


FETCH_STRATEGY_REGISTRY: dict[str, FetchStrategy] = {
    "api": _FunctionFetchStrategy("api", _run_tdm_fetch_strategy),
    "browser": _FunctionFetchStrategy("browser", _run_browser_fetch_strategy),
    "oa": _FunctionFetchStrategy("oa", _run_oa_fetch_strategy),
    "scihub": _FunctionFetchStrategy("scihub", _run_scihub_fetch_strategy),
}

TDM_PROVIDER_REGISTRY: dict[str, TDMProvider] = {
    "Elsevier": _FunctionTDMProvider("Elsevier", _run_elsevier_tdm_provider),
    "Springer": _FunctionTDMProvider("Springer", _run_springer_tdm_provider),
}


_FETCH_STRATEGY_ALIASES: dict[str, str] = {
    "api": "api",
    "tdm": "api",
    "browser": "browser",
    "headless": "browser",
    "oa": "oa",
    "scihub": "scihub",
}


def _normalize_fetch_strategy_name(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", name.strip().lower())
    return _FETCH_STRATEGY_ALIASES.get(normalized)


def _normalize_fetch_enabled(enabled: dict[str, bool] | None) -> dict[str, bool]:
    if not enabled:
        return {}
    normalized: dict[str, bool] = {}
    for key, value in enabled.items():
        canonical = _normalize_fetch_strategy_name(key)
        if canonical is None:
            continue
        normalized[canonical] = bool(value)
    return normalized


def build_fetch_strategies(order: list[str] | None = None) -> list[FetchStrategy]:
    resolved_order = order or ["api", "browser", "oa", "scihub"]
    strategies: list[FetchStrategy] = []
    seen: set[str] = set()
    for raw_name in resolved_order:
        canonical = _normalize_fetch_strategy_name(raw_name)
        if canonical is None or canonical in seen:
            continue
        strategy = FETCH_STRATEGY_REGISTRY.get(canonical)
        if strategy is None:
            continue
        strategies.append(strategy)
        seen.add(canonical)
    return strategies


def _resume_fetch_strategies(strategies: list[FetchStrategy]) -> list[FetchStrategy]:
    for index, strategy in enumerate(strategies):
        if strategy.name == "browser":
            return strategies[index:]
    browser = FETCH_STRATEGY_REGISTRY.get("browser")
    if browser is None:
        return strategies
    return [browser, *strategies]


def build_tdm_providers(order: list[str] | None = None) -> list[TDMProvider]:
    resolved_order = order or ["Elsevier", "Springer"]
    return [TDM_PROVIDER_REGISTRY[name] for name in resolved_order if name in TDM_PROVIDER_REGISTRY]


def _is_fetch_success(result: FetchResult) -> bool:
    return result.outcome in {"native_full_text", "pdf_obtained"}


def _is_fetch_partial(result: FetchResult) -> bool:
    return result.outcome == "metadata_only"


def _failed_result_priority(result: FetchResult) -> int:
    priority = {
        "challenge": 8,
        "blocked": 7,
        "timeout": 6,
        "site_unreachable": 5,
        "not_found": 4,
        "nobrowser": 3,
        "partial": 2,
        "parse_error": 2,
        "invalid_pdf": 2,
        "error": 1,
        "": 0,
    }
    return priority.get(result.state, 0)


def _prefer_failed_result(current: FetchResult | None, candidate: FetchResult) -> FetchResult:
    if current is None:
        return candidate
    current_priority = _failed_result_priority(current)
    candidate_priority = _failed_result_priority(candidate)
    if candidate_priority > current_priority:
        return candidate
    if candidate_priority < current_priority:
        return current
    if _metadata_signal_score(candidate.metadata) + len(candidate.asset_hints) > (
        _metadata_signal_score(current.metadata) + len(current.asset_hints)
    ):
        return candidate
    return current


def _metadata_signal_score(metadata: PublisherMetadata | None) -> int:
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


def _prefer_partial_result(current: FetchResult | None, candidate: FetchResult) -> FetchResult:
    if current is None:
        return candidate

    current_score = _metadata_signal_score(current.metadata) + len(current.asset_hints)
    candidate_score = _metadata_signal_score(candidate.metadata) + len(candidate.asset_hints)
    return candidate if candidate_score > current_score else current


def _format_fetch_warning(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip()
    if detail:
        return f"{prefix}: {exc.__class__.__name__}: {detail}"
    return f"{prefix}: {exc.__class__.__name__}"


def _trace_fetch_result(doi: str, result: FetchResult) -> dict[str, Any]:
    payload = {
        "via": result.via,
        "state": result.state,
        "outcome": result.outcome,
        "host": result.host,
        "manual": result.manual,
        "resume": result.resume,
    }
    digest = hashlib.sha1(
        json.dumps({"doi": doi, **payload}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return {
        **payload,
        "time": datetime.now(UTC).isoformat(),
        "hash": digest,
    }


async def fetch_paper(
    doi: str,
    api_keys: dict[str, str],
    etiquette_email: str,
    fetch_order: list[str] | None = None,
    fetch_enabled: dict[str, bool] | None = None,
    tdm_order: list[str] | None = None,
    tdm_enabled: dict[str, bool] | None = None,
    sci_hub_config: dict[str, Any] | None = None,
    headless_config: HeadlessBrowserConfig | None = None,
    paths: GRaDOSPaths | None = None,
    browser_resume: dict[str, str] | None = None,
) -> FetchResult:
    """Execute the fetch waterfall for a DOI."""
    strategies = build_fetch_strategies(fetch_order)
    if browser_resume is not None:
        strategies = _resume_fetch_strategies(strategies)
    enabled = _normalize_fetch_enabled(fetch_enabled) or {strategy.name: True for strategy in strategies}
    warnings: list[str] = []
    trace: list[dict[str, Any]] = []
    partial_result: FetchResult | None = None
    failure_result: FetchResult | None = None

    async with httpx.AsyncClient(follow_redirects=True) as client:
        context = FetchStrategyContext(
            doi=doi,
            api_keys=api_keys,
            etiquette_email=etiquette_email,
            client=client,
            tdm_order=tdm_order,
            tdm_enabled=tdm_enabled,
            sci_hub_config=sci_hub_config or {},
            headless_config=headless_config,
            paths=paths,
            browser_resume=browser_resume,
        )

        for strategy in strategies:
            if not enabled.get(strategy.name, True):
                continue

            result = await strategy.run(context)
            warnings.extend(result.warnings)
            trace.extend(result.trace or [_trace_fetch_result(doi, result)])
            if _is_fetch_success(result):
                result.warnings = warnings.copy()
                result.trace = trace.copy()
                return result
            if _is_fetch_partial(result):
                partial_result = _prefer_partial_result(partial_result, result)
                continue
            failure_result = _prefer_failed_result(failure_result, result)

    if partial_result is not None:
        partial_result.warnings = warnings.copy()
        partial_result.trace = trace.copy()
        return partial_result
    if failure_result is not None:
        failure_result.warnings = warnings.copy()
        failure_result.trace = trace.copy()
        return failure_result

    return FetchResult(outcome="failed", state="error", trace=trace, warnings=warnings)


# ── TDM ──────────────────────────────────────────────────────────────────────


async def _fetch_tdm(
    doi: str,
    api_keys: dict[str, str],
    client: httpx.AsyncClient,
    tdm_order: list[str] | None = None,
    tdm_enabled: dict[str, bool] | None = None,
) -> FetchResult:
    providers = build_tdm_providers(tdm_order)
    enabled = tdm_enabled or {provider.name: True for provider in providers}
    context = TDMProviderContext(doi=doi, api_keys=api_keys, client=client)
    partial_result: FetchResult | None = None

    for provider in providers:
        if not enabled.get(provider.name, True):
            continue
        result = await provider.run(context)
        if _is_fetch_success(result):
            return result
        if _is_fetch_partial(result):
            partial_result = _prefer_partial_result(partial_result, result)

    if partial_result is not None:
        return partial_result

    return FetchResult(outcome="failed", via="api", state="error")


# ── OA (Unpaywall) ──────────────────────────────────────────────────────────


@http_retry()
async def _unpaywall_lookup(
    client: httpx.AsyncClient,
    doi: str,
    etiquette_email: str,
) -> httpx.Response:
    resp = await client.get(
        f"https://api.unpaywall.org/v2/{doi}",
        params={"email": etiquette_email},
        timeout=current_fetch_timeout(),
    )
    # Non-2xx that is retryable (429/5xx) raises via raise_for_status; 404 stays
    # as a caller-decided outcome.
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


@http_retry()
async def _download_pdf(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.get(url, timeout=current_pdf_timeout(), follow_redirects=True)
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


async def _fetch_oa(
    doi: str,
    etiquette_email: str,
    client: httpx.AsyncClient,
) -> FetchResult:
    warnings: list[str] = []
    try:
        resp = await _unpaywall_lookup(client, doi, etiquette_email)
        if resp.status_code != 200:
            return FetchResult(
                outcome="failed",
                via="oa",
                state="error",
                warnings=[f"OA lookup failed: HTTP {resp.status_code}"],
            )

        locations = resp.json().get("oa_locations", [])
        # Prefer repository sources (arXiv, PMC) over publisher
        locations.sort(key=lambda loc: 0 if loc.get("host_type") == "repository" else 1)

        for loc in locations:
            pdf_url = loc.get("url_for_pdf")
            if not pdf_url:
                continue
            try:
                pdf_resp = await _download_pdf(client, pdf_url)
                ct = pdf_resp.headers.get("content-type", "")
                check = classify_pdf_content(pdf_resp.content, ct)
                if check["is_pdf"]:
                    return FetchResult(
                        pdf_buffer=pdf_resp.content,
                        outcome="pdf_obtained",
                        source="Unpaywall OA",
                        via="oa",
                        state="ok",
                    )
            except Exception as exc:
                warnings.append(_format_fetch_warning("OA PDF fetch failed", exc))
                continue
    except Exception as exc:
        warnings.append(_format_fetch_warning("OA lookup failed", exc))
    return FetchResult(outcome="failed", source="Unpaywall OA", via="oa", state="error", warnings=warnings)


# ── Sci-Hub ──────────────────────────────────────────────────────────────────


_SCI_HUB_NOT_FOUND_MARKERS = (
    "article not found",
    "paper not found",
    "doi not found",
    "not found in sci-hub",
    "could not be found",
)


def _normalize_scihub_endpoint(endpoint: object) -> str:
    value = str(endpoint or "").strip()
    if not value:
        return ""
    if "://" in value and not value.lower().startswith(("http://", "https://")):
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def _resolve_scihub_endpoints(config: dict[str, Any]) -> list[str]:
    configured = config.get("endpoints")
    raw_endpoints = configured if isinstance(configured, list) else []
    if not raw_endpoints:
        raw_endpoints = [config.get("fallback_mirror") or DEFAULT_SCI_HUB_ENDPOINT]

    endpoints: list[str] = []
    seen: set[str] = set()
    for raw_endpoint in raw_endpoints:
        endpoint = _normalize_scihub_endpoint(raw_endpoint)
        if not endpoint or endpoint in seen:
            continue
        endpoints.append(endpoint)
        seen.add(endpoint)
    fallback_endpoint = _normalize_scihub_endpoint(
        config.get("fallback_mirror") or DEFAULT_SCI_HUB_ENDPOINT
    )
    return endpoints or [fallback_endpoint or DEFAULT_SCI_HUB_ENDPOINT]


def _scihub_endpoint_role(index: int) -> str:
    return "primary" if index == 0 else "fallback"


def _scihub_host(url: str) -> str:
    return urlsplit(url).netloc or url


def _scihub_endpoint_label(endpoint: str, index: int) -> str:
    return f"Sci-Hub {_scihub_endpoint_role(index)} endpoint {_scihub_host(endpoint)}"


def _scihub_trace(
    doi: str,
    *,
    endpoint: str,
    index: int,
    outcome: str,
    state: str,
    status_code: int | None = None,
    reason: str = "",
    pdf_url: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "via": "scihub",
        "outcome": outcome,
        "state": state,
        "endpoint_index": index,
        "endpoint_role": _scihub_endpoint_role(index),
        "endpoint_host": _scihub_host(endpoint),
        "http_status": status_code,
        "reason": reason,
        "pdf_host": _scihub_host(pdf_url) if pdf_url else "",
    }
    digest = hashlib.sha1(
        json.dumps({"doi": doi, **payload}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return {
        **payload,
        "time": datetime.now(UTC).isoformat(),
        "hash": digest,
    }


def _scihub_failed_result(
    doi: str,
    *,
    endpoint: str,
    index: int,
    state: str,
    warning: str,
    status_code: int | None = None,
    reason: str = "",
) -> FetchResult:
    return FetchResult(
        outcome="failed",
        source="Sci-Hub",
        via="scihub",
        state=state,
        host=_scihub_host(endpoint),
        trace=[
            _scihub_trace(
                doi,
                endpoint=endpoint,
                index=index,
                outcome="failed",
                state=state,
                status_code=status_code,
                reason=reason,
            )
        ],
        warnings=[warning],
    )


def _classify_scihub_status(status_code: int | None) -> tuple[str, str]:
    if status_code in {404, 410}:
        return "not_found", "not_found_status"
    if status_code in {401, 403, 429}:
        return "blocked", "blocked_status"
    if status_code is not None and status_code >= 500:
        return "site_unreachable", "server_error_status"
    return "error", "unexpected_status"


def _looks_like_scihub_not_found(html: str) -> bool:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True).lower()
    return any(marker in text for marker in _SCI_HUB_NOT_FOUND_MARKERS)


def _scihub_endpoint_failure_priority(result: FetchResult) -> int:
    priority = {
        "challenge": 8,
        "blocked": 7,
        "parse_error": 6,
        "invalid_pdf": 6,
        "site_unreachable": 5,
        "timeout": 4,
        "error": 1,
        "": 0,
    }
    return priority.get(result.state, 0)


def _prefer_scihub_endpoint_failure(
    current: FetchResult | None,
    candidate: FetchResult,
) -> FetchResult:
    if current is None:
        return candidate
    return (
        candidate
        if _scihub_endpoint_failure_priority(candidate) > _scihub_endpoint_failure_priority(current)
        else current
    )


@http_retry()
async def _scihub_landing(client: httpx.AsyncClient, endpoint: str, doi: str) -> httpx.Response:
    resp = await client.get(
        f"{endpoint}/{doi}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"},
        timeout=current_fetch_timeout(),
    )
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


async def _fetch_scihub_endpoint(
    doi: str,
    client: httpx.AsyncClient,
    endpoint: str,
    index: int,
) -> FetchResult:
    label = _scihub_endpoint_label(endpoint, index)
    try:
        resp = await _scihub_landing(client, endpoint, doi)
    except httpx.TimeoutException as exc:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="timeout",
            warning=_format_fetch_warning(f"{label} timed out", exc),
            reason="landing_timeout",
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        state, reason = _classify_scihub_status(status_code)
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state=state,
            warning=f"{label} lookup failed: HTTP {status_code}",
            status_code=status_code,
            reason=reason,
        )
    except httpx.TransportError as exc:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="site_unreachable",
            warning=_format_fetch_warning(f"{label} is unreachable", exc),
            reason="landing_transport_error",
        )
    except Exception as exc:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="error",
            warning=_format_fetch_warning(f"{label} lookup failed", exc),
            reason="landing_error",
        )

    if resp.status_code != 200:
        state, reason = _classify_scihub_status(resp.status_code)
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state=state,
            warning=f"{label} lookup failed: HTTP {resp.status_code}",
            status_code=resp.status_code,
            reason=reason,
        )

    landing_url = f"{endpoint}/{doi}"
    if detect_bot_challenge("", resp.text, landing_url):
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="challenge",
            warning=f"{label} challenge detected",
            status_code=resp.status_code,
            reason="challenge_detected",
        )

    if _looks_like_scihub_not_found(resp.text):
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="not_found",
            warning=f"{label} reports no paper for DOI",
            status_code=resp.status_code,
            reason="not_found_marker",
        )

    pdf_url = _extract_scihub_pdf_url(resp.text, endpoint)
    if not pdf_url:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="parse_error",
            warning=f"{label} page did not expose a PDF link",
            status_code=resp.status_code,
            reason="pdf_link_missing",
        )

    try:
        pdf_resp = await _download_pdf(client, pdf_url)
    except httpx.TimeoutException as exc:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="timeout",
            warning=_format_fetch_warning(f"{label} PDF download timed out", exc),
            reason="pdf_timeout",
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        state, reason = _classify_scihub_status(status_code)
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state=state,
            warning=f"{label} PDF download failed: HTTP {status_code}",
            status_code=status_code,
            reason=reason,
        )
    except httpx.TransportError as exc:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="site_unreachable",
            warning=_format_fetch_warning(f"{label} PDF download failed", exc),
            reason="pdf_transport_error",
        )
    except Exception as exc:
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state="error",
            warning=_format_fetch_warning(f"{label} PDF download failed", exc),
            reason="pdf_error",
        )

    if pdf_resp.status_code != 200:
        state, reason = _classify_scihub_status(pdf_resp.status_code)
        return _scihub_failed_result(
            doi,
            endpoint=endpoint,
            index=index,
            state=state,
            warning=f"{label} PDF download failed: HTTP {pdf_resp.status_code}",
            status_code=pdf_resp.status_code,
            reason=reason,
        )

    ct = pdf_resp.headers.get("content-type", "")
    check = classify_pdf_content(pdf_resp.content, ct)
    if check["is_pdf"]:
        return FetchResult(
            pdf_buffer=pdf_resp.content,
            outcome="pdf_obtained",
            source="Sci-Hub",
            via="scihub",
            state="ok",
            host=_scihub_host(endpoint),
            trace=[
                _scihub_trace(
                    doi,
                    endpoint=endpoint,
                    index=index,
                    outcome="pdf_obtained",
                    state="ok",
                    status_code=pdf_resp.status_code,
                    reason="pdf_obtained",
                    pdf_url=pdf_url,
                )
            ],
        )
    return _scihub_failed_result(
        doi,
        endpoint=endpoint,
        index=index,
        state="invalid_pdf",
        warning=f"{label} PDF fetch failed: {check['reason']}",
        status_code=pdf_resp.status_code,
        reason="invalid_pdf",
    )


async def _fetch_scihub(
    doi: str,
    client: httpx.AsyncClient,
    config: dict[str, Any],
) -> FetchResult:
    warnings: list[str] = []
    trace: list[dict[str, Any]] = []
    failure_result: FetchResult | None = None

    for index, endpoint in enumerate(_resolve_scihub_endpoints(config)):
        result = await _fetch_scihub_endpoint(doi, client, endpoint, index)
        warnings.extend(result.warnings)
        trace.extend(result.trace or [_trace_fetch_result(doi, result)])
        if _is_fetch_success(result):
            result.warnings = warnings.copy()
            result.trace = trace.copy()
            return result
        if result.state == "not_found":
            result.warnings = warnings.copy()
            result.trace = trace.copy()
            return result
        failure_result = _prefer_scihub_endpoint_failure(failure_result, result)

    if failure_result is None:
        failure_result = FetchResult(
            outcome="failed",
            source="Sci-Hub",
            via="scihub",
            state="error",
            warnings=["Sci-Hub lookup failed: no endpoints configured"],
        )
    failure_result.warnings = warnings.copy() or failure_result.warnings
    failure_result.trace = trace.copy()
    return failure_result


def _extract_scihub_pdf_url(html: str, endpoint: str) -> str | None:
    """Extract PDF URL from Sci-Hub page."""
    soup = BeautifulSoup(html, "lxml")

    # embed[type=application/pdf]
    embed = soup.find("embed", attrs={"type": "application/pdf"})
    if embed and embed.get("src"):
        return _normalize_scihub_url(str(embed["src"]), endpoint)

    # iframe src
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        return _normalize_scihub_url(str(iframe["src"]), endpoint)

    # button onclick
    button = soup.find("button")
    if button and button.get("onclick"):
        match = re.search(r"location\.href='([^']+\.pdf[^']*)'", str(button["onclick"]))
        if match:
            return _normalize_scihub_url(match.group(1), endpoint)

    return None


def _normalize_scihub_url(url: str, endpoint: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{endpoint}{url}"
    if not url.startswith("http"):
        return f"{endpoint}/{url}"
    return url
