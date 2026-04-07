"""ChromaDB canonical storage for paper documents and retrieval chunks."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DOCS_COLLECTION_NAME = "papers_docs"
_CHUNKS_COLLECTION_NAME = "papers_chunks"
_MAX_CHUNK_CHARS = 1000
_DOC_PLACEHOLDER_EMBEDDING = [0.0]


def _get_client(chroma_dir: Path) -> Any:
    """Return a persistent ChromaDB client."""
    import chromadb

    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def _get_docs_collection(client: Any) -> Any:
    """Get or create the canonical document collection."""
    return client.get_or_create_collection(
        name=_DOCS_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _get_chunks_collection(client: Any) -> Any:
    """Get or create the retrieval chunk collection."""
    return client.get_or_create_collection(
        name=_CHUNKS_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3 :].strip()
    return text.strip()


def _chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks by paragraphs, respecting max_chars."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current:
        chunks.append(current)

    return chunks


def _extract_headings(markdown: str) -> list[str]:
    return re.findall(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)[:20]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _serialize_str_list(values: list[str] | None) -> str:
    return json.dumps([v for v in (values or []) if v], ensure_ascii=False)


def _deserialize_str_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw if str(v)]
    try:
        loaded = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(v) for v in loaded if str(v)]


def _doc_metadata(
    *,
    doi: str,
    safe_doi: str,
    title: str,
    source: str,
    fetch_outcome: str,
    authors: list[str] | None,
    year: str,
    journal: str,
    section_headings: list[str],
    assets_manifest_path: str,
    markdown: str,
) -> dict[str, Any]:
    return {
        "doi": doi,
        "safe_doi": safe_doi,
        "title": title,
        "source": source,
        "fetch_outcome": fetch_outcome,
        "authors_json": _serialize_str_list(authors),
        "year": year,
        "journal": journal,
        "section_headings_json": _serialize_str_list(section_headings),
        "assets_manifest_path": assets_manifest_path,
        "content_hash": _content_hash(markdown),
        "indexed_at": _now_iso(),
        "word_count": len(markdown.split()),
        "char_count": len(markdown),
    }


def _chunk_metadata(
    *,
    doi: str,
    safe_doi: str,
    title: str,
    source: str,
    fetch_outcome: str,
    year: str,
    journal: str,
    chunk_index: int,
) -> dict[str, Any]:
    return {
        "doi": doi,
        "safe_doi": safe_doi,
        "title": title,
        "source": source,
        "fetch_outcome": fetch_outcome,
        "year": year,
        "journal": journal,
        "chunk_index": chunk_index,
    }


def index_paper(
    chroma_dir: Path,
    doi: str,
    safe_doi: str,
    title: str,
    markdown: str,
    *,
    source: str = "",
    fetch_outcome: str = "",
    authors: list[str] | None = None,
    year: str = "",
    journal: str = "",
    section_headings: list[str] | None = None,
    assets_manifest_path: str = "",
) -> int:
    """Persist a paper canonically and rebuild its retrieval chunks.

    Returns the number of retrieval chunks indexed. The canonical document record is
    written even when the body is too short to chunk effectively.
    """
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    chunks_collection = _get_chunks_collection(client)

    body = _strip_frontmatter(markdown)
    headings = section_headings or _extract_headings(body)

    docs_collection.upsert(
        ids=[safe_doi],
        documents=[body],
        metadatas=[
            _doc_metadata(
                doi=doi,
                safe_doi=safe_doi,
                title=title,
                source=source,
                fetch_outcome=fetch_outcome,
                authors=authors,
                year=year,
                journal=journal,
                section_headings=headings,
                assets_manifest_path=assets_manifest_path,
                markdown=body,
            )
        ],
        embeddings=[_DOC_PLACEHOLDER_EMBEDDING],
    )

    _delete_paper_chunks(chunks_collection, safe_doi)

    if not body or len(body) < 100:
        return 0

    chunks = _chunk_text(body)
    if not chunks:
        return 0

    chunk_ids = [f"{safe_doi}__chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [
        _chunk_metadata(
            doi=doi,
            safe_doi=safe_doi,
            title=title,
            source=source,
            fetch_outcome=fetch_outcome,
            year=year,
            journal=journal,
            chunk_index=i,
        )
        for i in range(len(chunks))
    ]

    chunks_collection.upsert(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)
    return len(chunks)


def get_paper_document(chroma_dir: Path, safe_doi: str) -> dict[str, Any] | None:
    """Load the canonical stored paper document by safe_doi."""
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    result = docs_collection.get(ids=[safe_doi])

    ids = result.get("ids") or []
    if not ids:
        return None

    metadata = (result.get("metadatas") or [{}])[0] or {}
    document = (result.get("documents") or [""])[0] or ""

    return {
        "doi": str(metadata.get("doi", "")),
        "safe_doi": str(metadata.get("safe_doi", safe_doi)),
        "title": str(metadata.get("title", "")),
        "source": str(metadata.get("source", "")),
        "fetch_outcome": str(metadata.get("fetch_outcome", "")),
        "authors": _deserialize_str_list(metadata.get("authors_json")),
        "year": str(metadata.get("year", "")),
        "journal": str(metadata.get("journal", "")),
        "section_headings": _deserialize_str_list(metadata.get("section_headings_json")),
        "assets_manifest_path": str(metadata.get("assets_manifest_path", "")),
        "content_hash": str(metadata.get("content_hash", "")),
        "indexed_at": str(metadata.get("indexed_at", "")),
        "word_count": int(metadata.get("word_count", 0) or 0),
        "char_count": int(metadata.get("char_count", 0) or 0),
        "content_markdown": document,
    }


def list_paper_documents(chroma_dir: Path) -> list[dict[str, Any]]:
    """List canonical paper documents currently stored in ChromaDB."""
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    total = docs_collection.count()
    if total == 0:
        return []

    result = docs_collection.get(limit=total)
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    records: list[dict[str, Any]] = []

    for metadata, document in zip(metadatas, documents):
        safe_doi = str(metadata.get("safe_doi", ""))
        records.append(
            {
                "doi": str(metadata.get("doi", "")),
                "safe_doi": safe_doi,
                "title": str(metadata.get("title", "")),
                "source": str(metadata.get("source", "")),
                "fetch_outcome": str(metadata.get("fetch_outcome", "")),
                "authors": _deserialize_str_list(metadata.get("authors_json")),
                "year": str(metadata.get("year", "")),
                "journal": str(metadata.get("journal", "")),
                "section_headings": _deserialize_str_list(metadata.get("section_headings_json")),
                "word_count": int(metadata.get("word_count", 0) or 0),
                "char_count": int(metadata.get("char_count", 0) or 0),
                "uri": f"grados://papers/{safe_doi}",
                "content_markdown": document or "",
            }
        )

    return sorted(records, key=lambda item: item["safe_doi"])


def search_papers(
    chroma_dir: Path,
    query: str,
    limit: int = 10,
    *,
    doi: str = "",
    authors: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    journal: str = "",
    source: str = "",
    use_reranking: bool = True,
) -> list[dict[str, Any]]:
    """Hybrid paper search over canonical docs and retrieval chunks.

    Applies metadata prefiltering first, then dense chunk retrieval, then
    a lightweight lexical rerank to aggregate paper-level results.
    """
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    chunks_collection = _get_chunks_collection(client)

    if docs_collection.count() == 0:
        return []

    documents = list_paper_documents(chroma_dir)
    filtered_documents = [
        doc for doc in documents if _matches_filters(doc, doi, authors, year_from, year_to, journal, source)
    ]
    if not filtered_documents:
        return []

    candidate_ids = {doc["safe_doi"] for doc in filtered_documents}
    document_map = {doc["safe_doi"]: doc for doc in filtered_documents}
    anchor_phrase = _extract_anchor_phrase(query)
    query_terms = _query_terms(query)

    seen: dict[str, dict[str, Any]] = {}
    total_chunks = chunks_collection.count()
    if total_chunks > 0:
        n_results = min(max(limit * 8, 30), total_chunks)
        semantic_results = _query_chunks(
            chunks_collection,
            query,
            n_results=n_results,
            where_document={"$contains": anchor_phrase} if anchor_phrase else None,
        )
        if anchor_phrase and not semantic_results["documents"]:
            semantic_results = _query_chunks(chunks_collection, query, n_results=n_results, where_document=None)

        distances = semantic_results.get("distances", [[]])[0]
        chunk_documents = semantic_results.get("documents", [[]])[0]
        metadatas = semantic_results.get("metadatas", [[]])[0]

        for dist, doc, meta in zip(distances, chunk_documents, metadatas):
            safe_doi = str(meta.get("safe_doi", ""))
            if safe_doi not in candidate_ids:
                continue

            dense_score = 1.0 - dist
            lexical_score = _lexical_score(doc or "", query_terms, anchor_phrase)
            combined_score = _combine_scores(dense_score, lexical_score, use_reranking)

            if safe_doi in seen and combined_score <= seen[safe_doi]["score"]:
                continue

            record = document_map[safe_doi]
            seen[safe_doi] = {
                "doi": record["doi"],
                "safe_doi": safe_doi,
                "title": record["title"],
                "authors": record.get("authors", []),
                "year": record.get("year", ""),
                "journal": record.get("journal", ""),
                "source": record.get("source", ""),
                "score": combined_score,
                "dense_score": dense_score,
                "lexical_score": lexical_score,
                "snippet": _make_snippet(doc or record.get("content_markdown", ""), query_terms, anchor_phrase),
            }

    for record in filtered_documents:
        safe_doi = record["safe_doi"]
        if safe_doi in seen:
            continue
        lexical_score = _lexical_score(record.get("content_markdown", ""), query_terms, anchor_phrase)
        if lexical_score <= 0:
            continue
        seen[safe_doi] = {
            "doi": record["doi"],
            "safe_doi": safe_doi,
            "title": record["title"],
            "authors": record.get("authors", []),
            "year": record.get("year", ""),
            "journal": record.get("journal", ""),
            "source": record.get("source", ""),
            "score": _combine_scores(0.0, lexical_score, use_reranking),
            "dense_score": 0.0,
            "lexical_score": lexical_score,
            "snippet": _make_snippet(record.get("content_markdown", ""), query_terms, anchor_phrase),
        }

    return sorted(seen.values(), key=lambda item: item["score"], reverse=True)[:limit]


def _query_chunks(
    collection: Any,
    query: str,
    *,
    n_results: int,
    where_document: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        result: dict[str, Any] = collection.query(
            query_texts=[query],
            n_results=n_results,
            where_document=where_document,
        )
        return result
    except TypeError:
        result = collection.query(query_texts=[query], n_results=n_results)
        return result


def _matches_filters(
    document: dict[str, Any],
    doi: str,
    authors: str,
    year_from: int | None,
    year_to: int | None,
    journal: str,
    source: str,
) -> bool:
    if doi and document.get("doi", "").lower() != doi.lower():
        return False

    if authors:
        author_query = authors.lower()
        author_values = [str(author).lower() for author in document.get("authors", [])]
        if not any(author_query in author for author in author_values):
            return False

    if journal and journal.lower() not in str(document.get("journal", "")).lower():
        return False

    if source and source.lower() not in str(document.get("source", "")).lower():
        return False

    year_value = _coerce_year(document.get("year"))
    if year_from is not None and (year_value is None or year_value < year_from):
        return False
    if year_to is not None and (year_value is None or year_value > year_to):
        return False

    return True


def _coerce_year(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _extract_anchor_phrase(query: str) -> str:
    quoted: list[str] = re.findall(r'"([^"]+)"', query)
    if quoted:
        return quoted[0].strip()
    if _DOI_PATTERN_IN_TEXT.search(query):
        match = _DOI_PATTERN_IN_TEXT.search(query)
        return match.group(0).strip() if match else ""
    return ""


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
    "study",
    "paper",
}


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-zA-Z0-9./_-]+", query.lower())
    return [term for term in terms if len(term) >= 3 and term not in _STOPWORDS][:8]


_DOI_PATTERN_IN_TEXT = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def _lexical_score(text: str, query_terms: list[str], anchor_phrase: str) -> float:
    haystack = text.lower()
    score = 0.0
    if anchor_phrase and anchor_phrase.lower() in haystack:
        score += 1.5
    for term in query_terms:
        if term in haystack:
            score += 0.3
    return score


def _combine_scores(dense_score: float, lexical_score: float, use_reranking: bool) -> float:
    if not use_reranking:
        return dense_score
    return dense_score + lexical_score


def _make_snippet(text: str, query_terms: list[str], anchor_phrase: str, max_chars: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""

    anchors = [anchor_phrase.lower()] if anchor_phrase else []
    anchors.extend(query_terms)
    lower = compact.lower()
    for anchor in anchors:
        if not anchor:
            continue
        position = lower.find(anchor)
        if position == -1:
            continue
        start = max(0, position - 60)
        end = min(len(compact), position + max_chars)
        snippet = compact[start:end].strip()
        return ("..." if start > 0 else "") + snippet + ("..." if end < len(compact) else "")

    return compact[:max_chars] + ("..." if len(compact) > max_chars else "")


def index_all_papers(chroma_dir: Path, papers_dir: Path) -> tuple[int, int]:
    """Rebuild canonical docs and retrieval chunks from mirror markdown files."""
    if not papers_dir.is_dir():
        return 0, 0

    total_papers = 0
    total_chunks = 0

    for md_file in sorted(papers_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8", errors="replace")
        body = _strip_frontmatter(content)
        if not body:
            continue

        safe_doi = md_file.stem
        metadata = _parse_frontmatter_metadata(content)
        headings = _extract_headings(body)
        total_papers += 1
        total_chunks += index_paper(
            chroma_dir,
            doi=metadata.get("doi", safe_doi),
            safe_doi=safe_doi,
            title=metadata.get("title", ""),
            markdown=body,
            source=metadata.get("source", ""),
            fetch_outcome=metadata.get("fetch_outcome", ""),
            year=metadata.get("year", ""),
            journal=metadata.get("journal", ""),
            section_headings=headings,
            assets_manifest_path=metadata.get("assets_manifest_path", ""),
        )

    return total_papers, total_chunks


def get_index_stats(chroma_dir: Path) -> dict[str, int]:
    """Return basic canonical storage stats."""
    try:
        client = _get_client(chroma_dir)
        docs_collection = _get_docs_collection(client)
        chunks_collection = _get_chunks_collection(client)
        return {
            "total_chunks": chunks_collection.count(),
            "unique_papers": docs_collection.count(),
        }
    except Exception:
        return {"total_chunks": 0, "unique_papers": 0}


def _delete_paper_chunks(collection: Any, safe_doi: str) -> None:
    """Remove all chunk rows for a given paper."""
    try:
        existing = collection.get(where={"safe_doi": safe_doi})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass


def _parse_frontmatter_metadata(text: str) -> dict[str, str]:
    """Extract simple key/value metadata from YAML-like frontmatter."""
    metadata: dict[str, str] = {}
    if not text.startswith("---"):
        return metadata

    end = text.find("---", 3)
    if end == -1:
        return metadata

    frontmatter = text[3:end].strip()
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata
