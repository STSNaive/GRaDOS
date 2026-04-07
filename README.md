# GRaDOS

[English](./README.md) | [简体中文](./README.zh-CN.md)

<div align="center">
  <pre style="display:inline-block; margin:0; font-family:'Bitstream Vera Sans Mono', 'SF Mono', Consolas, monospace; font-size:15px; line-height:1.02; font-weight:bold; white-space:pre; text-align:left;">&nbsp;&nbsp;.oooooo.&nbsp;&nbsp;&nbsp;&nbsp;ooooooooo.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;oooooooooo.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;.oooooo.&nbsp;&nbsp;&nbsp;&nbsp;.oooooo..o
&nbsp;d8P'&nbsp;&nbsp;`Y8b&nbsp;&nbsp;&nbsp;`888&nbsp;&nbsp;&nbsp;`Y88.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;`888'&nbsp;&nbsp;&nbsp;`Y8b&nbsp;&nbsp;&nbsp;d8P'&nbsp;&nbsp;`Y8b&nbsp;&nbsp;d8P'&nbsp;&nbsp;&nbsp;&nbsp;`Y8
888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;.d88'&nbsp;&nbsp;.oooo.&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;Y88bo.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888ooo88P'&nbsp;&nbsp;`P&nbsp;&nbsp;)88b&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;`"Y8888o.&nbsp;
888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;ooooo&nbsp;&nbsp;888`88b.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;.oP"888&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;`"Y88b
`88.&nbsp;&nbsp;&nbsp;&nbsp;.88'&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;`88b.&nbsp;&nbsp;d8(&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;d88'&nbsp;`88b&nbsp;&nbsp;&nbsp;&nbsp;d88'&nbsp;oo&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;.d8P
&nbsp;`Y8bood8P'&nbsp;&nbsp;&nbsp;o888o&nbsp;&nbsp;o888o&nbsp;`Y888""8o&nbsp;o888bood8P'&nbsp;&nbsp;&nbsp;&nbsp;`Y8bood8P'&nbsp;&nbsp;8""88888P'&nbsp;</pre>
</div>

<p align="center">
  <strong style="font-size:1.75rem;">Graduate Research and Document Operating System</strong>
</p>

The Python MCP server for academic paper search, full-text extraction, visible local paper storage, and semantic retrieval over a built-in ChromaDB index.

GRaDOS gives AI agents (Claude, Codex, Cursor, and similar clients) a single stdio MCP server that can search academic databases, fetch papers through paywalls, parse PDFs into canonical Markdown, and revisit saved papers for citation-grounded writing.

## Architecture 🧭

GRaDOS is designed to sit inside an agent research workflow:

1. Check the local paper library first with `search_saved_papers`, `get_saved_paper_structure`, or `grados://papers/{safe_doi}`
2. Search remote academic sources in configured priority order
3. Fetch full text through `TDM -> OA -> Sci-Hub -> Headless`
4. Parse PDFs through `PyMuPDF -> Marker -> Docling`
5. Save raw PDFs to `downloads/`, canonical Markdown to `papers/`, and semantic data to ChromaDB
6. Re-open saved papers with low-token structure cards and deep-reading windows before citing them

### MCP Tools 🔧

| Server | Tool | Description |
| --- | --- | --- |
| GRaDOS | `search_academic_papers` | Search Crossref, PubMed, Web of Science, Elsevier, and Springer with DOI deduplication plus resumable continuation tokens. |
| GRaDOS | `search_saved_papers` | Search the canonical saved-paper library backed by ChromaDB. |
| GRaDOS | `extract_paper_full_text` | Fetch a paper by DOI through the full-text waterfall, parse it, and save canonical Markdown plus raw PDF assets. |
| GRaDOS | `read_saved_paper` | Read paragraph windows from a saved paper for synthesis and citation verification. |
| GRaDOS | `get_saved_paper_structure` | Return a low-token structure card with preview text, headings, and asset summary. |
| GRaDOS | `import_local_pdf_library` | Bulk-import an existing PDF folder into the canonical paper store and retrieval index. |
| GRaDOS | `parse_pdf_file` | Parse a local PDF into canonical Markdown, optionally binding it to a DOI. |
| GRaDOS | `save_paper_to_zotero` | Save actually cited papers to Zotero through the Web API. |

### MCP Resources 📚

| Resource | Description |
| --- | --- |
| `grados://papers/index` | Low-token index of all saved papers. |
| `grados://papers/{safe_doi}` | Canonical overview card for one saved paper. |

### Local Paper Library 🗂️

After extraction or import, GRaDOS keeps papers in a visible on-disk layout:

| Directory | Content | Purpose |
| --- | --- | --- |
| `config.json` | Runtime configuration | One config file for the whole install |
| `papers/` | Canonical Markdown papers with YAML front-matter | Deep reading, structure cards, and retrieval |
| `downloads/` | Raw `.pdf` files | Archival copies of fetched or imported papers |
| `database/chroma/` | ChromaDB collections | Built-in semantic retrieval store |
| `browser/` | Managed Chromium, profile, extensions | Browser fallback for difficult publisher pages |
| `models/` | Embedding and OCR model caches | Runtime assets warmed by setup |

### Repository Map 🗺️

- `README.md` / `README.zh-CN.md`: primary installation and usage guides
- `.mcp.json`: repo-local MCP wiring example
- `skills/grados/SKILL.md`: structured research workflow built on top of the MCP tools
- `grados-python-implementation-plan.md`: implementation plan and completion ledger
- `TODO.md`: concise execution snapshot derived from the implementation plan

## Installation 🚀

### Option A: `uv tool install` (recommended)

```bash
uv tool install grados
grados setup --all
```

This creates `~/GRaDOS/config.json`, prepares the visible directory layout, installs managed browser assets, and warms the default embedding model.

### Option B: extras, zero-install, or pip

```bash
# Core install
uv tool install grados

# Install optional parser extras
uv tool install "grados[marker]"
uv tool install "grados[docling]"
uv tool install "grados[full]"

# Zero-install run
uvx grados version

# Traditional Python install
pip install grados
```

Extras in the current package:

- `grados`: core MCP server, CLI, ChromaDB storage, default parser, browser automation, and built-in Zotero save support
- `grados[marker]`: core plus the Marker PDF parser
- `grados[docling]`: core plus the Docling PDF parser
- `grados[full]`: core plus both heavier parsers

### Option C: from source

```bash
git clone https://github.com/STSNaive/GRaDOS.git
cd GRaDOS
uv sync --all-extras
uv run grados setup --all
uv run grados status
```

### Quick Start ⚡

1. Install GRaDOS with `uv tool install grados`
2. Run `grados setup --all`
3. Edit `~/GRaDOS/config.json`
4. Run `grados status` to confirm dependencies, browser assets, and API keys
5. Point your MCP client at `grados` or `uvx grados`
6. If you already have a PDF library, run `grados import-pdfs --from /path/to/papers --recursive`

### Configure your MCP client 🔌

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

Use `uvx` when you want zero-install MCP launching. For long-lived local use, `uv tool install grados` plus the `grados` executable remains the primary path. If you want a custom data root, set `GRADOS_HOME` in your MCP client's environment.

## Configuration ⚙️

### Commands 🧰

| Command | Purpose |
| --- | --- |
| `grados` | Start the MCP stdio server |
| `grados setup --all` | Create directories, write `config.json`, install browser assets, and warm models |
| `grados setup --with browser` | Install only browser runtime assets |
| `grados setup --with models` | Warm only the embedding model |
| `grados import-pdfs --from /path/to/papers --recursive` | Import an existing local PDF library into the canonical paper store |
| `grados status` | Show config, dependency, runtime-asset, and API-key health |
| `grados paths` | Show the resolved GRaDOS filesystem layout |
| `grados update-db` | Build or refresh the ChromaDB index from `papers/` |
| `grados migrate-config --from /path/to/legacy` | Migrate data from an older GRaDOS install |
| `grados version` | Show package versions |

### Filesystem Layout 🗄️

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

### API Keys 🔑

| Key | Source | Required |
| --- | --- | --- |
| `ELSEVIER_API_KEY` | Elsevier Developer Portal | No |
| `WOS_API_KEY` | Clarivate Developer Portal | No |
| `SPRINGER_meta_API_KEY` | Springer Nature Metadata API | No |
| `SPRINGER_OA_API_KEY` | Springer Nature Open Access API | No |
| `LLAMAPARSE_API_KEY` | LlamaCloud | No |
| `ZOTERO_API_KEY` | Zotero Settings -> Keys | No |

Crossref and PubMed require no API keys. GRaDOS will use whichever services are configured and skip the rest. At minimum, the default remote search flow still works with the free sources, and the local paper workflow works without any third-party key.

### Runtime Order 🌊

Search priority:

```json
{
  "search": {
    "order": ["Elsevier", "Springer", "WebOfScience", "Crossref", "PubMed"]
  }
}
```

Full-text fetch priority:

```json
{
  "extract": {
    "fetchStrategy": {
      "order": ["TDM", "OA", "SciHub", "Headless"]
    }
  }
}
```

PDF parsing priority:

```json
{
  "extract": {
    "parsing": {
      "order": ["PyMuPDF", "Marker", "Docling"]
    }
  }
}
```

### Migrating From Older Installs ♻️

If you already have an older GRaDOS data directory, use `grados migrate-config` to carry papers, downloads, browser assets, models, and compatible settings into the current layout.

Recommended migration flow:

```bash
uv tool install grados
grados migrate-config --from /path/to/legacy
grados status
```

What `grados migrate-config` carries forward:

- Saved Markdown papers into `papers/`
- Archived PDFs into `downloads/`
- Managed browser assets into `browser/`
- Model caches into `models/`
- Compatible search, extraction, Zotero, and API-key settings into the new `config.json`

Path mapping:

| Older layout | Current layout |
| --- | --- |
| `grados-config.json` | `config.json` |
| `markdown/` | `papers/` |
| `downloads/` | `downloads/` |
| `.grados/browser/` | `browser/` |
| `models/` | `models/` |

## Development 🛠️

```bash
uv sync --all-extras
uv run grados version
uv run pytest
uv build
```
