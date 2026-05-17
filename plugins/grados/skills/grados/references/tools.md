# GRaDOS Tool Reference

## Contents

- [GRaDOS Server Tools](#grados-server-tools)
- [Indepth Mode](#indepth-mode)
- [Optional Codex Chrome Extension](#optional-codex-chrome-extension)
- [MCP Resources](#mcp-resources)

---

## GRaDOS Server Tools

| Tool | Purpose |
| --- | --- |
| `grados:search_academic_papers` | Waterfall search across academic databases. Returns deduplicated paper metadata with DOIs, abstracts, continuation state, and local saved/full-text/summary state. Optional `indepth=true` materializes returned candidates with the same `limit`. |
| `grados:search_saved_papers` | Compact paper-level search over the saved-paper store. Uses metadata prefiltering, ChromaDB dense retrieval, SQLite FTS/BM25 fallback, exact lookup, and hybrid RRF when reranking is enabled. Treat snippets, scores, and evidence anchors as screening/reranking material, not citation evidence. |
| `grados:get_saved_paper_structure` | Deterministic low-token paper card for one saved paper. Returns canonical URI, preview excerpt, section headings, section outline, counts, asset summary, and parser provenance summary when available. Use this before deep reading. |
| `grados:extract_paper_full_text` | Fetch full text by DOI via the configured `api`, `browser`, optional `codex`, and `scihub` order, then parse via `Docling -> MinerU -> PyMuPDF` by default. Optional Unpaywall resolution can supply OA `url_for_pdf` / `url_for_landing_page` start URLs for `codex` and `browser`; MinerU is an authenticated cloud fallback that requires `MINERU_API_KEY`; successful fetches write canonical Markdown to `papers/` and refresh the ChromaDB retrieval index; `codex` returns a host-action receipt. |
| `grados:import_local_pdf_library` | Import one local PDF file or a directory of PDFs into the canonical paper store. Supports recursive scanning, glob filtering, and optional raw-PDF archiving into `downloads/`. |
| `grados:parse_pdf_file` | Parse a local PDF file using the same Python parsing waterfall. If DOI is provided, it writes the canonical `.md` file to `papers/` and returns a compact save receipt. |
| `grados:ingest_codex_downloaded_pdf` | Complete a pending Codex Chrome extension handoff. It scans `extract.codex_handoff.download_watch_dir`, applies conservative PDF/age/settle/symlink/hash checks, and then reuses `parse_pdf_file(..., doi=..., copy_to_library=true, acquisition_via="codex")`. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved papers. Accepts `doi`, a GRaDOS-returned opaque `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window plus lightweight asset refs for synthesis and citation verification. |
| `grados:read_paper_asset` | List or read parser asset bundles for saved papers. Use it after `get_saved_paper_structure` or `read_saved_paper` when a figure, table, formula, page image, or source/debug asset is needed; `include_image=true` only inlines a specific image when it is under the configured limit. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero. Requires `ZOTERO_API_KEY` and Zotero library configuration. |
| `grados:save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, evidence grids, compression-safe evidence checkpoints, and run-linked artifacts in the local SQLite state store. Include `metadata.research_run_id` to attach an artifact to a run manifest. |
| `grados:query_research_artifacts` | Query previously saved research artifacts by id, kind, or keyword. Use `detail=true` to restore full JSON or Markdown content. |
| `grados:prepare_evidence_pack` | Retrieve candidate anchors, reread canonical paragraph blocks from `papers/*.md`, and persist a minimal `evidence_pack` artifact with pack hash, block hashes, and answerability status. Use this when evidence must survive handoff or context compression. |
| `grados:read_evidence_pack` | Restore a persisted evidence pack by pack id or artifact id. The stored text is a snapshot until `verify_evidence_pack` confirms it still matches current canonical Markdown. |
| `grados:verify_evidence_pack` | Rebuild the canonical block registry from current `papers/*.md` and report `snapshot_valid`, `current_valid`, missing papers, document changes, relocated blocks, missing blocks, hash mismatches, and ambiguous relocations. |
| `grados:preview_external_synthesis_packet` | Dry-run a compact external-synthesis packet from one current-valid evidence pack. It reports sendability, size estimates, prompt hash, and host guidance without saving artifacts or contacting any external service. |
| `grados:prepare_external_synthesis_packet` | Persist an `external_synthesis_packet` artifact with verified anchor ids, canonical paragraph coordinates, short excerpts, candidate claims, limitations, and a host prompt. It refuses stale or non-current packs. |
| `grados:save_external_synthesis_result` | Save a host-provided external synthesis response as an advisory `external_synthesis_result` artifact linked to its source pack, optional packet id, prompt hash, conversation/session URL, model label, and thinking label. |
| `grados:audit_external_synthesis_result` | Audit a saved external synthesis result against its source pack, flagging unknown anchor ids, pack-external DOIs, stale packs, and non-verified claims before any canonical reread or final citation. |
| `grados:audit_answer_against_pack` | Audit draft claims using only one evidence pack. In strict mode it does not search the full library, so non-verified claims remain visible instead of being silently patched. |
| `grados:suggest_missing_evidence` | Suggest follow-up evidence or revision work for non-verified pack-audit claims. It is suggestion-only and does not change strict audit results. |
| `grados:manage_failure_cases` | Record, query, and summarize failed fetch/parse/search/citation attempts. Can also suggest conservative retry steps. |
| `grados:get_citation_graph` | Return lightweight local citation relationships, including neighbors, common references, and reverse citing-paper lookups. |
| `grados:get_papers_full_context` | Return structured full-context material for a small paper set, with token estimates or actual section content for CAG-style deep reading. |
| `grados:build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. Rows are agent-side reranking material until reread through `grados:read_saved_paper`. |
| `grados:compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. Returned excerpts and anchors guide rereading; they are not citation-ready proof. |
| `grados:audit_draft_support` | Audit draft claims against the local paper library and return first-pass `verified`, `minor_distortion`, `major_distortion`, `unverifiable`, or `unverifiable_access` verdicts plus candidate evidence snippets, issue types, revision actions, and anchors. `candidate_limit` controls how many candidates are retrieved per claim for host-agent reranking. The host agent model must reread canonical paragraph windows before final support judgment. |

There is no separate local RAG server in the Python release. Saved-paper canonical storage and semantic retrieval are built directly into GRaDOS through ChromaDB.

## Host Agent Reasoning Boundary

GRaDOS tools do not call the host agent model. They provide deterministic search, storage, indexing, retrieval anchors, low-token structure cards, and canonical saved-paper reads. The host agent model is responsible for query planning, candidate screening, agent-side reranking, support judgment, and synthesis.

Outputs from `search_saved_papers`, `build_evidence_grid`, `compare_papers`, and `audit_draft_support` are navigation and audit material. They may include `canonical_uri`, `paragraph_start`, and `paragraph_count` so the agent can reread the source, but they are not final citation evidence until `grados:read_saved_paper` returns the canonical paragraph window.

Evidence packs are the durable citation handoff layer. `prepare_evidence_pack` stores canonical block snapshots from `papers/*.md` through `research_artifacts(kind="evidence_pack")`; `verify_evidence_pack` must return `current_valid=true` before a restored pack is treated as current evidence. Pack-scoped audit tools never search the whole saved-paper library to fill gaps.

External synthesis packets are a host-side handoff layer on top of current-valid evidence packs. `preview_external_synthesis_packet` is non-mutating; `prepare_external_synthesis_packet` persists `research_artifacts(kind="external_synthesis_packet")`; `save_external_synthesis_result` stores returned advisory text; `audit_external_synthesis_result` must pass before accepted claims are reread through canonical GRaDOS tools.

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

Restore an evidence pack with `grados:read_evidence_pack(pack_id=...)`, then call `grados:verify_evidence_pack(pack_id=...)`. If `current_valid=false`, treat the pack as a historical snapshot and either reread the affected canonical paper windows or prepare a fresh pack.

## Optional Codex Chrome Extension

`codex` is a disabled-by-default fetch-strategy entry for Codex host agents that have the [Codex Chrome extension](https://developers.openai.com/codex/app/chrome-extension) connected. It is not a GRaDOS server-internal browser backend. When enabled and placed in `extract.fetch_strategy.order`, GRaDOS stops at that position and returns a host-action receipt for Chrome. When `extract.unpaywall.enabled=true`, the receipt can start from an Unpaywall `url_for_pdf` or `url_for_landing_page` instead of the DOI URL.

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
2. If the receipt asks for `codex`, use Chrome with the Codex extension and start from the receipt URL. This can be an Unpaywall OA URL when available, otherwise `https://doi.org/{doi}`.
3. After Chrome reports the PDF download is complete, call `grados:ingest_codex_downloaded_pdf(doi=...)`. Pass `file_name_hint` or `downloaded_at` only as narrowing hints; GRaDOS still validates age, type, symlinks, stability, size, and hash.
4. If the host already knows the exact absolute PDF path, it may skip watch-dir scanning and call `grados:parse_pdf_file(file_path=..., doi=..., copy_to_library=true, acquisition_via="codex")`.

Relevant config:

- `extract.codex_handoff.download_watch_dir`: directory scanned only by `ingest_codex_downloaded_pdf`; it does not configure Chrome.
- `extract.codex_handoff.download_max_age_seconds`: candidate age limit after the handoff receipt is issued.
- `extract.codex_handoff.download_settle_seconds` / `download_settle_max_wait_seconds`: size/mtime stability checks before ingest.
- `extract.codex_handoff.download_scan_recursive`: opt-in recursive scan; the default scans only the watch-dir root.
- `extract.pdf_read_timeout`: direct remote PDF read timeout; separate from landing-page / HTML / XML / JSON `extract.fetch_read_timeout`.
- `extract.headless_browser.pdf_backfill_timeout`: browser context-request PDF backfill timeout; separate from browser navigation and polling deadlines.

## MCP Resources

If your client supports resource reading:

| Resource URI | Purpose |
| --- | --- |
| `grados://papers/index` | Lightweight index of saved papers in the canonical local paper store |
| `grados://papers/{safe_doi}` | Low-token overview resource for one saved paper: metadata, preview, section list, and asset counts |

Treat `safe_doi` as an opaque paper ID returned by GRaDOS receipts, search results, or resources. New IDs include a short normalized-DOI hash suffix for collision resistance; legacy pure-slug IDs remain readable, but agents should prefer DOI lookup or returned URIs over guessing IDs from DOI punctuation.
