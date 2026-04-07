# GRaDOS

[English](./README.md) | [简体中文](./README.zh-CN.md)

GRaDOS is a Python MCP server for academic search, full-text extraction, local paper storage, and semantic retrieval over a built-in ChromaDB index.

The Python migration removes the old `mcp-local-rag` and LanceDB split. Future GRaDOS installs use one package, one visible data root, and one local semantic store: ChromaDB.

## Documentation Map

Canonical documents:

- `README.md` / `README.zh-CN.md`: primary user-facing install and usage guides
- `.mcp.json`: repository-local MCP server wiring example
- `skills/grados/SKILL.md`: the structured research workflow built on top of the MCP tools
- `grados-python-implementation-plan.md`: authoritative engineering plan and completion ledger
- `TODO.md`: concise execution snapshot derived from the implementation plan

Retained for local development or historical reference:

- `grados-python-migration-plan.md`: earlier design draft, now folded into the implementation plan
- `status.md`: pre-migration engineering log for Elsevier/browser work
- `docs/global-install-guide.md`: legacy pre-Python operational guide kept for reference only

## What It Does

- Search Crossref, PubMed, Elsevier, Springer, and Web of Science
- Fetch papers through `TDM -> OA -> Sci-Hub -> Browser`
- Parse PDFs with `PyMuPDF -> Marker -> Docling`
- Import existing local PDF folders into the canonical paper store
- Mirror saved papers as Markdown with YAML front-matter
- Search saved papers semantically with ChromaDB
- Navigate papers with low-token structure cards before deep reading
- Run as a single stdio MCP server for Claude, Codex, Cursor, and similar clients

## Install

Recommended:

```bash
uv tool install grados
grados setup --all
```

Other options:

```bash
# Core install
uv tool install grados

# Full install, including heavier PDF parsers
uv tool install "grados[full]"

# Zero-install run
uvx grados version

# Traditional Python install
pip install grados
```

Install extras in the current package:

- `grados`: core MCP server, CLI, ChromaDB storage, default parser, browser automation, and built-in Zotero save support
- `grados[marker]`: core plus the heavier Marker PDF parser
- `grados[docling]`: core plus the heavier Docling PDF parser
- `grados[full]`: core plus both heavier PDF parsers

## Quick Start

1. Install with `uv tool install grados`.
2. Run `grados setup --all`.
3. Edit the generated config file at `~/GRaDOS/config.json`.
4. Run `grados status` to confirm dependencies, browser assets, and API keys.
5. Point your MCP client at `grados` or `uvx grados`.
6. If you already have a PDF library, run `grados import-pdfs --from /path/to/papers --recursive`.

## Commands

| Command | Purpose |
| --- | --- |
| `grados` | Start the MCP stdio server |
| `grados setup --all` | Create directories, write `config.json`, install browser assets, warm models |
| `grados import-pdfs --from /path/to/papers --recursive` | Import an existing local PDF library into the canonical paper store |
| `grados status` | Show config, dependency, runtime-asset, and API-key health |
| `grados paths` | Show the resolved GRaDOS filesystem layout |
| `grados update-db` | Build or refresh the ChromaDB index from `papers/` |
| `grados migrate-config --from /path/to/legacy` | Legacy compatibility helper for TypeScript-era installs |
| `grados version` | Show package versions |

## Filesystem Layout

By default, GRaDOS keeps everything in a visible directory:

```text
~/GRaDOS/
├── config.json
├── papers/
├── downloads/
├── browser/
│   ├── chromium/
│   ├── profile/
│   └── extensions/
├── models/
├── database/
│   └── chroma/
├── logs/
└── cache/
```

Root selection priority:

1. `GRADOS_HOME`
2. `~/GRaDOS`

## Module Layout

The Python mainline is organized into a few clear subsystems:

- `src/grados/server.py`: FastMCP server surface. Exposes 8 tools plus 2 paper resources: `grados://papers/index` and `grados://papers/{safe_doi}`.
- `src/grados/cli.py` and `src/grados/config.py`: CLI commands, config schema, and the visible filesystem layout rooted at `GRADOS_HOME` or `~/GRaDOS`.
- `src/grados/search/`: remote academic search. `academic.py` talks to Crossref / PubMed / Web of Science / Elsevier / Springer, and `resumable.py` handles continuation tokens and deduplication.
- `src/grados/extract/`, `src/grados/browser/`, `src/grados/publisher/`: DOI full-text waterfall. Fetch order is `TDM -> OA -> Sci-Hub -> Headless`; parse order is `PyMuPDF -> Marker -> Docling`; publisher adapters currently cover Elsevier and Springer.
- `src/grados/storage/`: canonical paper persistence. `vector.py` manages the ChromaDB `papers_docs` and `papers_chunks` collections; `papers.py` manages Markdown mirrors, structure cards, deep reading windows, and asset manifests.
- `src/grados/importing.py`: bulk import of local PDF folders into the canonical paper store and retrieval index.
- `src/grados/zotero.py`: optional Zotero export for papers that were actually cited.

