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
        return self.root / "database" / "chroma"

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


class ParsingConfig(BaseModel):
    order: list[str] = Field(default=["PyMuPDF", "Marker", "Docling"])
    enabled: dict[str, bool] = Field(default_factory=lambda: {
        "PyMuPDF": True,
        "Marker": False,
        "Docling": False,
    })
    marker_timeout: int = 120000


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


class ZoteroConfig(BaseModel):
    library_id: str = ""
    library_type: str = "user"
    default_collection_key: str = ""


class ApiKeysConfig(BaseModel):
    ELSEVIER_API_KEY: str = ""
    WOS_API_KEY: str = ""
    SPRINGER_meta_API_KEY: str = ""
    SPRINGER_OA_API_KEY: str = ""
    LLAMAPARSE_API_KEY: str = ""
    ZOTERO_API_KEY: str = ""


class GRaDOSConfig(BaseModel):
    """Root configuration model."""

    debug: bool = False
    search: SearchConfig = Field(default_factory=SearchConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    api_keys: ApiKeysConfig = Field(default_factory=ApiKeysConfig)
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
    return data
