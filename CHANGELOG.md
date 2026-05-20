# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with historical sections reconstructed from the repository's tagged releases and commit history.

## [Unreleased]

### Security
- Redacted `manage_failure_cases` context before persistence and again on read, so failure-memory rows cannot store or return API keys, bearer tokens, session IDs, or auth headers.
- Removed the bundled Marker parser dependency from the published extras graph because the latest `marker-pdf` release still pins vulnerable `Pillow<11` and `transformers<5` ranges; `grados[marker]` and `grados[full]` are now compatibility aliases, and the locked graph upgrades to safe parser-adjacent versions.
- Updated the locked Python dependency graph for vulnerable networking, XML, auth, multipart, and parser-adjacent packages, including `authlib`, `cryptography`, `lxml`, `python-multipart`, `urllib3`, and Docling parser dependencies.

### Added
- Added `run_external_synthesis`, the default GRaDOS-native ChatGPT Pro browser route for enabled external synthesis. It prepares or verifies evidence packets, uses a private GRaDOS ChatGPT profile, confirms Oracle's current Pro model and Pro Extended thinking route before sending, captures the advisory response, saves it, and audits it before canonical reread.
- Added ChatGPT browser-mode runtime scaffolding for external synthesis, including private profile/session paths, first-time `grados external-synthesis setup-browser`, `grados external-synthesis doctor [--live]`, profile readiness checks, Oracle-aligned model/thinking selector helpers, response capture, and operational session records.
- Added Oracle-style publisher PDF browser runtime diagnostics: `grados browser status --json`, `grados browser doctor [--live --doi DOI]`, persistent profile readiness checks, profile locking, PDF acquisition session records under `browser/pdf-sessions`, and capture metadata for response/download/backfill paths.
- Added an evidence-grounded writing workflow reference for the bundled GRaDOS skill, including `paper_writing.md`, four writing profiles for experimental protocols, literature reviews, experiment reports, and manuscripts, plus a mechanics/elastic-metamaterials domain profile.
- Added `prepare_external_synthesis_from_topic`, a higher-level MCP route that prepares an evidence pack from a topic and persists a verified external-synthesis packet while keeping the lower-level pack/packet tools available for recovery and explicit control.
- Added `research_run_manifest` artifacts with `research_run_id`, run-level artifact indexes, append-only event ledgers, redacted config/provenance snapshots, and automatic linking for `save_research_artifact(..., metadata={"research_run_id": ...})`.
- Added deterministic external synthesis packet/result tools: `preview_external_synthesis_packet`, `prepare_external_synthesis_packet`, `save_external_synthesis_result`, and `audit_external_synthesis_result`, keeping ChatGPT Pro advisory output tied to current-valid evidence packs before canonical rereading.
- Added default-off `research.external_synthesis.enabled` config, `grados external-synthesis is-enabled --quiet`, `grados external-synthesis status --json`, and host-agent protocol docs for using Oracle's current ChatGPT Pro browser route without making GRaDOS share unverified evidence.
- Added a canonical paragraph-block registry plus persisted `evidence_pack` artifacts and MCP tools: `prepare_evidence_pack`, `read_evidence_pack`, `verify_evidence_pack`, `audit_answer_against_pack`, and `suggest_missing_evidence`.
- Added a rebuildable SQLite FTS5/BM25 index at `database/fts.sqlite3`, exact lookup candidates, hybrid RRF saved-paper retrieval, and `grados eval-retrieval` for local retrieval fixtures.
- Added `ingest_codex_downloaded_pdf` plus `extract.codex_handoff.*` watch-dir settings so Codex Chrome-extension downloads can be validated and flowed back into the canonical library without trusting ambiguous local files.
- Added parser provenance sidecars under `papers/_parsed/{safe_doi}.json` and `parsed_manifest_path` frontmatter pointers so saved papers can expose parser/source hashes and block mapping summaries without treating parser JSON as citation content.
- Added parser asset bundles under `papers/_assets/{safe_doi}/` plus `read_paper_asset`, so saved papers can expose parser-generated figures, tables, formulas, page images, and source/debug files without inlining large payloads into `read_saved_paper`.
- Added `extract.security` byte ceilings for remote PDF downloads, native text/XML/HTML article payloads, local PDF parsing/import, browser PDF captures, MinerU result zips, and MinerU `full.md` extraction.
- Added MinerU as the authenticated cloud PDF parser fallback in the parsing waterfall (`Docling -> MinerU -> PyMuPDF` by default), including signed-upload polling, zip `full.md` extraction, config knobs, keychain support via `MINERU_API_KEY`, and smoke-test coverage.
- Added disabled-by-default `codex` fetch-strategy support so Codex host agents can place the Codex Chrome extension download handoff anywhere in `extract.fetch_strategy.order`.
- Added agent-side evidence anchors to saved-paper search and Stage B research helpers so snippets, grids, comparisons, and audits can point agents back to canonical `read_saved_paper` paragraph windows before citation.
- Added `candidate_limit` to `audit_draft_support` so draft audits can return more candidate evidence items for host-agent reranking before final support judgment.
- Added opt-in `indepth` search mode with default-off config, a `grados search --indepth` CLI surface, per-run `research_checkpoint` folders, and reusable query-independent `paper_summary` artifacts.
- Added a compression-safe `evidence_checkpoint` research-artifact convention to the GRaDOS skill and tool reference so claim evidence can be restored after context compression and reread from canonical saved-paper paragraphs before citation.
- Added `keyring` as a runtime dependency plus a new `grados.secrets` module that resolves API keys with `env -> keychain -> config` precedence, migrates plaintext `config.json` secrets into the OS keychain on first use, and clears migrated plaintext keys with an atomic rewrite.
- Added `grados auth set/status/migrate/clear` commands for explicit keychain management, masked source-aware API-key inspection, and one-shot migration of legacy plaintext config values.
- Added a dedicated `database/remote_metadata/` Chroma store plus `grados.storage.remote_metadata` helpers for validated per-DOI metadata caching, semantic title/abstract lookup, and fetch-status backfills outside the rebuildable paper index.
- Added `tenacity` (>=9.1) as a runtime dependency and a `grados._retry` module providing a unified async HTTP retry decorator (3 attempts, exponential 1→8s backoff with jitter, retries on 429/5xx/connect/read-timeout/protocol errors); see ADR-008.
- Added `PDF_DOWNLOAD_TIMEOUT` helper (connect=15s, read=60s) applied to PDF downloads in the Sci-Hub and Springer fetch paths so slow, large-file transfers no longer time out mid-stream at 30s.
- Added configurable timeout / retry knobs under nested `search`, `extract`, `extract.headless_browser`, and top-level `retry_policy` config sections (`grados-config.example.json`); new processes pick up edits without code changes. See `RetryPolicyConfig` plus `SearchConfig.connect_timeout`, `SearchConfig.read_timeout`, `ExtractConfig.fetch_connect_timeout`, `ExtractConfig.fetch_read_timeout`, and `HeadlessBrowserConfig.deadline_seconds`, `networkidle_timeout`, `poll_min_seconds`, `poll_max_seconds`.
- Added `grados._retry.install_runtime_defaults()` plus live getters (`current_search_timeout`, `current_fetch_timeout`, `current_pdf_timeout`, `current_browser_networkidle_timeout_ms`, `current_browser_deadline_seconds`, `current_browser_poll_bounds`); retry / timeout values are now resolved at call time instead of being frozen at decorator construction, so config changes take effect on process restart without code edits.
- Added a header-aware wait strategy that honors `Retry-After` and `X-RateLimit-Reset` response headers (capped at 60s) before falling back to exponential backoff with jitter; toggle via `retry_policy.respect_retry_after`.
- Added per-source async rate limiters (`grados._retry.throttle_source`) wired into PubMed (≥334ms between calls without an API key, ≥100ms with a key) and Web of Science (≥500ms between calls) so concurrent search calls no longer exceed upstream rate limits.
- Added optional `PUBMED_API_KEY` config threading for PubMed E-utilities requests so the keyed 100ms pacing path is reachable when a user explicitly configures it.
- Added `tests/test_timeout_retry.py` with regression coverage for retry sequences (503/503/200, `ConnectError` → success), `Retry-After` honoring, rate-limiter spacing, browser poll backoff sequence (0.5 → 1 → 2 cap), and runtime-policy config propagation.
- Added a dedicated GitHub `CI` workflow for `push`, `pull_request`, and manual runs, with separate Ruff linting, a Python 3.11/3.12/3.13 pytest matrix, and a package build plus local wheel smoke-install job.

