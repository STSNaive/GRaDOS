"""Embedding runtime and index compatibility helpers."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grados.config import GRaDOSPaths, IndexingConfig

__all__ = [
    "EmbeddingBackend",
    "IndexCompatibilityError",
    "build_index_manifest",
    "inspect_embedding_runtime",
    "inspect_index_compatibility",
    "load_embedding_backend",
    "read_index_manifest",
    "write_index_manifest",
]

_INDEX_MANIFEST_NAME = "index-manifest.json"
_INDEX_SCHEMA_VERSION = 2
_RETRIEVAL_STRATEGY = "two-stage-v1"
_CHUNKING_STRATEGY = "section-aware-v1"
_BACKEND_RUNTIME = "sentence-transformers"


class IndexCompatibilityError(RuntimeError):
    """Raised when the on-disk semantic index no longer matches the runtime config."""


@dataclass
class EmbeddingBackend:
    """SentenceTransformers-backed embedding runtime."""

    config: IndexingConfig
    cache_dir: Path
    _model: Any | None = None

    @property
    def model_id(self) -> str:
        return self.config.model_id

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def query_prompt_mode(self) -> str:
        if self.config.query_prompt_name:
            return f"prompt_name:{self.config.query_prompt_name}"
        if self.config.query_instruction:
            return "instruction"
        return "none"

    @property
    def embedding_dim(self) -> int:
        model = self._load_model()
        getter = getattr(model, "get_sentence_embedding_dimension", None)
        if callable(getter):
            dimension = getter()
            return int(dimension or 0)
        raise RuntimeError("Embedding backend does not expose embedding dimension.")

    def warmup(self) -> None:
        """Download and validate the default embedding runtime."""
        self.embed_documents(["warmup"])
        self.embed_query("warmup")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode retrieval documents without query instructions."""
        if not texts:
            return []
        return self._encode(texts)

    def embed_query(self, query: str) -> list[float]:
        """Encode a retrieval query with Harrier-compatible prompting."""
        embeddings = self._encode([query], query_mode=True)
        if not embeddings:
            raise RuntimeError("Query embedding returned no vectors.")
        return embeddings[0]

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Missing embedding runtime. Install `sentence-transformers` (and its torch/transformers "
                "dependencies) before using semantic indexing."
            ) from exc

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"cache_folder": str(self.cache_dir)}
        if self.config.device != "auto":
            kwargs["device"] = self.config.device

        try:
            model = SentenceTransformer(
                self.model_id,
                model_kwargs={"dtype": "auto"},
                **kwargs,
            )
        except TypeError:
            model = SentenceTransformer(self.model_id, **kwargs)

        if hasattr(model, "max_seq_length") and self.config.max_length > 0:
            model.max_seq_length = self.config.max_length

        self._model = model
        return model

    def _encode(self, texts: list[str], *, query_mode: bool = False) -> list[list[float]]:
        model = self._load_model()
        encode_kwargs: dict[str, Any] = {
            "normalize_embeddings": True,
            "show_progress_bar": False,
        }

        if query_mode:
            if self.config.query_prompt_name:
                try:
                    encoded = model.encode(texts, prompt_name=self.config.query_prompt_name, **encode_kwargs)
                except TypeError:
                    encoded = self._encode_with_instruction(model, texts, encode_kwargs)
            elif self.config.query_instruction:
                encoded = self._encode_with_instruction(model, texts, encode_kwargs)
            else:
                encoded = model.encode(texts, **encode_kwargs)
        else:
            encoded = model.encode(texts, **encode_kwargs)

        rows = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        return [[float(value) for value in row] for row in rows]

    def _encode_with_instruction(self, model: Any, texts: list[str], encode_kwargs: dict[str, Any]) -> Any:
        prompt = f"Instruct: {self.config.query_instruction}\nQuery: "
        return model.encode(texts, prompt=prompt, **encode_kwargs)


def load_embedding_backend(
    *,
    paths: GRaDOSPaths | None = None,
    config: IndexingConfig | None = None,
) -> EmbeddingBackend:
    """Construct the default embedding backend from config + resolved paths."""
    resolved_paths = paths or GRaDOSPaths()
    resolved_config = config or IndexingConfig()
    cache_dir = _resolve_cache_dir(resolved_paths, resolved_config)
    return EmbeddingBackend(config=resolved_config, cache_dir=cache_dir)


