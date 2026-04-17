"""Springer Nature API: metadata fetch, JATS XML, HTML, PDF retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from grados._retry import current_fetch_timeout, current_pdf_timeout, http_retry


@dataclass
class SpringerMetaRecord:
    doi: str = ""
    title: str = ""
    abstract: str = ""
    publisher: str = ""
    openaccess: bool = False
    html_url: str = ""
    pdf_url: str = ""


@dataclass
class SpringerFetchResult:
    text: str = ""
    pdf_buffer: bytes = b""
    metadata: SpringerMetaRecord | None = None
    outcome: str = ""  # native_full_text | pdf_obtained | failed
    text_format: str = ""  # xml | html | markdown | text
    asset_hints: list[dict[str, str]] = field(default_factory=list)


def _build_asset_hints(meta: SpringerMetaRecord | None) -> list[dict[str, str]]:
    if not meta:
        return []

    hints: list[dict[str, str]] = []
    if meta.html_url:
        hints.append({
            "kind": "article_html",
            "label": "Springer HTML landing page",
            "url": meta.html_url,
        })
    if meta.pdf_url:
        hints.append({
            "kind": "article_pdf",
            "label": "Springer PDF",
            "url": meta.pdf_url,
        })
    return hints


@http_retry()
async def _springer_meta_request(
    client: httpx.AsyncClient,
    doi: str,
    api_key: str,
) -> dict:
    resp = await client.get(
        "https://api.springernature.com/meta/v2/json",
        params={"q": f"doi:{doi}", "api_key": api_key},
        timeout=current_fetch_timeout(),
    )
    resp.raise_for_status()
    return resp.json()


@http_retry()
async def _springer_oa_jats_request(
    client: httpx.AsyncClient,
    doi: str,
    api_key: str,
) -> httpx.Response:
    resp = await client.get(
        "https://api.springernature.com/openaccess/jats",
        params={"q": f"doi:{doi}", "api_key": api_key},
        timeout=current_fetch_timeout(),
    )
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


@http_retry()
async def _springer_html_download(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.get(url, timeout=current_fetch_timeout(), follow_redirects=True)
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


@http_retry()
async def _springer_pdf_download(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.get(url, timeout=current_pdf_timeout(), follow_redirects=True)
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


async def fetch_springer_meta(
    doi: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> SpringerMetaRecord | None:
    """Fetch metadata from Springer Meta API."""
    if not api_key:
        return None
    try:
        payload = await _springer_meta_request(client, doi, api_key)
        records = payload.get("records", [])
        if not records:
            return None
        rec = records[0]
        urls = rec.get("url", [])
        html_url = ""
        pdf_url = ""
        for u in urls:
            fmt = u.get("format", "").lower()
            val = u.get("value", "")
            if "html" in fmt:
                html_url = val
            elif "pdf" in fmt:
                pdf_url = val
        return SpringerMetaRecord(
            doi=rec.get("doi", doi),
            title=rec.get("title", ""),
            abstract=rec.get("abstract", ""),
            publisher=rec.get("publisher", ""),
            openaccess=str(rec.get("openaccess", "")).lower() == "true",
            html_url=html_url,
            pdf_url=pdf_url,
        )
    except Exception:
        return None


async def fetch_springer_article(
    doi: str,
    meta_api_key: str,
    oa_api_key: str,
    client: httpx.AsyncClient,
) -> SpringerFetchResult:
    """Fetch article via Springer APIs: OA JATS XML → HTML → PDF."""
    meta = await fetch_springer_meta(doi, meta_api_key, client)
    if not meta:
        return SpringerFetchResult(outcome="failed")

    # 1. OA JATS XML
    if (meta.openaccess or oa_api_key) and oa_api_key:
        try:
            resp = await _springer_oa_jats_request(client, doi, oa_api_key)
            if resp.status_code == 200 and resp.text:
                if len(resp.text) > 1000:
                    return SpringerFetchResult(
                        text=resp.text,
                        metadata=meta,
                        outcome="native_full_text",
                        text_format="xml",
                        asset_hints=_build_asset_hints(meta),
                    )
        except Exception:
            pass

    # 2. Direct HTML
    if meta.html_url:
        try:
            resp = await _springer_html_download(client, meta.html_url)
            if resp.status_code == 200:
                if resp.text and len(resp.text) > 1000:
                    return SpringerFetchResult(
                        text=resp.text,
                        metadata=meta,
                        outcome="native_full_text",
                        text_format="html",
                        asset_hints=_build_asset_hints(meta),
                    )
        except Exception:
            pass

    # 3. Direct PDF
    if meta.pdf_url:
        try:
            resp = await _springer_pdf_download(client, meta.pdf_url)
            if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
                return SpringerFetchResult(
                    pdf_buffer=resp.content,
                    metadata=meta,
                    outcome="pdf_obtained",
                    asset_hints=_build_asset_hints(meta),
                )
        except Exception:
            pass

    return SpringerFetchResult(outcome="failed")
