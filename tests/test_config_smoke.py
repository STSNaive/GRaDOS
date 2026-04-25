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
