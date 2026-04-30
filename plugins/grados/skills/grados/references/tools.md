# GRaDOS Tool Reference

## Contents

- [GRaDOS Server Tools](#grados-server-tools)
- [Indepth Mode](#indepth-mode)
- [Optional Playwright MCP Tools](#optional-playwright-mcp-tools)
- [MCP Resources](#mcp-resources)

---

## GRaDOS Server Tools

| Tool | Purpose |
| --- | --- |
| `grados:search_academic_papers` | Waterfall search across academic databases. Returns deduplicated paper metadata with DOIs, abstracts, continuation state, and local saved/full-text/summary state. Optional `indepth=true` materializes returned candidates with the same `limit`. |
| `grados:search_saved_papers` | Compact paper-level search over the saved-paper store. Uses metadata prefiltering, ChromaDB chunk retrieval, and optional lightweight lexical reranking over canonical documents. Treat snippets as screening hints, not citation evidence. |
| `grados:get_saved_paper_structure` | Deterministic low-token paper card for one saved paper. Returns canonical URI, preview excerpt, section headings, section outline, counts, and asset summary. Use this before deep reading. |
| `grados:extract_paper_full_text` | Fetch full text by DOI via `api -> browser -> oa -> scihub`, then parse via `Docling -> Marker -> PyMuPDF`. Auto-saves to the canonical paper store, mirrors Markdown to `papers/`, and indexes into ChromaDB. Returns a compact, non-citable receipt rather than the full text. |
| `grados:import_local_pdf_library` | Import one local PDF file or a directory of PDFs into the canonical paper store. Supports recursive scanning, glob filtering, and optional raw-PDF archiving into `downloads/`. |
| `grados:parse_pdf_file` | Parse a local PDF file using the same Python parsing waterfall. If DOI is provided, it writes the canonical paper entry, mirrors `.md` to `papers/`, and returns a compact save receipt. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved papers. Accepts `doi`, `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window for synthesis and citation verification. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero. Requires `ZOTERO_API_KEY` and Zotero library configuration. |
| `grados:save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, evidence grids, and compression-safe evidence checkpoints in the local SQLite state store. |
| `grados:query_research_artifacts` | Query previously saved research artifacts by id, kind, or keyword. Use `detail=true` to restore full JSON or Markdown content. |
| `grados:manage_failure_cases` | Record, query, and summarize failed fetch/parse/search/citation attempts. Can also suggest conservative retry steps. |
| `grados:get_citation_graph` | Return lightweight local citation relationships, including neighbors, common references, and reverse citing-paper lookups. |
| `grados:get_papers_full_context` | Return structured full-context material for a small paper set, with token estimates or actual section content for CAG-style deep reading. |
| `grados:build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. |
| `grados:compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. |
| `grados:audit_draft_support` | Audit draft claims against the local paper library and return `supported`, `weak`, `unsupported`, or `misattributed` statuses. |

There is no separate local RAG server in the Python release. Saved-paper canonical storage and semantic retrieval are built directly into GRaDOS through ChromaDB.

When `extract_paper_full_text` returns a browser `challenge`, complete publisher verification in the managed browser profile and call the tool again with `resume_browser=true`. GRaDOS resumes at the browser strategy from the saved URL/profile when available, instead of restarting at the `api` strategy.

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
      "safe_doi": "10_xxxx_example",
      "canonical_uri": "grados://papers/10_xxxx_example",
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

## Optional Playwright MCP Tools

If Playwright MCP is registered, the agent can use it when `extract_paper_full_text` fails on a publisher page.

| Tool | Purpose |
| --- | --- |
| `playwright:browser_navigate` | Navigate to a URL such as `https://doi.org/{doi}` |
| `playwright:browser_snapshot` | Inspect page structure to find a PDF entrypoint |
| `playwright:browser_click` | Click the selected download element |
| `playwright:browser_take_screenshot` | Capture the page when CAPTCHA or anti-bot behavior needs diagnosis |

Typical fallback flow:

`browser_navigate` -> `browser_snapshot` -> `browser_click` -> download completes -> `grados:parse_pdf_file`

## MCP Resources

If your client supports resource reading:

| Resource URI | Purpose |
| --- | --- |
| `grados://papers/index` | Lightweight index of saved papers in the canonical local paper store |
| `grados://papers/{safe_doi}` | Low-token overview resource for one saved paper: metadata, preview, section list, and asset counts |
