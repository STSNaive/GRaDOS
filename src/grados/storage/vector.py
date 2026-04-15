"""ChromaDB canonical storage for paper documents and semantic retrieval."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.config import IndexingConfig
from grados.storage.chroma_client import (
    delete_paper_chunks,
    filter_query_result,
    get_chunks_collection,
    get_client,
    get_docs_collection,
    query_collection,
)
from grados.storage.chunking import (
    DOC_SUMMARY_MAX_CHARS,
    build_doc_summary,
    chunk_text,
    extract_headings,
    extract_reference_dois,
    extract_sections,
    resolve_indexing_config,
    split_paragraphs,
    strip_frontmatter,
)
from grados.storage.embedding import (
    IndexCompatibilityError,
    build_index_manifest,
    inspect_index_compatibility,
    load_embedding_backend,
    read_index_manifest,
    write_index_manifest,
)
from grados.storage.frontmatter import parse_authors_metadata, read_frontmatter_metadata
from grados.storage.hydration import (
    canonical_excerpt,
    get_paper_document_record,
    get_paper_documents_by_ids,
    hydrate_canonical_documents,
    list_index_document_summaries,
    list_paper_document_records,
)

_get_client = get_client
_get_docs_collection = get_docs_collection
_get_chunks_collection = get_chunks_collection
_hydrate_canonical_documents = hydrate_canonical_documents
_chunk_text = chunk_text
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperSearchResult:
    doi: str
    safe_doi: str
    title: str
    authors: list[str]
    year: str = ""
    journal: str = ""
    source: str = ""
    score: float = 0.0
    dense_score: float = 0.0
    doc_dense_score: float = 0.0
    chunk_dense_score: float = 0.0
    lexical_score: float = 0.0
    section_name: str = ""
    section_level: int = 0
    paragraph_start: int = 0
    paragraph_count: int = 0
    snippet: str = ""


@dataclass(frozen=True)
class _ChunkWindowCandidate:
    paragraph_start: int
    paragraph_count: int
    score: float
    dense_score: float
    doc_dense_score: float
    chunk_dense_score: float
    lexical_score: float
    section_name: str = ""
    section_level: int = 0


@dataclass(frozen=True)
class _MergedChunkWindow:
    paragraph_start: int
    paragraph_count: int
    score: float
    dense_score: float
    doc_dense_score: float
    chunk_dense_score: float
    lexical_score: float
    section_name: str = ""
    section_level: int = 0


@dataclass(frozen=True)
class IndexStats:
    total_chunks: int = 0
    unique_papers: int = 0
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dim: int = 0
    query_prompt_name: str = ""
    reindex_required: bool = False
    reindex_reason: str = ""
    index_manifest_present: bool = False


def _ensure_index_compatible(chroma_dir: Path, indexing_config: IndexingConfig) -> None:
    state = inspect_index_compatibility(chroma_dir, indexing_config)
    if state["reindex_required"]:
        raise IndexCompatibilityError(str(state["reason"]))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _serialize_str_list(values: list[str] | None) -> str:
    return json.dumps([value for value in (values or []) if value], ensure_ascii=False)


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
    doc_summary_source: str,
    cites: list[str],
    embedding_provider: str,
    embedding_model: str,
    embedding_dim: int,
    embedding_prompt_mode: str,
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
        "doc_summary_source": doc_summary_source,
        "cites_json": _serialize_str_list(cites),
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "embedding_prompt_mode": embedding_prompt_mode,
    }


def _resolve_papers_dir(chroma_dir: Path, papers_dir: Path | None = None) -> Path | None:
    if papers_dir is not None:
        return papers_dir
    if not isinstance(chroma_dir, Path):
        return None
    if chroma_dir.name == "chroma" and chroma_dir.parent.name == "database":
        return chroma_dir.parent.parent / "papers"
    return None


def _merge_chunk_windows(candidates: list[_ChunkWindowCandidate]) -> _MergedChunkWindow | None:
    if not candidates:
        return None

    sorted_candidates = sorted(candidates, key=lambda item: (item.paragraph_start, item.paragraph_count))
    clusters: list[list[_ChunkWindowCandidate]] = []
    current_cluster: list[_ChunkWindowCandidate] = []
    current_end = -1

    for candidate in sorted_candidates:
        start = max(0, candidate.paragraph_start)
        end = start + max(0, candidate.paragraph_count)
        if not current_cluster:
            current_cluster = [candidate]
            current_end = end
            continue
        if start <= current_end:
            current_cluster.append(candidate)
            current_end = max(current_end, end)
            continue
        clusters.append(current_cluster)
        current_cluster = [candidate]
        current_end = end

    if current_cluster:
        clusters.append(current_cluster)

    def build_cluster(cluster: list[_ChunkWindowCandidate]) -> _MergedChunkWindow:
        start = min(max(0, item.paragraph_start) for item in cluster)
        end = max(max(0, item.paragraph_start) + max(0, item.paragraph_count) for item in cluster)
        best = max(cluster, key=lambda item: item.score)
        return _MergedChunkWindow(
            paragraph_start=start,
            paragraph_count=max(0, end - start),
            score=max(item.score for item in cluster),
            dense_score=max(item.dense_score for item in cluster),
            doc_dense_score=max(item.doc_dense_score for item in cluster),
            chunk_dense_score=max(item.chunk_dense_score for item in cluster),
            lexical_score=max(item.lexical_score for item in cluster),
            section_name=best.section_name,
            section_level=best.section_level,
        )

    merged = [build_cluster(cluster) for cluster in clusters]
    return max(merged, key=lambda item: (item.score, item.paragraph_count))


def _build_search_result(
    *,
    record: dict[str, Any],
    safe_doi: str,
    score: float,
    dense_score: float,
    doc_dense_score: float,
    chunk_dense_score: float,
    lexical_score: float,
    section_name: str = "",
    section_level: int = 0,
    paragraph_start: int = 0,
    paragraph_count: int = 0,
    snippet: str = "",
) -> PaperSearchResult:
    return PaperSearchResult(
        doi=str(record.get("doi", "")),
        safe_doi=safe_doi,
        title=str(record.get("title", "")),
        authors=[str(value) for value in record.get("authors", []) if str(value)],
        year=str(record.get("year", "")),
        journal=str(record.get("journal", "")),
        source=str(record.get("source", "")),
        score=score,
        dense_score=dense_score,
        doc_dense_score=doc_dense_score,
        chunk_dense_score=chunk_dense_score,
        lexical_score=lexical_score,
        section_name=section_name,
        section_level=section_level,
        paragraph_start=paragraph_start,
        paragraph_count=paragraph_count,
        snippet=snippet,
    )


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
    section_name: str,
    section_level: int,
    section_index: int,
    paragraph_start: int,
    paragraph_count: int,
    embedding_provider: str,
    embedding_model: str,
    embedding_dim: int,
    embedding_prompt_mode: str,
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
        "section_name": section_name,
        "section_level": section_level,
        "section_index": section_index,
        "paragraph_start": paragraph_start,
        "paragraph_count": paragraph_count,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "embedding_prompt_mode": embedding_prompt_mode,
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
    indexing_config: IndexingConfig | None = None,
) -> int:
    """Persist a paper canonically and rebuild its document/chunk embeddings."""
    config = resolve_indexing_config(indexing_config)
    _ensure_index_compatible(chroma_dir, config)

    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    chunks_collection = _get_chunks_collection(client)
    backend = load_embedding_backend(config=config)

    body = strip_frontmatter(markdown)
    sections = extract_sections(body, fallback_title=title)
    headings = section_headings or extract_headings(body)
    doc_summary, doc_summary_source = build_doc_summary(title, body, sections)
    cited_dois = extract_reference_dois(body)
    doc_embedding = backend.embed_documents([doc_summary or body[:DOC_SUMMARY_MAX_CHARS]])[0]
    embedding_dim = len(doc_embedding)

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
                doc_summary_source=doc_summary_source,
                cites=cited_dois,
                embedding_provider=backend.provider,
                embedding_model=backend.model_id,
                embedding_dim=embedding_dim,
                embedding_prompt_mode=backend.query_prompt_mode,
            )
        ],
        embeddings=[doc_embedding],
    )

    delete_paper_chunks(chunks_collection, safe_doi)

    chunks = _chunk_text(body, config, fallback_title=title)
    if chunks:
        chunk_ids = [f"{safe_doi}__chunk_{index}" for index in range(len(chunks))]
        chunk_texts = [chunk["text"] for chunk in chunks]
        chunk_embeddings = backend.embed_documents(chunk_texts)
        chunk_metadatas = [
            _chunk_metadata(
                doi=doi,
                safe_doi=safe_doi,
                title=title,
                source=source,
                fetch_outcome=fetch_outcome,
                year=year,
                journal=journal,
                chunk_index=index,
                section_name=str(chunk["section_name"]),
                section_level=int(chunk["section_level"]),
                section_index=int(chunk["section_index"]),
                paragraph_start=int(chunk["paragraph_start"]),
                paragraph_count=int(chunk["paragraph_count"]),
                embedding_provider=backend.provider,
                embedding_model=backend.model_id,
                embedding_dim=embedding_dim,
                embedding_prompt_mode=backend.query_prompt_mode,
            )
            for index, chunk in enumerate(chunks)
        ]
        chunks_collection.upsert(
            ids=chunk_ids,
            documents=chunk_texts,
            metadatas=chunk_metadatas,
            embeddings=chunk_embeddings,
        )

    manifest = build_index_manifest(
        config=config,
        backend=backend,
        unique_papers=docs_collection.count(),
        total_chunks=chunks_collection.count(),
    )
    write_index_manifest(chroma_dir, manifest)
    return len(chunks)


def get_paper_document(chroma_dir: Path, safe_doi: str) -> dict[str, Any] | None:
    """Load the canonical stored paper document by safe_doi."""
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    return get_paper_document_record(docs_collection=docs_collection, safe_doi=safe_doi)


def list_paper_documents(chroma_dir: Path) -> list[dict[str, Any]]:
    """List canonical paper documents currently stored in ChromaDB."""
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    return list_paper_document_records(docs_collection=docs_collection)


def search_papers(
    chroma_dir: Path,
    query: str,
    limit: int = 10,
    *,
    papers_dir: Path | None = None,
    doi: str = "",
    authors: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    journal: str = "",
    source: str = "",
    use_reranking: bool = True,
    indexing_config: IndexingConfig | None = None,
) -> list[PaperSearchResult]:
    """Two-stage semantic search over docs first, then chunks within candidates."""
    config = resolve_indexing_config(indexing_config)
    _ensure_index_compatible(chroma_dir, config)

    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    chunks_collection = _get_chunks_collection(client)

    if docs_collection.count() == 0:
        return []

    index_documents = list_index_document_summaries(
        docs_collection=docs_collection,
        chroma_dir=chroma_dir,
        fallback_list_paper_documents=list_paper_documents,
    )
    filtered_documents = [
        doc for doc in index_documents if _matches_filters(doc, doi, authors, year_from, year_to, journal, source)
    ]
    if not filtered_documents:
        return []

    query_terms = _query_terms(query)
    anchor_phrase = _extract_anchor_phrase(query)
    backend = load_embedding_backend(config=config)
    query_embedding = backend.embed_query(query)

    candidate_ids = {doc["safe_doi"] for doc in filtered_documents}
    doc_scores = _select_doc_candidates(
        docs_collection=docs_collection,
        filtered_documents=filtered_documents,
        candidate_ids=candidate_ids,
        query_embedding=query_embedding,
        query_terms=query_terms,
        anchor_phrase=anchor_phrase,
        limit=limit,
    )
    ranked_candidate_ids = [
        safe_doi
        for safe_doi, _score in sorted(
            doc_scores.items(),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
    ]
    papers_path = _resolve_papers_dir(chroma_dir, papers_dir)
    documents = _hydrate_canonical_documents(
        get_paper_documents_by_ids(
            docs_collection=docs_collection,
            chroma_dir=chroma_dir,
            safe_dois=ranked_candidate_ids,
            fallback_list_paper_documents=list_paper_documents,
        ),
        papers_path,
    )
    document_map = {doc["safe_doi"]: doc for doc in documents}
    hydrated_candidate_ids = set(document_map)
    if not hydrated_candidate_ids:
        return []

    seen: dict[str, PaperSearchResult] = {}
    semantic_windows: dict[str, list[_ChunkWindowCandidate]] = {}
    total_chunks = chunks_collection.count()
    if total_chunks > 0 and hydrated_candidate_ids:
        chunk_limit = min(max(limit * 8, 30), total_chunks)
        semantic_results = _query_chunks(
            collection=chunks_collection,
            query_embedding=query_embedding,
            n_results=chunk_limit,
            candidate_doc_ids=list(hydrated_candidate_ids),
            anchor_phrase=anchor_phrase,
        )
        if anchor_phrase and not semantic_results.get("documents", [[]])[0]:
            semantic_results = _query_chunks(
                collection=chunks_collection,
                query_embedding=query_embedding,
                n_results=chunk_limit,
                candidate_doc_ids=list(hydrated_candidate_ids),
                anchor_phrase="",
            )

        distances = semantic_results.get("distances", [[]])[0]
        chunk_documents = semantic_results.get("documents", [[]])[0]
        metadatas = semantic_results.get("metadatas", [[]])[0]

        for dist, doc_text, metadata in zip(distances, chunk_documents, metadatas):
            safe_doi = str(metadata.get("safe_doi", ""))
            if safe_doi not in doc_scores or safe_doi not in hydrated_candidate_ids:
                continue

            chunk_dense = _dense_score(dist)
            doc_dense = doc_scores.get(safe_doi, 0.0)
            dense_score = round((chunk_dense * 0.7) + (doc_dense * 0.3), 6)
            lexical_score = _lexical_score(doc_text or "", query_terms, anchor_phrase)
            combined_score = _combine_scores(dense_score, lexical_score, use_reranking)

            semantic_windows.setdefault(safe_doi, []).append(
                _ChunkWindowCandidate(
                    paragraph_start=int(metadata.get("paragraph_start", 0) or 0),
                    paragraph_count=int(metadata.get("paragraph_count", 0) or 0),
                    score=combined_score,
                    dense_score=dense_score,
                    doc_dense_score=doc_dense,
                    chunk_dense_score=chunk_dense,
                    lexical_score=lexical_score,
                    section_name=str(metadata.get("section_name", "")),
                    section_level=int(metadata.get("section_level", 0) or 0),
                )
            )

    for safe_doi, candidates in semantic_windows.items():
        merged = _merge_chunk_windows(candidates)
        if merged is None:
            continue
        record = document_map[safe_doi]
        excerpt = canonical_excerpt(record, merged.paragraph_start, merged.paragraph_count)
        seen[safe_doi] = _build_search_result(
            record=record,
            safe_doi=safe_doi,
            score=merged.score,
            dense_score=merged.dense_score,
            doc_dense_score=merged.doc_dense_score,
            chunk_dense_score=merged.chunk_dense_score,
            lexical_score=merged.lexical_score,
            section_name=merged.section_name,
            section_level=merged.section_level,
            paragraph_start=merged.paragraph_start,
            paragraph_count=merged.paragraph_count,
            snippet=excerpt
            or _make_snippet(
                str(record.get("content_markdown", "") or ""),
                query_terms,
                anchor_phrase,
            ),
        )

    for record in documents:
        safe_doi = record["safe_doi"]
        if safe_doi in seen:
            continue
        content_markdown = str(record.get("content_markdown", "") or "")
        lexical_score = _lexical_score(content_markdown, query_terms, anchor_phrase)
        if lexical_score <= 0:
            continue
        doc_dense = doc_scores.get(safe_doi, 0.0)
        paragraph_start, paragraph_count = _paragraph_window_for_query(
            content_markdown,
            query_terms,
            anchor_phrase,
        )
        seen[safe_doi] = _build_search_result(
            record=record,
            safe_doi=safe_doi,
            score=_combine_scores(doc_dense, lexical_score, use_reranking),
            dense_score=doc_dense,
            doc_dense_score=doc_dense,
            chunk_dense_score=0.0,
            lexical_score=lexical_score,
            paragraph_start=paragraph_start,
            paragraph_count=paragraph_count,
            snippet=canonical_excerpt(record, paragraph_start, paragraph_count)
            or _make_snippet(content_markdown, query_terms, anchor_phrase),
        )

    return sorted(seen.values(), key=lambda item: item.score, reverse=True)[:limit]


def _select_doc_candidates(
    *,
    docs_collection: Any,
    filtered_documents: list[dict[str, Any]],
    candidate_ids: set[str],
    query_embedding: list[float],
    query_terms: list[str],
    anchor_phrase: str,
    limit: int,
) -> dict[str, float]:
    total_docs = len(filtered_documents)
    if total_docs <= 0:
        return {}

    doc_limit = min(max(limit * 8, 30), total_docs)
    result = _query_docs(
        collection=docs_collection,
        query_embedding=query_embedding,
        n_results=doc_limit,
        candidate_doc_ids=list(candidate_ids),
        anchor_phrase=anchor_phrase,
    )
    if anchor_phrase and not result.get("documents", [[]])[0]:
        result = _query_docs(
            collection=docs_collection,
            query_embedding=query_embedding,
            n_results=doc_limit,
            candidate_doc_ids=list(candidate_ids),
        )

    scores: dict[str, float] = {}
    distances = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]

    for dist, metadata in zip(distances, metadatas):
        safe_doi = str(metadata.get("safe_doi", ""))
        if safe_doi not in candidate_ids:
            continue
        scores[safe_doi] = max(scores.get(safe_doi, 0.0), _dense_score(dist))

    for record in filtered_documents:
        if len(scores) >= doc_limit:
            break
        safe_doi = record["safe_doi"]
        if safe_doi in scores:
            continue
        scores[safe_doi] = 0.0

    return scores


def _query_docs(
    *,
    collection: Any,
    query_embedding: list[float],
    n_results: int,
    candidate_doc_ids: list[str],
    anchor_phrase: str = "",
) -> dict[str, Any]:
    return query_collection(
        collection=collection,
        query_embedding=query_embedding,
        n_results=n_results,
        where={"safe_doi": {"$in": candidate_doc_ids}} if candidate_doc_ids else None,
        where_document={"$contains": anchor_phrase} if anchor_phrase else None,
    )


def _query_chunks(
    *,
    collection: Any,
    query_embedding: list[float],
    n_results: int,
    candidate_doc_ids: list[str],
    anchor_phrase: str = "",
) -> dict[str, Any]:
    where = {"safe_doi": {"$in": candidate_doc_ids}} if candidate_doc_ids else None
    result = query_collection(
        collection=collection,
        query_embedding=query_embedding,
        n_results=n_results,
        where=where,
        where_document={"$contains": anchor_phrase} if anchor_phrase else None,
    )

    metadatas = result.get("metadatas", [[]])[0]
    if not candidate_doc_ids or not metadatas:
        return result

    allowed = set(candidate_doc_ids)
    filtered_positions = [
        index
        for index, metadata in enumerate(metadatas)
        if str(metadata.get("safe_doi", "")) in allowed
    ]
    if len(filtered_positions) == len(metadatas):
        return result
    return filter_query_result(result, filtered_positions)


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
    match = _DOI_PATTERN_IN_TEXT.search(query)
    if match:
        return match.group(0).strip()
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


def _dense_score(distance: Any) -> float:
    try:
        return max(0.0, 1.0 - float(distance))
    except (TypeError, ValueError):
        return 0.0


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


def _paragraph_window_for_query(text: str, query_terms: list[str], anchor_phrase: str) -> tuple[int, int]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return 0, 0

    paragraph_scores = [_lexical_score(paragraph, query_terms, anchor_phrase) for paragraph in paragraphs]
    best_index = max(range(len(paragraphs)), key=lambda index: paragraph_scores[index])
    best_score = paragraph_scores[best_index]
    if best_score <= 0:
        return 0, 0

    start = best_index
    end = best_index

    while start > 0 and paragraph_scores[start - 1] > 0:
        start -= 1
    while end + 1 < len(paragraphs) and paragraph_scores[end + 1] > 0:
        end += 1

    if start > 0 and re.match(r"^#{1,6}\s+", paragraphs[start - 1]):
        start -= 1

    return start, (end - start) + 1


def index_all_papers(
    chroma_dir: Path,
    papers_dir: Path,
    *,
    indexing_config: IndexingConfig | None = None,
) -> tuple[int, int]:
    """Rebuild canonical docs and retrieval chunks from mirror markdown files."""
    if not papers_dir.is_dir():
        return 0, 0

    config = resolve_indexing_config(indexing_config)
    _ensure_index_compatible(chroma_dir, config)

    total_papers = 0
    total_chunks = 0

    for md_file in sorted(papers_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8", errors="replace")
        body = strip_frontmatter(content)
        if not body:
            continue

        metadata = read_frontmatter_metadata(content)
        safe_doi = md_file.stem
        headings = extract_headings(body)
        total_papers += 1
        total_chunks += index_paper(
            chroma_dir,
            doi=metadata.get("doi", safe_doi),
            safe_doi=safe_doi,
            title=metadata.get("title", ""),
            markdown=body,
            source=metadata.get("source", ""),
            fetch_outcome=metadata.get("fetch_outcome", ""),
            authors=parse_authors_metadata(metadata) or None,
            year=metadata.get("year", ""),
            journal=metadata.get("journal", ""),
            section_headings=headings,
            assets_manifest_path=metadata.get("assets_manifest_path", ""),
            indexing_config=config,
        )

    return total_papers, total_chunks


def get_index_stats(
    chroma_dir: Path,
    *,
    indexing_config: IndexingConfig | None = None,
) -> IndexStats:
    """Return index stats plus compatibility / migration hints."""
    config = resolve_indexing_config(indexing_config)
    compatibility = inspect_index_compatibility(chroma_dir, config)
    manifest = compatibility.get("manifest") or read_index_manifest(chroma_dir) or {}

    stats = IndexStats(
        total_chunks=0,
        unique_papers=0,
        embedding_provider=str(manifest.get("provider", config.provider)),
        embedding_model=str(manifest.get("model_id", config.model_id)),
        embedding_dim=int(manifest.get("embedding_dim", 0) or 0),
        query_prompt_name=str(manifest.get("query_prompt_name", config.query_prompt_name)),
        reindex_required=bool(compatibility.get("reindex_required", False)),
        reindex_reason=str(compatibility.get("reason", "")),
        index_manifest_present=bool(manifest),
    )

    try:
        client = _get_client(chroma_dir)
        docs_collection = _get_docs_collection(client)
        chunks_collection = _get_chunks_collection(client)
        stats = IndexStats(
            total_chunks=chunks_collection.count(),
            unique_papers=docs_collection.count(),
            embedding_provider=stats.embedding_provider,
            embedding_model=stats.embedding_model,
            embedding_dim=stats.embedding_dim,
            query_prompt_name=stats.query_prompt_name,
            reindex_required=stats.reindex_required,
            reindex_reason=stats.reindex_reason,
            index_manifest_present=stats.index_manifest_present,
        )
    except Exception:
        logger.exception("Failed to inspect Chroma index stats for %s", chroma_dir)

    return stats