## Runtime Components

Core runtime components in the current Python release:

- `FastMCP`: stdio MCP server runtime plus tool / resource registration
- `Click` + `Rich`: CLI entrypoints, setup/status output, and import summaries
- `httpx` + `BeautifulSoup` + `lxml`: API access, HTML parsing, DOI redirects, and OA / Sci-Hub / publisher fallbacks
- `Patchright`: browser automation and PDF capture for difficult publisher pages
- `pymupdf4llm`: default in-process PDF-to-Markdown parser
- `Marker` and `Docling`: optional heavier PDF parsing backends enabled through extras
- `ChromaDB`: the only built-in managed semantic store, used for canonical paper documents and retrieval chunks
- `Zotero Web API integration`: built in through `httpx` when you configure Zotero credentials

## MCP Client Configuration

Claude Code / Claude Desktop:

```json
{
  "mcpServers": {
    "grados": {
      "command": "uvx",
      "args": ["grados"]
    }
  }
}
```

Codex:

```toml
[mcp_servers.grados]
command = "uvx"
args = ["grados"]
```

Use `uvx` when you want zero-install MCP launching. For normal long-lived local use, `uv tool install grados` plus the `grados` executable remains the primary path.

If you want a custom data root, set `GRADOS_HOME` in your MCP client's environment.

## MCP + Skill Layout

This repository keeps a lightweight MCP + skill integration layout instead of a Claude plugin package:

- `.mcp.json` provides a repo-local MCP server example for `grados` plus optional `playwright`
- `skills/grados/SKILL.md` contains the structured academic-research workflow
- `skills/grados/references/tools.md` documents the tool contract used by the skill

The skill assumes the current paper workflow is:

1. Search remotely or locally
2. Inspect a structure card or paper resource
3. Deep-read the canonical saved paper
4. Write with inline citations
5. Re-open cited papers for verification

If you do not use repo-local MCP configs, copy the same `grados` server definition into your client settings and keep the skill file in your agent skill directory.

## Recommended Research Flow

For citation-grounded writing, the intended workflow is:

1. `search_saved_papers` or `search_academic_papers`
2. `get_saved_paper_structure` or `grados://papers/{safe_doi}`
3. `read_saved_paper`
4. Write with inline citations
5. Re-read with `read_saved_paper` to verify every cited claim

## Migrating From The TypeScript Release

This section is for users moving from the legacy Node.js / TypeScript release (`grados-config.json`, `markdown/`, `lancedb/`) to the Python release (`config.json`, `papers/`, built-in ChromaDB).

### What Changed

- Installation moved from `npm` to `uv` / `pip`
- The runtime is now a single Python package
- Local semantic search now uses ChromaDB only
- The default data root is `~/GRaDOS/`
- The primary config file is now `config.json`

Legacy `mcp-local-rag` and LanceDB are no longer part of the recommended setup.

### Recommended Migration Flow

```bash
uv tool install grados
grados migrate-config --from /path/to/legacy
grados status
```

If you want browser assets and warmed models installed immediately:

```bash
grados setup --all
```

### What `grados migrate-config` Does

- Reads a legacy `grados-config.json`
- Writes a Python-style `config.json` into your current GRaDOS home
- Copies saved Markdown papers into `papers/`
- Copies archived PDFs into `downloads/`
- Copies managed browser assets into `browser/`
- Copies model caches into `models/`
- Ignores legacy LanceDB data

The migration is intentionally filesystem-based. It carries forward useful assets, not the old runtime model.

### Path Mapping

| Legacy | Python |
| --- | --- |
| `grados-config.json` | `config.json` |
| `markdown/` | `papers/` |
| `downloads/` | `downloads/` |
| `.grados/browser/` | `browser/` |
| `models/` | `models/` |
| `lancedb/` | removed |

### Config Differences

Important behavior changes:

- `GRADOS_HOME` now chooses the whole data root
- `--config` / `GRADOS_CONFIG_PATH` are part of the legacy model and should be replaced by a stable GRaDOS home
- The parser stack is now `PyMuPDF -> Marker -> Docling`
- Semantic search is now built in via ChromaDB

The migration command converts compatible search, extraction, Zotero, and API-key settings into the new schema automatically.

### If You Still Need The Old Layout

The legacy TypeScript line has been archived separately. Use the main `GRaDOS` repository for the Python release, and use `GRaDOS-legacy` only when you explicitly need the archived TypeScript codebase.

## Development

```bash
uv sync --all-extras
uv run grados version
uv run pytest
uv build
```
