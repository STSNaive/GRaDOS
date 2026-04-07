"""PDF fetch waterfall: TDM → OA → Sci-Hub → Headless (Phase 2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup

from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.publisher.common import classify_pdf_content, detect_bot_challenge
from grados.publisher.elsevier import ElsevierFetchResult, fetch_elsevier_article
from grados.publisher.springer import SpringerFetchResult, fetch_springer_article


@dataclass
class FetchResult:
    text: str = ""
    pdf_buffer: bytes = b""
    outcome: str = ""  # native_full_text | pdf_obtained | metadata_only | failed
    source: str = ""  # e.g. "Elsevier TDM", "Unpaywall OA", "Sci-Hub"
    metadata: Any = None
    asset_hints: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    order = fetch_order or ["TDM", "OA", "SciHub", "Headless"]
    enabled = fetch_enabled or {s: True for s in order}
    warnings: list[str] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for strategy in order:
            if not enabled.get(strategy, True):
                continue

            if strategy == "TDM":
                result = await _fetch_tdm(doi, api_keys, client, tdm_order, tdm_enabled)
                if result.outcome in ("native_full_text", "pdf_obtained"):
                    result.warnings = warnings
                    return result

            elif strategy == "OA":
                result = await _fetch_oa(doi, etiquette_email, client)
                if result.outcome == "pdf_obtained":
                    result.warnings = warnings
                    return result

            elif strategy == "SciHub":
                result = await _fetch_scihub(doi, client, sci_hub_config or {})
                if result.outcome == "pdf_obtained":
                    result.warnings = warnings
                    return result

            elif strategy == "Headless":
                if headless_config and paths:
                    from grados.browser.generic import fetch_with_browser

                    browser_result = await fetch_with_browser(doi, headless_config, paths)
                    if browser_result["pdf_buffer"]:
                        return FetchResult(
                            pdf_buffer=browser_result["pdf_buffer"],
                            outcome="pdf_obtained",
                            source=browser_result["source"],
                            warnings=warnings + browser_result.get("warnings", []),
                        )
                    warnings.extend(browser_result.get("warnings", []))
                else:
                    warnings.append("Headless browser not configured.")

    return FetchResult(outcome="failed", warnings=warnings)


# ── TDM ──────────────────────────────────────────────────────────────────────


async def _fetch_tdm(
    doi: str,
    api_keys: dict[str, str],
    client: httpx.AsyncClient,
    tdm_order: list[str] | None = None,
    tdm_enabled: dict[str, bool] | None = None,
) -> FetchResult:
    order = tdm_order or ["Elsevier", "Springer"]
    enabled = tdm_enabled or {publisher: True for publisher in order}
    for publisher in order:
        if not enabled.get(publisher, True):
            continue
        if publisher == "Elsevier":
            key = api_keys.get("ELSEVIER_API_KEY", "")
            if not key:
                continue
            r: ElsevierFetchResult = await fetch_elsevier_article(doi, key, client)
            if r.outcome == "native_full_text":
                return FetchResult(
                    text=r.text,
                    outcome="native_full_text",
                    source="Elsevier TDM",
                    metadata=r.metadata,
                    asset_hints=r.asset_hints,
                )
            if r.outcome == "metadata_only":
                # Save metadata signal but don't return yet — try other strategies
                pass

        elif publisher == "Springer":
            meta_key = api_keys.get("SPRINGER_meta_API_KEY", "")
            oa_key = api_keys.get("SPRINGER_OA_API_KEY", "")
            if not meta_key:
                continue
            r2: SpringerFetchResult = await fetch_springer_article(doi, meta_key, oa_key, client)
            if r2.outcome == "native_full_text":
                return FetchResult(
                    text=r2.text,
                    outcome="native_full_text",
                    source="Springer TDM",
                    asset_hints=r2.asset_hints,
                )
            if r2.outcome == "pdf_obtained":
                return FetchResult(
                    pdf_buffer=r2.pdf_buffer,
                    outcome="pdf_obtained",
                    source="Springer TDM",
                    asset_hints=r2.asset_hints,
                )

    return FetchResult(outcome="failed")


# ── OA (Unpaywall) ──────────────────────────────────────────────────────────


async def _fetch_oa(
    doi: str,
    etiquette_email: str,
    client: httpx.AsyncClient,
) -> FetchResult:
    try:
        resp = await client.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": etiquette_email},
            timeout=30,
        )
        if resp.status_code != 200:
            return FetchResult(outcome="failed")

        locations = resp.json().get("oa_locations", [])
        # Prefer repository sources (arXiv, PMC) over publisher
        locations.sort(key=lambda loc: 0 if loc.get("host_type") == "repository" else 1)

        for loc in locations:
            pdf_url = loc.get("url_for_pdf")
            if not pdf_url:
                continue
            try:
                pdf_resp = await client.get(pdf_url, timeout=30)
                ct = pdf_resp.headers.get("content-type", "")
                check = classify_pdf_content(pdf_resp.content, ct)
                if check["is_pdf"]:
                    return FetchResult(pdf_buffer=pdf_resp.content, outcome="pdf_obtained", source="Unpaywall OA")
            except Exception:
                continue
    except Exception:
        pass
    return FetchResult(outcome="failed")


# ── Sci-Hub ──────────────────────────────────────────────────────────────────


async def _fetch_scihub(
    doi: str,
    client: httpx.AsyncClient,
    config: dict[str, Any],
) -> FetchResult:
    mirror = config.get("fallback_mirror") or config.get("fallbackMirror") or "https://sci-hub.se"

    try:
        resp = await client.get(
            f"{mirror}/{doi}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"},
            timeout=30,
        )
        if resp.status_code != 200:
            return FetchResult(outcome="failed")

        if detect_bot_challenge("", resp.text, f"{mirror}/{doi}"):
            return FetchResult(outcome="failed", warnings=["Sci-Hub challenge detected"])

        # Extract PDF link
        pdf_url = _extract_scihub_pdf_url(resp.text, mirror)
        if not pdf_url:
            return FetchResult(outcome="failed")

        pdf_resp = await client.get(pdf_url, timeout=30)
        ct = pdf_resp.headers.get("content-type", "")
        check = classify_pdf_content(pdf_resp.content, ct)
        if check["is_pdf"]:
            return FetchResult(pdf_buffer=pdf_resp.content, outcome="pdf_obtained", source="Sci-Hub")

    except Exception:
        pass
    return FetchResult(outcome="failed")


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
