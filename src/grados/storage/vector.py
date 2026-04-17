"""ChromaDB canonical storage for paper documents and semantic retrieval."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.config import IndexingConfig
from grados.storage.chroma_client import (
    delete_paper_chunks,
    get_chunks_collection,
    get_client,
    get_docs_collection,
)
from grados.storage.chunking import (
    DOC_SUMMARY_MAX_CHARS,
    build_doc_summary,
    chunk_text,
    extract_headings,
    extract_reference_dois,
    extract_sections,
    resolve_indexing_config,
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
    PaperDocument,
    PaperDocumentSummary,
    canonical_excerpt,
    get_paper_document_record,
    get_paper_documents_by_ids,
    hydrate_canonical_documents,
    list_index_document_summaries,
    list_paper_document_records,
    paper_document_from_record,
    paper_document_summary_from_record,
)
from grados.storage.paths import resolve_papers_dir
from grados.storage.retrieval import (
    ChunkWindowCandidate,
    PaperSearchResult,
    build_search_result,
    combine_scores,
    dense_score,
    extract_anchor_phrase,
    lexical_score,
    make_snippet,
    matches_filters,
    merge_chunk_windows,
    paragraph_window_for_query,
    query_chunks,
    query_terms,
    select_doc_candidates,
)

_get_client = get_client
_get_docs_collection = get_docs_collection
_get_chunks_collection = get_chunks_collection
_hydrate_canonical_documents = hydrate_canonical_documents
_chunk_text = chunk_text
logger = logging.getLogger(__name__)


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


def get_paper_document(chroma_dir: Path, safe_doi: str) -> PaperDocument | None:
    """Load the canonical stored paper document by safe_doi."""
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    record = get_paper_document_record(docs_collection=docs_collection, safe_doi=safe_doi)
    if record is None:
        return None
    return paper_document_from_record(record)


def list_paper_documents(chroma_dir: Path) -> list[PaperDocumentSummary]:
    """List canonical paper documents currently stored in ChromaDB."""
    client = _get_client(chroma_dir)
    docs_collection = _get_docs_collection(client)
    return [
        paper_document_summary_from_record(record)
        for record in list_paper_document_records(docs_collection=docs_collection)
    ]


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
        doc for doc in index_documents if matches_filters(doc, doi, authors, year_from, year_to, journal, source)
    ]
    if not filtered_documents:
        return []

    query_term_list = query_terms(query)
    anchor_phrase = extract_anchor_phrase(query)
    backend = load_embedding_backend(config=config)
    query_embedding = backend.embed_query(query)

    candidate_ids = {doc["safe_doi"] for doc in filtered_documents}
    doc_scores = select_doc_candidates(
        docs_collection=docs_collection,
        filtered_documents=filtered_documents,
        candidate_ids=candidate_ids,
        query_embedding=query_embedding,
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
    papers_path = papers_dir or resolve_papers_dir(chroma_dir)
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
    semantic_windows: dict[str, list[ChunkWindowCandidate]] = {}
    total_chunks = chunks_collection.count()
    if total_chunks > 0 and hydrated_candidate_ids:
        chunk_limit = min(max(limit * 8, 30), total_chunks)
        semantic_results = query_chunks(
            collection=chunks_collection,
            query_embedding=query_embedding,
            n_results=chunk_limit,
            candidate_doc_ids=list(hydrated_candidate_ids),
            anchor_phrase=anchor_phrase,
        )
        if anchor_phrase and not semantic_results.get("documents", [[]])[0]:
            semantic_results = query_chunks(
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

            chunk_dense = dense_score(dist)
            doc_dense = doc_scores.get(safe_doi, 0.0)
            dense_score_value = round((chunk_dense * 0.7) + (doc_dense * 0.3), 6)
            lexical_score_value = lexical_score(doc_text or "", query_term_list, anchor_phrase)
            combined_score = combine_scores(dense_score_value, lexical_score_value, use_reranking)

            semantic_windows.setdefault(safe_doi, []).append(
                ChunkWindowCandidate(
                    paragraph_start=int(metadata.get("paragraph_start", 0) or 0),
                    paragraph_count=int(metadata.get("paragraph_count", 0) or 0),
                    score=combined_score,
                    dense_score=dense_score_value,
                    doc_dense_score=doc_dense,
                    chunk_dense_score=chunk_dense,
                    lexical_score=lexical_score_value,
                    section_name=str(metadata.get("section_name", "")),
                    section_level=int(metadata.get("section_level", 0) or 0),
                )
            )

    for safe_doi, candidates in semantic_windows.items():
        merged = merge_chunk_windows(candidates)
        if merged is None:
            continue
        record = document_map[safe_doi]
        excerpt = canonical_excerpt(record, merged.paragraph_start, merged.paragraph_count)
        seen[safe_doi] = build_search_result(
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
            or make_snippet(
                str(record.get("content_markdown", "") or ""),
                query_term_list,
                anchor_phrase,
            ),
        )

    for record in documents:
        safe_doi = record["safe_doi"]
        if safe_doi in seen:
            continue
        content_markdown = str(record.get("content_markdown", "") or "")
        lexical_score_value = lexical_score(content_markdown, query_term_list, anchor_phrase)
        if lexical_score_value <= 0:
            continue
        doc_dense = doc_scores.get(safe_doi, 0.0)
        paragraph_start, paragraph_count = paragraph_window_for_query(
            content_markdown,
            query_term_list,
            anchor_phrase,
        )
        seen[safe_doi] = build_search_result(
            record=record,
            safe_doi=safe_doi,
            score=combine_scores(doc_dense, lexical_score_value, use_reranking),
            dense_score=doc_dense,
            doc_dense_score=doc_dense,
            chunk_dense_score=0.0,
            lexical_score=lexical_score_value,
            paragraph_start=paragraph_start,
            paragraph_count=paragraph_count,
            snippet=canonical_excerpt(record, paragraph_start, paragraph_count)
            or make_snippet(content_markdown, query_term_list, anchor_phrase),
        )

    return sorted(seen.values(), key=lambda item: item.score, reverse=True)[:limit]

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
