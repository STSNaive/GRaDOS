"""Remote metadata cache stored outside the rebuildable paper index."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from grados.config import IndexingConfig
from grados.publisher.common import (
    PublisherMetadata,
    normalize_doi,
    safe_doi_filename,
    safe_doi_filename_candidates,
)
from grados.search.academic import PaperMetadata
from grados.storage.chroma_client import (
    collection_get,
    get_client,
    get_remote_metadata_collection,
    query_collection,
)
from grados.storage.chunking import resolve_indexing_config
from grados.storage.embedding import load_embedding_backend

__all__ = [
    "RemoteMetadataRecord",
    "get_remote_metadata_by_doi",
    "migrate_remote_metadata_store",
    "query_remote_metadata",
    "record_remote_fetch_result",
    "upsert_remote_metadata",
]


class RemoteMetadataRecord(BaseModel):
    """Normalized remote metadata row stored in the remote metadata Chroma store."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    doi: str = ""
    safe_doi: str = ""
    paper_id: str = ""
    title: str = ""
    authors: str = "[]"
    year: str = ""
    journal: str = ""
    source: str = ""
    source_id: str = ""
    has_abstract: bool = False
    has_fulltext: bool = False
    fetch_status: str = "metadata_only"
    fetch_via: str = ""
    fetch_state: str = ""
    fetch_host: str = ""
    fetch_resume: str = ""
    fetch_manual: bool = False
    fetch_trace: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    updated_at: str = ""
    abstract: str = Field(default="", exclude=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize_authors(authors: list[str] | None) -> str:
    return json.dumps([author for author in (authors or []) if author], ensure_ascii=False)


def _deserialize_authors(raw: Any) -> list[str]:
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


def _stable_paper_id(*, doi: str, title: str, year: str, source: str, source_id: str) -> str:
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return safe_doi_filename(normalized_doi)
    fingerprint = "|".join([
        title.strip().lower(),
        year.strip().lower(),
        source.strip().lower(),
        source_id.strip().lower(),
    ])
    digest = hashlib.sha1(fingerprint.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"remote_{digest}"


def _remote_record_lookup_ids(record: RemoteMetadataRecord) -> list[str]:
    ids: list[str] = []
    for value in [record.paper_id, *(safe_doi_filename_candidates(record.doi) if record.doi else [])]:
        if value and value not in ids:
            ids.append(value)
    return ids


def _existing_record_for(
    existing: dict[str, RemoteMetadataRecord],
    record: RemoteMetadataRecord,
) -> RemoteMetadataRecord | None:
    for lookup_id in _remote_record_lookup_ids(record):
        if lookup_id in existing:
            return existing[lookup_id]
    return None


def _build_document(title: str, abstract: str) -> str:
    title = title.strip()
    abstract = abstract.strip()
    if title and abstract:
        return f"{title}\n\n{abstract}"
    return title or abstract


def _coerce_remote_record(record: Any) -> RemoteMetadataRecord | None:
    if isinstance(record, RemoteMetadataRecord):
        return record

    if isinstance(record, PaperMetadata):
        doi = normalize_doi(record.doi)
        safe_doi = safe_doi_filename(doi) if doi else ""
        source_id = record.url.strip()
        return RemoteMetadataRecord(
            doi=doi,
            safe_doi=safe_doi,
            paper_id=_stable_paper_id(
                doi=doi,
                title=record.title,
                year=record.year,
                source=record.source,
                source_id=source_id,
            ),
            title=record.title.strip(),
            authors=_serialize_authors(record.authors),
            year=record.year.strip(),
            journal="",
            source=record.source.strip() or record.publisher.strip(),
            source_id=source_id,
            has_abstract=bool(record.abstract.strip()),
            has_fulltext=False,
            fetch_status="metadata_only",
            abstract=record.abstract.strip(),
        )

    if isinstance(record, PublisherMetadata):
        doi = normalize_doi(record.doi)
        safe_doi = safe_doi_filename(doi) if doi else ""
        source_id = (
            record.pii.strip()
            or record.eid.strip()
            or record.scidir_url.strip()
            or record.html_url.strip()
            or record.pdf_url.strip()
        )
        return RemoteMetadataRecord(
            doi=doi,
            safe_doi=safe_doi,
            paper_id=_stable_paper_id(
                doi=doi,
                title=record.title,
                year=record.year,
                source=record.publisher,
                source_id=source_id,
            ),
            title=record.title.strip(),
            authors=_serialize_authors(record.authors),
            year=record.year.strip(),
            journal=record.journal.strip(),
            source=record.publisher.strip(),
            source_id=source_id,
            has_abstract=bool(record.abstract.strip()),
            has_fulltext=False,
            fetch_status="metadata_only",
            abstract=record.abstract.strip(),
        )

    if isinstance(record, dict):
        payload = dict(record)
        doi = normalize_doi(str(payload.get("doi", "") or ""))
        title = str(payload.get("title", "") or "").strip()
        abstract = str(payload.get("abstract", "") or "").strip()
        year = str(payload.get("year", "") or "").strip()
        source = str(payload.get("source", "") or "").strip()
        source_id = str(payload.get("source_id", "") or "").strip()
        safe_doi = str(payload.get("safe_doi", "") or "").strip() or (safe_doi_filename(doi) if doi else "")
        paper_id = str(payload.get("paper_id", "") or "").strip() or _stable_paper_id(
            doi=doi,
            title=title,
            year=year,
            source=source,
            source_id=source_id,
        )
        authors = payload.get("authors", "[]")
        authors_json = authors if isinstance(authors, str) else _serialize_authors(_deserialize_authors(authors))
        return RemoteMetadataRecord.model_validate(
            {
                "schema_version": int(payload.get("schema_version", 1) or 1),
                "doi": doi,
                "safe_doi": safe_doi,
                "paper_id": paper_id,
                "title": title,
                "authors": authors_json,
                "year": year,
                "journal": str(payload.get("journal", "") or "").strip(),
                "source": source,
                "source_id": source_id,
                "has_abstract": bool(payload.get("has_abstract", bool(abstract))),
                "has_fulltext": bool(payload.get("has_fulltext", False)),
                "fetch_status": str(payload.get("fetch_status", "metadata_only") or "metadata_only"),
                "fetch_via": str(payload.get("fetch_via", "") or ""),
                "fetch_state": str(payload.get("fetch_state", "") or ""),
                "fetch_host": str(payload.get("fetch_host", "") or ""),
                "fetch_resume": str(payload.get("fetch_resume", "") or ""),
                "fetch_manual": bool(payload.get("fetch_manual", False)),
                "fetch_trace": str(payload.get("fetch_trace", "") or ""),
                "first_seen_at": str(payload.get("first_seen_at", "") or ""),
                "last_seen_at": str(payload.get("last_seen_at", "") or ""),
                "updated_at": str(payload.get("updated_at", "") or ""),
                "abstract": abstract,
            }
        )

    return None


def _record_from_chroma_row(metadata: dict[str, Any], document: str = "") -> RemoteMetadataRecord:
    record = RemoteMetadataRecord.model_validate({
        "schema_version": int(metadata.get("schema_version", 1) or 1),
        "doi": str(metadata.get("doi", "") or ""),
        "safe_doi": str(metadata.get("safe_doi", "") or ""),
        "paper_id": str(metadata.get("paper_id", "") or ""),
        "title": str(metadata.get("title", "") or ""),
        "authors": str(metadata.get("authors", "[]") or "[]"),
        "year": str(metadata.get("year", "") or ""),
        "journal": str(metadata.get("journal", "") or ""),
        "source": str(metadata.get("source", "") or ""),
        "source_id": str(metadata.get("source_id", "") or ""),
        "has_abstract": bool(metadata.get("has_abstract", False)),
        "has_fulltext": bool(metadata.get("has_fulltext", False)),
        "fetch_status": str(metadata.get("fetch_status", "metadata_only") or "metadata_only"),
        "fetch_via": str(metadata.get("fetch_via", "") or ""),
        "fetch_state": str(metadata.get("fetch_state", "") or ""),
        "fetch_host": str(metadata.get("fetch_host", "") or ""),
        "fetch_resume": str(metadata.get("fetch_resume", "") or ""),
        "fetch_manual": bool(metadata.get("fetch_manual", False)),
        "fetch_trace": str(metadata.get("fetch_trace", "") or ""),
        "first_seen_at": str(metadata.get("first_seen_at", "") or ""),
        "last_seen_at": str(metadata.get("last_seen_at", "") or ""),
        "updated_at": str(metadata.get("updated_at", "") or ""),
        "abstract": document.split("\n\n", 1)[1].strip() if "\n\n" in document else "",
    })
    if not record.paper_id:
        record.paper_id = record.safe_doi or _stable_paper_id(
            doi=record.doi,
            title=record.title,
            year=record.year,
            source=record.source,
            source_id=record.source_id,
        )
    return record


def _deserialize_fetch_trace(raw: str) -> list[Any]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _merge_fetch_trace(existing: str, incoming: str) -> str:
    incoming_items = _deserialize_fetch_trace(incoming)
    if not incoming_items:
        return existing

    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*_deserialize_fetch_trace(existing), *incoming_items]:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return json.dumps(merged, sort_keys=True, ensure_ascii=False)


def _merge_records(existing: RemoteMetadataRecord | None, incoming: RemoteMetadataRecord) -> RemoteMetadataRecord:
    now = _now_iso()
    if existing is None:
        record = incoming.model_copy(update={
            "first_seen_at": incoming.first_seen_at or now,
            "last_seen_at": now,
            "updated_at": now,
        })
        return record

    existing_authors = _deserialize_authors(existing.authors)
    incoming_authors = _deserialize_authors(incoming.authors)
    merged_abstract = incoming.abstract if len(incoming.abstract) >= len(existing.abstract) else existing.abstract
    incoming_succeeded = incoming.has_fulltext or incoming.fetch_status == "fulltext"
    merged = existing.model_copy(update={
        "doi": incoming.doi or existing.doi,
        "safe_doi": incoming.safe_doi or existing.safe_doi,
        "paper_id": incoming.paper_id or existing.paper_id,
        "title": incoming.title or existing.title,
        "authors": _serialize_authors(incoming_authors or existing_authors),
        "year": incoming.year or existing.year,
        "journal": incoming.journal or existing.journal,
        "source": incoming.source or existing.source,
        "source_id": incoming.source_id or existing.source_id,
        "has_abstract": existing.has_abstract or incoming.has_abstract or bool(merged_abstract),
        "has_fulltext": existing.has_fulltext or incoming.has_fulltext,
        "fetch_status": _merge_fetch_status(existing.fetch_status, incoming.fetch_status),
        "fetch_via": incoming.fetch_via or existing.fetch_via,
        "fetch_state": incoming.fetch_state or existing.fetch_state,
        "fetch_host": incoming.fetch_host or existing.fetch_host,
        "fetch_resume": "" if incoming_succeeded else incoming.fetch_resume or existing.fetch_resume,
        "fetch_manual": False if incoming_succeeded else incoming.fetch_manual or existing.fetch_manual,
        "fetch_trace": _merge_fetch_trace(existing.fetch_trace, incoming.fetch_trace),
        "first_seen_at": existing.first_seen_at or incoming.first_seen_at or now,
        "last_seen_at": now,
        "updated_at": now,
        "abstract": merged_abstract,
    })
    return merged


def _merge_fetch_status(existing: str, incoming: str) -> str:
    priority = {
        "fulltext": 7,
        "summary_failed": 6,
        "partial_success": 5,
        "host_action_required": 4,
        "challenge": 4,
        "failed": 3,
        "metadata_only": 2,
        "discovered": 1,
        "": 0,
    }
    return incoming if priority.get(incoming, 0) >= priority.get(existing, 0) else existing


def _existing_records_by_id(collection: Any, ids: list[str]) -> dict[str, RemoteMetadataRecord]:
    if not ids:
        return {}
    result = collection_get(collection=collection, ids=ids)
    row_ids = result.get("ids", [])
    metadatas = result.get("metadatas", [])
    documents = result.get("documents", [])
    if row_ids and isinstance(row_ids[0], list):
        row_ids = row_ids[0]
    if metadatas and isinstance(metadatas[0], list):
        metadatas = metadatas[0]
    if documents and isinstance(documents[0], list):
        documents = documents[0]

    existing: dict[str, RemoteMetadataRecord] = {}
    for row_id, metadata, document in zip(row_ids or [], metadatas or [], documents or []):
        if not isinstance(metadata, dict):
            continue
        record = _record_from_chroma_row(metadata, str(document or ""))
        existing[str(row_id)] = record
    return existing


def _all_records_from_collection(collection: Any) -> list[RemoteMetadataRecord]:
    count = int(collection.count())
    if count <= 0:
        return []
    result = collection_get(collection=collection, limit=count)
    metadatas = result.get("metadatas", [])
    documents = result.get("documents", [])
    if metadatas and isinstance(metadatas[0], list):
        metadatas = metadatas[0]
    if documents and isinstance(documents[0], list):
        documents = documents[0]
    return [
        _record_from_chroma_row(metadata, str(document or ""))
        for metadata, document in zip(metadatas or [], documents or [])
        if isinstance(metadata, dict)
    ]


def migrate_remote_metadata_store(
    legacy_chroma_dir: Path,
    metadata_dir: Path,
    *,
    indexing_config: IndexingConfig | None = None,
) -> int:
    """Copy legacy remote metadata rows out of the rebuildable paper index store."""
    if legacy_chroma_dir.resolve() == metadata_dir.resolve():
        return 0
    if not legacy_chroma_dir.exists() or not any(legacy_chroma_dir.iterdir()):
        return 0

    legacy_client = get_client(legacy_chroma_dir)
    legacy_collection = get_remote_metadata_collection(legacy_client)
    records = _all_records_from_collection(legacy_collection)
    if not records:
        return 0
    return upsert_remote_metadata(metadata_dir, records, indexing_config=indexing_config)


def upsert_remote_metadata(
    metadata_dir: Path,
    records: list[Any],
    *,
    indexing_config: IndexingConfig | None = None,
) -> int:
    """Upsert one row per paper into the remote metadata store."""
    normalized = [record for item in records if (record := _coerce_remote_record(item)) is not None]
    if not normalized:
        return 0

    config = resolve_indexing_config(indexing_config)
    client = get_client(metadata_dir)
    collection = get_remote_metadata_collection(client)
    lookup_ids: list[str] = []
    for record in normalized:
        for lookup_id in _remote_record_lookup_ids(record):
            if lookup_id not in lookup_ids:
                lookup_ids.append(lookup_id)
    existing = _existing_records_by_id(collection, lookup_ids)

    merged_records = [_merge_records(_existing_record_for(existing, record), record) for record in normalized]
    documents = [_build_document(record.title, record.abstract) for record in merged_records]
    backend = load_embedding_backend(config=config)
    embeddings = backend.embed_documents(documents)
    metadatas = [record.model_dump(exclude={"abstract"}) for record in merged_records]
    ids = [record.paper_id for record in merged_records]

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return len(ids)


def get_remote_metadata_by_doi(
    metadata_dir: Path,
    doi: str,
) -> RemoteMetadataRecord | None:
    """Load one remote metadata row by DOI."""
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return None
    paper_ids = safe_doi_filename_candidates(normalized_doi)
    client = get_client(metadata_dir)
    collection = get_remote_metadata_collection(client)
    existing = _existing_records_by_id(collection, paper_ids)
    for paper_id in paper_ids:
        if paper_id in existing:
            return existing[paper_id]
    return None


def query_remote_metadata(
    metadata_dir: Path,
    query: str,
    *,
    where: dict[str, Any] | None = None,
    limit: int = 10,
    indexing_config: IndexingConfig | None = None,
) -> list[RemoteMetadataRecord]:
    """Run a semantic query against the remote metadata collection."""
    client = get_client(metadata_dir)
    collection = get_remote_metadata_collection(client)
    if collection.count() == 0:
        return []

    if not query.strip():
        result = collection_get(collection=collection, limit=limit, where=where)
        ids = result.get("ids", [])
        metadatas = result.get("metadatas", [])
        documents = result.get("documents", [])
        if ids and isinstance(ids[0], list):
            ids = ids[0]
        if metadatas and isinstance(metadatas[0], list):
            metadatas = metadatas[0]
        if documents and isinstance(documents[0], list):
            documents = documents[0]
        return [
            _record_from_chroma_row(metadata, str(document or ""))
            for metadata, document in zip(metadatas or [], documents or [])
            if isinstance(metadata, dict)
        ]

    config = resolve_indexing_config(indexing_config)
    backend = load_embedding_backend(config=config)
    query_embedding = backend.embed_query(query)
    result = query_collection(
        collection=collection,
        query_embedding=query_embedding,
        n_results=max(1, min(limit, 100)),
        where=where,
    )
    metadatas = result.get("metadatas", [[]])
    documents = result.get("documents", [[]])
    rows = metadatas[0] if metadatas and isinstance(metadatas[0], list) else metadatas or []
    docs = documents[0] if documents and isinstance(documents[0], list) else documents or []
    return [
        _record_from_chroma_row(metadata, str(document or ""))
        for metadata, document in zip(rows, docs)
        if isinstance(metadata, dict)
    ]


def record_remote_fetch_result(
    metadata_dir: Path,
    *,
    doi: str,
    fetch_status: str,
    has_fulltext: bool,
    source: str = "",
    title: str = "",
    metadata: PublisherMetadata | None = None,
    fetch_via: str = "",
    fetch_state: str = "",
    fetch_host: str = "",
    fetch_resume: dict[str, str] | None = None,
    fetch_manual: bool = False,
    fetch_trace: list[dict[str, Any]] | None = None,
    indexing_config: IndexingConfig | None = None,
) -> int:
    """Upsert one DOI after a fetch/materialize attempt."""
    normalized = normalize_doi(doi)
    base_title = title.strip()
    if metadata is not None:
        record = _coerce_remote_record(metadata)
        payload: dict[str, Any] = record.model_dump() if record is not None else {}
    else:
        payload = {}

    payload.update({
        "doi": normalized,
        "safe_doi": safe_doi_filename(normalized) if normalized else "",
        "paper_id": safe_doi_filename(normalized) if normalized else "",
        "title": payload.get("title") or base_title,
        "source": source.strip() or payload.get("source", ""),
        "has_fulltext": has_fulltext,
        "fetch_status": fetch_status,
        "fetch_via": fetch_via.strip(),
        "fetch_state": fetch_state.strip(),
        "fetch_host": fetch_host.strip(),
        "fetch_resume": json.dumps(fetch_resume, sort_keys=True, ensure_ascii=False) if fetch_resume else "",
        "fetch_manual": bool(fetch_manual),
        "fetch_trace": (
            json.dumps(fetch_trace, sort_keys=True, ensure_ascii=False)
            if fetch_trace is not None
            else ""
        ),
    })
    return upsert_remote_metadata(metadata_dir, [payload], indexing_config=indexing_config)
