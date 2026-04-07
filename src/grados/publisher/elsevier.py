"""Elsevier API: article retrieval, metadata extraction, ScienceDirect candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup


@dataclass
class ElsevierMetadataSignal:
    doi: str = ""
    title: str = ""
    abstract: str = ""
    pii: str = ""
    eid: str = ""
    openaccess: bool = False
    scidir_url: str = ""


@dataclass
class ElsevierFetchResult:
    text: str = ""
    metadata: ElsevierMetadataSignal | None = None
    outcome: str = ""  # native_full_text | metadata_only | failed
    asset_hints: list[dict[str, str]] = field(default_factory=list)


def _build_asset_hints(metadata: ElsevierMetadataSignal | None) -> list[dict[str, str]]:
    if not metadata:
        return []

    hints: list[dict[str, str]] = []
    if metadata.scidir_url:
        hints.append({
            "kind": "article_landing",
            "label": "ScienceDirect landing page",
            "url": metadata.scidir_url,
        })
    if metadata.pii:
        hints.append({
            "kind": "object_api_meta",
            "label": "Elsevier object metadata",
            "url": f"https://api.elsevier.com/content/object/pii/{metadata.pii}?view=META",
        })
    if metadata.eid:
        hints.append({
            "kind": "scopus_eid",
            "label": "Scopus EID",
            "value": metadata.eid,
        })
    return hints


def extract_metadata_signal(payload: Any, fallback_doi: str) -> ElsevierMetadataSignal | None:
    """Extract metadata from Elsevier API response."""
    try:
        retrieval = payload.get("full-text-retrieval-response", payload)
        coredata = retrieval.get("coredata", {})
        links = coredata.get("link", [])
        scidir = ""
        for link in links:
            href = link.get("@href", "")
            rel = link.get("@rel", "")
            if "scidir" in href or "scidir" in rel:
                scidir = href
                break
        return ElsevierMetadataSignal(
            doi=coredata.get("prism:doi", fallback_doi),
            title=coredata.get("dc:title", ""),
            abstract=coredata.get("dc:description", ""),
            pii=coredata.get("pii", ""),
            eid=coredata.get("eid", ""),
            openaccess=str(coredata.get("openaccess", "")).lower() in ("true", "1"),
            scidir_url=scidir,
        )
    except Exception:
        return None


async def fetch_elsevier_article(
    doi: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> ElsevierFetchResult:
    """Fetch article via Elsevier TDM API with waterfall: FULL JSON → text/plain → metadata."""
    if not api_key:
        return ElsevierFetchResult(outcome="failed")

    base = f"https://api.elsevier.com/content/article/doi/{doi}"

    # 1. FULL JSON
    try:
        resp = await client.get(
            base,
            params={"view": "FULL"},
            headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            retrieval = data.get("full-text-retrieval-response", {})
            text = retrieval.get("originalText", "")
            if text and len(text) > 1000:
                metadata = extract_metadata_signal(data, doi)
                return ElsevierFetchResult(
                    text=text,
                    metadata=metadata,
                    outcome="native_full_text",
                    asset_hints=_build_asset_hints(metadata),
                )
    except Exception:
        pass

    # 2. text/plain
    try:
        resp = await client.get(
            base,
            params={"httpAccept": "text/plain"},
            headers={"X-ELS-APIKey": api_key},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.text
            if text and len(text) > 1000:
                return ElsevierFetchResult(
                    text=text,
                    metadata=extract_metadata_signal(
                        {"full-text-retrieval-response": {"coredata": {"prism:doi": doi}}},
                        doi,
                    ),
                    outcome="native_full_text",
                )
    except Exception:
        pass

    # 3. Metadata only
    try:
        resp = await client.get(
            base,
            headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            metadata = extract_metadata_signal(resp.json(), doi)
            return ElsevierFetchResult(
                metadata=metadata,
                outcome="metadata_only",
                asset_hints=_build_asset_hints(metadata),
            )
    except Exception:
        pass

    return ElsevierFetchResult(outcome="failed")


def extract_sciencedirect_pdf_candidates(html: str, page_url: str) -> list[dict[str, str]]:
    """Extract PDF download candidate URLs from a ScienceDirect page."""
    candidates: list[dict[str, str]] = []
    soup = BeautifulSoup(html, "lxml")

    # citation_pdf_url meta tag
    meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if meta and meta.get("content"):
        candidates.append({"url": str(meta["content"]), "source": "citation_pdf_url"})

    # #pdfLink href
    pdf_link = soup.find(id="pdfLink")
    if pdf_link and pdf_link.get("href"):
        candidates.append({"url": str(pdf_link["href"]), "source": "pdfLink_href"})

    # Embedded object
    embed = soup.select_one(".PdfEmbed > object")
    if embed and embed.get("data"):
        candidates.append({"url": str(embed["data"]), "source": "embedded_object"})

    # Dropdown menu
    dropdown = soup.select_one(".PdfDropDownMenu a[href]")
    if dropdown:
        candidates.append({"url": str(dropdown["href"]), "source": "dropdown_menu"})

    # Fallback download button
    dl_btn = soup.select_one(".pdf-download-btn-link")
    if dl_btn and dl_btn.get("href"):
        candidates.append({"url": str(dl_btn["href"]), "source": "fallback_download_button"})

    # Canonical URL → /pdfft
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        pdfft_url = re.sub(r"/?$", "/pdfft?download=true", str(canonical["href"]))
        candidates.append({"url": pdfft_url, "source": "canonical_pdfft"})

    return candidates


def parse_sciencedirect_intermediate_redirect(html: str, url: str) -> str | None:
    """Detect meta-refresh redirect on ScienceDirect intermediate pages."""
    match = re.search(
        r'<meta[^>]+http-equiv=["\']?Refresh["\']?[^>]+content=["\']?\d+\s*;\s*url=([^"\'>\s]+)',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    soup = BeautifulSoup(html, "lxml")
    redirect_link = soup.select_one("#redirect-message a[href]")
    if redirect_link:
        return str(redirect_link["href"])

    return None
