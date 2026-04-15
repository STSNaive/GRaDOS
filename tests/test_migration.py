from __future__ import annotations

import json
from pathlib import Path

from grados.config import GRaDOSPaths
from grados.setup.migration import find_legacy_config, migrate_legacy_install


def test_find_legacy_config_accepts_directory(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    config_path = legacy_root / "grados-config.json"
    config_path.write_text("{}", encoding="utf-8")

    found = find_legacy_config(legacy_root)

    assert found == config_path.resolve()


def test_migrate_legacy_install_copies_assets_and_skips_lancedb(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()

    papers_dir = legacy_root / "markdown"
    downloads_dir = legacy_root / "downloads"
    browser_cache_dir = legacy_root / ".grados" / "browser" / "browsers" / "playwright"
    browser_profile_dir = legacy_root / ".grados" / "browser" / "profiles" / "chrome"
    models_dir = legacy_root / "models"
    lancedb_dir = legacy_root / "lancedb"

    papers_dir.mkdir(parents=True)
    downloads_dir.mkdir(parents=True)
    browser_cache_dir.mkdir(parents=True)
    browser_profile_dir.mkdir(parents=True)
    models_dir.mkdir(parents=True)
    lancedb_dir.mkdir(parents=True)

    (papers_dir / "10_1234_demo.md").write_text("# Demo Paper\n\ncontent", encoding="utf-8")
    (downloads_dir / "10_1234_demo.pdf").write_bytes(b"%PDF-1.4")
    (browser_cache_dir / "chromium.txt").write_text("browser-cache", encoding="utf-8")
    (browser_profile_dir / "Preferences").write_text("profile", encoding="utf-8")
    (models_dir / "cache.bin").write_text("model-cache", encoding="utf-8")
    (lancedb_dir / "data.mdb").write_text("legacy-index", encoding="utf-8")

    legacy_config = {
        "debug": True,
        "search": {
            "order": ["Crossref", "PubMed"],
            "enabled": {"Crossref": True, "PubMed": True},
        },
        "extract": {
            "papersDirectory": "./markdown",
            "downloadDirectory": "./downloads",
            "fetchStrategy": {
                "order": ["OA", "SciHub", "Headless"],
                "enabled": {"TDM": False, "OA": True, "SciHub": True, "Headless": True},
            },
            "sciHub": {
                "autoUpdateMirror": False,
                "fallbackMirror": "https://sci-hub.se",
            },
            "headlessBrowser": {
                "managedDataDirectory": "./.grados/browser",
                "preferManagedBrowser": True,
                "autoInstallManagedBrowser": True,
                "usePersistentProfile": True,
                "reuseInteractiveWindow": True,
                "keepInteractiveWindowOpen": True,
                "closePdfPageAfterCapture": True,
            },
            "parsing": {
                "order": ["LlamaParse", "Marker", "Native"],
                "enabled": {"LlamaParse": True, "Marker": True, "Native": True},
                "markerTimeout": 150000,
            },
            "qa": {"minCharacters": 1800},
        },
        "localRag": {
            "enabled": True,
            "dbPath": "./lancedb",
            "cacheDir": "./models",
        },
        "zotero": {
            "libraryId": "12345",
            "libraryType": "user",
            "defaultCollectionKey": "ABCDE",
        },
        "apiKeys": {
            "ELSEVIER_API_KEY": "elsevier-key",
            "ZOTERO_API_KEY": "zotero-key",
            "LLAMAPARSE_API_KEY": "llamaparse-key",
        },
        "academicEtiquetteEmail": "research@example.edu",
    }
    config_path = legacy_root / "grados-config.json"
    config_path.write_text(json.dumps(legacy_config), encoding="utf-8")

    target_paths = GRaDOSPaths(tmp_path / "GRaDOS")
    result = migrate_legacy_install(config_path, target_paths)

    migrated_config = json.loads(target_paths.config_file.read_text(encoding="utf-8"))

    assert result.wrote_config is True
    assert migrated_config["debug"] is True
    assert migrated_config["academic_etiquette_email"] == "research@example.edu"
    assert migrated_config["extract"]["parsing"]["order"] == ["Docling", "Marker", "PyMuPDF"]
    assert migrated_config["extract"]["parsing"]["enabled"] == {
        "PyMuPDF": True,
        "Marker": True,
        "Docling": True,
    }
    assert "localRag" not in migrated_config
    assert migrated_config["api_keys"]["ELSEVIER_API_KEY"] == "elsevier-key"
    assert migrated_config["_comment_semantic_search"] == "GRaDOS now uses ChromaDB only."

    assert (target_paths.papers / "10_1234_demo.md").is_file()
    assert (target_paths.downloads / "10_1234_demo.pdf").is_file()
    assert (target_paths.browser_chromium / "chromium.txt").is_file()
    assert (target_paths.browser_profile / "Preferences").is_file()
    assert (target_paths.models_root / "cache.bin").is_file()
    assert not (target_paths.root / "database" / "lancedb").exists()
    assert any("LanceDB" in warning for warning in result.warnings)
