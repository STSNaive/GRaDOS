"""Thin ChromaDB client helpers for storage facade modules."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

__all__ = [
    "collection_get",
    "current_chroma_call_timeout_seconds",
    "delete_paper_chunks",
    "filter_query_result",
    "get_chunks_collection",
    "get_client",
    "get_docs_collection",
    "query_collection",
]

_DOCS_COLLECTION_NAME = "papers_docs"
_CHUNKS_COLLECTION_NAME = "papers_chunks"
_CHROMA_CALL_TIMEOUT_SECONDS = 10.0
_CHROMA_CALL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="grados-chroma")
logger = logging.getLogger(__name__)


def current_chroma_call_timeout_seconds() -> float:
    return _CHROMA_CALL_TIMEOUT_SECONDS


def _run_with_timeout(label: str, func: Any) -> Any:
    timeout_seconds = current_chroma_call_timeout_seconds()
    future = _CHROMA_CALL_EXECUTOR.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"Chroma {label} timed out after {timeout_seconds:.2f}s.") from exc


def _as_result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    return {}


def get_client(chroma_dir: Path) -> Any:
    """Return persistent ChromaDB client."""
    import chromadb

    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def get_docs_collection(client: Any) -> Any:
    """Get or create canonical document collection."""
    return client.get_or_create_collection(
        name=_DOCS_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def get_chunks_collection(client: Any) -> Any:
    """Get or create retrieval chunk collection."""
    return client.get_or_create_collection(
        name=_CHUNKS_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def collection_get(
    *,
    collection: Any,
    ids: list[str] | None = None,
    limit: int | None = None,
    include: list[str] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if ids is not None:
        params["ids"] = ids
    if limit is not None:
        params["limit"] = limit
    if include is not None:
        params["include"] = include

    warnings: list[str] = []
    try:
        return _as_result_dict(_run_with_timeout("get()", lambda: collection.get(**params)))
    except TimeoutError as exc:
        warnings.append(str(exc))
        logger.warning(warnings[-1])
        return {"degraded_filter": True, "warnings": warnings}
    except TypeError:
        warnings.append("Chroma get() does not support include projection; retried without include.")
        logger.warning(warnings[-1])
        params.pop("include", None)
        try:
            result = _as_result_dict(_run_with_timeout("get()", lambda: collection.get(**params)))
        except TimeoutError as exc:
            warnings.append(str(exc))
            logger.warning(warnings[-1])
            return {"degraded_filter": True, "warnings": warnings}
        return {
            **result,
            "degraded_filter": True,
            "warnings": warnings,
        }
    except Exception as exc:
        warnings.append(f"Chroma get() failed with projection parameters: {exc.__class__.__name__}: {exc}")
        logger.warning(warnings[-1])
        params.pop("include", None)
        try:
            result = _as_result_dict(_run_with_timeout("get()", lambda: collection.get(**params)))
            return {
                **result,
                "degraded_filter": True,
                "warnings": warnings,
            }
        except TimeoutError as timeout_exc:
            warnings.append(str(timeout_exc))
            logger.warning(warnings[-1])
            return {"degraded_filter": True, "warnings": warnings}
        except Exception:
            logger.exception("Chroma get() failed after fallback retry")
            return {"degraded_filter": True, "warnings": warnings}


def query_collection(
    *,
    collection: Any,
    query_embedding: list[float],
    n_results: int,
    where: dict[str, Any] | None = None,
    where_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
    }
    if where is not None:
        params["where"] = where
    if where_document is not None:
        params["where_document"] = where_document

    warnings: list[str] = []
    try:
        return _as_result_dict(_run_with_timeout("query()", lambda: collection.query(**params)))
    except TimeoutError as exc:
        warnings.append(str(exc))
        logger.warning(warnings[-1])
        return {"degraded_filter": True, "warnings": warnings}
    except TypeError:
        if "where_document" in params:
            warnings.append("Chroma query() does not support where_document; retried without document filter.")
            logger.warning(warnings[-1])
            params.pop("where_document", None)
        try:
            return {
                **_as_result_dict(_run_with_timeout("query()", lambda: collection.query(**params))),
                "degraded_filter": bool(warnings),
                "warnings": warnings,
            }
        except TimeoutError as exc:
            warnings.append(str(exc))
            logger.warning(warnings[-1])
            return {"degraded_filter": True, "warnings": warnings}
        except TypeError:
            if "where" in params:
                warnings.append("Chroma query() does not support where filter; retried without metadata filter.")
                logger.warning(warnings[-1])
                params.pop("where", None)
            try:
                return {
                    **_as_result_dict(_run_with_timeout("query()", lambda: collection.query(**params))),
                    "degraded_filter": bool(warnings),
                    "warnings": warnings,
                }
            except TimeoutError as exc:
                warnings.append(str(exc))
                logger.warning(warnings[-1])
                return {"degraded_filter": True, "warnings": warnings}
    except Exception as exc:
        warnings.append(f"Chroma query() failed with filters: {exc.__class__.__name__}: {exc}")
        logger.warning(warnings[-1])
        params.pop("where_document", None)
        params.pop("where", None)
        try:
            return {
                **_as_result_dict(_run_with_timeout("query()", lambda: collection.query(**params))),
                "degraded_filter": True,
                "warnings": warnings,
            }
        except TimeoutError as timeout_exc:
            warnings.append(str(timeout_exc))
            logger.warning(warnings[-1])
            return {"degraded_filter": True, "warnings": warnings}
        except Exception:
            logger.exception("Chroma query() failed after fallback retry")
            return {"degraded_filter": True, "warnings": warnings}


def filter_query_result(result: dict[str, Any], positions: list[int]) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in result.items():
        if not isinstance(value, list) or not value or not isinstance(value[0], list):
            filtered[key] = value
            continue
        filtered[key] = [[row[index] for index in positions if index < len(row)] for row in value]
    return filtered


def delete_paper_chunks(collection: Any, safe_doi: str) -> None:
    """Remove all chunk rows for one paper."""
    try:
        existing = collection.get(where={"safe_doi": safe_doi})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        logger.exception("Failed to delete Chroma chunks for %s", safe_doi)
