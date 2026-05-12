from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from grados.config import GRaDOSConfig, GRaDOSPaths, _snake_to_camel_keys, generate_default_config


def _nested_model_type(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    origin = get_origin(annotation)
    if origin is None:
        return None

    for arg in get_args(annotation):
        if arg is type(None):
            continue
        nested = _nested_model_type(arg)
        if nested is not None:
            return nested
    return None


def _unknown_model_keys(data: Any, model_type: type[BaseModel], prefix: str = "") -> list[str]:
    if not isinstance(data, dict):
        return []

    unknown: list[str] = []
    fields = model_type.model_fields
    for key, value in data.items():
        field = fields.get(key)
        if field is None:
            unknown.append(f"{prefix}{key}")
            continue

        nested = _nested_model_type(field.annotation)
        if nested is not None:
            unknown.extend(_unknown_model_keys(value, nested, f"{prefix}{key}."))
    return unknown


def test_example_config_matches_generated_runtime_defaults() -> None:
    example = json.loads(Path("grados-config.example.json").read_text(encoding="utf-8"))
    generated = generate_default_config(GRaDOSPaths())

    assert example == generated


def test_example_config_contains_no_unknown_runtime_keys() -> None:
    example = json.loads(Path("grados-config.example.json").read_text(encoding="utf-8"))
    normalized = _snake_to_camel_keys(example)

    assert _unknown_model_keys(normalized, GRaDOSConfig) == []
    GRaDOSConfig.model_validate(normalized)


def test_config_normalization_preserves_literal_enabled_map_keys() -> None:
    raw = {
        "search": {
            "enabled": {
                "Elsevier": False,
                "WebOfScience": True,
            }
        },
        "extract": {
            "fetchStrategy": {
                "enabled": {
                    "api": True,
                    "browser": False,
                }
            },
            "tdm": {
                "enabled": {
                    "Elsevier": False,
                    "Springer": True,
                }
            },
            "parsing": {
                "enabled": {
                    "Docling": True,
                    "MinerU": True,
                    "PyMuPDF": True,
                    "Marker": False,
                }
            },
        },
    }

    normalized = _snake_to_camel_keys(raw)
    config = GRaDOSConfig.model_validate(normalized)

    assert config.search.enabled["Elsevier"] is False
    assert config.search.enabled["WebOfScience"] is True
    assert "web_of_science" not in config.search.enabled
    assert config.extract.fetch_strategy.enabled["browser"] is False
    assert config.extract.tdm.enabled["Elsevier"] is False
    assert config.extract.parsing.enabled["Docling"] is True
    assert config.extract.parsing.enabled["MinerU"] is True
    assert config.extract.parsing.enabled["PyMuPDF"] is True
    assert "py_mu_p_d_f" not in config.extract.parsing.enabled


def test_default_config_exposes_disabled_codex_chrome_extension_strategy() -> None:
    config = GRaDOSConfig()

    assert config.extract.fetch_strategy.order == [
        "api",
        "browser",
        "codex",
        "scihub",
    ]
    assert config.extract.fetch_strategy.enabled["codex"] is False
    assert config.extract.unpaywall.enabled is True
    assert config.extract.codex_handoff.download_watch_dir == "~/Downloads"
    assert config.extract.codex_handoff.download_max_age_seconds == 900.0
    assert config.extract.codex_handoff.download_settle_seconds == 2.0
    assert config.extract.codex_handoff.download_settle_max_wait_seconds == 30.0
    assert config.extract.codex_handoff.download_scan_recursive is False
    assert config.extract.fetch_read_timeout == 60.0
    assert config.extract.pdf_read_timeout == 120.0
    assert config.extract.headless_browser.pdf_backfill_timeout == 120.0
    assert config.extract.security.max_remote_pdf_bytes == 200 * 1024 * 1024
    assert config.extract.security.max_remote_text_bytes == 50 * 1024 * 1024
    assert config.extract.security.max_local_pdf_bytes == 200 * 1024 * 1024
    assert config.extract.security.max_browser_capture_bytes == 200 * 1024 * 1024
    assert config.extract.security.max_mineru_zip_bytes == 256 * 1024 * 1024
    assert config.extract.security.max_mineru_full_md_bytes == 100 * 1024 * 1024
    assert config.extract.assets.mode == "all"
    assert config.extract.assets.docling_image_scale == 2.0
    assert config.extract.assets.max_asset_file_bytes == 32 * 1024 * 1024
    assert config.extract.assets.max_asset_total_bytes == 512 * 1024 * 1024
    assert config.extract.assets.max_asset_inline_bytes == 8 * 1024 * 1024
    assert config.extract.assets.max_asset_count == 3000


def test_scihub_legacy_fallback_mirror_populates_endpoints() -> None:
    config = GRaDOSConfig.model_validate(
        {"extract": {"sci_hub": {"fallback_mirror": "https://legacy.example"}}}
    )

    assert config.extract.sci_hub.endpoints == ["https://legacy.example"]


def test_scihub_configured_endpoints_take_priority_over_legacy_fallback() -> None:
    config = GRaDOSConfig.model_validate(
        {
            "extract": {
                "sci_hub": {
                    "endpoints": ["https://primary.example", "https://fallback.example"],
                    "fallback_mirror": "https://legacy.example",
                }
            }
        }
    )

    assert config.extract.sci_hub.endpoints == [
        "https://primary.example",
        "https://fallback.example",
    ]


def test_indepth_defaults_to_disabled() -> None:
    config = GRaDOSConfig()

    assert config.research.indepth.enabled is False
    assert config.research.indepth.auto_summarize is True
