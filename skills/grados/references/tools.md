# GRaDOS Tool Reference

## Contents
- [GRaDOS Server Tools](#grados-server-tools)
- [Local RAG Server Tools](#local-rag-server-tools)
- [MCP Resources](#mcp-resources)

---

## GRaDOS Server Tools

| Tool | Purpose |
|---|---|
| `grados:search_academic_papers` | Waterfall search across academic databases (Crossref, PubMed, Web of Science, Elsevier, Springer). Returns deduplicated paper metadata with DOIs and abstracts. |
| `grados:extract_paper_full_text` | Fetch full-text paper by DOI via TDM -> OA -> Sci-Hub -> Headless waterfall, then parse PDF via LlamaParse -> Marker -> Native. Auto-saves `.md` to `papers/` directory. **Returns a compact, non-citable saved-paper summary** (title, DOI, canonical path/URI, short preview, section headings), not the full text. |
| `grados:parse_pdf_file` | Parse a local PDF file using the configured parsing waterfall (LlamaParse -> Marker -> Native). Use when you have downloaded a PDF via browser automation (e.g., Playwright MCP) and need to extract text. If DOI is provided, saves `.md` to `papers/` with front-matter and returns the same saved-paper summary contract as `extract_paper_full_text`. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved paper Markdown in `papers/`. Accepts `doi`, `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window for synthesis/citation verification. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero web library. Requires ZOTERO_API_KEY and zotero.libraryId in config. |

## Local RAG Server Tools

> If `local-rag` tools are not available (mcp-local-rag not installed), skip all local library steps and proceed directly to remote search.

| Tool | Purpose |
|---|---|
| `local-rag:query_documents` | Semantic + keyword search over locally indexed papers. Returns relevant text chunks. |
| `local-rag:ingest_file` | Index a Markdown paper file into the local LanceDB vector store for future retrieval. |
| `local-rag:list_files` | List all indexed papers with their ingestion status. |
| `local-rag:delete_file` | Remove stale indexed entries when you need to clean up the local paper library. |
| `local-rag:status` | Check local RAG database health and configuration when query/ingest behavior looks wrong. |

## Playwright MCP Tools (Optional Browser Fallback)

> If Playwright MCP (`@playwright/mcp`) is registered, the agent can use these tools when `extract_paper_full_text` fails. See Step 3b in SKILL.md.

| Tool | Purpose |
|---|---|
| `playwright:browser_navigate` | Navigate to a URL (e.g., `https://doi.org/{doi}`) |
| `playwright:browser_snapshot` | Get accessibility tree of the current page — use to identify download buttons |
| `playwright:browser_click` | Click an element identified from the snapshot |
| `playwright:browser_take_screenshot` | Take a screenshot — useful for diagnosing CAPTCHA or anti-bot pages |

Workflow: `browser_navigate` → `browser_snapshot` → `browser_click` (PDF link) → download completes → `grados:parse_pdf_file` on downloaded file.

## MCP Resources

If your client supports resource reading (Claude Code `@` mentions, Codex resource wrappers):

| Resource URI | Purpose |
|---|---|
| `grados://about` | Service overview: name, version, capabilities, and tool list |
| `grados://status` | Health check: config loaded, directories exist, API keys configured |
| `grados://tools` | Read-only mirror of tool schemas with parameter details and common failure modes |
| `grados://papers/index` | Lightweight index of saved papers in the configured `papers/` directory |
| `grados://papers/{safe_doi}` | Canonical full Markdown content for one saved paper |
