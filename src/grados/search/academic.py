"""Academic database search providers: Crossref, PubMed, WoS, Elsevier (Scopus), Springer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from grados.publisher.common import looks_like_doi


# ── Shared types ─────────────────────────────────────────────────────────────


@dataclass
class PaperMetadata:
    title: str = ""
    doi: str = ""
    abstract: str = ""
    publisher: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    url: str = ""
    source: str = ""


@dataclass
class SearchPageResult:
    papers: list[PaperMetadata]
    exhausted: bool
    warnings: list[str] = field(default_factory=list)


def _clamp_page_size(limit: int, max_size: int) -> int:
    if limit <= 0:
        return min(15, max_size)
    return max(1, min(limit, max_size))


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text) if text else ""


# ── Crossref ─────────────────────────────────────────────────────────────────


@dataclass
class CrossrefState:
    cursor: str = "*"
    rows: int = 15
    pages_fetched: int = 0
    cursor_issued_at: str = ""


async def search_crossref(
    query: str,
    limit: int,
    state: CrossrefState,
    etiquette_email: str,
    client: httpx.AsyncClient,
) -> tuple[SearchPageResult, CrossrefState]:
    try:
        resp = await client.get(
            "https://api.crossref.org/works",
            params={
                "query": query,
                "rows": state.rows,
                "cursor": state.cursor,
                "select": "DOI,title,abstract,publisher,author,published-print,URL",
            },
            headers={
                "User-Agent": f"GRaDOS/1.0 (mailto:{etiquette_email}) Python/httpx",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return (
            SearchPageResult([], exhausted=True, warnings=[f"Crossref search failed: {e}"]),
            state,
        )

    items = data.get("message", {}).get("items", [])
    next_cursor = data.get("message", {}).get("next-cursor")

    papers = []
    for item in items:
        papers.append(PaperMetadata(
            title=(item.get("title") or [""])[0],
            doi=item.get("DOI", ""),
            abstract=_strip_html(item.get("abstract", "")),
            publisher=item.get("publisher", ""),
            authors=[f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (item.get("author") or [])],
            year=str((item.get("published-print") or {}).get("date-parts", [[""]])[0][0] or ""),
            url=item.get("URL", ""),
            source="Crossref",
        ))

    exhausted = len(items) == 0 or len(items) < state.rows or not next_cursor
    new_state = CrossrefState(
        cursor=next_cursor or state.cursor,
        rows=state.rows,
        pages_fetched=state.pages_fetched + 1,
        cursor_issued_at=state.cursor_issued_at or datetime.now(timezone.utc).isoformat(),
    )
    return SearchPageResult(papers, exhausted), new_state


# ── PubMed ───────────────────────────────────────────────────────────────────


@dataclass
class PubMedState:
    retstart: int = 0
    page_size: int = 15
    total_count: int | None = None


async def search_pubmed(
    query: str,
    limit: int,
    state: PubMedState,
    client: httpx.AsyncClient,
) -> tuple[SearchPageResult, PubMedState]:
    try:
        # Step 1: ESearch
        esearch_resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmode": "json", "retstart": state.retstart, "retmax": state.page_size},
            timeout=30,
        )
        esearch_resp.raise_for_status()
        esearch = esearch_resp.json()
        result = esearch.get("esearchresult", {})
        pmids = result.get("idlist", [])
        total = int(result.get("count", 0))

        if not pmids:
            new_state = PubMedState(state.retstart, state.page_size, total)
            return SearchPageResult([], exhausted=True), new_state

        # Step 2: ESummary
        esummary_resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
            timeout=30,
        )
        esummary_resp.raise_for_status()
        summaries = esummary_resp.json().get("result", {})

        # Step 3: EFetch for abstracts (best-effort)
        abstracts: dict[str, str] = {}
        try:
            efetch_resp = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(pmids), "rettype": "xml", "retmode": "xml"},
                timeout=30,
            )
            efetch_resp.raise_for_status()
            soup = BeautifulSoup(efetch_resp.text, "lxml-xml")
            for article in soup.find_all("PubmedArticle"):
                pmid_tag = article.find("PMID")
                abstract_tag = article.find("Abstract")
                if pmid_tag and abstract_tag:
                    abstracts[pmid_tag.get_text()] = abstract_tag.get_text(separator=" ", strip=True)
        except Exception:
            pass  # Continue without abstracts

        papers = []
        for pmid in pmids:
            paper = summaries.get(pmid, {})
            if not isinstance(paper, dict):
                continue
            doi = ""
            for aid in paper.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi = aid.get("value", "")
                    break
            papers.append(PaperMetadata(
                title=paper.get("title", ""),
                doi=doi,
                abstract=abstracts.get(pmid, ""),
                publisher=paper.get("fulljournalname", ""),
                authors=[a.get("name", "") for a in (paper.get("authors") or [])],
                year=(paper.get("pubdate") or "").split(" ")[0] if paper.get("pubdate") else "",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                source="PubMed",
            ))

        exhausted = (
            len(pmids) < state.page_size
            or (total > 0 and state.retstart + state.page_size >= total)
        )
        new_state = PubMedState(state.retstart + state.page_size, state.page_size, total)
        return SearchPageResult(papers, exhausted), new_state

    except Exception as e:
        return (
            SearchPageResult([], exhausted=True, warnings=[f"PubMed search failed: {e}"]),
            state,
        )


# ── Web of Science ───────────────────────────────────────────────────────────


@dataclass
class WoSState:
    page: int = 1
    page_size: int = 15


async def search_wos(
    query: str,
    limit: int,
    state: WoSState,
    api_key: str,
    client: httpx.AsyncClient,
) -> tuple[SearchPageResult, WoSState]:
    if not api_key:
        return SearchPageResult([], exhausted=True, warnings=["WoS API key not configured"]), state

    try:
        q = f"DO=({query})" if looks_like_doi(query) else f"TS=({query})"
        resp = await client.get(
            "https://api.clarivate.com/apis/wos-starter/v1/documents",
            params={"q": q, "page": state.page, "limit": state.page_size},
            headers={"X-ApiKey": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return (
            SearchPageResult([], exhausted=True, warnings=[f"WoS search failed: {e}"]),
            state,
        )

    hits = data.get("hits", [])
    total = data.get("metadata", {}).get("total", 0)

    papers = []
    for hit in hits:
        papers.append(PaperMetadata(
            title=hit.get("title", ""),
            doi=(hit.get("identifiers") or {}).get("doi", ""),
            abstract=hit.get("abstract", ""),
            publisher=(hit.get("source") or {}).get("sourceTitle", ""),
            authors=[a.get("displayName", "") for a in (hit.get("names", {}).get("authors") or [])],
            year=str((hit.get("source") or {}).get("publishYear", "")),
            url=(hit.get("links") or {}).get("record", ""),
            source="Web of Science",
        ))

    exhausted = len(hits) == 0 or len(hits) < state.page_size or (total > 0 and state.page * state.page_size >= total)
    new_state = WoSState(state.page + 1, state.page_size)
    return SearchPageResult(papers, exhausted), new_state


# ── Elsevier (Scopus) ────────────────────────────────────────────────────────


@dataclass
class ElsevierState:
    start: int = 0
    page_size: int = 15


async def search_elsevier(
    query: str,
    limit: int,
    state: ElsevierState,
    api_key: str,
    client: httpx.AsyncClient,
) -> tuple[SearchPageResult, ElsevierState]:
    if not api_key:
        return SearchPageResult([], exhausted=True, warnings=["Elsevier API key not configured"]), state

    try:
        resp = await client.get(
            "https://api.elsevier.com/content/search/scopus",
            params={"query": query, "count": state.page_size, "start": state.start, "view": "COMPLETE"},
            headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return (
            SearchPageResult([], exhausted=True, warnings=[f"Elsevier search failed: {e}"]),
            state,
        )

    entries = data.get("search-results", {}).get("entry", [])
    total = int(data.get("search-results", {}).get("opensearch:totalResults", 0))

    papers = []
    for item in entries:
        papers.append(PaperMetadata(
            title=item.get("dc:title", ""),
            doi=item.get("prism:doi", ""),
            abstract=item.get("dc:description", ""),
            publisher=item.get("prism:publicationName", ""),
            authors=[a.get("authname", "") for a in (item.get("author") or [])],
            year=(item.get("prism:coverDate") or "").split("-")[0],
            url=item.get("prism:url", ""),
            source="Elsevier (Scopus)",
        ))

    exhausted = len(entries) == 0 or len(entries) < state.page_size or (total > 0 and state.start + state.page_size >= total)
    new_state = ElsevierState(state.start + state.page_size, state.page_size)
    return SearchPageResult(papers, exhausted), new_state


# ── Springer ─────────────────────────────────────────────────────────────────


@dataclass
class SpringerState:
    fetched: bool = False
    page_size: int = 15


async def search_springer(
    query: str,
    limit: int,
    state: SpringerState,
    api_key: str,
    client: httpx.AsyncClient,
) -> tuple[SearchPageResult, SpringerState]:
    if not api_key:
        return SearchPageResult([], exhausted=True, warnings=["Springer API key not configured"]), state

    if state.fetched:
        return SearchPageResult([], exhausted=True), state

    try:
        q = f"doi:{query.strip()}" if looks_like_doi(query) else f'keyword:"{query.replace(chr(34), "")}"'
        resp = await client.get(
            "https://api.springernature.com/meta/v2/json",
            params={"q": q, "p": state.page_size, "api_key": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return (
            SearchPageResult([], exhausted=True, warnings=[f"Springer search failed: {e}"]),
            SpringerState(fetched=True, page_size=state.page_size),
        )

    records = data.get("records", [])
    papers = []
    for item in records:
        urls = item.get("url", [])
        url = urls[0].get("value", "") if urls else ""
        papers.append(PaperMetadata(
            title=item.get("title", ""),
            doi=item.get("doi", ""),
            abstract=item.get("abstract", ""),
            publisher=item.get("publisher", ""),
            authors=[c.get("creator", "") for c in (item.get("creators") or [])],
            year=(item.get("publicationDate") or "").split("-")[0],
            url=url,
            source="Springer Nature",
        ))

    warnings = ["Springer uses conservative single-page strategy."]
    new_state = SpringerState(fetched=True, page_size=state.page_size)
    return SearchPageResult(papers, exhausted=True, warnings=warnings), new_state


# ── Adapter registry ─────────────────────────────────────────────────────────

# Maps source name → (state_factory, fetch_function, max_page_size)
# Used by resumable search to generically iterate sources.

SearchSourceName = str
SearchState = CrossrefState | PubMedState | WoSState | ElsevierState | SpringerState


def build_search_adapters(
    api_keys: dict[str, str],
    etiquette_email: str,
    limit: int,
) -> dict[str, dict[str, Any]]:
    """Build adapter registry for all search sources."""
    return {
        "Crossref": {
            "init": lambda: CrossrefState(
                cursor="*",
                rows=_clamp_page_size(limit, 100),
                cursor_issued_at=datetime.now(timezone.utc).isoformat(),
            ),
            "fetch": lambda q, lim, st, cl: search_crossref(q, lim, st, etiquette_email, cl),
            "max_page_size": 100,
        },
        "PubMed": {
            "init": lambda: PubMedState(page_size=_clamp_page_size(limit, 100)),
            "fetch": lambda q, lim, st, cl: search_pubmed(q, lim, st, cl),
            "max_page_size": 100,
        },
        "WebOfScience": {
            "init": lambda: WoSState(page_size=_clamp_page_size(limit, 50)),
            "fetch": lambda q, lim, st, cl: search_wos(q, lim, st, api_keys.get("WOS_API_KEY", ""), cl),
            "max_page_size": 50,
        },
        "Elsevier": {
            "init": lambda: ElsevierState(page_size=_clamp_page_size(limit, 25)),
            "fetch": lambda q, lim, st, cl: search_elsevier(q, lim, st, api_keys.get("ELSEVIER_API_KEY", ""), cl),
            "max_page_size": 25,
        },
        "Springer": {
            "init": lambda: SpringerState(page_size=_clamp_page_size(limit, 50)),
            "fetch": lambda q, lim, st, cl: search_springer(q, lim, st, api_keys.get("SPRINGER_meta_API_KEY", ""), cl),
            "max_page_size": 50,
        },
    }
