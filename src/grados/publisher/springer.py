"""Springer Nature API: metadata fetch, JATS XML, HTML, PDF retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup


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
    outcome: str = ""  # native_full_text | pdf_obtained | failed
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


async def fetch_springer_meta(
    doi: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> SpringerMetaRecord | None:
    """Fetch metadata from Springer Meta API."""
    if not api_key:
        return None
    try:
        resp = await client.get(
            "https://api.springernature.com/meta/v2/json",
            params={"q": f"doi:{doi}", "api_key": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])
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
    """Fetch article via Springer APIs: OA JATS → HTML → PDF."""
    meta = await fetch_springer_meta(doi, meta_api_key, client)
    if not meta:
        return SpringerFetchResult(outcome="failed")

    # 1. OA JATS XML
    if (meta.openaccess or oa_api_key) and oa_api_key:
        try:
            resp = await client.get(
                "https://api.springernature.com/openaccess/jats",
                params={"q": f"doi:{doi}", "api_key": oa_api_key},
                timeout=30,
            )
            if resp.status_code == 200 and resp.text:
                text = _extract_jats_text(resp.text)
                if text and len(text) > 1000:
                    return SpringerFetchResult(
                        text=text,
                        outcome="native_full_text",
                        asset_hints=_build_asset_hints(meta),
                    )
        except Exception:
            pass

    # 2. Direct HTML
    if meta.html_url:
        try:
            resp = await client.get(meta.html_url, timeout=30, follow_redirects=True)
            if resp.status_code == 200:
                text = _extract_html_text(resp.text, meta.title, meta.abstract)
                if text and len(text) > 1000:
                    return SpringerFetchResult(
                        text=text,
                        outcome="native_full_text",
                        asset_hints=_build_asset_hints(meta),
                    )
        except Exception:
            pass

    # 3. Direct PDF
    if meta.pdf_url:
        try:
            resp = await client.get(meta.pdf_url, timeout=30, follow_redirects=True)
            if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
                return SpringerFetchResult(
                    pdf_buffer=resp.content,
                    outcome="pdf_obtained",
                    asset_hints=_build_asset_hints(meta),
                )
        except Exception:
            pass

    return SpringerFetchResult(outcome="failed")


def _extract_jats_text(xml: str) -> str:
    """Extract text from Springer JATS XML."""
    soup = BeautifulSoup(xml, "lxml-xml")
    body = soup.find("body")
    if not body:
        return ""
    sections: list[str] = []
    for sec in body.find_all("sec"):
        title = sec.find("title")
        if title:
            sections.append(f"## {title.get_text(strip=True)}")
        for p in sec.find_all("p", recursive=False):
            sections.append(p.get_text(separator=" ", strip=True))
    return "\n\n".join(sections)


def _extract_html_text(html: str, title: str, abstract: str) -> str:
    """Extract main article text from Springer HTML page."""
    soup = BeautifulSoup(html, "lxml")
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    if abstract:
        parts.append(f"## Abstract\n\n{abstract}")

    # Try article body
    article = soup.find("article") or soup.find("main") or soup
    for section in article.find_all(["section", "div"], class_=lambda c: c and "section" in str(c).lower()):
        heading = section.find(["h1", "h2", "h3", "h4"])
        if heading:
            parts.append(f"## {heading.get_text(strip=True)}")
        for p in section.find_all("p", recursive=False):
            text = p.get_text(separator=" ", strip=True)
            if text:
                parts.append(text)

    return "\n\n".join(parts)