### Changed
- Changed external synthesis from a host/manual ChatGPT handoff into the default GRaDOS-native browser workflow behind the existing `research.external_synthesis.enabled` switch, while keeping lower-level packet/save/audit tools for recovery and leaving the separate `extract.fetch_strategy.codex` PDF acquisition route intact.
- Changed the publisher `browser` fetch strategy to use Oracle-style browser runtime stability and recovery controls while keeping the existing acquisition contract: browser code captures PDF bytes or returns manual-resume challenge metadata, and only the downstream parser/QA/persist pipeline writes canonical `papers/*.md`.
- Changed the bundled GRaDOS skill and Codex plugin metadata to route evidence-grounded writing tasks through the writing workflow reference while keeping writing profiles as skill documentation rather than new MCP tools.
- Changed `extract_paper_full_text` to be idempotent by default for already saved DOIs, returning an already-saved receipt and `read_saved_paper` next action unless `force_refresh=true` is passed for explicit refetch/reparse/rebuild work.
- Changed `search_academic_papers(indepth=true)` to process returned DOI candidates using the same search `limit` without the previous hidden 8-candidate cap.
- Changed `save_external_synthesis_result` to default to `audit=true`, returning the required external-synthesis audit result immediately after saving unless explicitly disabled for recovery/debug workflows.
- Changed `audit_answer_against_pack` to optionally attach suggestion-only follow-up planning with `include_suggestions=true`, while keeping strict audit verdicts unchanged.
- Changed external synthesis audit to validate ChatGPT Pro references against the saved packet when one is linked, accept structured `claims[].anchor_ids` without requiring author-year prose citations, and store packet artifacts without embedding a duplicate full host prompt.
- Changed draft and pack audit outputs from `status` / `status_counts` to `verdict` / `verdict_counts`, using the paper-revision verdict set `verified`, `minor_distortion`, `major_distortion`, `unverifiable`, and `unverifiable_access` with issue types and revision actions instead of the removed `supported`, `weak`, `unsupported`, and `misattributed` status language.
- Changed `search_saved_papers` to use dense retrieval, SQLite FTS/BM25, exact lookup, and RRF when reranking is enabled; if dense retrieval is unavailable it now returns FTS fallback results with mode/retriever/rank/score/query trace.
- Changed direct PDF download timeout handling so `current_pdf_timeout()` uses `extract.pdf_read_timeout=120s`, while landing-page/native text fetches keep `extract.fetch_read_timeout=60s`.
- Changed browser direct-PDF backfill to use the configurable `extract.headless_browser.pdf_backfill_timeout`, separate from browser navigation and polling deadlines.
- Changed Codex host-action receipts to include `issued_at`, `download_watch_dir`, `download_max_age_seconds`, and the next action to call `ingest_codex_downloaded_pdf`.
- Changed canonical library persistence to write `_parsed` provenance sidecars best-effort alongside Markdown and asset manifests; sidecar failures surface as warnings without blocking canonical Markdown, PDF archiving, or index refresh.
- Changed Elsevier XML parsing to use `defusedxml`, and changed remote/local document reads to reject oversized payloads before buffering whenever content length or streamed byte counts exceed configured limits.
- Changed the legacy cloud-parser API-key surface to MinerU; the old cloud-parser key is no longer part of the generated config, docs, or managed secret list.
- Changed the optional `codex` fetch-strategy handoff to the Codex Chrome extension.
- Changed Unpaywall from an `oa` download strategy into an optional `extract.unpaywall.enabled` resolver that supplies OA `url_for_pdf` / `url_for_landing_page` start URLs to `codex` and `browser` without affecting `api` or `scihub`.
- Changed Sci-Hub PDF URL extraction to recognize additional mirror markup patterns and standard URL resolution while keeping the strategy as an HTTP fallback.
- Changed `parse_pdf_file` to support `copy_to_library` and `acquisition_via` for explicit-DOI local PDF handoffs, including raw-PDF archiving and `remote_metadata` backfill after a successful parse/save.
- Changed `search_saved_papers` to include an `Evidence Anchor` JSON block with `canonical_uri`, paragraph coordinates, query, and score breakdown while preserving the existing human-readable Markdown output.
- Changed `build_evidence_grid`, `compare_papers`, and `audit_draft_support` typed payloads to carry reusable reread anchors and score metadata; comparison excerpts now include per-axis section-level evidence items.
- Changed new saved-paper, PDF, asset-manifest, and remote-metadata DOI identifiers to append a short hash of the normalized DOI to the readable slug, preventing distinct DOIs from collapsing to the same `safe_doi` while keeping legacy IDs readable by DOI lookup.
- Changed remote search results to expose local saved/full-text/summary state even when `indepth` is disabled, and changed extraction receipts to include explicit `paper_id`, `safe_doi`, `fetch_status`, and `has_fulltext` fields.
- Changed `grados setup` and `grados status` to treat the OS keychain as the preferred API-key store: setup now points users to `grados auth set`, status reports keychain health plus each key's source (`env`, `keychain`, or legacy `config`), and both READMEs plus `grados-config.example.json` now describe `config.json` plaintext keys as a temporary import path rather than the long-term source of truth.
- Changed config normalization to preserve all-caps keys such as `ELSEVIER_API_KEY`, preventing secret-field names and strategy IDs from being mangled during `config.json` loading.
- Changed `search_academic_papers` to upsert deduplicated remote results into `remote_metadata` before returning the screening list, and changed `extract_paper_full_text` to backfill `metadata_only`, `challenge`, `failed`, and `fulltext` status transitions into the same cache during materialization.
- Changed `grados reindex` to preserve legacy `remote_metadata` rows by copying them from `database/chroma/` into `database/remote_metadata/` before clearing the rebuildable paper index.
- Changed canonical Chroma document/chunk metadata to carry explicit `paper_id`, `remote_source`, and `doc_id` join keys so later corpus-layer work can associate saved full text with the remote metadata cache without re-deriving identifiers.
- Changed phase-1 canonical corpus persistence to reserve `corpus/tier/workset_id/promoted_at/promote_reason` on both markdown frontmatter and Chroma metadata, while older saved papers continue to hydrate with `canonical/stable` defaults when those fields are absent.
- Changed full-text acquisition defaults to use canonical fetch-strategy names (`api`, `browser`, `codex`, `scihub`) and the order `api -> browser -> codex -> scihub`; legacy `TDM` / `SciHub` / `Headless` values remain accepted as aliases, while stale `oa` entries are ignored.
- Changed Sci-Hub configuration from a single `fallback_mirror` runtime value to ordered `extract.sci_hub.endpoints`; the first endpoint is preferred, later endpoints are fallbacks, and the legacy `fallback_mirror` value is used only when `endpoints` is omitted or empty.
- Changed fetch results and browser results to surface `via` and `state` fields, and changed the main fetch waterfall to preserve browser `challenge` / `timeout` / `nobrowser` states instead of collapsing every browser miss into a generic final `failed`.
- Changed all academic search calls (Crossref, PubMed ESearch/ESummary/EFetch, Web of Science, Elsevier Scopus, Springer Meta) to go through the unified retry decorator so transient 429/5xx responses and network errors no longer fail the whole page fetch.
- Changed Elsevier TDM full-text + metadata fallback calls and Springer OA JATS / HTML / PDF fallback calls to use the retry decorator, improving reliability against upstream transient errors.
- Changed browser automation `wait_for_load_state("networkidle")` to use an explicit 15s ceiling (now configurable via `extract.headless_browser.networkidle_timeout`) so SPA-style background polling can no longer silently consume the browser deadline before falling through to the main capture loop (see ADR-008).
- Changed the browser main polling loop from a fixed `asyncio.sleep(1)` to an exponential backoff between idle ticks (`0.5s → 1s → 2s`, configurable via `extract.headless_browser.poll_min_seconds` / `poll_max_seconds`), reducing CPU and event-loop churn on slow publisher pages without hurting first-tick responsiveness.
- Changed hardcoded `timeout=30` values in search, fetch, and publisher HTTP calls to call-time runtime getters (`current_search_timeout()`, `current_fetch_timeout()`, `current_pdf_timeout()`), so user-provided timeouts in `~/GRaDOS/config.json` take effect without touching code.
- Changed browser-assisted ScienceDirect fallback navigation so failed manual/candidate/redirect hops are surfaced in outer `warnings[]` instead of being silently swallowed; generic browser best-effort fallbacks are now annotated in code and covered by regression tests.
- Changed browser challenge handling to propagate `manual`, `host`, and `resume` metadata through `fetch_paper`, extraction receipts, and `remote_metadata`, while detailed fetch traces remain in fetch results instead of being written to `remote_metadata`; `extract_paper_full_text(resume_browser=true)` now starts at the browser strategy with the saved URL/profile after publisher verification instead of rerunning the full `api`-first chain.
- Changed Codex plugin docs and marketplace labeling to center the documented `codex plugin marketplace add STSNaive/GRaDOS` install flow instead of the older repo-open local-marketplace walkthrough.
- Changed local Chroma `collection_get()` / `query_collection()` helpers to enforce a 10s timeout guard; stalled local index calls now return degraded warnings instead of hanging indefinitely.
- Changed remote metadata cache lookup to de-duplicate generated lookup IDs before loading existing records, avoiding repeated cache probes for duplicate DOI/title identifiers.
- Changed scoped DOI evidence-grid searches to batch DOI-constrained saved-paper lookups through `search_papers`, preserving result ordering while avoiding repeated per-DOI vector searches.
- Changed `audit_draft_support` to de-duplicate repeated stripped queries within one audit run, so repeated claims reuse the same local search results instead of re-querying Chroma each time.
- Changed local citation-graph loading to use a process-local `papers/*.md` file-signature cache (`name + size + mtime_ns`), so repeat `get_citation_graph()` calls reuse canonical records while any saved-paper edit invalidates immediately.
- Changed storage helper boundaries so retrieval-only logic now lives in `storage/retrieval.py`, while `research_tools.py` and `papers.py` reuse shared DOI / paragraph helpers from `storage.chunking` instead of each maintaining a parallel implementation.
- Changed storage retrieval internals so `storage/vector.py` is now a thinner facade over dedicated `retrieval`, `hydration`, `chroma_client`, and shared `paths` helpers; `get_paper_document()` / `list_paper_documents()` now return typed `PaperDocument` / `PaperDocumentSummary` results instead of another loose dict boundary.
- Changed canonical saved-paper helpers so `load_paper_record()` / `read_paper()` / `get_paper_structure()` no longer carry an unused `chroma_dir` parameter, and `papers_dir` resolution is centralized in `storage/paths.py::resolve_papers_dir()`.
- Changed local-library ingest orchestration so `extract_paper_full_text`, `parse_pdf_file`, and `import_local_pdf_library` now share a typed workflow in `src/grados/workflows/library.py`; `server_tools/library_tools.py` and `importing.py` keep only entry adaptation, receipt rendering, and batch orchestration.
- Changed browser orchestration layering so `src/grados/browser/generic.py` is now a thin facade over `session_runtime`, `fetch_runtime`, and `browser/strategies`; session lifecycle, listener cleanup, and page polling/backfill contracts are defined once without changing `BrowserFetchResult`.
- Changed Stage B research helper layout so `src/grados/research_tools.py` is now a thin public facade over `src/grados/research/` (`models`, `common`, `full_context`, `citation_graph`, `evidence_grid`, `compare`, `draft_audit`), reducing cross-responsibility coupling while keeping MCP/server payloads stable.
- Changed the GRaDOS skill tool reference and plugin mirror to carry checked live MCP contract guardrails for selected schema constraints from `mcp.list_tools()`.
- Changed CI and PyPI publishing preflight to enforce `mypy` strict-mode checks alongside Ruff, pytest, native-TLS package builds, and local wheel smoke installs before release publication.

