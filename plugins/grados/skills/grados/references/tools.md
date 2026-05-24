# GRaDOS Tool Reference

## Contents

- [Default Tool Routes](#default-tool-routes)
- [Tool Tiers](#tool-tiers)
- [GRaDOS Server Tools](#grados-server-tools)
- [Live MCP Contract Guardrails](#live-mcp-contract-guardrails)
- [Indepth Mode](#indepth-mode)
- [Optional Codex Chrome Extension](#optional-codex-chrome-extension)
- [MCP Resources](#mcp-resources)

---

## Default Tool Routes

Use these routes to keep the host agent from treating every public MCP tool as an equal first choice. They are defaults, not mandatory sequences: the host agent may skip, reorder, or replace a helper step when a known DOI, exact paragraph selector, task-specific audit request, recovery state, or context budget makes another path more reliable. The hard rule is evidence quality: final factual claims still require canonical rereading through `grados:read_saved_paper` or a current-valid evidence pack.

| Task | Default route | Mechanical steps already handled inside GRaDOS |
| --- | --- | --- |
| Ordinary research answer | `grados:search_saved_papers` for local reuse/context, then `grados:search_academic_papers` for current database coverage. Read saved DOIs directly; extract only unsaved relevant DOIs with `grados:extract_paper_full_text`, then structure/read. Call extraction on a saved DOI only for explicit refresh, reparse, acquisition debugging, or rebuild work. If exact selectors are already known, read directly. | `grados:extract_paper_full_text` controls fetch strategy order, browser PDF acquisition, parser fallback, canonical save, index refresh, and remote metadata updates. Browser acquisition returns PDF bytes or manual-resume challenge metadata; it does not write `papers/*.md` directly. It returns an already-saved receipt by default when canonical Markdown already exists; pass `force_refresh=true` to refetch/reparse. |
| Local PDF workflow | `grados:import_local_pdf_library` for directories or `grados:parse_pdf_file` for one PDF -> structure/read. Use `grados:plan_library_pdf_cleanup` only as a dry-run maintenance report. | Local PDF parsing uses the configured parser waterfall, byte limits, shared PDF materialization, canonical save, index refresh, and metadata update when a DOI is provided. |
| Codex Chrome download handoff | `grados:ingest_codex_downloaded_pdf` after a pending `codex` receipt, or with `downloaded_file_path` when the host already knows the exact PDF path. | The ingest tool validates the download candidate and calls `grados:parse_pdf_file` internally. |
| Evidence organization | Saved papers -> `grados:build_evidence_grid`, `grados:compare_papers`, or `grados:get_papers_full_context(mode="estimate")` -> accepted anchors reread with `grados:read_saved_paper`. | Helper tools rank, align, or budget context, but they do not create final citation evidence. |
| Draft audit | `grados:audit_draft_support` -> reread accepted or disputed anchors with `grados:read_saved_paper`. | The audit proposes verdicts and anchors; final support judgment still requires canonical reread. |
| Pack-scoped audit | `grados:audit_answer_against_pack` with a known pack id; set `include_suggestions=true` only when follow-up gap planning is wanted in the same response. | The pack audit tool calls pack verification internally; call `grados:verify_evidence_pack` separately only when you need a standalone status report. Missing-evidence suggestions are suggestion-only and do not change audit verdicts. |
| Handoff or context recovery | Prefer `grados:prepare_evidence_pack`; restore with `grados:read_evidence_pack` only when inspection is needed, then `grados:verify_evidence_pack` before treating restored text as current. | `grados:prepare_evidence_pack` retrieves candidate anchors, materializes canonical blocks, and persists the pack through research artifacts. |
| External synthesis | Gate first, then use `grados:run_external_synthesis` with either a topic or a current-valid pack id. | The default route prepares or verifies the pack, persists the packet, uses GRaDOS's private ChatGPT browser profile, confirms GRaDOS-validated Pro model and Pro Extended thinking route, captures the response, saves it, and audits it. Keep lower-level preview, packet, save, and audit tools for dry runs, recovery, and explicit reruns. |

Do not start ordinary research with audit, comparison, external synthesis, Zotero, failure memory, or generic artifact tools unless the user asks for that mode or the run needs recovery. Do not use snippets, summaries, grids, comparisons, audits, checkpoints, receipts, or external synthesis prose as final citation evidence.

## Tool Tiers

| Tier | Tools | Use when |
| --- | --- | --- |
| Default research path | `grados:search_saved_papers`, `grados:search_academic_papers`, `grados:extract_paper_full_text`, `grados:get_saved_paper_structure`, `grados:read_saved_paper` | Normal literature questions, local reuse plus current database discovery, selective extraction, and citation-grade reading. |
| Conditional input/assets | `grados:import_local_pdf_library`, `grados:parse_pdf_file`, `grados:ingest_codex_downloaded_pdf`, `grados:plan_library_pdf_cleanup`, `grados:read_paper_asset` | The user provides PDFs, a Codex download needs ingest, duplicate PDF cleanup needs a dry-run report, or a cited paragraph depends on a figure/table/formula/source asset. |
| Analysis helpers | `grados:build_evidence_grid`, `grados:compare_papers`, `grados:audit_draft_support`, `grados:get_papers_full_context`, `grados:get_citation_graph` | Saved papers need organization, comparison, context budgeting, citation neighborhoods, or first-pass claim auditing. |
| Handoff/recovery | `grados:prepare_evidence_pack`, `grados:read_evidence_pack`, `grados:verify_evidence_pack`, `grados:audit_answer_against_pack`, `grados:suggest_missing_evidence`, `grados:save_research_artifact`, `grados:query_research_artifacts`, `grados:manage_failure_cases` | Evidence must survive compression/handoff, a pack needs audit or inspection, or workflow/failure state needs explicit recovery. |
| Advanced external/admin | `grados:run_external_synthesis`, `grados:preview_external_synthesis_packet`, `grados:prepare_external_synthesis_packet`, `grados:prepare_external_synthesis_from_topic`, `grados:save_external_synthesis_result`, `grados:audit_external_synthesis_result`, `grados:save_paper_to_zotero` | External synthesis is explicitly enabled or the final actually cited papers should be saved to Zotero. |

`grados:save_research_artifact`, `grados:query_research_artifacts`, and `grados:manage_failure_cases` are advanced recovery surfaces. Prefer typed higher-level tools when they exist.

## GRaDOS Server Tools

| Tool | Purpose |
| --- | --- |
| `grados:search_academic_papers` | Waterfall search across academic databases. Returns deduplicated paper metadata with DOIs, abstracts, continuation state, and local saved/full-text/summary state. Optional `indepth=true` materializes returned candidates with the same `limit`. |
| `grados:search_saved_papers` | Compact paper-level search over the saved-paper store. Uses metadata prefiltering, ChromaDB dense retrieval, SQLite FTS/BM25 fallback, exact lookup, and hybrid RRF when reranking is enabled. Treat snippets, scores, and evidence anchors as screening/reranking material, not citation evidence. |
| `grados:get_saved_paper_structure` | Deterministic low-token paper card for one saved paper. Returns canonical URI, preview excerpt, section headings, section outline, counts, asset summary, and parser provenance summary when available. Use this before deep reading. |
| `grados:extract_paper_full_text` | Fetch full text by DOI via the configured `api`, `browser`, optional `codex`, and `scihub` order, then parse via `Docling -> MinerU -> PyMuPDF` by default. Optional Unpaywall resolution can supply OA `url_for_pdf` / `url_for_landing_page` start URLs for `codex` and `browser`; the publisher browser path uses a locked persistent profile plus `browser/pdf-sessions` records, and only returns PDF bytes or challenge metadata before the normal parser/QA/persist pipeline writes canonical Markdown to `papers/`; MinerU is an authenticated cloud fallback that requires `MINERU_API_KEY`; `codex` returns a host-action receipt. If the DOI is already saved, the default `force_refresh=false` returns an already-saved receipt; set `force_refresh=true` for explicit refresh/reparse/debug work. |
| `grados:import_local_pdf_library` | Import one local PDF file or a directory of PDFs into the canonical paper store. Supports recursive scanning, glob filtering, and optional shared raw-PDF materialization into `downloads/{safe_doi}.pdf`. |
| `grados:parse_pdf_file` | Parse a local PDF file using the same Python parsing waterfall. If DOI is provided, it writes the canonical `.md` file to `papers/`, materializes the managed PDF when `copy_to_library=true`, and returns a compact save receipt. |
| `grados:ingest_codex_downloaded_pdf` | Complete a Codex Chrome extension handoff. It validates `downloaded_file_path` when provided, otherwise scans `extract.codex_handoff.download_watch_dir` plus a noncanonical project-downloads fallback, applies conservative PDF/age/settle/symlink/hash checks, and then reuses `parse_pdf_file(..., doi=..., copy_to_library=true, acquisition_via="codex")`. |
| `grados:plan_library_pdf_cleanup` | Dry-run scan for noncanonical PDFs in `downloads/` that duplicate a DOI's managed `downloads/{safe_doi}.pdf` by hash. It reports candidates only and never deletes files. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved papers. Accepts `doi`, a GRaDOS-returned opaque `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window plus lightweight asset refs for synthesis and citation verification. |
| `grados:read_paper_asset` | List or read parser asset bundles for saved papers. Use it after `get_saved_paper_structure` or `read_saved_paper` when a figure, table, formula, page image, or source/debug asset is needed; `include_image=true` only inlines a specific image when it is under the configured limit. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero. Requires `ZOTERO_API_KEY` and Zotero library configuration. |
| `grados:save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, evidence grids, compression-safe evidence checkpoints, and run-linked artifacts in the local SQLite state store. Include `metadata.research_run_id` to attach an artifact to a run manifest. |
| `grados:query_research_artifacts` | Query previously saved research artifacts by id, kind, or keyword. Use `detail=true` to restore full JSON or Markdown content. |
| `grados:prepare_evidence_pack` | Retrieve candidate anchors, reread canonical paragraph blocks from `papers/*.md`, and persist a minimal `evidence_pack` artifact with pack hash, block hashes, and answerability status. Use this when evidence must survive handoff or context compression. |
| `grados:read_evidence_pack` | Inspect or recover a persisted evidence pack snapshot by pack id or artifact id. Do not call it as a required pre-step before verification or pack-scoped audit; `verify_evidence_pack` and `audit_answer_against_pack` read and validate the pack internally. |
| `grados:verify_evidence_pack` | Rebuild the canonical block registry from current `papers/*.md` and report `snapshot_valid`, `current_valid`, missing papers, document changes, relocated blocks, missing blocks, hash mismatches, and ambiguous relocations. |
| `grados:preview_external_synthesis_packet` | Dry-run a compact external-synthesis packet from one current-valid evidence pack. It reports sendability, size estimates, prompt hash, and host guidance without saving artifacts or contacting any external service. Optional before prepare, not required for the send path. |
| `grados:prepare_external_synthesis_packet` | Persist an `external_synthesis_packet` artifact with verified anchor ids, canonical paragraph coordinates, short excerpts, candidate claims, limitations, and prompt hash, returning the host prompt as a regenerable view. It refuses stale or non-current packs. |
| `grados:prepare_external_synthesis_from_topic` | Prepare a fresh evidence pack from a topic, verify it through packet preparation, persist an `external_synthesis_packet`, and return the host prompt plus pack/packet ids. Use this default route when external synthesis starts from a topic rather than an existing pack id. |
| `grados:run_external_synthesis` | Run the default GRaDOS-native ChatGPT Pro browser route. It accepts either a topic or pack id, prepares or verifies the packet, uses the private ChatGPT profile, confirms GRaDOS-validated Pro model and Pro Extended thinking route before sending, captures the advisory response, saves it, audits it, and returns the canonical reread next action. |
| `grados:save_external_synthesis_result` | Save a host-provided external synthesis response as an advisory `external_synthesis_result` artifact linked to its source pack, optional packet id, prompt hash, conversation/session URL, model label, and thinking label. Defaults to `audit=true`, so the returned receipt includes the required audit result before canonical reread. |
| `grados:audit_external_synthesis_result` | Audit a saved external synthesis result against its linked packet when available, otherwise its source pack; structured `claims[].anchor_ids` are the primary handoff contract, while prose audit remains a risk scan before canonical reread. |
| `grados:audit_answer_against_pack` | Audit draft claims using only one evidence pack. In strict mode it does not search the full library, so non-verified claims remain visible instead of being silently patched. Set `include_suggestions=true` only when suggestion-only follow-up planning should be attached. |
| `grados:suggest_missing_evidence` | Suggest follow-up evidence or revision work for non-verified pack-audit claims. It is suggestion-only and does not change strict audit results. |
| `grados:manage_failure_cases` | Record, query, and summarize failed fetch/parse/search/citation attempts. Can also suggest conservative retry steps. |
| `grados:get_citation_graph` | Return lightweight local citation relationships, including neighbors, common references, and reverse citing-paper lookups. |
| `grados:get_papers_full_context` | Return structured full-context material for saved-paper batches, with token estimates or actual section content for CAG-style deep reading. Use `mode="estimate"` and batching to manage broad reading; do not treat one call's DOI list as a research paper-count target. |
| `grados:build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. Rows are agent-side reranking material until reread through `grados:read_saved_paper`. |
| `grados:compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. Returned excerpts and anchors guide rereading; they are not citation-ready proof. |
| `grados:audit_draft_support` | Audit draft claims against the local paper library and return first-pass `verified`, `minor_distortion`, `major_distortion`, `unverifiable`, or `unverifiable_access` verdicts plus candidate evidence snippets, issue types, revision actions, and anchors. `candidate_limit` controls how many candidates are retrieved per claim for host-agent reranking. The host agent model must reread canonical paragraph windows before final support judgment. |

There is no separate local RAG server in the Python release. Saved-paper canonical storage and semantic retrieval are built directly into GRaDOS through ChromaDB.

## Live MCP Contract Guardrails

These checked guardrails mirror selected hard schema facts from the live FastMCP surface (`mcp.list_tools()`). Update this table when the public MCP schema changes.

| Tool | Live schema guardrail |
| --- | --- |
| `grados:search_academic_papers` | `query` minLength=1; `limit` range 1-50; optional `indepth` uses the same `limit`. |
| `grados:extract_paper_full_text` | `force_refresh` defaults to false; set true only to refetch/reparse already saved full text. |
| `grados:ingest_codex_downloaded_pdf` | `downloaded_file_path` defaults to null; pass it when the host already knows the exact PDF path. |
| `grados:search_saved_papers` | `query` minLength=1; `limit` range 1-25; `use_reranking` defaults to true. |
| `grados:read_saved_paper` | accepts `doi`, `safe_doi`, or `uri`; `start_paragraph` minimum 0; `max_paragraphs` range 1-100. |
| `grados:read_paper_asset` | list-mode `limit` range 1-100; `offset` minimum 0; `include_image` is explicit opt-in. |
| `grados:query_research_artifacts` | filters by `artifact_id`, `kind`, or `query`; `detail` defaults to false; `limit` range 1-50. |
| `grados:get_papers_full_context` | `dois` minItems=1 for a context-budgeted batch; `mode` enum `estimate` / `full`; `max_total_tokens` range 1000-128000. |
| `grados:build_evidence_grid` | `max_papers` range 1-12 per subquestion per call; use scoped or repeated batches for broader evidence maps. |
| `grados:audit_draft_support` | `draft_text` minLength=1; `citation_style` enum `author_year` / `numeric`; `strictness` enum `strict` / `balanced`; `candidate_limit` range 1-25. |
| `grados:audit_answer_against_pack` | `include_suggestions` defaults to false; `max_suggestions` range 1-25. |
| `grados:run_external_synthesis` | exactly one of `topic` or `pack_id` is required unless `recover_session_id` is provided; browser model/thinking are protocol defaults, not tool inputs. |
| `grados:save_external_synthesis_result` | `audit` defaults to true. |

## Host Agent Reasoning Boundary

GRaDOS tools do not call the host agent model. They provide deterministic search, storage, indexing, retrieval anchors, low-token structure cards, and canonical saved-paper reads. The host agent model is responsible for query planning, candidate screening, agent-side reranking, support judgment, and synthesis.

Outputs from `search_saved_papers`, `build_evidence_grid`, `compare_papers`, and `audit_draft_support` are navigation and audit material. They may include `canonical_uri`, `paragraph_start`, and `paragraph_count` so the agent can reread the source, but they are not final citation evidence until `grados:read_saved_paper` returns the canonical paragraph window.

Evidence packs are the durable citation handoff layer. `prepare_evidence_pack` stores canonical block snapshots from `papers/*.md` through `research_artifacts(kind="evidence_pack")`; `verify_evidence_pack` must return `current_valid=true` before a restored pack is treated as current evidence. Pack-scoped audit tools never search the whole saved-paper library to fill gaps.

External synthesis packets are a browser-mediated advisory layer on top of current-valid evidence packs. `run_external_synthesis` is the default enabled route: it prepares or verifies the packet, uses GRaDOS's private ChatGPT browser profile, confirms GRaDOS-validated Pro model and Pro Extended thinking route before sending, saves the returned advisory text, and audits it. `preview_external_synthesis_packet` is non-mutating; `prepare_external_synthesis_from_topic` and `prepare_external_synthesis_packet` persist `research_artifacts(kind="external_synthesis_packet")` for lower-level recovery; `save_external_synthesis_result(audit=true)` and `audit_external_synthesis_result` remain available for explicit reruns before accepted claims are reread through canonical GRaDOS tools.

Research run manifests are directory pages, not evidence sources. A manifest may link search queries, candidates, extraction receipts, parser receipts, `paper_summary`, `research_checkpoint`, `evidence_checkpoint`, `evidence_pack`, audit result IDs, canonical anchors, and failure records. It may also keep an append-only event ledger and a redacted config/provenance snapshot; append correction events rather than editing prior ledger entries, and never store secrets. Final claims and citations must still be grounded by rereading canonical `papers/*.md` files or current-valid evidence packs.

Audit tools emit only these verdicts: `verified`, `minor_distortion`, `major_distortion`, `unverifiable`, and `unverifiable_access`. Do not emit, parse, or preserve compatibility aliases for the removed statuses `supported`, `weak`, `unsupported`, `misattributed`, `overgeneralized`, `uncited_factual_claim`, or `needs_human_review`. Use `minor_distortion` for small wording, scope, precision, or locator fixes; use `major_distortion` for material misstatement, overclaim, or citation mismatch; use `unverifiable_access` when a source or pack trail exists but GRaDOS cannot read enough canonical full text or paragraph context.

For broad tasks, a host client may use subagents to triage independent paper sets, claim sets, or subquestions. Subagents are not GRaDOS server tools; their output should be limited to candidate anchors, rejected/non-verified items, gaps, warnings, and exact reread selectors such as `canonical_uri`, `paragraph_start`, and `paragraph_count`. The main host agent must reread accepted anchors through `grados:read_saved_paper` before citation or final support judgment.

## Indepth Mode

`indepth` is disabled by default. Use `search_academic_papers(indepth=true)` or `grados search "query" --indepth` to run one opt-in full-text pass over the returned search candidates. The mode uses the same `limit` as metadata search, writes `research_checkpoint` folders under `GRADOS_HOME/research_checkpoints/`, and can generate reusable query-independent `paper_summary` artifacts under `GRADOS_HOME/paper_summaries/`.

See [indepth.md](indepth.md) for the checkpoint schema, paper-summary invalidation rules, and failure semantics.

## Compression-Safe Evidence Checkpoints

Prefer `grados:prepare_evidence_pack` when evidence has to survive context compression, handoff, or a later drafting pass and might be cited later. Use `grados:save_research_artifact(kind="evidence_checkpoint")` for looser recovery notes and navigation state. An `evidence_checkpoint` is not a citation source by itself.

Recommended content schema:

```json
{
  "schema_version": 1,
  "user_question": "The research question or writing task.",
  "search_queries": ["english query used for discovery"],
  "evidence_anchors": [
    {
      "doi": "10.xxxx/example",
      "safe_doi": "10_xxxx_example__abc123def456",
      "canonical_uri": "grados://papers/10_xxxx_example__abc123def456",
      "section_name": "Results",
      "paragraph_start": 42,
      "paragraph_count": 3,
      "claim": "The claim this evidence supports.",
      "support_reason": "Why this paragraph window supports or limits the claim."
    }
  ],
  "open_questions": ["Evidence gaps still unresolved."],
  "next_actions": ["Concrete follow-up reading, extraction, or audit steps."],
  "warnings": ["Known limitations, non-verified support, or imprecise coordinates."]
}
```

Recommended metadata:

```json
{
  "schema_name": "evidence_checkpoint",
  "schema_version": 1,
  "query_topic": "short topic label",
  "paper_count": 3,
  "anchor_count": 7
}
```

Restore with `grados:query_research_artifacts(kind="evidence_checkpoint", detail=true)`. Before citing, auditing, or comparing any restored claim, call `grados:read_saved_paper` with the saved `canonical_uri` or `safe_doi`, `start_paragraph`, and `max_paragraphs=paragraph_count`. Search snippets, summaries, checkpoints, and tool previews are only navigation material; final answers and citations must be checked against canonical `papers/*.md` content.

For a standalone pack status check, call `grados:verify_evidence_pack(pack_id=...)`; it reads the pack internally. Use `grados:read_evidence_pack(pack_id=...)` only when you need to inspect the stored snapshot. If `current_valid=false`, treat the pack as historical and either reread the affected canonical paper windows or prepare a fresh pack.

## Optional Codex Chrome Extension

`codex` is a disabled-by-default fetch-strategy entry for Codex host agents that have the [Codex Chrome extension](https://developers.openai.com/codex/app/chrome-extension) connected. It is not a GRaDOS server-internal browser backend. When enabled and placed in `extract.fetch_strategy.order`, GRaDOS stops at that position and returns a host-action receipt for Chrome. Host agents should treat that receipt as requiring the Codex `@chrome` plugin / extension backend as the acquisition route. When `extract.unpaywall.enabled=true`, the receipt can start from an Unpaywall `url_for_pdf` or `url_for_landing_page` instead of the DOI URL.

Config shape:

```json
{
  "extract": {
    "fetch_strategy": {
      "order": ["api", "browser", "codex", "scihub"],
      "enabled": {
        "api": true,
        "browser": true,
        "codex": true,
        "scihub": true
      }
    },
    "unpaywall": {
      "enabled": true
    }
  }
}
```

Typical host-agent flow:

1. Call `grados:extract_paper_full_text` with the DOI.
2. If the receipt asks for `codex`, use the Codex `@chrome` plugin / Chrome extension backend and start from the receipt URL. This can be an Unpaywall OA URL when available, otherwise `https://doi.org/{doi}`.
3. After Chrome reports the PDF download is complete, call `grados:ingest_codex_downloaded_pdf(doi=..., downloaded_file_path=...)` when the absolute path is known. That path is used only for the receipt and `_parsed` PDF materialization provenance, not for canonical Markdown/frontmatter/index citation metadata.
4. If the exact path is not known, call `grados:ingest_codex_downloaded_pdf(doi=...)`. Pass `file_name_hint` or `downloaded_at` only as narrowing hints; GRaDOS still validates age, type, symlinks, stability, size, and hash. If the watch dir scan is empty, pass the real `downloaded_file_path` or call `grados:parse_pdf_file(file_path=..., doi=..., copy_to_library=true, acquisition_via="codex")`; do not click the publisher download button again just because the watch dir missed the file.

Relevant config:

- `extract.codex_handoff.download_watch_dir`: directory scanned only by `ingest_codex_downloaded_pdf`; it does not configure Chrome.
- `extract.codex_handoff.download_max_age_seconds`: candidate age limit after the handoff receipt is issued.
- `extract.codex_handoff.download_settle_seconds` / `download_settle_max_wait_seconds`: size/mtime stability checks before ingest.
- `extract.codex_handoff.download_scan_recursive`: opt-in recursive scan; the default scans only the watch-dir root.
- `extract.pdf_read_timeout`: direct remote PDF read timeout; separate from landing-page / HTML / XML / JSON `extract.fetch_read_timeout`.
- `extract.headless_browser.pdf_backfill_timeout`: browser context-request PDF backfill timeout; separate from browser navigation and polling deadlines.

PDF materialization contract:

- The only managed library PDF artifact for a DOI is `downloads/{safe_doi}.pdf`.
- Same DOI + same PDF hash reuses, renames, or copies to that managed path. Same DOI + different hash returns a conflict receipt with both hashes/paths and does not overwrite or delete either file.
- New `papers/*.md` frontmatter does not store `fetch_outcome`, `original_pdf_path`, `copied_pdf_path`, `source_pdf_hash`, or `acquisition_via`. Route information belongs in receipts and `remote_metadata.fetch_via`; parser and PDF materialization provenance belongs in `papers/_parsed/{safe_doi}.json`.

## MCP Resources

If your client supports resource reading:

| Resource URI | Purpose |
| --- | --- |
| `grados://papers/index` | Lightweight index of saved papers in the canonical local paper store |
| `grados://papers/{safe_doi}` | Low-token overview resource for one saved paper: metadata, preview, section list, and asset counts |

Treat `safe_doi` as an opaque paper ID returned by GRaDOS receipts, search results, or resources. New IDs include a short normalized-DOI hash suffix for collision resistance; legacy pure-slug IDs remain readable, but agents should prefer DOI lookup or returned URIs over guessing IDs from DOI punctuation.
