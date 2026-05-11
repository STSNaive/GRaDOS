"""Paper-bound parser asset bundle storage and lookup helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.http_limits import (
    DEFAULT_MAX_ASSET_COUNT,
    DEFAULT_MAX_ASSET_FILE_BYTES,
    DEFAULT_MAX_ASSET_INLINE_BYTES,
    DEFAULT_MAX_ASSET_TOTAL_BYTES,
)

ASSET_SCHEMA_VERSION = 2

_ALLOWED_SUFFIXES = {
    ".bmp",
    ".csv",
    ".gif",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".png",
    ".svg",
    ".tex",
    ".tif",
    ".tiff",
    ".txt",
    ".webp",
}

_IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class AssetLimits:
    max_asset_file_bytes: int = DEFAULT_MAX_ASSET_FILE_BYTES
    max_asset_total_bytes: int = DEFAULT_MAX_ASSET_TOTAL_BYTES
    max_asset_inline_bytes: int = DEFAULT_MAX_ASSET_INLINE_BYTES
    max_asset_count: int = DEFAULT_MAX_ASSET_COUNT


@dataclass
class PendingAsset:
    kind: str
    role: str = "content"
    source_ref: str = ""
    filename: str = ""
    mime_type: str = ""
    data: bytes | None = None
    page: int | None = None
    bbox: list[float] = field(default_factory=list)
    caption: str = ""
    text: str = ""
    latex: str = ""
    html: str = ""
    csv: str = ""
    markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetBundleSaveResult:
    manifest_path: str = ""
    warnings: list[str] = field(default_factory=list)
    markdown_rewrites: dict[str, str] = field(default_factory=dict)
    asset_count: int = 0
    skipped_count: int = 0


def persist_asset_bundle(
    *,
    doi: str,
    safe_doi: str,
    papers_dir: Path,
    source: str = "",
    assets: list[PendingAsset] | None = None,
    mode: str = "all",
    limits: AssetLimits | None = None,
) -> AssetBundleSaveResult:
    """Persist parser-produced assets under papers/_assets/{safe_doi}/."""
    pending = _filter_assets(assets or [], mode=mode)
    if not pending:
        return AssetBundleSaveResult()

    limits = limits or AssetLimits()
    warnings: list[str] = []
    skipped: list[dict[str, Any]] = []
    rewrites: dict[str, str] = {}
    saved: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    total_bytes = 0

    assets_root = (papers_dir / "_assets").resolve()
    bundle_dir = (assets_root / safe_doi).resolve()
    try:
        bundle_dir.relative_to(assets_root)
    except ValueError:
        return AssetBundleSaveResult(warnings=[f"Unsafe asset bundle path for {safe_doi!r}"])

    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    for child in ("files", "tables", "pages", "debug", "source"):
        (bundle_dir / child).mkdir(parents=True, exist_ok=True)

    for asset in pending:
        if len(saved) >= limits.max_asset_count:
            skipped.append(_skip_record(asset, "asset_count_limit"))
            continue

        kind = normalize_asset_kind(asset.kind, source_ref=asset.source_ref, filename=asset.filename)
        role = normalize_asset_role(asset.role)
        counters[kind] = counters.get(kind, 0) + 1
        asset_id = f"{_asset_id_prefix(kind)}_{counters[kind]:03d}"

        entry: dict[str, Any] = {
            "asset_id": asset_id,
            "kind": kind,
            "role": role,
            "uri": f"grados://papers/{safe_doi}/assets/{asset_id}",
            "relative_path": "",
            "mime_type": asset.mime_type or "",
            "bytes": 0,
            "sha256": "",
            "page": asset.page,
            "bbox": [float(x) for x in asset.bbox],
            "caption": asset.caption.strip(),
            "text": asset.text.strip(),
            "latex": asset.latex.strip(),
            "html_path": "",
            "csv_path": "",
            "markdown_path": "",
            "source_ref": asset.source_ref,
            "metadata": asset.metadata,
        }

        primary = _primary_bytes(asset)
        primary_suffix = _asset_suffix(asset, kind=kind)
        primary_skip_recorded = False
        if primary is not None:
            allowed = _suffix_allowed(primary_suffix)
            if not allowed:
                warnings.append(f"Skipped {asset.source_ref or asset.filename or asset_id}: unsupported asset type")
                skipped.append(_skip_record(asset, "unsupported_asset_type", bytes=len(primary)))
                primary_skip_recorded = True
            elif len(primary) > limits.max_asset_file_bytes:
                skipped.append(_skip_record(asset, "asset_file_size_limit", bytes=len(primary)))
                primary_skip_recorded = True
            elif total_bytes + len(primary) > limits.max_asset_total_bytes:
                skipped.append(_skip_record(asset, "asset_total_size_limit", bytes=len(primary)))
                primary_skip_recorded = True
            else:
                rel_path = _primary_relative_path(kind, role, asset_id, primary_suffix)
                target = bundle_dir / rel_path
                target.write_bytes(primary)
                total_bytes += len(primary)
                entry["relative_path"] = rel_path.as_posix()
                entry["mime_type"] = asset.mime_type or guess_mime_type(target)
                entry["bytes"] = len(primary)
                entry["sha256"] = hashlib.sha256(primary).hexdigest()
                if asset.source_ref:
                    rewrites[asset.source_ref] = f"_assets/{safe_doi}/{rel_path.as_posix()}"

        for field_name, suffix, manifest_key in (
            ("html", ".html", "html_path"),
            ("csv", ".csv", "csv_path"),
            ("markdown", ".md", "markdown_path"),
        ):
            value = getattr(asset, field_name).strip()
            if not value:
                continue
            payload = value.encode("utf-8")
            if len(payload) > limits.max_asset_file_bytes:
                skipped.append(_skip_record(asset, f"{field_name}_size_limit", bytes=len(payload)))
                continue
            if total_bytes + len(payload) > limits.max_asset_total_bytes:
                skipped.append(_skip_record(asset, "asset_total_size_limit", bytes=len(payload)))
                continue
            rel_path = Path("tables") / f"{asset_id}{suffix}"
            (bundle_dir / rel_path).write_bytes(payload)
            total_bytes += len(payload)
            entry[manifest_key] = rel_path.as_posix()
            if not entry["relative_path"]:
                entry["relative_path"] = rel_path.as_posix()

        if not _asset_has_payload(entry):
            if not primary_skip_recorded:
                skipped.append(_skip_record(asset, "empty_asset"))
            continue

        saved.append(entry)

    if not saved and not skipped:
        return AssetBundleSaveResult(warnings=warnings)

    manifest = _build_manifest(
        doi=doi,
        safe_doi=safe_doi,
        source=source,
        assets=saved,
        skipped=skipped,
        total_bytes=total_bytes,
        warnings=warnings,
    )
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return AssetBundleSaveResult(
        manifest_path=str(manifest_path.relative_to(papers_dir)),
        warnings=warnings,
        markdown_rewrites=rewrites,
        asset_count=len(saved),
        skipped_count=len(skipped),
    )


def load_asset_manifest(papers_dir: Path, manifest_path: str) -> dict[str, Any] | None:
    if not manifest_path:
        return None
    path = Path(manifest_path)
    if not path.is_absolute():
        path = papers_dir / path
    try:
        path = path.resolve()
        path.relative_to(papers_dir.resolve())
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def manifest_assets(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        return []
    assets = manifest.get("assets")
    if isinstance(assets, list):
        return [asset for asset in assets if isinstance(asset, dict)]
    legacy: list[dict[str, Any]] = []
    for key, kind in (("figures", "figure"), ("tables", "table"), ("objects", "object")):
        values = manifest.get(key)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values, start=1):
            if not isinstance(value, dict):
                continue
            legacy.append({
                "asset_id": value.get("asset_id") or f"{kind}_{index:03d}",
                "kind": kind,
                "role": value.get("role") or "content",
                "uri": value.get("uri") or "",
                "caption": value.get("caption") or value.get("label") or "",
                "source_ref": value.get("url") or value.get("value") or "",
                "relative_path": value.get("relative_path") or "",
                "page": value.get("page"),
                "bbox": value.get("bbox") or [],
                "text": value.get("text") or "",
                "latex": value.get("latex") or "",
                "html_path": value.get("html_path") or "",
                "csv_path": value.get("csv_path") or "",
                "markdown_path": value.get("markdown_path") or "",
            })
    return legacy


def compact_asset_refs(
    manifest: dict[str, Any] | None,
    *,
    include_pages: bool = False,
    include_debug: bool = False,
    limit: int = 12,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for asset in manifest_assets(manifest):
        kind = str(asset.get("kind", ""))
        role = str(asset.get("role", ""))
        if kind == "page" and not include_pages:
            continue
        if (kind == "debug" or role in {"debug", "source"}) and not include_debug:
            continue
        refs.append(_compact_asset_ref(asset))
        if len(refs) >= limit:
            break
    return refs


def matching_asset_refs_for_text(
    manifest: dict[str, Any] | None,
    text: str,
    *,
    max_refs: int = 12,
) -> list[dict[str, Any]]:
    haystack = text.lower()
    refs: list[dict[str, Any]] = []
    for asset in manifest_assets(manifest):
        kind = str(asset.get("kind", ""))
        role = str(asset.get("role", ""))
        if kind == "page" or role in {"debug", "source"}:
            continue
        needles = [
            str(asset.get("uri", "")),
            str(asset.get("relative_path", "")),
            str(asset.get("source_ref", "")),
            Path(str(asset.get("source_ref", ""))).name,
            str(asset.get("caption", ""))[:120],
            str(asset.get("latex", ""))[:120],
        ]
        if any(needle and needle.lower() in haystack for needle in needles):
            refs.append(_compact_asset_ref(asset))
        if len(refs) >= max_refs:
            break
    return refs


def parse_asset_uri(asset_uri: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"grados://papers/([^/]+)/assets/([^/]+)", asset_uri.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def resolve_asset_path(papers_dir: Path, manifest_path: str, asset: dict[str, Any]) -> Path | None:
    rel = str(asset.get("relative_path") or "")
    if not rel:
        return None
    return resolve_manifest_relative_path(papers_dir, manifest_path, rel)


def resolve_manifest_relative_path(papers_dir: Path, manifest_path: str, relative_path: str) -> Path | None:
    rel = str(relative_path or "")
    if not rel:
        return None
    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = papers_dir / manifest_file
    papers_root = papers_dir.resolve()
    try:
        manifest_file = manifest_file.resolve()
        manifest_file.relative_to(papers_root)
    except ValueError:
        return None
    bundle_root = manifest_file.parent
    candidate = (bundle_root / rel).resolve()
    try:
        candidate.relative_to(bundle_root)
        candidate.relative_to(papers_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def guess_mime_type(path: Path) -> str:
    mimetypes.add_type("image/webp", ".webp")
    guessed, _ = mimetypes.guess_type(path, strict=False)
    return guessed or "application/octet-stream"


def normalize_asset_kind(kind: str, *, source_ref: str = "", filename: str = "") -> str:
    raw = str(kind or "").lower().strip()
    ref = f"{source_ref} {filename}".lower()
    if "equation" in raw or "formula" in raw or "math" in raw:
        return "formula"
    if "table" in raw:
        return "table"
    if "page" in raw or "/page" in ref or "page_" in ref:
        return "page"
    if "debug" in raw or "span" in ref or "layout" in ref:
        return "debug"
    if "source" in raw or "content_list" in ref or "middle" in ref or "model" in ref:
        return "object"
    if "chart" in raw:
        return "figure"
    if "image" in raw or "figure" in raw or Path(filename or source_ref).suffix.lower() in _IMAGE_SUFFIXES:
        return "figure"
    return "object"


def normalize_asset_role(role: str) -> str:
    raw = str(role or "").lower().strip()
    if raw in {"content", "page", "debug", "source", "supporting"}:
        return raw
    return "content"


def is_image_asset(asset: dict[str, Any]) -> bool:
    mime = str(asset.get("mime_type", ""))
    path = str(asset.get("relative_path", ""))
    return mime.startswith("image/") or Path(path).suffix.lower() in _IMAGE_SUFFIXES


def _filter_assets(assets: list[PendingAsset], *, mode: str) -> list[PendingAsset]:
    normalized = (mode or "all").strip().lower()
    if normalized == "none":
        return []
    if normalized == "referenced":
        return [
            asset for asset in assets
            if normalize_asset_role(asset.role) == "content"
            and normalize_asset_kind(asset.kind, source_ref=asset.source_ref, filename=asset.filename)
            not in {"page", "debug"}
        ]
    return list(assets)


def _primary_bytes(asset: PendingAsset) -> bytes | None:
    return asset.data


def _asset_suffix(asset: PendingAsset, *, kind: str) -> str:
    suffix = Path(asset.filename or asset.source_ref).suffix.lower()
    if suffix:
        return suffix
    if asset.mime_type:
        guessed = mimetypes.guess_extension(asset.mime_type)
        if guessed:
            return guessed
    if kind in {"figure", "page", "table"} and asset.data is not None:
        return ".png"
    return ".txt"


def _suffix_allowed(suffix: str) -> bool:
    return suffix.lower() in _ALLOWED_SUFFIXES


def _primary_relative_path(kind: str, role: str, asset_id: str, suffix: str) -> Path:
    if kind == "page" or role == "page":
        folder = "pages"
    elif role == "debug" or kind == "debug":
        folder = "debug"
    elif role == "source":
        folder = "source"
    elif kind == "table":
        folder = "tables"
    else:
        folder = "files"
    return Path(folder) / f"{asset_id}{suffix.lower()}"


def _asset_id_prefix(kind: str) -> str:
    return {
        "figure": "fig",
        "table": "table",
        "formula": "formula",
        "page": "page",
        "debug": "debug",
        "object": "obj",
    }.get(kind, "asset")


def _asset_has_payload(entry: dict[str, Any]) -> bool:
    return any(
        str(entry.get(key, "")).strip()
        for key in ("relative_path", "text", "latex", "html_path", "csv_path", "markdown_path", "caption")
    )


def _skip_record(asset: PendingAsset, reason: str, *, bytes: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "reason": reason,
        "kind": asset.kind,
        "role": asset.role,
        "source_ref": asset.source_ref,
        "filename": asset.filename,
    }
    if bytes is not None:
        record["bytes"] = bytes
    return record


def _build_manifest(
    *,
    doi: str,
    safe_doi: str,
    source: str,
    assets: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    total_bytes: int,
    warnings: list[str],
) -> dict[str, Any]:
    figures = [asset for asset in assets if asset.get("kind") == "figure"]
    tables = [asset for asset in assets if asset.get("kind") == "table"]
    formulas = [asset for asset in assets if asset.get("kind") == "formula"]
    pages = [asset for asset in assets if asset.get("kind") == "page"]
    debug = [asset for asset in assets if asset.get("kind") == "debug" or asset.get("role") in {"debug", "source"}]
    objects = [
        asset
        for asset in assets
        if asset not in figures
        and asset not in tables
        and asset not in formulas
        and asset not in pages
        and asset not in debug
    ]
    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "doi": doi,
        "safe_doi": safe_doi,
        "source": source,
        "generated_at": datetime.now(UTC).isoformat(),
        "asset_count": len(assets),
        "total_bytes": total_bytes,
        "assets": assets,
        "figures": figures,
        "tables": tables,
        "formulas": formulas,
        "pages": pages,
        "debug": debug,
        "objects": objects,
        "skipped_assets": skipped,
        "warnings": warnings,
    }


def _compact_asset_ref(asset: dict[str, Any]) -> dict[str, Any]:
    caption = str(asset.get("caption") or asset.get("text") or asset.get("latex") or "")
    return {
        "asset_id": asset.get("asset_id", ""),
        "kind": asset.get("kind", ""),
        "role": asset.get("role", ""),
        "uri": asset.get("uri", ""),
        "page": asset.get("page"),
        "caption": caption[:240],
        "relative_path": asset.get("relative_path", ""),
    }