### Fixed
- Fixed Bandit high-severity findings for non-security SHA1 fingerprints by explicitly marking those digest uses as `usedforsecurity=False`.
- Fixed saved-paper selector handling so caller-provided `safe_doi` values and `grados://papers/...` suffixes are treated as opaque IDs, validated against a filename-token allowlist, and resolved under the canonical `papers/` directory before reading.
- Fixed Springer metadata-only fetches so a successful Meta API record is preserved as `metadata_only` with metadata and asset hints when JATS, HTML, and PDF full text are unavailable.
- Fixed Sci-Hub endpoint fallback behavior so one endpoint returning `not_found` no longer prevents later configured endpoints from being tried; a final `not_found` is returned only after all endpoints miss.
- Fixed plaintext API-key import for mixed-case secret fields such as `SPRINGER_meta_API_KEY`, so `config.json` one-shot keys are migrated into the OS keychain and then cleared instead of being dropped by config-key normalization.
- Fixed canonical paper saves from `extract_paper_full_text`, `parse_pdf_file`, and `import_local_pdf_library` to pass the active `IndexingConfig` through to Chroma indexing, preventing newly saved papers from being indexed with default embedding/chunking settings after users customize config.
- Fixed the bundled GRaDOS skill tool reference to describe the current `api -> browser -> codex -> scihub` fetch order and `Docling -> MinerU -> PyMuPDF` default parse order.
- Fixed `_HeaderAwareWait` so `Retry-After: 0` is honored as an explicit immediate retry instead of being treated as a missing header and falling back to exponential backoff.
- Fixed retained browser-session error handling so `fetch_with_browser()` now detaches `response` / `download` / `page` listeners even when the polling loop raises, preventing listener leaks across reused visible sessions.
- Fixed `audit_draft_support` to split Chinese claims on sentence-ending punctuation without requiring whitespace, parse Chinese author-year citations such as `（张三，2025）`, and strip those citations before evidence lookup.
- Fixed `search_academic_papers` to warn when a provided `continuation_token` is stale, invalid, or tied to a different query instead of silently rendering a restarted first page.
- Fixed `compare_papers(output_format=\"table\")` to escape pipe characters and normalize multiline cell content so paper titles and excerpts no longer break Markdown table structure.

