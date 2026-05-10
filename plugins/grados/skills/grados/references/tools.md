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
| `grados:search_saved_papers` | Compact paper-level search over the saved-paper store. Uses metadata prefiltering, ChromaDB chunk retrieval, and optional lightweight lexical reranking over canonical documents. Treat snippets and evidence anchors as screening/reranking material, not citation evidence. |
| `grados:get_saved_paper_structure` | Deterministic low-token paper card for one saved paper. Returns canonical URI, preview excerpt, section headings, section outline, counts, and asset summary. Use this before deep reading. |
| `grados:extract_paper_full_text` | Fetch full text by DOI via the configured `api`, `browser`, optional `codex`, and `scihub` order, then parse via `Docling -> MinerU -> Marker -> PyMuPDF`. Optional Unpaywall resolution can supply OA `url_for_pdf` / `url_for_landing_page` start URLs for `codex` and `browser`; MinerU is an authenticated cloud fallback that requires `MINERU_API_KEY`; successful fetches write canonical Markdown to `papers/` and refresh the ChromaDB retrieval index; `codex` returns a host-action receipt. |
| `grados:import_local_pdf_library` | Import one local PDF file or a directory of PDFs into the canonical paper store. Supports recursive scanning, glob filtering, and optional raw-PDF archiving into `downloads/`. |
| `grados:parse_pdf_file` | Parse a local PDF file using the same Python parsing waterfall. If DOI is provided, it writes the canonical `.md` file to `papers/` and returns a compact save receipt. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved papers. Accepts `doi`, a GRaDOS-returned opaque `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window for synthesis and citation verification. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero. Requires `ZOTERO_API_KEY` and Zotero library configuration. |
| `grados:save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, evidence grids, and compression-safe evidence checkpoints in the local SQLite state store. |
| `grados:query_research_artifacts` | Query previously saved research artifacts by id, kind, or keyword. Use `detail=true` to restore full JSON or Markdown content. |
| `grados:manage_failure_cases` | Record, query, and summarize failed fetch/parse/search/citation attempts. Can also suggest conservative retry steps. |
| `grados:get_citation_graph` | Return lightweight local citation relationships, including neighbors, common references, and reverse citing-paper lookups. |
| `grados:get_papers_full_context` | Return structured full-context material for a small paper set, with token estimates or actual section content for CAG-style deep reading. |
| `grados:build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. Rows are agent-side reranking material until reread through `grados:read_saved_paper`. |
| `grados:compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. Returned excerpts and anchors guide rereading; they are not citation-ready proof. |
| `grados:audit_draft_support` | Audit draft claims against the local paper library and return first-pass `supported`, `weak`, `unsupported`, or `misattributed` statuses plus candidate evidence snippets and anchors. `candidate_limit` controls how many candidates are retrieved per claim for host-agent reranking. The host agent model must reread canonical paragraph windows before final support judgment. |

There is no separate local RAG server in the Python release. Saved-paper canonical storage and semantic retrieval are built directly into GRaDOS through ChromaDB.

## Host Agent Reasoning Boundary

GRaDOS tools do not call the host agent model. They provide deterministic search, storage, indexing, retrieval anchors, low-token structure cards, and canonical saved-paper reads. The host agent model is responsible for query planning, candidate screening, agent-side reranking, support judgment, and synthesis.

Outputs from `search_saved_papers`, `build_evidence_grid`, `compare_papers`, and `audit_draft_support` are navigation and audit material. They may include `canonical_uri`, `paragraph_start`, and `paragraph_count` so the agent can reread the source, but they are not final citation evidence until `grados:read_saved_paper` returns the canonical paragraph window.

For broad tasks, a host client may use subagents to triage independent paper sets, claim sets, or subquestions. Subagents are not GRaDOS server tools; their output should be limited to candidate anchors, rejected/weak items, gaps, warnings, and exact reread selectors such as `canonical_uri`, `paragraph_start`, and `paragraph_count`. The main host agent must reread accepted anchors through `grados:read_saved_paper` before citation or final support judgment.

## Indepth Mode

`indepth` is disabled by default. Use `search_academic_papers(indepth=true)` or `grados search "query" --indepth` to run one opt-in full-text pass over the returned search candidates. The mode uses the same `limit` as metadata search, writes `research_checkpoint` folders under `GRADOS_HOME/research_checkpoints/`, and can generate reusable query-independent `paper_summary` artifacts under `GRADOS_HOME/paper_summaries/`.

See [indepth.md](indepth.md) for the checkpoint schema, paper-summary invalidation rules, and failure semantics.

## Compression-Safe Evidence Checkpoints

Use `grados:save_research_artifact(kind="evidence_checkpoint")` when evidence has to survive context compression, handoff, or a later drafting pass. This artifact is a recovery and navigation record, not a citation source by itself.

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
  "warnings": ["Known limitations, weak support, or imprecise coordinates."]
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
3. After Chrome downloads the PDF, identify the downloaded `.pdf` path and validate it is a PDF.
4. Call `grados:parse_pdf_file(file_path=..., doi=..., copy_to_library=true, acquisition_via="codex")`.

## MCP Resources

If your client supports resource reading:

| Resource URI | Purpose |
| --- | --- |
| `grados://papers/index` | Lightweight index of saved papers in the canonical local paper store |
| `grados://papers/{safe_doi}` | Low-token overview resource for one saved paper: metadata, preview, section list, and asset counts |

Treat `safe_doi` as an opaque paper ID returned by GRaDOS receipts, search results, or resources. New IDs include a short normalized-DOI hash suffix for collision resistance; legacy pure-slug IDs remain readable, but agents should prefer DOI lookup or returned URIs over guessing IDs from DOI punctuation.
