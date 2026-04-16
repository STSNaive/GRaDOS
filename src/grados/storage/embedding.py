"""Embedding runtime and index compatibility helpers."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from grados.config import GRaDOSPaths, IndexingConfig

__all__ = [
    "EmbeddingBackend",
    "IndexCompatibilityError",
    "build_index_manifest",
    "clear_embedding_backend_cache",
    "inspect_embedding_runtime",
    "inspect_index_compatibility",
    "load_embedding_backend",
    "read_index_manifest",
    "write_index_manifest",
]

_INDEX_MANIFEST_NAME = "index-manifest.json"
_INDEX_SCHEMA_VERSION = 3
_RETRIEVAL_STRATEGY = "two-stage-v1"
_CHUNKING_STRATEGY = "section-aware-v3"
_BACKEND_RUNTIME = "sentence-transformers"
_EMBEDDING_BACKEND_CACHE: dict[tuple[Any, ...], EmbeddingBackend] = {}
_EMBEDDING_BACKEND_CACHE_LOCK = RLock()


class IndexCompatibilityError(RuntimeError):
    """Raised when the on-disk semantic index no longer matches the runtime config."""


@dataclass
class EmbeddingBackend:
    """SentenceTransformers-backed embedding runtime."""

    config: IndexingConfig
    cache_dir: Path
    _model: Any | None = None
    _model_lock: Any = field(default_factory=RLock, init=False, repr=False)

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

        with self._model_lock:
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

            max_length = self._resolved_max_length(model)
            if hasattr(model, "max_seq_length") and max_length > 0:
                model.max_seq_length = max_length

            self._model = model
            return model

    def _encode(self, texts: list[str], *, query_mode: bool = False) -> list[list[float]]:
        model = self._load_model()
        encode_kwargs: dict[str, Any] = {
            "batch_size": self._resolved_batch_size(model, query_mode=query_mode),
            "normalize_embeddings": True,
            "show_progress_bar": False,
        }

        try:
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
        except Exception as exc:
            if not _looks_like_oom(exc):
                raise
            diagnostics = self._encoding_diagnostics(model, texts, query_mode=query_mode)
            raise RuntimeError(
                "Embedding encode ran out of memory. "
                f"{diagnostics}. Consider lowering `indexing.max_length`, reducing `indexing.batch_size`, "
                "or switching to `microsoft/harrier-oss-v1-270m` on memory-constrained machines."
            ) from exc

        rows = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        return [[float(value) for value in row] for row in rows]

    def _encode_with_instruction(self, model: Any, texts: list[str], encode_kwargs: dict[str, Any]) -> Any:
        prompt = f"Instruct: {self.config.query_instruction}\nQuery: "
        return model.encode(texts, prompt=prompt, **encode_kwargs)

    def _resolved_max_length(self, model: Any) -> int:
        if self.config.max_length <= 0:
            return 0

        tokenizer = getattr(model, "tokenizer", None)
        tokenizer_limit = getattr(tokenizer, "model_max_length", 0)
        if isinstance(tokenizer_limit, int) and 0 < tokenizer_limit < 1_000_000:
            return min(self.config.max_length, tokenizer_limit)
        return self.config.max_length

    def _resolved_runtime_device(self, model: Any) -> str:
        if self.config.device != "auto":
            return str(self.config.device)
        device = getattr(model, "device", None)
        if device is None:
            return "cpu"
        return str(getattr(device, "type", device))

    def _resolved_batch_size(self, model: Any, *, query_mode: bool) -> int:
        if query_mode:
            return 1
        if self.config.batch_size > 0:
            return self.config.batch_size

        device = self._resolved_runtime_device(model).lower()
        if device.startswith("cuda"):
            return 16
        if _is_harrier_0_6b(self.model_id):
            return 1
        return 2

    def _encoding_diagnostics(self, model: Any, texts: list[str], *, query_mode: bool) -> str:
        batch_size = self._resolved_batch_size(model, query_mode=query_mode)
        device = self._resolved_runtime_device(model)
        max_length = self._resolved_max_length(model)
        longest = sorted(
            ((len(text), index, text) for index, text in enumerate(texts)),
            reverse=True,
        )[:3]
        summaries: list[str] = []
        for char_count, index, text in longest:
            token_count = self._token_count(model, text)
            summary = f"#{index}: {char_count} chars"
            if token_count is not None:
                summary += f", ~{token_count} tokens"
            summaries.append(summary)
        return (
            f"model={self.model_id}, device={device}, batch_size={batch_size}, max_length={max_length}, "
            f"texts={len(texts)}, longest=[{'; '.join(summaries)}]"
        )

    def _token_count(self, model: Any, text: str) -> int | None:
        tokenize = getattr(model, "tokenize", None)
        if not callable(tokenize):
            return None
        try:
            payload = tokenize([text])
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        input_ids = payload.get("input_ids")
        if input_ids is None:
            return None
        if hasattr(input_ids, "shape") and len(getattr(input_ids, "shape", ())) >= 2:
            return int(input_ids.shape[1])
        try:
            return len(input_ids[0])
        except (IndexError, TypeError):
            return None


def load_embedding_backend(
    *,
    paths: GRaDOSPaths | None = None,
    config: IndexingConfig | None = None,
) -> EmbeddingBackend:
    """Construct process-local embedding backend from config + resolved paths.

    Cache lifecycle:
    - Lives only in current Python process
    - Reused while backend-significant config stays same
    - Invalidated automatically when model/device/prompt/max_length/cache_dir changes
    - Cleared on process exit or `clear_embedding_backend_cache()`
    """
    resolved_paths = paths or GRaDOSPaths()
    resolved_config = config.model_copy(deep=True) if config is not None else IndexingConfig()
    cache_dir = _resolve_cache_dir(resolved_paths, resolved_config)
    cache_key = _backend_cache_key(resolved_config, cache_dir)

    with _EMBEDDING_BACKEND_CACHE_LOCK:
        cached = _EMBEDDING_BACKEND_CACHE.get(cache_key)
        if cached is not None:
            return cached

        backend = EmbeddingBackend(config=resolved_config, cache_dir=cache_dir)
        _EMBEDDING_BACKEND_CACHE[cache_key] = backend
        return backend


def clear_embedding_backend_cache() -> None:
    """Clear process-local embedding backend cache."""
    with _EMBEDDING_BACKEND_CACHE_LOCK:
        _EMBEDDING_BACKEND_CACHE.clear()


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
        "max_length": config.max_length,
        "batch_size": config.batch_size,
        "batch_size_hint": _batch_size_hint(config),
        "cache_dir": str(cache_dir),
        "cache_ready": cache_ready,
        "runtime": _BACKEND_RUNTIME,
        "dependencies": deps,
        "warnings": _runtime_warnings(config),
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


def _backend_cache_key(config: IndexingConfig, cache_dir: Path) -> tuple[Any, ...]:
    return (
        config.provider,
        config.model_id,
        config.query_prompt_name,
        config.query_instruction,
        config.max_length,
        config.device,
        str(cache_dir),
    )


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _query_prompt_mode(config: IndexingConfig) -> str:
    if config.query_prompt_name:
        return f"prompt_name:{config.query_prompt_name}"
    if config.query_instruction:
        return "instruction"
    return "none"


def _is_harrier_0_6b(model_id: str) -> bool:
    return model_id.strip().lower() == "microsoft/harrier-oss-v1-0.6b"


def _batch_size_hint(config: IndexingConfig) -> str:
    if config.batch_size > 0:
        return str(config.batch_size)
    if config.device == "cuda":
        return "auto (16 on CUDA)"
    if config.device in {"cpu", "mps"}:
        return "auto (1 for Harrier 0.6B, else 2)"
    return "auto (1-2 on CPU/MPS, 16 on CUDA)"


def _runtime_warnings(config: IndexingConfig) -> list[str]:
    warnings: list[str] = []
    if _is_harrier_0_6b(config.model_id):
        warnings.append(
            "Harrier 0.6B is opt-in for roomy machines now; on CPU/MPS, keep `batch_size` small and prefer "
            "`max_length` around 4096 for reindex runs."
        )
    if config.max_length > 8192:
        warnings.append(
            "`indexing.max_length` is above the recommended local indexing range. Model max context is not the "
            "same as a safe indexing length."
        )
    return warnings


def _looks_like_oom(exc: Exception) -> bool:
    if isinstance(exc, MemoryError):
        return True
    message = str(exc).lower()
    markers = (
        "out of memory",
        "unable to allocate",
        "cannot allocate",
        "can't allocate",
        "mps backend out of memory",
        "cuda out of memory",
        "std::bad_alloc",
        "insufficient memory",
    )
    return any(marker in message for marker in markers)