### Removed
- Removed the legacy standalone `marker-worker/` directory and `markerWorkerDirectory` example-config knob; Marker parsing is now only driven by the Python runtime path configured for the in-process parser worker.
- Removed the temporary `fallbackMirror` compatibility shim from `extract/fetch.py`; raw `sci_hub_config` callers must now pass snake_case keys such as `endpoints` or `fallback_mirror`.
- Removed the legacy `grados migrate-config` TypeScript-to-Python migration command and its old-install documentation; the supported carry-forward path is now `grados import-pdfs` plus normal runtime setup.
- Removed unused `extract.sci_hub.auto_update_mirror` and `mirror_url_file` config fields; the current `scihub` runtime uses ordered `endpoints` with `fallback_mirror` retained for legacy configs.

### Tests
- Added plugin-manifest drift coverage for the evidence-grounded writing workflow reference, writing profiles, domain profile, and Codex plugin writing prompts.
- Added regression coverage for idempotent extraction reuse with `force_refresh`, uncapped `indepth` materialization across more than eight candidates, topic-to-packet external synthesis preparation, default save-and-audit behavior, and optional pack-audit suggestions.
- Added regression coverage for canonical block manifests, evidence-pack schema/hash verification, current-valid failures after canonical Markdown edits, block relocation detection, and pack-scoped audits.
- Added regression coverage for FTS indexing/search, dense-unavailable fallback, hybrid RRF trace output, and `grados eval-retrieval`.
- Added regression coverage for Codex handoff receipts, watch-dir PDF candidate validation, ambiguity/failure records, timeout split contracts, and parsed sidecar frontmatter/structure summaries.
- Added regression coverage for saved-paper Evidence Anchor payloads, evidence-grid and draft-audit anchor propagation, configurable audit candidate limits, comparison evidence items, and MCP schema exposure.
- Added regression coverage for safe saved-paper selector validation, DOI slug-collision avoidance, legacy safe DOI lookup, Springer metadata-only fallback, Sci-Hub `not_found` endpoint fallthrough, and the existing config/search/reindex review fixes.
- Added regression coverage for browser fallback warnings, local Chroma timeout guards, remote metadata lookup de-duplication, batched DOI evidence-grid lookups, repeated-query deduplication and citation-graph cache invalidation in `research_tools`, typed paper-document accessors, shared `papers_dir` resolution, narrow storage helper boundaries (DOI extraction / frontmatter stripping / paragraph splitting), and the dropped raw `fallbackMirror` Sci-Hub fetch shim.
- Added workflow coverage for the shared library ingest pipeline plus `parse_pdf_file` smoke coverage for QA-warning and index-partial-success receipts.
- Added browser regression coverage for retained-session teardown, listener cleanup, and challenge/timeout paths after splitting `browser/generic.py` into runtime/strategy layers.
- Reorganized Stage B research smoke coverage into module-scoped suites for state persistence, citation graph, full-context reads, evidence-grid plus draft-audit, and compare flows; server smoke monkeypatches now target the new research submodules directly.
- Added local validation coverage for the declared `mypy` strict-mode build gate, including typed retry decorators, optional keychain/parser imports, strategy registries, and research helper payload boundaries.
- Added docs/live-schema drift coverage for MCP tool-name tables, README tool coverage, selected schema guardrails, and removed `project_id` claims.

