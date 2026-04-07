# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with historical sections reconstructed from the repository's tagged releases and commit history.

## [Unreleased]

_No unreleased changes._

## [0.6.6] - 2026-04-03

**GRaDOS 完成了从 TypeScript/Node.js 到 Python 的完整重写。** 自本版本起，GRaDOS 是一个纯 Python MCP 服务器，以标准 PyPI 包形式分发，不再需要 Node.js 运行时。0.6.5 中的全部 TS 能力已在 Python 实现中延续。

### Added — Runtime & Packaging

- Rewrote the entire codebase (~6K LoC TypeScript → ~3.5K LoC Python) as a `hatchling`-built Python package (`src/grados/`).
- Added `uv tool install "grados[all]"` as the primary installation path; `uvx "grados[all]"` for zero-install MCP client configuration.
- Added 7 optional dependency groups: `semantic`, `zotero`, `ocr`, `marker`, `docling`, `all`, `full`.
- Historical note (2026-04-05): the packaging surface was later simplified after a runtime audit. Current public install paths are `uv tool install grados`, `uvx grados`, and the real parser extras `grados[marker]`, `grados[docling]`, `grados[full]`.
- Added `py.typed` (PEP 561) marker for downstream type-checker support.
- Added `[tool.hatch.build.targets.sdist]` exclude rules to keep source distributions clean.
- Added CI workflow for pre-publish verification and post-publish PyPI smoke tests.

### Added — CLI

- Added `grados setup [--all] [--with browser,models]`: interactive setup wizard with runtime asset downloads.
- Added `grados status`: health check displaying versions, dependencies, API keys, and runtime assets.
- Added `grados paths`: file path overview with file counts and mode detection.
- Added `grados update-db`: batch-index `papers/` into ChromaDB.
- Added `grados import-pdfs --from /path [--recursive] [--glob] [--copy-to-library]`: bulk local PDF library import.
- Added `grados migrate-config`: legacy TS installation migration (compatibility command).
- Added `grados version`: version display.

### Added — MCP Tools & Resources

- Added `get_saved_paper_structure` tool: deterministic structural navigation (title, section outline, preview, word count, assets summary) for low-token decision-making before deep reads.
- Added `import_local_pdf_library` tool: agent-facing entry point for batch PDF import with DOI inference, content-hash dedup, and progress summary.
- Added `grados://papers/index` resource: list all saved papers with canonical metadata.
- Added `grados://papers/{safe_doi}` resource template: low-token paper overview (not full text).

### Added — Canonical Storage (ChromaDB-first)

- Added canonical-first Chroma architecture with two collections:
  - `papers_docs`: one document-level record per paper (full normalized Markdown + structured metadata).
  - `papers_chunks`: retrieval-optimized chunks with DOI and section metadata.
- Added canonical paper schema: `doi`, `safe_doi`, `title`, `authors`, `year`, `journal`, `source`, `fetch_outcome`, `content_markdown`, `section_headings`, `assets_manifest_path`, `content_hash`, `indexed_at`.
- Added `search_saved_papers` metadata prefilter: `doi`, `authors`, `year_from`, `year_to`, `journal`, `source`.
- Added hybrid retrieval: dense embedding search + `where_document` lexical constraints + paper-level aggregation + lightweight heuristic reranking.
- Added in-process ChromaDB with ONNX all-MiniLM-L6-v2 default embedding (no PyTorch required).

### Added — Asset Management

- Added manifest-first asset model: `save_asset_manifest` persists figure/table/object metadata to `papers/_assets/{safe_doi}.json`.
- Added Elsevier and Springer asset hint passthrough from publisher APIs to the extraction save pipeline.
- Added asset summary integration in `get_saved_paper_structure` and paper resources.

### Added — Configuration

- Added `~/GRaDOS/` as the default non-hidden data root (cross-platform; customizable via `GRADOS_HOME`).
- Added Pydantic v2 configuration model hierarchy: `GRaDOSConfig`, `SearchConfig`, `ExtractConfig`, `FetchStrategyConfig`, `TDMConfig`, `SciHubConfig`, `HeadlessBrowserConfig`, `ParsingConfig`, `QAConfig`, `ZoteroConfig`, `ApiKeysConfig`.
- Added `extract.tdm.order` / `extract.tdm.enabled` for per-publisher TDM configuration.
- Added automatic camelCase-to-snake_case JSON key conversion for backward compatibility with existing `config.json` files.

### Added — Tests

- Added 9 test files (30 test functions) covering CLI, server tools, resources, storage, search, browser, parsing, PDF import, and migration.
- Added `[tool.pytest.ini_options]` filterwarnings for upstream ChromaDB/ONNX deprecation warnings.

