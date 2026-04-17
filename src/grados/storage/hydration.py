"""Canonical document hydration helpers for Chroma-backed retrieval."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from grados.storage.chroma_client import collection_get
from grados.storage.chunking import extract_reference_dois, split_paragraphs

__all__ = [
    "canonical_excerpt",
    "document_record_from_metadata",
    "get_paper_document_record",
    "get_paper_documents_by_ids",
    "hydrate_canonical_documents",
    "list_index_document_summaries",
    "list_paper_document_records",
    "PaperDocument",
    "PaperDocumentSummary",
    "paper_document_from_record",
    "paper_document_summary_from_record",
]


@dataclass(frozen=True)
class PaperDocumentSummary:
    doi: str
    safe_doi: str
    title: str
    source: str
    fetch_outcome: str
    authors: list[str]
    year: str
    journal: str
    section_headings: list[str]
    assets_manifest_path: str
    word_count: int
    char_count: int
    doc_summary_source: str
    cites: list[str]
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    embedding_prompt_mode: str
    uri: str
    content_hash: str = ""
    indexed_at: str = ""


@dataclass(frozen=True)
class PaperDocument(PaperDocumentSummary):
    content_markdown: str = ""


def _deserialize_str_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(value) for value in raw if str(value)]
    try:
        loaded = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(value) for value in loaded if str(value)]


def _normalize_result_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    if value and isinstance(value[0], list):
        return list(value[0])
    return list(value)


def _document_like_to_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, type) and is_dataclass(value):
        payload = asdict(value)
        if isinstance(payload, dict):
            return payload
        return {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def document_record_from_metadata(metadata: dict[str, Any], document: str = "") -> dict[str, Any]:
    safe_doi = str(metadata.get("safe_doi", ""))
    return {
        "doi": str(metadata.get("doi", "")),
        "safe_doi": safe_doi,
        "title": str(metadata.get("title", "")),
        "source": str(metadata.get("source", "")),
        "fetch_outcome": str(metadata.get("fetch_outcome", "")),
        "authors": _deserialize_str_list(metadata.get("authors_json")),
        "year": str(metadata.get("year", "")),
        "journal": str(metadata.get("journal", "")),
        "section_headings": _deserialize_str_list(metadata.get("section_headings_json")),
        "assets_manifest_path": str(metadata.get("assets_manifest_path", "")),
        "word_count": int(metadata.get("word_count", 0) or 0),
        "char_count": int(metadata.get("char_count", 0) or 0),
        "doc_summary_source": str(metadata.get("doc_summary_source", "")),
        "cites": _deserialize_str_list(metadata.get("cites_json")),
        "embedding_provider": str(metadata.get("embedding_provider", "")),
        "embedding_model": str(metadata.get("embedding_model", "")),
        "embedding_dim": int(metadata.get("embedding_dim", 0) or 0),
        "embedding_prompt_mode": str(metadata.get("embedding_prompt_mode", "")),
        "uri": f"grados://papers/{safe_doi}",
        "content_markdown": document,
    }


def paper_document_summary_from_record(record: dict[str, Any]) -> PaperDocumentSummary:
    return PaperDocumentSummary(
        doi=str(record.get("doi", "")),
        safe_doi=str(record.get("safe_doi", "")),
        title=str(record.get("title", "")),
        source=str(record.get("source", "")),
        fetch_outcome=str(record.get("fetch_outcome", "")),
        authors=[str(value) for value in record.get("authors", []) if str(value)],
        year=str(record.get("year", "")),
        journal=str(record.get("journal", "")),
        section_headings=[str(value) for value in record.get("section_headings", []) if str(value)],
        assets_manifest_path=str(record.get("assets_manifest_path", "")),
        word_count=int(record.get("word_count", 0) or 0),
        char_count=int(record.get("char_count", 0) or 0),
        doc_summary_source=str(record.get("doc_summary_source", "")),
        cites=[str(value) for value in record.get("cites", []) if str(value)],
        embedding_provider=str(record.get("embedding_provider", "")),
        embedding_model=str(record.get("embedding_model", "")),
        embedding_dim=int(record.get("embedding_dim", 0) or 0),
        embedding_prompt_mode=str(record.get("embedding_prompt_mode", "")),
        uri=str(record.get("uri", "")),
        content_hash=str(record.get("content_hash", "")),
        indexed_at=str(record.get("indexed_at", "")),
    )


def paper_document_from_record(record: dict[str, Any]) -> PaperDocument:
    summary = paper_document_summary_from_record(record)
    return PaperDocument(
        **asdict(summary),
        content_markdown=str(record.get("content_markdown", "")),
    )


def get_paper_document_record(*, docs_collection: Any, safe_doi: str) -> dict[str, Any] | None:
    result = collection_get(collection=docs_collection, ids=[safe_doi])
    ids = _normalize_result_list(result.get("ids"))
    if not ids:
        return None

    metadatas = _normalize_result_list(result.get("metadatas"))
    documents = _normalize_result_list(result.get("documents"))
    metadata = metadatas[0] if metadatas and isinstance(metadatas[0], dict) else {}
    document = str(documents[0] or "") if documents else ""
    record = document_record_from_metadata(metadata, document)
    record["content_hash"] = str(metadata.get("content_hash", ""))
    record["indexed_at"] = str(metadata.get("indexed_at", ""))
    record["cites"] = record["cites"] or extract_reference_dois(document)
    if not record["safe_doi"]:
        record["safe_doi"] = safe_doi
        record["uri"] = f"grados://papers/{safe_doi}"
    return record


def list_paper_document_records(*, docs_collection: Any) -> list[dict[str, Any]]:
    total = docs_collection.count()
    if total == 0:
        return []

    result = collection_get(collection=docs_collection, limit=total)
    documents = _normalize_result_list(result.get("documents"))
    metadatas = _normalize_result_list(result.get("metadatas"))
    records: list[dict[str, Any]] = []

    for metadata, document in zip(metadatas, documents):
        if not isinstance(metadata, dict):
            continue
        record = document_record_from_metadata(metadata, str(document or ""))
        record["content_hash"] = str(metadata.get("content_hash", ""))
        record["indexed_at"] = str(metadata.get("indexed_at", ""))
        record["cites"] = record["cites"] or extract_reference_dois(str(document or ""))
        records.append(record)

    return sorted(records, key=lambda item: item["safe_doi"])


def list_index_document_summaries(
    *,
    docs_collection: Any,
    chroma_dir: Path,
    fallback_list_paper_documents: Callable[[Path], list[Any]],
) -> list[dict[str, Any]]:
    total = docs_collection.count()
    if total <= 0:
        return []

    result = collection_get(collection=docs_collection, limit=total, include=["metadatas"])
    metadatas = _normalize_result_list(result.get("metadatas"))
    if not metadatas:
        return [
            {**record, "content_markdown": ""}
            for document in fallback_list_paper_documents(chroma_dir)
            if (record := _document_like_to_record(document))
        ]

    summaries = [
        document_record_from_metadata(metadata or {})
        for metadata in metadatas
        if isinstance(metadata, dict)
    ]
    return sorted(summaries, key=lambda item: item["safe_doi"])


def get_paper_documents_by_ids(
    *,
    docs_collection: Any,
    chroma_dir: Path,
    safe_dois: list[str],
    fallback_list_paper_documents: Callable[[Path], list[Any]],
) -> list[dict[str, Any]]:
    if not safe_dois:
        return []

    result = collection_get(collection=docs_collection, ids=safe_dois)
    raw_ids = _normalize_result_list(result.get("ids"))
    metadatas = _normalize_result_list(result.get("metadatas"))
    documents = _normalize_result_list(result.get("documents"))

    if not raw_ids:
        allowed = set(safe_dois)
        return [
            record
            for document in fallback_list_paper_documents(chroma_dir)
            if (record := _document_like_to_record(document)).get("safe_doi", "") in allowed
        ]

    by_safe_doi: dict[str, dict[str, Any]] = {}
    for index, raw_id in enumerate(raw_ids):
        metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
        document = str(documents[index] or "") if index < len(documents) else ""
        record = document_record_from_metadata(metadata, document)
        safe_doi = str(record.get("safe_doi", "") or raw_id)
        if not record["safe_doi"]:
            record["safe_doi"] = safe_doi
            record["uri"] = f"grados://papers/{safe_doi}"
        by_safe_doi[safe_doi] = record

    return [by_safe_doi[safe_doi] for safe_doi in safe_dois if safe_doi in by_safe_doi]


def hydrate_canonical_documents(documents: list[dict[str, Any]], papers_dir: Path | None) -> list[dict[str, Any]]:
    if papers_dir is None or not papers_dir.is_dir():
        return documents

    from grados.storage.papers import load_paper_record

    hydrated: list[dict[str, Any]] = []
    for document in documents:
        safe_doi = str(document.get("safe_doi", "")).strip()
        if not safe_doi:
            continue
        canonical = load_paper_record(papers_dir, safe_doi=safe_doi)
        if not canonical:
            continue
        hydrated.append({**document, **asdict(canonical)})
    return hydrated


def canonical_excerpt(record: dict[str, Any], paragraph_start: int, paragraph_count: int) -> str:
    text = str(record.get("content_markdown", "") or "").strip()
    if not text:
        return ""
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return ""
    start = max(0, paragraph_start)
    if start >= len(paragraphs):
        return ""
    count = max(0, paragraph_count)
    end = len(paragraphs) if count <= 0 else min(len(paragraphs), start + count)
    return "\n\n".join(paragraphs[start:end]).strip()