## [0.6.9] - 2026-04-16

### Added
- Added `scripts/release.py` to bump plugin manifest versions, create the release commit/tag sequence, and optionally push in one command.

### Changed
- Changed package versioning from dual static declarations (`pyproject.toml` + `__init__.py`) to `hatch-vcs` dynamic versioning derived from git tags, so normal releases no longer require a manual Python package version bump.
- Changed `src/grados/__init__.py` to read the installed package version via `importlib.metadata.version()` instead of a hardcoded string.
- Changed `publish.yml` to drop the redundant tag-vs-pyproject version verification step after switching to git-tag-derived package versions.

## [0.6.8] - 2026-04-16

### Changed
- Changed the local indexing defaults from Harrier 0.6B / `max_length=32768` to `microsoft/harrier-oss-v1-270m` with `max_length=4096`; Harrier 0.6B remains available as an explicit opt-in for roomier machines.
- Changed section-aware chunking so overlong single paragraphs are re-split by sentence or clause with small overlap before embedding, preventing giant one-paragraph chunks from exploding memory during `grados reindex`.
- Changed embedding runtime diagnostics so `grados setup`, `grados status`, `grados update-db`, and `grados reindex` now surface `max_length`, batch sizing, and clearer OOM guidance instead of opaque allocator failures.

