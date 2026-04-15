# GRaDOS Tool Reference

## Contents

- [GRaDOS Server Tools](#grados-server-tools)
- [Optional Playwright MCP Tools](#optional-playwright-mcp-tools)
- [MCP Resources](#mcp-resources)

---

## GRaDOS Server Tools

| Tool | Purpose |
| --- | --- |
| `grados:search_academic_papers` | Waterfall search across academic databases. Returns deduplicated paper metadata with DOIs and abstracts. |
| `grados:search_saved_papers` | Compact paper-level search over the saved-paper store. Uses metadata prefiltering, ChromaDB chunk retrieval, and optional lightweight lexical reranking over canonical documents. Treat snippets as screening hints, not citation evidence. |
| `grados:get_saved_paper_structure` | Deterministic low-token paper card for one saved paper. Returns canonical URI, preview excerpt, section headings, section outline, counts, and asset summary. Use this before deep reading. |
| `grados:extract_paper_full_text` | Fetch full text by DOI via `TDM -> OA -> Sci-Hub -> Headless`, then parse via `PyMuPDF -> Marker -> Docling`. Auto-saves to the canonical paper store, mirrors Markdown to `papers/`, and indexes into ChromaDB. Returns a compact, non-citable receipt rather than the full text. |
| `grados:import_local_pdf_library` | Import one local PDF file or a directory of PDFs into the canonical paper store. Supports recursive scanning, glob filtering, and optional raw-PDF archiving into `downloads/`. |
| `grados:parse_pdf_file` | Parse a local PDF file using the same Python parsing waterfall. If DOI is provided, it writes the canonical paper entry, mirrors `.md` to `papers/`, and returns a compact save receipt. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved papers. Accepts `doi`, `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window for synthesis and citation verification. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero. Requires `ZOTERO_API_KEY` and Zotero library configuration. |
| `grados:save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, and evidence grids in the local SQLite state store. |
| `grados:query_research_artifacts` | Query previously saved research artifacts by id, kind, project id, or keyword. |
| `grados:manage_failure_cases` | Record, query, and summarize failed fetch/parse/search/citation attempts. Can also suggest conservative retry steps. |
| `grados:get_citation_graph` | Return lightweight local citation relationships, including neighbors, common references, and reverse citing-paper lookups. |
| `grados:get_papers_full_context` | Return structured full-context material for a small paper set, with token estimates or actual section content for CAG-style deep reading. |
| `grados:build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. |
| `grados:compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. |
| `grados:audit_draft_support` | Audit draft claims against the local paper library and return `supported`, `weak`, `unsupported`, or `misattributed` statuses. |

There is no separate local RAG server in the Python release. Saved-paper canonical storage and semantic retrieval are built directly into GRaDOS through ChromaDB.

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
