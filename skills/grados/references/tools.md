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
| `grados:search_saved_papers` | Compact paper-level search over the saved-paper store in `papers/`. Uses built-in ChromaDB semantic search when an index exists, otherwise falls back to lexical matching. Returns `doi`, `safe_doi`, `canonical_uri`, snippets, and matched sections instead of raw chunks. |
| `grados:extract_paper_full_text` | Fetch full text by DOI via `TDM -> OA -> Sci-Hub -> Headless`, then parse via `PyMuPDF -> Marker -> Docling`. Auto-saves `.md` to `papers/` and indexes it into ChromaDB. Returns a compact, non-citable saved-paper summary rather than the full text. |
| `grados:parse_pdf_file` | Parse a local PDF file using the same Python parsing waterfall. If DOI is provided, saves `.md` to `papers/` with front-matter and returns the same saved-paper summary contract as `extract_paper_full_text`. |
| `grados:read_saved_paper` | Canonical deep-reading tool for previously saved papers. Accepts `doi`, `safe_doi`, or `grados://papers/{safe_doi}` and returns a paragraph window for synthesis and citation verification. |
| `grados:save_paper_to_zotero` | Save cited paper metadata to Zotero. Requires `ZOTERO_API_KEY` and Zotero library configuration. |

There is no separate local RAG server in the Python release. Saved-paper semantic search is built into GRaDOS through ChromaDB.

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
| `grados://about` | Service overview: name, version, capabilities, and tool list |
| `grados://status` | Health check: config loaded, directories exist, API keys configured |
| `grados://tools` | Read-only mirror of tool schemas with parameter details and common failure modes |
| `grados://papers/index` | Lightweight index of saved papers in `papers/` |
| `grados://papers/{safe_doi}` | Canonical full Markdown content for one saved paper |
