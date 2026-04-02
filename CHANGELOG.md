# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with historical sections reconstructed from the repository's tagged releases and commit history.

## [Unreleased]

### Added
- Added a structured publisher-fetch outcome model covering cases such as `native_full_text`, `metadata_only`, `publisher_challenge`, `publisher_pdf_obtained`, and `publisher_html_instead_of_pdf`.
- Added `/Users/macfish/Projects/GRaDOS/src/publisher-utils.ts` to centralize ScienceDirect candidate extraction, intermediate redirect parsing, PDF validation, Elsevier metadata extraction, and benchmark-log helpers.
- Added debug-gated fetch benchmarking and diagnostics output, including optional benchmark summaries in failure paths.
- Added ScienceDirect-focused validation scripts:
  - `/Users/macfish/Projects/GRaDOS/tests/sciencedirect-utils.mjs`
  - `/Users/macfish/Projects/GRaDOS/tests/sciencedirect-benchmark.mjs`
- Added `/Users/macfish/Projects/GRaDOS/status.md` as the project-wide engineering status document.
- Added a managed-browser bootstrap flow:
  - `grados --init` now best-effort prepares a dedicated Playwright-managed Chrome for Testing cache for GRaDOS
  - `grados --prepare-browser` can re-run browser bootstrap later without regenerating the config
  - a dedicated persistent GRaDOS browser profile can now live under project-local `.grados/browser/profiles/chrome`
- Added a managed browser data layout designed to stay stable across future packaging work:
  - `.grados/browser/browsers/playwright`
  - `.grados/browser/profiles/chrome`

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
- Updated package contents and scripts so `dist/publisher-utils.js` is published and `test:sciencedirect-utils` is available.
- Updated setup documentation in both READMEs and Claude setup instructions so browser bootstrap is part of the normal installation flow.
- Updated browser-install defaults to favor a single GRaDOS-managed data root that can later move cleanly into OS application-data locations during Python packaging.

### Fixed
- Fixed duplicate ScienceDirect PDF-tab openings caused by racing the explicit `View PDF` click path against a second candidate-link fallback.
- Fixed earlier Elsevier fallback behavior so metadata signals such as `openaccess`, `pii`, `eid`, and `scidir` are retained when full text is unavailable.
- Preserved compatibility of the AIP browser flow after introducing ScienceDirect-specific browser hardening.
- Fixed managed-browser resolution so `preferManagedBrowser=true` now genuinely prefers the GRaDOS-managed Chrome runtime before any configured executable path.

### Removed
- Removed the experimental Privacy Pass integration from browser bootstrap, runtime launch, configuration, and documentation after it proved too inconsistent to justify keeping it in the main product flow.

### Docs
- Documented the latest managed-browser findings in `/Users/macfish/Projects/GRaDOS/status.md`, including:
  - the dedicated GRaDOS browser/profile direction
  - the removal of the experimental Privacy Pass route
- Recorded the current packaging direction inspired by `zotero-mcp`:
  - Python package distribution
  - optional component/extras selection
  - setup/bootstrap commands for heavyweight managed assets such as browsers and profiles
- Clarified the intended managed runtime layout so browser binaries and profiles can move into stable GRaDOS-controlled data directories instead of temporary locations.
- Consolidated Python-migration documentation roles:
  - `grados-python-implementation-plan.md` is now the authoritative engineering plan / completion ledger
  - `TODO.md` is the concise execution snapshot
  - `grados-python-migration-plan.md`, `status.md`, `docs/claude-code-plugin-guide.md`, and `docs/global-install-guide.md` are retained as historical references with explicit status notes

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