### Tests
- Added regression coverage for overlong single-paragraph chunk splitting, conservative local batching, and OOM diagnostic surfacing in the embedding backend.

## [0.6.7] - 2026-04-15

### Added
- Added Phase A indexing configuration (`config.indexing`) with Harrier 0.6B as the default local embedding model.
- Added a dedicated embedding backend abstraction with explicit query/document separation, Harrier prompt support, and model warmup in `grados setup`.
- Added `grados reindex` plus index-manifest compatibility checks so model/chunking changes fail loudly instead of silently mixing old and new embeddings.
- Added `grados client install|list|doctor|remove` so Claude Code and Codex can be registered from the GRaDOS CLI, including bundled skill installation.
- Added native plugin distribution metadata for Claude Code and Codex, including `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, `plugins/grados/.codex-plugin/plugin.json`, and plugin-scoped `plugin.mcp.json` copies for both plugin surfaces.
- Added Stage B research-state persistence in `database/research.sqlite3`, including reusable artifact storage and local failure memory.
- Added 8 Stage B MCP tools: `save_research_artifact`, `query_research_artifacts`, `manage_failure_cases`, `get_citation_graph`, `get_papers_full_context`, `build_evidence_grid`, `compare_papers`, and `audit_draft_support`.
- Added a lightweight local citation graph layer by extracting reference DOIs into canonical paper metadata (`cites_json`) and exposing neighbor/common-reference/reverse-citation queries.
- Added a canonical full-text normalization layer so publisher-native XML/HTML and document-style inputs are converted into a shared Markdown contract before indexing and deep reading.
- Added absolute paragraph-coordinate metadata (`paragraph_start`, `paragraph_count`) to retrieval chunks so search hits can be mapped back to canonical source paragraphs in `papers/*.md`.
- Added a typed `PaperSearchResult` boundary for the high-frequency local retrieval path so internal search results no longer have to propagate as another loose `dict[str, Any]` contract.
- Added `tests/LIVE_CHECKS.md` to separate offline contract fixtures from manually triggered live checks for Elsevier, Springer, browser fetch, and local import validation.

### Changed
- Changed semantic retrieval from chunk-only search to abstract-first docs → chunks two-stage retrieval.
- Changed chunking from fixed 1000-character paragraph packing to section-aware chunking with overlap metadata.
- Changed `grados setup` to always prepare browser and embedding runtime assets directly, instead of splitting them across `--all` / `--with`.
- Changed `grados status` to report embedding runtime details, active model, and reindex requirements.
- Changed the repo-local MCP example from the removed `uvx grados[all]` path to the current `uvx grados`.
- Changed the Codex plugin packaging to follow the official local marketplace layout more closely, with `.agents/plugins/marketplace.json` pointing at the self-contained `plugins/grados/` bundle instead of the repo root.
- Changed the local paper contract from "search and deep read only" to a broader Stage B research surface with explicit artifacts, failure memory, citation graph, CAG context packs, and draft-support auditing.
- Changed the skill and README documentation to reflect the expanded 16-tool MCP surface, the `grados client install ...` workflow, and the merged writing-stage guidance in `skills/grados/SKILL.md`.
- Changed the default parser/install surface so `uv tool install grados` now includes Docling by default; `grados[docling]` remains as a compatibility alias and `PyMuPDF` is now a fallback parser behind `Docling -> MinerU -> PyMuPDF`.
- Changed source-of-truth semantics so `papers/*.md` is now the user-facing canonical full-text store, while `database/chroma` is treated as a rebuildable retrieval index.
- Changed `search_saved_papers` from returning index-resident snippets to an "index recall + canonical reread" flow that resolves final evidence windows from `papers/*.md`.
- Changed Elsevier full-text handling from JSON `originalText` as the primary path to XML-first deterministic parsing, preserving publisher-native sections, authors, keywords, and references before rendering canonical Markdown.
- Changed Springer native full-text handling so publisher XML/HTML now enters the shared normalization pipeline instead of being flattened early into ad hoc plain text.
- Changed canonical paper frontmatter and reindex behavior so `authors/year/journal` survive in `papers/*.md`, allowing `grados reindex` to rebuild retrieval metadata from the source library alone.
- Changed the index manifest to schema version `3` with chunking strategy `section-aware-v2`; existing local indexes must be rebuilt with `grados reindex`.
- Changed save/import/parse receipts so Chroma indexing failures are surfaced as warnings / partial-success instead of being silently swallowed after the canonical Markdown file is written.
- Changed Marker parsing so `config.extract.parsing.marker_timeout` now enforces a real subprocess timeout instead of being a dead config knob; timed-out Marker runs now fall back cleanly to the next parser.
- Changed parser runtime setup and diagnostics so `grados setup` now prewarms Docling models, while Docling/Marker failures are surfaced through standardized warning/debug messages instead of silent fallbacks.
- Changed local saved-paper retrieval so lexical fallback and result snippets now prefer canonical content from `papers/*.md` when available, instead of continuing to lean on Chroma doc copies for the final returned evidence text.
- Changed local saved-paper retrieval so overlapping chunk hits for the same paper are merged into a single canonical paragraph window before evidence is returned, reducing duplicate or fragmented excerpts.
- Changed canonical save ordering so `save_paper_markdown()` writes `papers/*.md` before refreshing Chroma, preventing index-only state when canonical Markdown writes fail.
- Changed canonical paper frontmatter handling to use `python-frontmatter` + `PyYAML` for save/read/list flows, so multiline YAML values and colon-rich metadata round-trip correctly through `papers/*.md`.
- Changed `list_saved_papers()` frontmatter scanning to read until the closing `---` marker (bounded to 4 KB) instead of truncating metadata after 500 characters.
- Changed publisher fetch handling so `metadata_only` outcomes, typed publisher metadata, and asset hints now survive the TDM waterfall into user-visible extraction receipts, instead of collapsing into generic fetch failures.
- Changed OA/Sci-Hub fetch failures and Chroma filter/projection fallbacks to surface warnings, degraded-filter markers, and logged exceptions instead of silently dropping into opaque fallback behavior.
- Changed embedding backend loading to use a process-local cache keyed by backend-significant config, so repeated `grados setup`, `index_paper()`, and `search_papers()` calls in one process reuse the same heavy model runtime instead of reinitializing it.
- Changed local citation-graph analysis so `research_tools.get_citation_graph` now rebuilds local citation relationships from canonical records in `papers/*.md` instead of depending on Chroma doc listings as an internal source.
- Changed the canonical paper-store boundary so `load_paper_record()` and `list_saved_papers()` now return explicit dataclasses, with `server`, `importing`, and `research_tools` migrated to attribute-based access instead of loose dict payloads.
- Changed typed local-search results from transitional dict-compatible wrappers to plain `PaperSearchResult` dataclasses, removing temporary `.get(...)` / item-access compatibility shims after callers were migrated.
- Changed Stage B research helpers so their internal result boundaries are now explicit dataclasses, with MCP-facing handlers serializing them only at the outer boundary instead of propagating nested dict payloads through the service layer.
- Changed browser fetch and local index-stat payloads to typed result objects, reducing remaining high-frequency `dict[str, Any]` contracts in fetch/search orchestration paths.
- Changed the MCP server layout from one monolithic `server.py` file to a thin entrypoint plus domain registration modules: `search_tools`, `library_tools`, `research_tools_api`, and `admin_tools`.
- Changed `fetch`, `parse`, and browser automation orchestration from hard-coded `if/elif` waterfalls to static strategy registries, so new publishers, parsers, and browser flows can be added without inflating the core dispatch loops.
- Changed the TDM stage from publisher-name branching to a provider registry, and changed non-PDF normalization to a format resolver that maps `markdown/text/html/xml` inputs onto explicit normalization strategies.
- Changed local saved-paper retrieval to use an index-first candidate pipeline before canonical hydration, so search and Stage B audit tools only reread candidate `papers/*.md` files instead of reopening the whole library on each query.
- Changed `audit_draft_support` so `misattributed` remains reserved for resolvable author-year citations; numeric citations now stay in a conservative support-only mode until bibliography mapping exists.
- Changed storage internals so `storage/vector.py` now acts as a thinner facade over dedicated `chunking`, `chroma_client`, and `hydration` helpers, and `research_tools` now consumes public chunking APIs instead of importing private vector symbols.

### Removed
- Removed the Claude-only startup hook at `hooks/hooks.json`.
- Removed the separate `grados-writing` skill split; its useful Stage B writing guidance now lives in `skills/grados/SKILL.md`.

### Tests
- Added regression coverage for keychain-backed secret resolution, automatic `config.json` secret migration + plaintext clearing, and the `grados auth` CLI flows.
- Added regression coverage for remote-metadata helper upserts/queries, search-time metadata-cache population, extract-time `metadata_only`/`challenge`/`fulltext` status backfills, and canonical `paper_id` / `doc_id` metadata joins.
- Added regression coverage for phase-1 corpus defaults so new canonical saves write `corpus/tier/workset` metadata and older Chroma records without those fields still hydrate as `canonical/stable`.
- Added regression coverage for browser-first fetch strategy defaults, legacy fetch-strategy alias compatibility, preserved browser challenge states, browser success short-circuiting, and user-facing `Via/State` receipt lines.
- Added Stage B smoke coverage for research artifacts, failure memory, citation graphs, full-context retrieval, evidence grids, paper comparison, and draft-support auditing.
- Added smoke coverage for client install flows and plugin manifests.
- Added regression coverage for Docling-first parsing, Elsevier XML deterministic normalization, and canonical paragraph reread after Chroma retrieval.
- Added end-to-end regression coverage for the full "index recall + canonical reread" path, including user-facing `search_saved_papers` output after an indexed paper's canonical Markdown file is updated.
- Added regression coverage for fetch/parser/browser strategy registries so order preservation and unknown-strategy filtering stay stable during future extensions.
- Added regression coverage for canonical-Markdown-first saves so failed `papers/*.md` writes cannot leave Chroma in an index-only state.
- Added regression coverage for YAML frontmatter round-trips, long-header saved-paper listing, and visible Chroma/OA/Sci-Hub fallback warnings.
- Added regression coverage for process-local embedding cache reuse and invalidation, including shared backend reuse across `index_paper()` and `search_papers()`.
- Added offline contract-fixture coverage for Elsevier metadata fallback, Springer waterfall fallback, browser anti-bot HTML masquerading as PDF, and nested local-import warning paths.
- Added regression coverage for metadata-only extraction receipts, typed publisher metadata persistence, candidate-only canonical hydration, and numeric-citation support-only auditing.

## [0.6.6] - 2026-04-03

**GRaDOS 完成了从 TypeScript/Node.js 到 Python 的完整重写。** 自本版本起，GRaDOS 是一个纯 Python MCP 服务器，以标准 PyPI 包形式分发，不再需要 Node.js 运行时。0.6.5 中的全部 TS 能力已在 Python 实现中延续。

### Added — Runtime & Packaging

- Rewrote the entire codebase (~6K LoC TypeScript → ~3.5K LoC Python) as a `hatchling`-built Python package (`src/grados/`).
- Added `uv tool install "grados[all]"` as the primary installation path; `uvx "grados[all]"` for zero-install MCP client configuration.
- Added 7 optional dependency groups: `semantic`, `zotero`, `ocr`, `marker`, `docling`, `all`, `full`.
- Historical note (2026-04-05): the packaging surface was later simplified after runtime and dependency audits. Current public install paths are `uv tool install grados`, `uvx grados`, and compatibility extras `grados[docling]`, `grados[marker]`, `grados[full]`.
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
