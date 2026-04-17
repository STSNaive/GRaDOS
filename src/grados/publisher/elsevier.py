"""Elsevier API: article retrieval, metadata extraction, ScienceDirect candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from grados._retry import current_fetch_timeout, http_retry


@dataclass
class ElsevierMetadataSignal:
    doi: str = ""
    title: str = ""
    abstract: str = ""
    pii: str = ""
    eid: str = ""
    journal: str = ""
    year: str = ""
    authors: list[str] = field(default_factory=list)
    openaccess: bool = False
    scidir_url: str = ""


@dataclass
class ElsevierFetchResult:
    text: str = ""
    metadata: ElsevierMetadataSignal | None = None
    outcome: str = ""  # native_full_text | metadata_only | failed
    text_format: str = ""  # markdown | text
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
            journal=coredata.get("prism:publicationName", ""),
            year=str(coredata.get("prism:coverDate", "")).split("-")[0],
            openaccess=str(coredata.get("openaccess", "")).lower() in ("true", "1"),
            scidir_url=scidir,
        )
    except Exception:
        return None


@http_retry()
async def _elsevier_article_xml(
    client: httpx.AsyncClient,
    base: str,
    api_key: str,
) -> httpx.Response:
    resp = await client.get(
        base,
        params={"view": "FULL"},
        headers={"X-ELS-APIKey": api_key, "Accept": "application/xml"},
        timeout=current_fetch_timeout(),
    )
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


@http_retry()
async def _elsevier_article_json(
    client: httpx.AsyncClient,
    base: str,
    api_key: str,
) -> httpx.Response:
    resp = await client.get(
        base,
        headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
        timeout=current_fetch_timeout(),
    )
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


async def fetch_elsevier_article(
    doi: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> ElsevierFetchResult:
    """Fetch article via Elsevier TDM API with waterfall: XML FULL → metadata."""
    if not api_key:
        return ElsevierFetchResult(outcome="failed")

    base = f"https://api.elsevier.com/content/article/doi/{doi}"

    # 1. FULL XML
    try:
        resp = await _elsevier_article_xml(client, base, api_key)
        if resp.status_code == 200 and resp.text:
            text, metadata = _extract_elsevier_markdown_from_xml(resp.text, fallback_doi=doi)
            if text and len(text) > 1000:
                return ElsevierFetchResult(
                    text=text,
                    metadata=metadata,
                    outcome="native_full_text",
                    text_format="markdown",
                    asset_hints=_build_asset_hints(metadata),
                )
    except Exception:
        pass

    # 2. Metadata only
    try:
        resp = await _elsevier_article_json(client, base, api_key)
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


def _extract_elsevier_markdown_from_xml(
    xml_payload: str,
    *,
    fallback_doi: str,
) -> tuple[str, ElsevierMetadataSignal | None]:
    root = ET.fromstring(xml_payload)
    metadata = _extract_metadata_from_xml(root, fallback_doi=fallback_doi)

    parts: list[str] = []
    title = (metadata.title if metadata else "").strip()
    if title:
        parts.append(f"# {title}")

    author_line = _format_author_line(metadata)
    if author_line:
        parts.append(author_line)

    abstract = (metadata.abstract if metadata else "").strip()
    if abstract:
        parts.append(f"## Abstract\n\n{abstract}")

    keywords = _extract_keywords_from_xml(root)
    if keywords:
        parts.append("## Keywords\n\n" + "\n".join(f"- {keyword}" for keyword in keywords))

    sections = _extract_sections_from_xml(root)
    if sections:
        parts.extend(sections)

    references = _extract_references_from_xml(root)
    if references:
        parts.append("## References\n\n" + "\n\n".join(references))

    markdown = "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return markdown, metadata


def _extract_metadata_from_xml(root: ET.Element, *, fallback_doi: str) -> ElsevierMetadataSignal | None:
    coredata = _find_first_local(root, "coredata")
    if coredata is None:
        return None

    doi = _text_for_first_local(coredata, "doi") or fallback_doi
    title = _text_for_first_local(coredata, "title")
    abstract = _text_for_first_local(coredata, "description")
    pii = _text_for_first_local(coredata, "pii")
    eid = _text_for_first_local(coredata, "eid")
    journal = _text_for_first_local(coredata, "publicationName")
    cover_date = _text_for_first_local(coredata, "coverDate")
    scidir_url = ""
    if pii:
        scidir_url = f"https://www.sciencedirect.com/science/article/pii/{pii}"

    return ElsevierMetadataSignal(
        doi=doi,
        title=title,
        abstract=abstract,
        pii=pii,
        eid=eid,
        journal=journal,
        year=cover_date.split("-")[0] if cover_date else "",
        authors=_extract_authors_from_xml(root),
        openaccess=_text_for_first_local(coredata, "openaccess").lower() in {"1", "true"},
        scidir_url=scidir_url,
    )


def _extract_authors_from_xml(root: ET.Element) -> list[str]:
    names: list[str] = []
    author_group = _find_first_local(root, "author-group")
    if author_group is None:
        return names

    for author in _find_children_local(author_group, "author"):
        given = _text_for_first_local(author, "given-name")
        surname = _text_for_first_local(author, "surname")
        name = " ".join(part for part in [given, surname] if part).strip()
        if name:
            names.append(name)
    return names


def _extract_keywords_from_xml(root: ET.Element) -> list[str]:
    keywords: list[str] = []
    for keyword in _find_all_local(root, "keyword"):
        text = _clean_inline_text(" ".join(keyword.itertext()))
        if text and text not in keywords:
            keywords.append(text)
    return keywords


def _extract_sections_from_xml(root: ET.Element) -> list[str]:
    sections_root = _find_first_local(root, "sections")
    if sections_root is None:
        return []

    rendered: list[str] = []
    for section in _find_children_local(sections_root, "section"):
        rendered.extend(_render_elsevier_section(section, depth=2))
    return rendered


def _render_elsevier_section(section: ET.Element, *, depth: int) -> list[str]:
    parts: list[str] = []
    title = _text_for_first_local(section, "section-title") or _text_for_first_local(section, "title")
    if title:
        parts.append(f"{'#' * max(2, depth)} {title}")

    for child in list(section):
        local_name = _local_name(child)
        if local_name in {"para", "simple-para"}:
            paragraph = _clean_inline_text(" ".join(child.itertext()))
            if paragraph:
                parts.append(paragraph)
        elif local_name == "section":
            parts.extend(_render_elsevier_section(child, depth=depth + 1))

    return parts


def _extract_references_from_xml(root: ET.Element) -> list[str]:
    bibliography = _find_first_local(root, "bibliography-sec")
    if bibliography is None:
        bibliography = _find_first_local(root, "bibliography")
    if bibliography is None:
        return []

    references: list[str] = []
    for reference in _find_all_local(bibliography, "bib-reference"):
        text = _clean_inline_text(" ".join(reference.itertext()))
        if text:
            references.append(text)
    return references


def _local_name(element: ET.Element) -> str:
    return element.tag.split("}", 1)[-1]


def _find_first_local(root: ET.Element, name: str) -> ET.Element | None:
    for element in root.iter():
        if _local_name(element) == name:
            return element
    return None


def _find_all_local(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element) == name]


def _find_children_local(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in list(root) if _local_name(element) == name]


def _text_for_first_local(root: ET.Element, name: str) -> str:
    element = _find_first_local(root, name)
    if element is None:
        return ""
    return _clean_inline_text(" ".join(element.itertext()))


def _clean_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _format_author_line(metadata: ElsevierMetadataSignal | None) -> str:
    if not metadata or not metadata.authors:
        return ""
    return "Authors: " + ", ".join(metadata.authors)


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