def inspect_embedding_runtime(paths: GRaDOSPaths, config: IndexingConfig) -> dict[str, Any]:
    """Summarize the local embedding runtime without loading the heavy model."""
    deps = {
        "sentence_transformers": _module_available("sentence_transformers"),
        "transformers": _module_available("transformers"),
        "torch": _module_available("torch"),
    }
    cache_dir = _resolve_cache_dir(paths, config)
    cache_ready = cache_dir.exists() and any(cache_dir.iterdir()) if cache_dir.exists() else False
    return {
        "provider": config.provider,
        "model_id": config.model_id,
        "query_prompt_name": config.query_prompt_name,
        "query_prompt_mode": _query_prompt_mode(config),
        "device": config.device,
        "cache_dir": str(cache_dir),
        "cache_ready": cache_ready,
        "runtime": _BACKEND_RUNTIME,
        "dependencies": deps,
        "ready": all(deps.values()) and cache_ready,
    }


def build_index_manifest(
    *,
    config: IndexingConfig,
    backend: EmbeddingBackend,
    unique_papers: int,
    total_chunks: int,
) -> dict[str, Any]:
    """Build the persisted semantic-index manifest."""
    return {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "provider": config.provider,
        "model_id": config.model_id,
        "embedding_dim": backend.embedding_dim,
        "max_length": config.max_length,
        "query_prompt_name": config.query_prompt_name,
        "query_prompt_mode": backend.query_prompt_mode,
        "runtime": _BACKEND_RUNTIME,
        "retrieval_strategy": _RETRIEVAL_STRATEGY,
        "chunking_strategy": _CHUNKING_STRATEGY,
        "chunk_min_chars": config.chunk_min_chars,
        "chunk_max_chars": config.chunk_max_chars,
        "chunk_overlap_paragraphs": config.chunk_overlap_paragraphs,
        "unique_papers": unique_papers,
        "total_chunks": total_chunks,
    }


def read_index_manifest(chroma_dir: Path | None) -> dict[str, Any] | None:
    """Read the semantic-index manifest if it exists."""
    if chroma_dir is None:
        return None
    path = chroma_dir / _INDEX_MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_index_manifest(chroma_dir: Path, manifest: dict[str, Any]) -> None:
    """Persist the semantic-index manifest alongside the Chroma store."""
    chroma_dir.mkdir(parents=True, exist_ok=True)
    path = chroma_dir / _INDEX_MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def inspect_index_compatibility(chroma_dir: Path | None, config: IndexingConfig) -> dict[str, Any]:
    """Compare the on-disk semantic index with the active indexing config."""
    if chroma_dir is None:
        return {
            "compatible": True,
            "reindex_required": False,
            "reason": "",
            "expected": _expected_signature(config),
            "manifest": None,
            "mismatches": [],
        }

    manifest = read_index_manifest(chroma_dir)
    has_files = chroma_dir.exists() and any(chroma_dir.iterdir()) if chroma_dir.exists() else False
    expected = _expected_signature(config)

    if manifest is None:
        if has_files:
            return {
                "compatible": False,
                "reindex_required": True,
                "reason": "现有向量库缺少 index-manifest.json，默认视为旧索引，需要重新构建。",
                "expected": expected,
                "manifest": None,
                "mismatches": ["missing_manifest"],
            }
        return {
            "compatible": True,
            "reindex_required": False,
            "reason": "",
            "expected": expected,
            "manifest": None,
            "mismatches": [],
        }

    actual = {key: manifest.get(key) for key in expected}
    mismatches = [key for key, value in expected.items() if actual.get(key) != value]
    if mismatches:
        reason = (
            "当前 indexing 配置与现有语义索引不一致："
            + ", ".join(
                f"{key}={actual.get(key)!r} -> {expected[key]!r}"
                for key in mismatches
            )
            + "。请先运行 `grados reindex`。"
        )
        return {
            "compatible": False,
            "reindex_required": True,
            "reason": reason,
            "expected": expected,
            "manifest": manifest,
            "mismatches": mismatches,
        }

    return {
        "compatible": True,
        "reindex_required": False,
        "reason": "",
        "expected": expected,
        "manifest": manifest,
        "mismatches": [],
    }


def _expected_signature(config: IndexingConfig) -> dict[str, Any]:
    return {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "provider": config.provider,
        "model_id": config.model_id,
        "max_length": config.max_length,
        "retrieval_strategy": _RETRIEVAL_STRATEGY,
        "chunking_strategy": _CHUNKING_STRATEGY,
        "chunk_min_chars": config.chunk_min_chars,
        "chunk_max_chars": config.chunk_max_chars,
        "chunk_overlap_paragraphs": config.chunk_overlap_paragraphs,
    }


def _resolve_cache_dir(paths: GRaDOSPaths, config: IndexingConfig) -> Path:
    if config.cache_dir:
        return Path(config.cache_dir).expanduser().resolve()
    return paths.models_embedding


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _query_prompt_mode(config: IndexingConfig) -> str:
    if config.query_prompt_name:
        return f"prompt_name:{config.query_prompt_name}"
    if config.query_instruction:
        return "instruction"
    return "none"
