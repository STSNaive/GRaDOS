"""PDF fetch waterfall: TDM → OA → Sci-Hub → Headless (Phase 2)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from bs4 import BeautifulSoup

from grados._retry import current_fetch_timeout, current_pdf_timeout, http_retry
from grados.config import GRaDOSPaths, HeadlessBrowserConfig
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
    text_format: str = ""  # markdown | text | html | xml
    metadata: PublisherMetadata | None = None
    asset_hints: list[dict[str, str]] = field(default_factory=list)
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


async def _run_headless_fetch_strategy(context: FetchStrategyContext) -> FetchResult:
    if not context.headless_config or not context.paths:
        return FetchResult(outcome="failed", warnings=["Headless browser not configured."])

    from grados.browser.generic import fetch_with_browser

    browser_result = await fetch_with_browser(context.doi, context.headless_config, context.paths)
    if browser_result.pdf_buffer:
        return FetchResult(
            pdf_buffer=browser_result.pdf_buffer,
            outcome="pdf_obtained",
            source=browser_result.source,
            warnings=browser_result.warnings,
        )
    return FetchResult(
        outcome="failed",
        source=browser_result.source,
        warnings=browser_result.warnings,
    )


async def _run_elsevier_tdm_provider(context: TDMProviderContext) -> FetchResult:
    key = context.api_keys.get("ELSEVIER_API_KEY", "")
    if not key:
        return FetchResult(outcome="failed")

    result: ElsevierFetchResult = await fetch_elsevier_article(context.doi, key, context.client)
    if result.outcome == "native_full_text":
        return FetchResult(
            text=result.text,
            outcome="native_full_text",
            source="Elsevier TDM",
            text_format=result.text_format,
            metadata=normalize_publisher_metadata(result.metadata),
            asset_hints=result.asset_hints,
        )
    return FetchResult(
        outcome=result.outcome or "failed",
        source="Elsevier TDM",
        metadata=normalize_publisher_metadata(result.metadata),
        asset_hints=result.asset_hints,
    )


async def _run_springer_tdm_provider(context: TDMProviderContext) -> FetchResult:
    meta_key = context.api_keys.get("SPRINGER_meta_API_KEY", "")
    oa_key = context.api_keys.get("SPRINGER_OA_API_KEY", "")
    if not meta_key:
        return FetchResult(outcome="failed")

    result: SpringerFetchResult = await fetch_springer_article(context.doi, meta_key, oa_key, context.client)
    if result.outcome == "native_full_text":
        return FetchResult(
            text=result.text,
            outcome="native_full_text",
            source="Springer TDM",
            text_format=result.text_format,
            metadata=normalize_publisher_metadata(result.metadata),
            asset_hints=result.asset_hints,
        )
    if result.outcome == "pdf_obtained":
        return FetchResult(
            pdf_buffer=result.pdf_buffer,
            outcome="pdf_obtained",
            source="Springer TDM",
            metadata=normalize_publisher_metadata(result.metadata),
            asset_hints=result.asset_hints,
        )
    return FetchResult(
        outcome=result.outcome or "failed",
        source="Springer TDM",
        metadata=normalize_publisher_metadata(result.metadata),
        asset_hints=result.asset_hints,
    )


FETCH_STRATEGY_REGISTRY: dict[str, FetchStrategy] = {
    "TDM": _FunctionFetchStrategy("TDM", _run_tdm_fetch_strategy),
    "OA": _FunctionFetchStrategy("OA", _run_oa_fetch_strategy),
    "SciHub": _FunctionFetchStrategy("SciHub", _run_scihub_fetch_strategy),
    "Headless": _FunctionFetchStrategy("Headless", _run_headless_fetch_strategy),
}

TDM_PROVIDER_REGISTRY: dict[str, TDMProvider] = {
    "Elsevier": _FunctionTDMProvider("Elsevier", _run_elsevier_tdm_provider),
    "Springer": _FunctionTDMProvider("Springer", _run_springer_tdm_provider),
}


def build_fetch_strategies(order: list[str] | None = None) -> list[FetchStrategy]:
    resolved_order = order or ["TDM", "OA", "SciHub", "Headless"]
    return [FETCH_STRATEGY_REGISTRY[name] for name in resolved_order if name in FETCH_STRATEGY_REGISTRY]


def build_tdm_providers(order: list[str] | None = None) -> list[TDMProvider]:
    resolved_order = order or ["Elsevier", "Springer"]
    return [TDM_PROVIDER_REGISTRY[name] for name in resolved_order if name in TDM_PROVIDER_REGISTRY]


def _is_fetch_success(result: FetchResult) -> bool:
    return result.outcome in {"native_full_text", "pdf_obtained"}


def _is_fetch_partial(result: FetchResult) -> bool:
    return result.outcome == "metadata_only"


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
) -> FetchResult:
    """Execute the fetch waterfall for a DOI."""
    strategies = build_fetch_strategies(fetch_order)
    enabled = fetch_enabled or {strategy.name: True for strategy in strategies}
    warnings: list[str] = []
    partial_result: FetchResult | None = None

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
        )

        for strategy in strategies:
            if not enabled.get(strategy.name, True):
                continue

            result = await strategy.run(context)
            warnings.extend(result.warnings)
            if _is_fetch_success(result):
                result.warnings = warnings.copy()
                return result
            if _is_fetch_partial(result):
                partial_result = _prefer_partial_result(partial_result, result)

    if partial_result is not None:
        partial_result.warnings = warnings.copy()
        return partial_result

    return FetchResult(outcome="failed", warnings=warnings)


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

    return FetchResult(outcome="failed")


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
            return FetchResult(outcome="failed", warnings=[f"OA lookup failed: HTTP {resp.status_code}"])

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
                    return FetchResult(pdf_buffer=pdf_resp.content, outcome="pdf_obtained", source="Unpaywall OA")
            except Exception as exc:
                warnings.append(_format_fetch_warning("OA PDF fetch failed", exc))
                continue
    except Exception as exc:
        warnings.append(_format_fetch_warning("OA lookup failed", exc))
    return FetchResult(outcome="failed", warnings=warnings)


# ── Sci-Hub ──────────────────────────────────────────────────────────────────


@http_retry()
async def _scihub_landing(client: httpx.AsyncClient, mirror: str, doi: str) -> httpx.Response:
    resp = await client.get(
        f"{mirror}/{doi}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"},
        timeout=current_fetch_timeout(),
    )
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


async def _fetch_scihub(
    doi: str,
    client: httpx.AsyncClient,
    config: dict[str, Any],
) -> FetchResult:
    mirror = config.get("fallback_mirror") or "https://sci-hub.se"
    warnings: list[str] = []

    try:
        resp = await _scihub_landing(client, mirror, doi)
        if resp.status_code != 200:
            return FetchResult(outcome="failed", warnings=[f"Sci-Hub lookup failed: HTTP {resp.status_code}"])

        if detect_bot_challenge("", resp.text, f"{mirror}/{doi}"):
            return FetchResult(outcome="failed", warnings=["Sci-Hub challenge detected"])

        # Extract PDF link
        pdf_url = _extract_scihub_pdf_url(resp.text, mirror)
        if not pdf_url:
            return FetchResult(outcome="failed", warnings=["Sci-Hub lookup failed: no PDF link found"])

        pdf_resp = await _download_pdf(client, pdf_url)
        ct = pdf_resp.headers.get("content-type", "")
        check = classify_pdf_content(pdf_resp.content, ct)
        if check["is_pdf"]:
            return FetchResult(pdf_buffer=pdf_resp.content, outcome="pdf_obtained", source="Sci-Hub")
        warnings.append(f"Sci-Hub PDF fetch failed: {check['reason']}")

    except Exception as exc:
        warnings.append(_format_fetch_warning("Sci-Hub fetch failed", exc))
    return FetchResult(outcome="failed", warnings=warnings)


def _extract_scihub_pdf_url(html: str, mirror: str) -> str | None:
    """Extract PDF URL from Sci-Hub page."""
    soup = BeautifulSoup(html, "lxml")

    # embed[type=application/pdf]
    embed = soup.find("embed", attrs={"type": "application/pdf"})
    if embed and embed.get("src"):
        return _normalize_scihub_url(str(embed["src"]), mirror)

    # iframe src
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        return _normalize_scihub_url(str(iframe["src"]), mirror)

    # button onclick
    button = soup.find("button")
    if button and button.get("onclick"):
        match = re.search(r"location\.href='([^']+\.pdf[^']*)'", str(button["onclick"]))
        if match:
            return _normalize_scihub_url(match.group(1), mirror)

    return None


def _normalize_scihub_url(url: str, mirror: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{mirror}{url}"
    if not url.startswith("http"):
        return f"{mirror}/{url}"
    return url
