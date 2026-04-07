"""Legacy TypeScript-to-Python migration helpers."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grados.config import GRaDOSConfig, GRaDOSPaths, generate_default_config


@dataclass
class MigrationAction:
    """A single filesystem action taken during migration."""

    label: str
    source: Path
    destination: Path
    status: str
    detail: str = ""


@dataclass
class MigrationResult:
    """Summary of a migration run."""

    source_config: Path
    target_root: Path
    target_config: Path
    actions: list[MigrationAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    wrote_config: bool = False


@dataclass
class LegacyLayout:
    """Important legacy paths derived from grados-config.json."""

    project_root: Path
    papers_dir: Path
    downloads_dir: Path
    browser_root: Path
    browser_cache_dir: Path
    browser_profile_dir: Path
    browser_extensions_dir: Path
    models_dir: Path
    lancedb_dir: Path


def find_legacy_config(explicit: Path | None = None, target_paths: GRaDOSPaths | None = None) -> Path | None:
    """Find the first legacy grados-config.json to migrate."""

    candidates: list[Path] = []

    def add_candidate(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.expanduser()
        if resolved.is_dir():
            resolved = resolved / "grados-config.json"
        if resolved not in candidates:
            candidates.append(resolved)

    add_candidate(explicit)

    if env := os.environ.get("GRADOS_CONFIG_PATH"):
        add_candidate(Path(env))
    if target_paths is not None:
        add_candidate(target_paths.root / "grados-config.json")

    add_candidate(Path.cwd() / "grados-config.json")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    return None


def migrate_legacy_install(
    source_config: Path,
    target_paths: GRaDOSPaths,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> MigrationResult:
    """Convert legacy config/data into the Python layout."""

    source_config = source_config.expanduser().resolve()
    raw = json.loads(source_config.read_text(encoding="utf-8"))

    migrated_config, layout, warnings = build_migrated_config(raw, source_config, target_paths)
    result = MigrationResult(
        source_config=source_config,
        target_root=target_paths.root,
        target_config=target_paths.config_file,
        warnings=warnings.copy(),
    )

    if target_paths.config_file.exists() and not force:
        raise FileExistsError(
            f"目标配置文件已存在: {target_paths.config_file}. "
            "如需覆盖，请重新运行并添加 --force。"
        )

    _copy_directory(
        "已保存论文",
        layout.papers_dir,
        target_paths.papers,
        result,
        dry_run=dry_run,
        force=force,
    )
    _copy_directory(
        "PDF 下载归档",
        layout.downloads_dir,
        target_paths.downloads,
        result,
        dry_run=dry_run,
        force=force,
    )
    _copy_directory(
        "浏览器二进制",
        layout.browser_cache_dir,
        target_paths.browser_chromium,
        result,
        dry_run=dry_run,
        force=force,
    )
    _copy_directory(
        "浏览器 profile",
        layout.browser_profile_dir,
        target_paths.browser_profile,
        result,
        dry_run=dry_run,
        force=force,
    )
    _copy_directory(
        "浏览器扩展",
        layout.browser_extensions_dir,
        target_paths.browser_extensions,
        result,
        dry_run=dry_run,
        force=force,
    )
    _copy_directory("模型缓存", layout.models_dir, target_paths.models_root, result, dry_run=dry_run, force=force)

    if layout.lancedb_dir.exists():
        result.warnings.append(
            f"检测到旧版 LanceDB 目录 {layout.lancedb_dir}，已按要求跳过。"
        )

    if not dry_run:
        target_paths.ensure_directories()
        target_paths.browser_root.mkdir(parents=True, exist_ok=True)
        target_paths.models_root.mkdir(parents=True, exist_ok=True)
        target_paths.database_chroma.parent.mkdir(parents=True, exist_ok=True)
        target_paths.config_file.write_text(
            json.dumps(migrated_config, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result.wrote_config = True

    return result


def build_migrated_config(
    raw: dict[str, Any],
    source_config: Path,
    target_paths: GRaDOSPaths,
) -> tuple[dict[str, Any], LegacyLayout, list[str]]:
    """Convert legacy config JSON into the Python config schema."""

    project_root = source_config.parent
    extract_cfg = _as_dict(raw.get("extract"))
    search_cfg = _as_dict(raw.get("search"))
    headless_cfg = _as_dict(extract_cfg.get("headlessBrowser"))
    parsing_cfg = _as_dict(extract_cfg.get("parsing"))
    local_rag_cfg = _as_dict(raw.get("localRag"))

    managed_root = _resolve_path(project_root, headless_cfg.get("managedDataDirectory"), ".grados/browser")
    browser_cache_dir = _resolve_path(
        project_root,
        headless_cfg.get("managedBrowserDirectory"),
        str(managed_root / "browsers" / "playwright"),
    )
    browser_profile_dir = _resolve_path(
        project_root,
        headless_cfg.get("profileDirectory"),
        str(managed_root / "profiles" / "chrome"),
    )

    layout = LegacyLayout(
        project_root=project_root,
        papers_dir=_resolve_path(project_root, extract_cfg.get("papersDirectory"), "markdown"),
        downloads_dir=_resolve_path(project_root, extract_cfg.get("downloadDirectory"), "downloads"),
        browser_root=managed_root,
        browser_cache_dir=browser_cache_dir,
        browser_profile_dir=browser_profile_dir,
        browser_extensions_dir=managed_root / "extensions",
        models_dir=_resolve_path(project_root, local_rag_cfg.get("cacheDir"), "models"),
        lancedb_dir=_resolve_path(project_root, local_rag_cfg.get("dbPath"), "lancedb"),
    )

    data = generate_default_config(target_paths)
    warnings: list[str] = []

    data["debug"] = bool(raw.get("debug", data["debug"]))
    data["academic_etiquette_email"] = str(raw.get("academicEtiquetteEmail", data["academic_etiquette_email"]))

    data["search"]["order"] = _list_or_default(search_cfg.get("order"), data["search"]["order"])
    data["search"]["enabled"] = _bool_map(search_cfg.get("enabled"), data["search"]["enabled"])

    fetch_strategy = _as_dict(extract_cfg.get("fetchStrategy"))
    tdm_cfg = _as_dict(extract_cfg.get("tdm"))
    data["extract"]["fetch_strategy"]["order"] = _list_or_default(
        fetch_strategy.get("order"),
        data["extract"]["fetch_strategy"]["order"],
    )
    data["extract"]["fetch_strategy"]["enabled"] = _bool_map(
        fetch_strategy.get("enabled"),
        data["extract"]["fetch_strategy"]["enabled"],
    )
    data["extract"]["tdm"]["order"] = _list_or_default(tdm_cfg.get("order"), data["extract"]["tdm"]["order"])
    data["extract"]["tdm"]["enabled"] = _bool_map(tdm_cfg.get("enabled"), data["extract"]["tdm"]["enabled"])

    scihub_cfg = _as_dict(extract_cfg.get("sciHub"))
    data["extract"]["sci_hub"]["auto_update_mirror"] = bool(
        scihub_cfg.get("autoUpdateMirror", data["extract"]["sci_hub"]["auto_update_mirror"])
    )
    data["extract"]["sci_hub"]["fallback_mirror"] = str(
        scihub_cfg.get("fallbackMirror", data["extract"]["sci_hub"]["fallback_mirror"])
    )
    data["extract"]["sci_hub"]["mirror_url_file"] = str(target_paths.cache / "scihub-mirrors.txt")

    data["extract"]["headless_browser"]["browser"] = str(
        headless_cfg.get("browser", data["extract"]["headless_browser"]["browser"])
    )
    data["extract"]["headless_browser"]["prefer_managed_browser"] = bool(
        headless_cfg.get(
            "preferManagedBrowser",
            data["extract"]["headless_browser"]["prefer_managed_browser"],
        )
    )
    data["extract"]["headless_browser"]["auto_install_managed_browser"] = bool(
        headless_cfg.get(
            "autoInstallManagedBrowser",
            data["extract"]["headless_browser"]["auto_install_managed_browser"],
        )
    )
    data["extract"]["headless_browser"]["use_persistent_profile"] = bool(
        headless_cfg.get(
            "usePersistentProfile",
            data["extract"]["headless_browser"]["use_persistent_profile"],
        )
    )
    data["extract"]["headless_browser"]["executable_path"] = str(
        headless_cfg.get("executablePath", data["extract"]["headless_browser"]["executable_path"])
    )
    data["extract"]["headless_browser"]["reuse_interactive_window"] = bool(
        headless_cfg.get(
            "reuseInteractiveWindow",
            data["extract"]["headless_browser"]["reuse_interactive_window"],
        )
    )
    data["extract"]["headless_browser"]["keep_interactive_window_open"] = bool(
        headless_cfg.get(
            "keepInteractiveWindowOpen",
            data["extract"]["headless_browser"]["keep_interactive_window_open"],
        )
    )
    data["extract"]["headless_browser"]["close_pdf_page_after_capture"] = bool(
        headless_cfg.get(
            "closePdfPageAfterCapture",
            data["extract"]["headless_browser"]["close_pdf_page_after_capture"],
        )
    )

    raw_parsing_order = _list_or_default(parsing_cfg.get("order"), [])
    migrated_parsing_order = _migrate_parser_order(raw_parsing_order)
    if migrated_parsing_order:
        data["extract"]["parsing"]["order"] = migrated_parsing_order

    raw_parsing_enabled = _as_dict(parsing_cfg.get("enabled"))
    data["extract"]["parsing"]["enabled"] = {
        "PyMuPDF": bool(raw_parsing_enabled.get("Native", True)),
        "Marker": bool(raw_parsing_enabled.get("Marker", False)),
        "Docling": bool(raw_parsing_enabled.get("Docling", False)),
    }
    data["extract"]["parsing"]["marker_timeout"] = int(
        parsing_cfg.get("markerTimeout", data["extract"]["parsing"]["marker_timeout"])
    )

    qa_cfg = _as_dict(extract_cfg.get("qa"))
    data["extract"]["qa"]["min_characters"] = int(
        qa_cfg.get("minCharacters", data["extract"]["qa"]["min_characters"])
    )

    zotero_cfg = _as_dict(raw.get("zotero"))
    data["zotero"]["library_id"] = str(zotero_cfg.get("libraryId", data["zotero"]["library_id"]))
    data["zotero"]["library_type"] = str(zotero_cfg.get("libraryType", data["zotero"]["library_type"]))
    data["zotero"]["default_collection_key"] = str(
        zotero_cfg.get("defaultCollectionKey", data["zotero"]["default_collection_key"])
    )

    api_keys_cfg = _as_dict(raw.get("apiKeys"))
    for key in data["api_keys"]:
        if key in api_keys_cfg:
            data["api_keys"][key] = str(api_keys_cfg[key])

    if "LlamaParse" in raw_parsing_order:
        warnings.append("旧版解析顺序包含 LlamaParse；Python 版已改为 PyMuPDF/Marker/Docling。")
    if raw.get("LLAMAPARSE_API_KEY") or api_keys_cfg.get("LLAMAPARSE_API_KEY"):
        warnings.append("检测到 LlamaParse API key；Python 版当前不会使用它进行 PDF 解析。")
    if local_rag_cfg:
        warnings.append("旧版 localRag 配置已废弃；GRaDOS 现在内置 ChromaDB 语义搜索。")
    if layout.papers_dir != target_paths.papers or layout.downloads_dir != target_paths.downloads:
        warnings.append(
            f"已保存论文和 PDF 将统一迁移到 {target_paths.root} 下的 papers/ 与 downloads/ 目录。"
        )
    if layout.browser_root.exists():
        warnings.append(
            f"浏览器运行时将统一收拢到 {target_paths.browser_root}。"
        )

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    data["_comment_migrated_from"] = f"Migrated from {source_config} on {timestamp}."
    data["_comment_semantic_search"] = "GRaDOS now uses ChromaDB only."

    _validate_migrated_config(data)
    return data, layout, warnings


def _copy_directory(
    label: str,
    source: Path,
    destination: Path,
    result: MigrationResult,
    *,
    dry_run: bool,
    force: bool,
) -> None:
    if not source.exists():
        result.actions.append(MigrationAction(label, source, destination, "missing"))
        return

    if source.resolve() == destination.resolve():
        result.actions.append(MigrationAction(label, source, destination, "same"))
        return

    destination_has_content = destination.exists() and any(destination.iterdir())
    if destination_has_content and not force:
        result.actions.append(
            MigrationAction(label, source, destination, "skipped", "destination already has content"),
        )
        return

    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)

    result.actions.append(MigrationAction(label, source, destination, "planned" if dry_run else "copied"))


def _resolve_path(base_dir: Path, raw_path: Any, default_rel: str) -> Path:
    candidate = str(raw_path).strip() if raw_path not in (None, "") else default_rel
    path = Path(candidate).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_default(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return list(default)


def _bool_map(value: Any, default: dict[str, bool]) -> dict[str, bool]:
    if not isinstance(value, dict):
        return dict(default)
    return {key: bool(value.get(key, default_value)) for key, default_value in default.items()}


def _migrate_parser_order(order: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in order:
        normalized = item.strip()
        replacement = {
            "Native": "PyMuPDF",
            "PyMuPDF": "PyMuPDF",
            "Marker": "Marker",
            "Docling": "Docling",
        }.get(normalized)
        if replacement and replacement not in mapped:
            mapped.append(replacement)

    if "PyMuPDF" not in mapped:
        mapped.insert(0, "PyMuPDF")

    return mapped


def _validate_migrated_config(data: dict[str, Any]) -> None:
    cleaned = {key: value for key, value in data.items() if not key.startswith("_comment")}
    GRaDOSConfig.model_validate(cleaned)