### Changed

- Replaced Node.js + TypeScript runtime with pure Python (≥ 3.11).
- Replaced `puppeteer-core` (already migrated to Patchright in 0.6.1) with Python Patchright for browser automation.
- Replaced `mcp-local-rag` external MCP server dependency with in-process ChromaDB semantic search.
- Changed paper storage from Markdown-file-as-truth to ChromaDB-canonical with optional Markdown mirror.
- Changed `search_saved_papers` from title/DOI-only lexical fallback to metadata-filtered dense retrieval with paper-level aggregation.
- Changed `extract_paper_full_text` return contract to compact receipt (not full text), leaving deep reading to `read_saved_paper`.
- Changed `grados://papers/{safe_doi}` from full-text resource to low-token overview, separating navigation from deep reading.
- Changed fetch waterfall TDM stage from hardcoded publisher order to config-driven `extract.tdm.order` / `extract.tdm.enabled`.
- Updated both READMEs to reflect Python installation, CLI, tool contracts, and citation-aware writing workflow.
- Updated `skills/grados/SKILL.md` and `skills/grados/references/tools.md` for the citation-aware `search → structure → deep read → cite → verify` protocol.
- Updated `.mcp.json` to use `uvx` as the MCP server command.

### Fixed

- Fixed 42 mypy strict-mode type errors across 11 source files (return types, BS4 attribute casts, generic parameters, ChromaDB `Any` returns).
- Fixed duplicate `dev` dependency declaration (removed from `[project.optional-dependencies]`, kept in `[dependency-groups]`).
- Fixed non-standard `__dataclass_fields__` access in `resumable.py` with `dataclasses.fields()`.
- Fixed Playwright `ViewportSize` type mismatch with targeted `type: ignore[arg-type]`.

### Removed

- Removed the entire TypeScript codebase (`src/index.ts`, `src/resumable-search.ts`, `tsconfig.json`, `package.json`, `package-lock.json`).
- Removed all Node.js test scripts (`tests/*.mjs`).
- Removed the Claude Code plugin distribution path (`.claude-plugin/`, `commands/`); retained MCP + skill structure.
- Removed `SemanticScholar` and `OpenAlex` from default search source configuration (not present in TS original).

### Docs

- Added `grados-python-implementation-plan.md` as the authoritative engineering plan and completion ledger.
- Consolidated documentation roles: `grados-python-migration-plan.md`, `status.md`, `docs/claude-code-plugin-guide.md`, `docs/global-install-guide.md` retained as historical references.
- Updated both READMEs with Python installation, `uv`/`uvx` commands, tool contract descriptions, and citation-aware writing workflow.
- Updated skill protocol to `search → structure → deep read → cite → verify`.

## [0.6.5] - 2026-04-01

Final TypeScript-era feature release. These capabilities were subsequently carried forward into the Python rewrite (0.6.6).

### Added
- Added a structured publisher-fetch outcome model covering cases such as `native_full_text`, `metadata_only`, `publisher_challenge`, `publisher_pdf_obtained`, and `publisher_html_instead_of_pdf`.
- Added centralized ScienceDirect candidate extraction, intermediate redirect parsing, PDF validation, Elsevier metadata extraction, and benchmark-log helpers.
- Added debug-gated fetch benchmarking and diagnostics output, including optional benchmark summaries in failure paths.
- Added `status.md` as the project-wide engineering status document.
- Added a managed-browser bootstrap flow:
  - `grados --init` best-effort prepares a dedicated Playwright-managed Chrome for Testing cache
  - `grados --prepare-browser` can re-run browser bootstrap later without regenerating the config
  - a dedicated persistent GRaDOS browser profile under the managed data root
- Added a managed browser data layout designed to stay stable across future packaging work.

### Changed
- Refactored Elsevier retrieval so no-view metadata responses are treated as first-class `metadata_only` results instead of hard failures.
- Refactored browser automation toward a reusable visible-window model with a retained control page and automatic closing of spawned PDF tabs after successful capture.
- Simplified browser behavior to use a visible Chromium window only for publisher automation, removing the earlier hidden-first/escalate-later split.
- Hardened the ScienceDirect browser state machine so that:
  - `View PDF` is only clicked on ScienceDirect article landing pages
  - PDF-flow pages such as `/pdfft`, `craft/capi/cfts/init`, and `pdf.sciencedirectassets.com` are observed rather than recursively re-opened
  - actual PDF capture happens only after the flow reaches a real PDF URL/content state
- Updated `grados-config.example.json` with debug controls and browser-session reuse options.
- Updated the browser configuration model so GRaDOS prefers its own managed Chrome/profile first, then falls back to configured or system Chromium browsers.
- Updated setup documentation in both READMEs so browser bootstrap is part of the normal installation flow.
- Updated browser-install defaults to favor a single GRaDOS-managed data root.

