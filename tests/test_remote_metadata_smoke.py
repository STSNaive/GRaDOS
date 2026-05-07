from __future__ import annotations

from pathlib import Path

from grados.publisher.common import PublisherMetadata, safe_doi_filename
from grados.search.academic import PaperMetadata
from grados.storage.remote_metadata import (
    get_remote_metadata_by_doi,
    migrate_remote_metadata_store,
    query_remote_metadata,
    record_remote_fetch_result,
    upsert_remote_metadata,
)


class FakeEmbeddingBackend:
    provider = "fake"
    model_id = "fake-model"
    query_prompt_mode = "query_document"

    def embed_documents(self, documents):  # noqa: ANN001
        return [[float(len(document))] for document in documents]

    def embed_query(self, query):  # noqa: ANN001
        return [float(len(query))]


class FakeRemoteMetadataCollection:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def upsert(self, *, ids, documents, metadatas, embeddings) -> None:  # noqa: ANN001, ANN003
        for row_id, document, metadata, embedding in zip(ids, documents, metadatas, embeddings, strict=False):
            self.rows[str(row_id)] = {
                "document": document,
                "metadata": metadata,
                "embedding": embedding,
            }

    def get(self, *, ids=None, limit=None, where=None, include=None):  # noqa: ANN001, ANN003
        items = list(self.rows.items())
        if ids is not None:
            requested = {str(row_id) for row_id in ids}
            items = [(row_id, row) for row_id, row in items if row_id in requested]
        if where:
            items = [
                (row_id, row)
                for row_id, row in items
                if all(row["metadata"].get(key) == value for key, value in where.items())
            ]
        if limit is not None:
            items = items[:limit]
        return {
            "ids": [row_id for row_id, _row in items],
            "documents": [row["document"] for _row_id, row in items],
            "metadatas": [row["metadata"] for _row_id, row in items],
        }

    def query(self, *, query_embeddings, n_results, where=None, where_document=None):  # noqa: ANN001, ANN003
        _ = query_embeddings
        _ = where_document
        items = list(self.rows.items())
        if where:
            items = [
                (row_id, row)
                for row_id, row in items
                if all(row["metadata"].get(key) == value for key, value in where.items())
            ]
        items = items[:n_results]
        return {
            "ids": [[row_id for row_id, _row in items]],
            "documents": [[row["document"] for _row_id, row in items]],
            "metadatas": [[row["metadata"] for _row_id, row in items]],
            "embeddings": [[row["embedding"] for _row_id, row in items]],
        }

    def count(self) -> int:
        return len(self.rows)


def test_remote_metadata_upsert_query_and_fetch_updates(tmp_path: Path, monkeypatch) -> None:
    import grados.storage.remote_metadata as remote_metadata

    collection = FakeRemoteMetadataCollection()

    monkeypatch.setattr(remote_metadata, "get_client", lambda chroma_dir: object())
    monkeypatch.setattr(remote_metadata, "get_remote_metadata_collection", lambda client: collection)
    monkeypatch.setattr(remote_metadata, "load_embedding_backend", lambda config=None: FakeEmbeddingBackend())

    inserted = upsert_remote_metadata(
        tmp_path / "chroma",
        [
            PaperMetadata(
                title="Composite Damping Study",
                abstract="A semantically searchable abstract.",
                authors=["Alice Smith"],
                doi="10.1234/demo",
                year="2026",
                source="Crossref",
                url="https://doi.org/10.1234/demo",
            )
        ],
    )

    assert inserted == 1

    record = get_remote_metadata_by_doi(tmp_path / "chroma", "10.1234/demo")

    assert record is not None
    assert record.paper_id == safe_doi_filename("10.1234/demo")
    assert record.fetch_status == "metadata_only"
    assert record.has_fulltext is False

    queried = query_remote_metadata(
        tmp_path / "chroma",
        "composite damping",
        where={"has_fulltext": False},
        limit=5,
    )

    assert len(queried) == 1
    assert queried[0].doi == "10.1234/demo"

    challenged = record_remote_fetch_result(
        tmp_path / "chroma",
        doi="10.1234/demo",
        fetch_status="challenge",
        has_fulltext=False,
        source="Headless Browser",
        fetch_via="browser",
        fetch_state="challenge",
        fetch_host="www.sciencedirect.com",
        fetch_resume={
            "kind": "browser_profile",
            "doi": "10.1234/demo",
            "host": "www.sciencedirect.com",
            "url": "https://www.sciencedirect.com/science/article/pii/S1234567890",
            "profile_dir": "/tmp/grados/browser/profile",
        },
        fetch_manual=True,
        fetch_trace=[
            {
                "via": "browser",
                "state": "challenge",
                "host": "www.sciencedirect.com",
                "time": "2026-04-25T00:00:00+00:00",
                "hash": "abc123",
                "manual": True,
                "resume": {"kind": "browser_profile"},
            }
        ],
    )

    assert challenged == 1

    challenge_record = get_remote_metadata_by_doi(tmp_path / "chroma", "10.1234/demo")

    assert challenge_record is not None
    assert challenge_record.fetch_status == "challenge"
    assert challenge_record.fetch_via == "browser"
    assert challenge_record.fetch_state == "challenge"
    assert challenge_record.fetch_host == "www.sciencedirect.com"
    assert challenge_record.fetch_manual is True
    assert '"kind": "browser_profile"' in challenge_record.fetch_resume
    assert '"state": "challenge"' in challenge_record.fetch_trace

    updated = record_remote_fetch_result(
        tmp_path / "chroma",
        doi="10.1234/demo",
        fetch_status="fulltext",
        has_fulltext=True,
        source="Elsevier TDM",
        metadata=PublisherMetadata(
            doi="10.1234/demo",
            title="Composite Damping Study",
            authors=["Alice Smith"],
            year="2026",
            journal="Composite Structures",
            publisher="Elsevier",
        ),
        fetch_trace=[
            {
                "via": "browser",
                "state": "ok",
                "host": "www.sciencedirect.com",
                "time": "2026-04-25T00:01:00+00:00",
                "hash": "def456",
                "manual": False,
                "resume": {},
            }
        ],
    )

    assert updated == 1

    refreshed = get_remote_metadata_by_doi(tmp_path / "chroma", "10.1234/demo")

    assert refreshed is not None
    assert refreshed.fetch_status == "fulltext"
    assert refreshed.has_fulltext is True
    assert refreshed.source == "Elsevier TDM"
    assert refreshed.journal == "Composite Structures"
    assert refreshed.fetch_manual is False
    assert refreshed.fetch_resume == ""
    assert '"state": "challenge"' in refreshed.fetch_trace
    assert '"state": "ok"' in refreshed.fetch_trace


