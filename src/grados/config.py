"""GRaDOS configuration loading and path resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "GRaDOSConfig",
    "GRaDOSPaths",
    "IndexingConfig",
    "generate_default_config",
    "load_config",
    "resolve_data_root",
]

# ── Path resolution ──────────────────────────────────────────────────────────


def resolve_data_root() -> Path:
    """Resolve the GRaDOS data root directory.

    Priority:
      1. GRADOS_HOME environment variable
      2. ~/GRaDOS/ (default, non-hidden, cross-platform)
    """
    if env := os.environ.get("GRADOS_HOME"):
        return Path(env).expanduser().resolve()
    return Path.home() / "GRaDOS"


# ── Well-known subdirectories ────────────────────────────────────────────────


class GRaDOSPaths:
    """All well-known paths derived from a single data root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or resolve_data_root()

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def papers(self) -> Path:
        return self.root / "papers"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def database_root(self) -> Path:
        return self.root / "database"

    @property
    def browser_root(self) -> Path:
        return self.root / "browser"

    @property
    def browser_chromium(self) -> Path:
        return self.browser_root / "chromium"

    @property
    def browser_profile(self) -> Path:
        return self.browser_root / "profile"

    @property
    def browser_extensions(self) -> Path:
        return self.browser_root / "extensions"

    @property
    def models_root(self) -> Path:
        return self.root / "models"

    @property
    def models_embedding(self) -> Path:
        return self.models_root / "embedding"

    @property
    def models_ocr(self) -> Path:
        return self.models_root / "ocr"

    @property
    def database_chroma(self) -> Path:
        return self.database_root / "chroma"

    @property
    def database_state(self) -> Path:
        return self.database_root / "research.sqlite3"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    def ensure_directories(self) -> None:
        """Create the core directory structure."""
        for d in [
            self.root,
            self.papers,
            self.downloads,
            self.database_root,
            self.logs,
            self.cache,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def all_paths(self) -> list[tuple[str, Path]]:
        """Return label-path pairs for display."""
        return [
            ("数据根目录", self.root),
            ("配置文件", self.config_file),
            ("论文目录", self.papers),
            ("下载目录", self.downloads),
            ("状态数据库", self.database_state),
            ("浏览器二进制", self.browser_chromium),
            ("浏览器配置", self.browser_profile),
            ("浏览器扩展", self.browser_extensions),
            ("嵌入模型", self.models_embedding),
            ("ChromaDB", self.database_chroma),
            ("日志目录", self.logs),
        ]


# ── Pydantic config models (mirrors grados-config.json) ─────────────────────


class SearchConfig(BaseModel):
    order: list[str] = Field(
        default=["Elsevier", "Springer", "WebOfScience", "Crossref", "PubMed"]
    )
    enabled: dict[str, bool] = Field(default_factory=lambda: {
        "Elsevier": True,
        "Springer": True,
        "WebOfScience": True,
        "Crossref": True,
        "PubMed": True,
    })
    connect_timeout: float = Field(
        default=10.0,
        ge=1.0,
        description="TCP connect timeout (seconds) for search API calls.",
    )
    read_timeout: float = Field(
        default=30.0,
        ge=1.0,
        description="Response read timeout (seconds) for search API calls.",
    )


class FetchStrategyConfig(BaseModel):
    order: list[str] = Field(default=["TDM", "OA", "SciHub", "Headless"])
    enabled: dict[str, bool] = Field(default_factory=lambda: {
        "TDM": True,
        "OA": True,
        "SciHub": True,
        "Headless": True,
    })


class SciHubConfig(BaseModel):
    auto_update_mirror: bool = True
    mirror_url_file: str = ""
    fallback_mirror: str = "https://sci-hub.se"


class HeadlessBrowserConfig(BaseModel):
    browser: str = "chrome"
    prefer_managed_browser: bool = True
    auto_install_managed_browser: bool = True
    use_persistent_profile: bool = True
    executable_path: str = ""
    reuse_interactive_window: bool = True
    keep_interactive_window_open: bool = True
    close_pdf_page_after_capture: bool = True
    deadline_seconds: float = Field(
        default=120.0,
        ge=10.0,
        description="Maximum wall-clock seconds for the main browser polling loop (per DOI).",
    )
    networkidle_timeout: float = Field(
        default=15.0,
        ge=1.0,
        description=(
            "Ceiling (seconds) for wait_for_load_state('networkidle'). SPA "
            "background polling can keep the network busy indefinitely; this "
            "timeout hands control back to the main polling loop instead of "
            "silently eating the deadline."
        ),
    )
    poll_min_seconds: float = Field(
        default=0.5,
        ge=0.1,
        description=(
            "Starting sleep between main-loop iterations. Backs off up to "
            "poll_max_seconds on consecutive idle ticks."
        ),
    )
    poll_max_seconds: float = Field(
        default=2.0,
        ge=0.1,
        description="Upper bound for the main-loop sleep after backoff. Must be >= poll_min_seconds.",
    )


class ParsingConfig(BaseModel):
    order: list[str] = Field(default=["Docling", "Marker", "PyMuPDF"])
    enabled: dict[str, bool] = Field(default_factory=lambda: {
        "Docling": True,
        "Marker": False,
        "PyMuPDF": True,
    })
    marker_timeout: int = Field(default=120000, description="Timeout in milliseconds for isolated Marker parser runs.")


class QAConfig(BaseModel):
    min_characters: int = 1500


class TDMConfig(BaseModel):
    order: list[str] = Field(default=["Elsevier", "Springer"])
    enabled: dict[str, bool] = Field(default_factory=lambda: {
        "Elsevier": True,
        "Springer": True,
    })


class ExtractConfig(BaseModel):
    fetch_strategy: FetchStrategyConfig = Field(default_factory=FetchStrategyConfig)
    tdm: TDMConfig = Field(default_factory=TDMConfig)
    sci_hub: SciHubConfig = Field(default_factory=SciHubConfig)
    headless_browser: HeadlessBrowserConfig = Field(default_factory=HeadlessBrowserConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    fetch_connect_timeout: float = Field(
        default=15.0,
        ge=1.0,
        description="TCP connect timeout (seconds) for OA / TDM / Sci-Hub landing calls.",
    )
    fetch_read_timeout: float = Field(
        default=60.0,
        ge=1.0,
        description=(
            "Response read timeout (seconds) for PDF / landing-page "
            "downloads. Keep generous: large PDFs and publisher intermediate "
            "redirects can stream slowly."
        ),
    )


class IndexingConfig(BaseModel):
    provider: str = "harrier"
    model_id: str = "microsoft/harrier-oss-v1-270m"
    query_prompt_name: str = "web_search_query"
    query_instruction: str = (
        "Given a scientific literature search query, retrieve relevant abstracts and passages that answer the query"
    )
    max_length: int = 4096
    batch_size: int = Field(default=0, ge=0)
    device: str = "auto"
    cache_dir: str = ""
    chunk_min_chars: int = 300
    chunk_max_chars: int = 2000
    chunk_overlap_paragraphs: int = 1


class ZoteroConfig(BaseModel):
    library_id: str = ""
    library_type: str = "user"
    default_collection_key: str = ""


class ApiKeysConfig(BaseModel):
    ELSEVIER_API_KEY: str = ""
    PUBMED_API_KEY: str = ""
    WOS_API_KEY: str = ""
    SPRINGER_meta_API_KEY: str = ""
    SPRINGER_OA_API_KEY: str = ""
    LLAMAPARSE_API_KEY: str = ""
    ZOTERO_API_KEY: str = ""


class RetryPolicyConfig(BaseModel):
    """Retry knobs for external HTTP calls (ADR-008).

    Retries are triggered by transient failures: 429, 5xx, httpx.ConnectError,
    httpx.ReadTimeout, httpx.WriteError, httpx.PoolTimeout,
    httpx.RemoteProtocolError. Non-retryable errors propagate immediately.
    """

    max_attempts: int = Field(
        default=3,
        ge=1,
        description="Total attempts including the first call. 1 disables retries.",
    )
    max_wait: float = Field(
        default=8.0,
        ge=0.0,
        description="Upper bound (seconds) on a single backoff wait between attempts.",
    )
    respect_retry_after: bool = Field(
        default=True,
        description=(
            "When true, honor Retry-After / X-RateLimit-Reset response "
            "headers if the upstream requests a specific backoff."
        ),
    )


class GRaDOSConfig(BaseModel):
    """Root configuration model."""

    debug: bool = False
    search: SearchConfig = Field(default_factory=SearchConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    api_keys: ApiKeysConfig = Field(default_factory=ApiKeysConfig)
    retry_policy: RetryPolicyConfig = Field(default_factory=RetryPolicyConfig)
    academic_etiquette_email: str = "your-email@university.edu"


# ── Config loading ───────────────────────────────────────────────────────────


def _snake_to_camel_keys(data: Any) -> Any:
    """Recursively convert camelCase JSON keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            # Strip _comment fields
            if k.startswith("_comment"):
                continue
            # camelCase → snake_case
            snake = ""
            for i, ch in enumerate(k):
                if ch.isupper() and i > 0:
                    snake += "_"
                snake += ch.lower()
            out[snake] = _snake_to_camel_keys(v)
        return out
    if isinstance(data, list):
        return [_snake_to_camel_keys(item) for item in data]
    return data


def load_config(paths: GRaDOSPaths | None = None) -> GRaDOSConfig:
    """Load config.json, falling back to defaults."""
    paths = paths or GRaDOSPaths()
    config_file = paths.config_file
    if config_file.is_file():
        raw = json.loads(config_file.read_text(encoding="utf-8"))
        normalized = _snake_to_camel_keys(raw)
        return GRaDOSConfig.model_validate(normalized)
    return GRaDOSConfig()


def generate_default_config(paths: GRaDOSPaths) -> dict[str, Any]:
    """Generate a default config dict for writing to disk."""
    config = GRaDOSConfig()
    data = config.model_dump()
    # Add helpful comments
    data["_comment_debug"] = "Set to true to enable verbose benchmark logs."
    data["_comment_academic_etiquette_email"] = (
        "Email for academic APIs (Crossref, Unpaywall). Change to your real institutional email."
    )
    data["_comment_indexing"] = (
        "Semantic indexing defaults. Changing model_id or section-aware chunking settings requires `grados reindex`."
    )
    data["indexing"]["_comment_provider"] = "Default local embedding provider used for semantic indexing."
    data["indexing"]["_comment_model_id"] = (
        "Default embedding model. Harrier 270M is the stable local default; use 0.6B only when you have headroom."
    )
    data["indexing"]["_comment_query_prompt_name"] = (
        "Harrier query-side prompt name. Query/document encoding is intentionally asymmetric."
    )
    data["indexing"]["_comment_max_length"] = (
        "Recommended indexing length cap. Model max context is not the same as a safe local indexing length."
    )
    data["indexing"]["_comment_batch_size"] = (
        "Embedding encode batch size. Use 0 for conservative auto-sizing by device."
    )
    data["indexing"]["_comment_cache_dir"] = (
        "Optional model cache override. Leave empty to use GRaDOS_HOME/models/embedding."
    )

    # Timeout / retry surface (ADR-008)
    data["search"]["_comment_connect_timeout"] = (
        "TCP connect timeout in seconds for academic search APIs (Crossref, PubMed, WoS, Elsevier, Springer)."
    )
    data["search"]["_comment_read_timeout"] = (
        "Response read timeout in seconds for academic search APIs."
    )
    data["extract"]["_comment_fetch_connect_timeout"] = (
        "TCP connect timeout in seconds for OA / TDM / Sci-Hub HTTP calls."
    )
    data["extract"]["_comment_fetch_read_timeout"] = (
        "Response read timeout in seconds for PDF and landing-page downloads. "
        "Keep generous: large PDFs and intermediate redirects can stream slowly."
    )
    data["extract"]["headless_browser"]["_comment_deadline_seconds"] = (
        "Maximum wall-clock seconds for the browser main polling loop per DOI."
    )
    data["extract"]["headless_browser"]["_comment_networkidle_timeout"] = (
        "Ceiling in seconds for wait_for_load_state('networkidle'). See ADR-008."
    )
    data["extract"]["headless_browser"]["_comment_poll_min_seconds"] = (
        "Starting sleep between browser main-loop iterations."
    )
    data["extract"]["headless_browser"]["_comment_poll_max_seconds"] = (
        "Upper bound for the browser main-loop sleep after exponential backoff."
    )
    data["_comment_retry_policy"] = (
        "Unified retry knobs for external HTTP calls (ADR-008). Retries cover 429, 5xx, network errors."
    )
    data["retry_policy"]["_comment_max_attempts"] = (
        "Total attempts including the first call. 1 disables retries."
    )
    data["retry_policy"]["_comment_max_wait"] = (
        "Upper bound in seconds on a single backoff wait between attempts."
    )
    data["retry_policy"]["_comment_respect_retry_after"] = (
        "Honor Retry-After / X-RateLimit-Reset response headers when true."
    )
    return data