### Fixed
- Fixed duplicate ScienceDirect PDF-tab openings caused by racing the explicit `View PDF` click path against a second candidate-link fallback.
- Fixed earlier Elsevier fallback behavior so metadata signals such as `openaccess`, `pii`, `eid`, and `scidir` are retained when full text is unavailable.
- Preserved compatibility of the AIP browser flow after introducing ScienceDirect-specific browser hardening.
- Fixed managed-browser resolution so `preferManagedBrowser=true` now genuinely prefers the GRaDOS-managed Chrome runtime before any configured executable path.

### Removed
- Removed the experimental Privacy Pass integration from browser bootstrap, runtime launch, configuration, and documentation after it proved too inconsistent to justify keeping it in the main product flow.

### Docs
- Documented the latest managed-browser findings, including the dedicated GRaDOS browser/profile direction and the removal of the experimental Privacy Pass route.
- Recorded the Python packaging direction inspired by `zotero-mcp`: Python package distribution, optional extras, and setup/bootstrap commands for heavyweight managed assets.
- Clarified the intended managed runtime layout so browser binaries and profiles can move into stable GRaDOS-controlled data directories.
- Consolidated Python-migration documentation roles:
  - `grados-python-implementation-plan.md` is now the authoritative engineering plan / completion ledger
  - `TODO.md` is the concise execution snapshot
  - `grados-python-migration-plan.md`, `status.md`, `docs/claude-code-plugin-guide.md`, and `docs/global-install-guide.md` are retained as historical references

## [0.6.4] - 2026-03-30

### Fixed
- Supported project-scoped Marker installs so GRaDOS can discover and use Marker workers inside the active project layout.

## [0.6.3] - 2026-03-30

### Fixed
- Included resumable-search support files in the npm package.

## [0.6.2] - 2026-03-30

### Added
- Added saved-paper search support.
- Aligned local-rag defaults with the saved-paper search workflow.

## [0.6.1] - 2026-03-30

### Changed
- Replaced `puppeteer-core` with `patchright` for stronger CDP-level anti-detection behavior.

## [0.6.0] - 2026-03-28

### Added
- Improved academic source extraction across supported academic providers.

## [0.5.1] - 2026-03-26

### Changed
- Renamed `mcp-config` to `grados-config` across the codebase and project workflow.

## [0.5.0] - 2026-03-25

### Added
- Added resumable search continuation support.
- Added cross-platform installer and runtime discovery for Marker.

### Docs
- Added update-check instructions for npm packages.

## [0.4.1] - 2026-03-24

### Fixed
- Aligned plugin documentation and added a unified version CLI workflow.

## [0.4.0] - 2026-03-24

### Added
- Added install-agnostic paper APIs.
- Optimized context usage with tiered paper-extraction responses.

### Docs
- Updated `extract_paper_full_text` descriptions in both READMEs.

## [0.3.1] - 2026-03-22

### Added
- Added Claude Code plugin support.
- Improved plugin setup with auto-config, bundled MCP servers, and setup flow.

### Changed
- Cleaned up the repository after merging the plugin branch.

### Fixed
- Aligned plugin files with Claude Code plugin documentation.

### Docs
- Reorganized README structure and expanded configuration examples.
- Refreshed ignore rules and supporting docs around plugin work.

### CI
- Added workflow support to sync `main` into the plugin branch.

## [0.3.0] - 2026-03-19

### Added
- Added `parse_pdf_file` tooling.
- Added Puppeteer stealth support.
- Added a Playwright MCP fallback workflow.
- Updated local-rag-related docs and resource metadata.

### Changed
- Removed accidentally committed system files and planning artifacts.

## [0.2.2] - 2026-03-19

### Added
- Added config-directory-aware path resolution.
- Added `--config` support.
- Added MCP resource support.

### CI
- Upgraded npm publishing workflow for trusted publishing.

## [0.2.1] - 2026-03-18

### CI
- Added npm publish workflow support.
- Added support for manual npm publish runs on existing tags.
- Fixed publish workflow tag-version checks.

### Changed
- Cleaned package contents by excluding test scripts and stopping tracked manifest metadata drift.
- Synced release metadata for `0.2.1`.

### Docs
- Refreshed README content and formatting, including the Chinese README.
- Reworked ASCII-art presentation and README styling.

## [0.2.0] - 2026-03-18

### Added
- Initial tagged `0.2.0` release of GRaDOS.

## [Pre-0.2.0]

### Fixed
- Resolved a batch of MCP server and Marker worker bugs before the first tagged release.