def test_remote_metadata_preserves_computer_use_host_action(tmp_path: Path, monkeypatch) -> None:
    import grados.storage.remote_metadata as remote_metadata

    collection = FakeRemoteMetadataCollection()

    monkeypatch.setattr(remote_metadata, "get_client", lambda chroma_dir: object())
    monkeypatch.setattr(remote_metadata, "get_remote_metadata_collection", lambda client: collection)
    monkeypatch.setattr(remote_metadata, "load_embedding_backend", lambda config=None: FakeEmbeddingBackend())

    record_remote_fetch_result(
        tmp_path / "chroma",
        doi="10.1234/demo",
        fetch_status="failed",
        has_fulltext=False,
        source="API",
        fetch_via="api",
        fetch_state="error",
    )

    record_remote_fetch_result(
        tmp_path / "chroma",
        doi="10.1234/demo",
        fetch_status="host_action_required",
        has_fulltext=False,
        source="Codex Computer Use",
        fetch_via="codex",
        fetch_state="host_action_required",
        fetch_host="Microsoft Edge",
        fetch_resume={
            "kind": "codex",
            "doi": "10.1234/demo",
            "start_url": "https://doi.org/10.1234/demo",
        },
        fetch_manual=True,
    )

    record = get_remote_metadata_by_doi(tmp_path / "chroma", "10.1234/demo")

    assert record is not None
    assert record.fetch_status == "host_action_required"
    assert record.fetch_via == "codex"
    assert record.fetch_state == "host_action_required"
    assert record.fetch_host == "Microsoft Edge"
    assert record.fetch_manual is True
    assert '"kind": "codex"' in record.fetch_resume


def test_migrate_remote_metadata_store_copies_legacy_records(tmp_path: Path, monkeypatch) -> None:
    import grados.storage.remote_metadata as remote_metadata

    legacy_dir = tmp_path / "database" / "chroma"
    metadata_dir = tmp_path / "database" / "remote_metadata"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "chroma.sqlite3").write_text("stub", encoding="utf-8")

    legacy_collection = FakeRemoteMetadataCollection()
    metadata_collection = FakeRemoteMetadataCollection()
    legacy_collection.upsert(
        ids=["10_1234_demo"],
        documents=["Legacy Demo\n\nA cached remote abstract."],
        metadatas=[
            {
                "schema_version": 1,
                "doi": "10.1234/demo",
                "safe_doi": "10_1234_demo",
                "paper_id": "10_1234_demo",
                "title": "Legacy Demo",
                "authors": "[]",
                "fetch_status": "challenge",
                "fetch_manual": True,
                "fetch_resume": '{"kind": "browser_profile"}',
            }
        ],
        embeddings=[[1.0]],
    )

    collections = {
        legacy_dir: legacy_collection,
        metadata_dir: metadata_collection,
    }

    monkeypatch.setattr(remote_metadata, "get_client", lambda path: path)
    monkeypatch.setattr(remote_metadata, "get_remote_metadata_collection", lambda client: collections[client])
    monkeypatch.setattr(remote_metadata, "load_embedding_backend", lambda config=None: FakeEmbeddingBackend())

    migrated = migrate_remote_metadata_store(legacy_dir, metadata_dir)

    assert migrated == 1
    record = get_remote_metadata_by_doi(metadata_dir, "10.1234/demo")
    assert record is not None
    assert record.fetch_status == "challenge"
    assert record.fetch_manual is True
    assert record.abstract == "A cached remote abstract."
