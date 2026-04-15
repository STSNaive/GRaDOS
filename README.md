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

Phase A now ships with a stronger local retrieval stack by default: `microsoft/harrier-oss-v1-0.6b`, abstract-first document embeddings, section-aware chunking, and docs → chunks two-stage retrieval.

## Architecture 🧭

GRaDOS is designed to sit inside an agent research workflow:

1. Check the local paper library first with `search_saved_papers`, `get_saved_paper_structure`, or `grados://papers/{safe_doi}`
2. Search remote academic sources in configured priority order
3. Fetch full text through `TDM -> OA -> Sci-Hub -> Headless`
4. Parse PDFs through `Docling -> Marker -> PyMuPDF`
5. Save raw PDFs to `downloads/`, canonical Markdown to `papers/`, and semantic data to ChromaDB
6. Re-open saved papers with low-token structure cards and deep-reading windows before citing them

### MCP Tools 🔧

| Server | Tool | Description |
| --- | --- | --- |
| GRaDOS | `search_academic_papers` | Search remote academic databases for paper metadata only, with DOI deduplication and resumable continuation tokens. Use this to screen candidate DOIs before extraction. |
| GRaDOS | `search_saved_papers` | Search the local saved-paper library with semantic retrieval, metadata filters, and optional lexical reranking. Returned snippets are screening hints, not citation evidence. |
| GRaDOS | `extract_paper_full_text` | Fetch, parse, and save one paper's canonical full text by DOI. Returns a compact save receipt with URI, file path, sections, and warnings rather than the full paper text. |
| GRaDOS | `read_saved_paper` | Read paragraph windows from one saved paper for canonical deep reading and citation verification. Accepts a DOI, safe DOI, or `grados://papers/...` URI. |
| GRaDOS | `get_saved_paper_structure` | Return a low-token structure card for one saved paper with preview text, headings, and asset summary. Use it for screening before deep reading, not as the final citation source. |
| GRaDOS | `import_local_pdf_library` | Import a local PDF file or directory into the canonical paper store and retrieval index. Returns an import summary plus the first 25 item results. |
| GRaDOS | `parse_pdf_file` | Parse a local PDF into markdown. Without a DOI it returns a truncated preview; with a DOI it saves the paper into the canonical library and returns a save receipt. |
| GRaDOS | `save_paper_to_zotero` | Save one paper to the configured Zotero library through the Web API, typically for papers that actually support the final answer. |
| GRaDOS | `save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, and evidence grids in the local SQLite state store. |
| GRaDOS | `query_research_artifacts` | Query previously saved research artifacts by id, kind, project id, or keyword. `detail=true` returns the full stored content. |
| GRaDOS | `manage_failure_cases` | Record, inspect, and summarize failed fetch, parse, search, or citation attempts. Can also suggest conservative retry steps from local failure memory. |
| GRaDOS | `get_citation_graph` | Return lightweight local citation relationships, including citation neighbors, common references, and reverse citing-paper lookups. |
| GRaDOS | `get_papers_full_context` | Return structured full-context material for a small paper set, with token estimates or actual section content for CAG-style deep reading. |
| GRaDOS | `build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. |
| GRaDOS | `compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. |
| GRaDOS | `audit_draft_support` | Audit draft claims against the local paper library and return `supported`, `weak`, `unsupported`, or `misattributed` statuses with candidate evidence. |

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
- `.claude-plugin/`: native Claude Code plugin manifests
- `.agents/plugins/marketplace.json`: repo-scoped Codex marketplace entry
- `plugin.mcp.json`: root plugin-scoped MCP config used by the Claude Code plugin
- `plugins/grados/.codex-plugin/`: self-contained Codex plugin bundle for local marketplace installs
- `plugins/grados/plugin.mcp.json`: plugin-scoped MCP config copied into the Codex bundle
- `skills/grados/SKILL.md`: structured research workflow built on top of the MCP tools
- `grados-python-implementation-plan.md`: implementation plan and completion ledger
- `TODO.md`: concise execution snapshot derived from the implementation plan

## Installation 🚀

### Option A: `uv tool install` (recommended)

```bash
uv tool install grados
grados setup
grados client install all
```

This creates `~/GRaDOS/config.json`, prepares the visible directory layout, installs managed browser assets, and warms the default Harrier embedding runtime. `docling` is now included in the default install because the canonical parsing pipeline is Docling-first.

### Option B: extras, zero-install, or pip

```bash
# Default install (includes Docling)
uv tool install grados

# Optional heavier parser extras
uv tool install "grados[marker]"
uv tool install "grados[full]"

# Zero-install run
uvx grados version

# Traditional Python install
pip install grados
```

Extras in the current package:

- `grados`: core MCP server, CLI, ChromaDB storage, Docling-first default parser, PyMuPDF fallback, browser automation, and built-in Zotero save support
- `grados[marker]`: core plus the Marker PDF parser
- `grados[docling]`: compatibility alias for the built-in Docling runtime
- `grados[full]`: core plus the Marker parser

### Option C: from source

```bash
git clone https://github.com/STSNaive/GRaDOS.git
cd GRaDOS
uv sync --all-extras
uv run grados setup
uv run grados client install all
uv run grados status
```

### Quick Start ⚡

1. Install GRaDOS with `uv tool install grados` (this now includes Docling by default)
2. Run `grados setup`
3. Run `grados client install all` to register Claude Code and Codex in one step
4. Edit `~/GRaDOS/config.json`
5. Run `grados status` to confirm dependencies, browser assets, and API keys
6. If you already have a PDF library, run `grados import-pdfs --from /path/to/papers --recursive`
7. If you are upgrading from an older MiniLM-backed index, run `grados reindex` once before semantic search

### Configure your clients 🔌

Recommended:

```bash
grados client install all
```

This currently installs GRaDOS into both Claude Code and Codex:

- registers the `grados` MCP server through each client's own CLI
- copies the bundled `grados` skill into the user's skills directory

You can also target a single client:

```bash
grados client install claude
grados client install codex
grados client list
grados client doctor
```

### Manual MCP wiring (fallback)

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

Use `uvx` when you want zero-install MCP launching. For long-lived local use, `uv tool install grados` plus the `grados` executable remains the primary path, and now brings Docling with it by default. If you want a custom data root, set `GRADOS_HOME` in your MCP client's environment.

### Native Plugin Install 🧩

GRaDOS now ships native plugin metadata for both Claude Code and Codex. The Codex path follows the current official local marketplace layout: `.agents/plugins/marketplace.json` points at a self-contained bundle under `plugins/grados/`, which mirrors the canonical `skills/grados/` files and includes its own `plugin.mcp.json`.

Claude Code:

```text
/plugin marketplace add STSNaive/GRaDOS
/plugin install grados@grados-plugins
/reload-plugins
```

This uses the repo's `.claude-plugin/marketplace.json` and `.claude-plugin/plugin.json` directly. The plugin bundles the GRaDOS skill plus the `grados` MCP server.

Codex:

1. Clone and open this repository in Codex.
2. Run `/plugins` to open the plugin directory.
3. Choose the `GRaDOS Repository Plugins` marketplace from `.agents/plugins/marketplace.json`.
4. Install the `GRaDOS` plugin from `plugins/grados/.codex-plugin/plugin.json`.
5. Start a new thread and ask Codex to use `@grados`, or describe the research task directly.

This matches the current official Codex flow for custom repo plugins: repo marketplace + plugin directory. Codex does not currently document a public equivalent of Claude Code's `/plugin install owner/repo` workflow for arbitrary GitHub-hosted custom plugins.

### Companion Skill 🤖

GRaDOS still ships a repo-local skill in `skills/grados/`. The `grados client install ...` flow above is now the preferred path for local use. Plugin install remains the alternative when you specifically want the native plugin packaging.

The Codex plugin bundle under `plugins/grados/skills/grados/` is a mirrored copy of the canonical `skills/grados/` directory so the local marketplace install remains self-contained.

- `skills/grados/SKILL.md` contains the current `search -> structure -> deep read -> cite -> verify` workflow
- `skills/grados/references/tools.md` documents the current 16 tools and 2 resources
- `skills/grados/agents/openai.yaml` describes the OpenAI / Codex-facing dependency on the `grados` MCP server

Codex and Claude Code use the same skill directory shape, `<skills-root>/grados/SKILL.md`, with the same supporting files under that directory. Only the skills root differs:

- Codex personal skills: `~/.agents/skills`
- Claude Code personal skills: `~/.claude/skills`
- Claude Code project skills: `.claude/skills`

Install it by copying the **entire** `skills/grados/` directory into the appropriate skills root:

```bash
mkdir -p "<skills-root>"
cp -R skills/grados "<skills-root>/"
```

- For Codex, set `<skills-root>` to `~/.agents/skills`
- For Claude Code personal skills, set `<skills-root>` to `~/.claude/skills`
- For Claude Code project skills, set `<skills-root>` to `.claude/skills`

This fallback assumes the `grados` MCP server is already registered in your client. This repository's `.mcp.json` is the minimal repo-local example; after copying the skill, reload your client so it can discover the new skill files.

## Configuration ⚙️

### Commands 🧰

| Command | Purpose |
| --- | --- |
| `grados` | Start the MCP stdio server |
| `grados setup` | Create directories, write `config.json`, install browser assets, and warm models |
| `grados client install claude` | Register GRaDOS in Claude Code and install bundled skills into `~/.claude/skills` |
| `grados client install codex` | Register GRaDOS in Codex and install bundled skills into `~/.agents/skills` |
| `grados client install all` | Install GRaDOS into both Claude Code and Codex |
| `grados client list` | Show which supported clients currently have GRaDOS installed |
| `grados client doctor` | Run a lightweight health check for supported clients |
| `grados client remove claude|codex|all` | Remove GRaDOS MCP wiring and bundled skills from one or more clients |
| `grados import-pdfs --from /path/to/papers --recursive` | Import an existing local PDF library into the canonical paper store |
| `grados status` | Show config, dependency, runtime-asset, and API-key health |
| `grados paths` | Show the resolved GRaDOS filesystem layout |
| `grados update-db` | Incrementally refresh the ChromaDB index from `papers/` when the active indexing config is unchanged |
| `grados reindex` | Rebuild the semantic index from scratch after embedding-model or chunking changes |
| `grados migrate-config --from /path/to/legacy` | Migrate data from an older GRaDOS install |
| `grados version` | Show package versions |

If you change `indexing.model_id`, `indexing.max_length`, or the section-aware chunking settings in `config.json`, use `grados reindex` instead of `grados update-db`.

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
      "order": ["Docling", "Marker", "PyMuPDF"]
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

## Project Docs 📚

- [TODO.md](./TODO.md)
  - Tracks only unfinished work and current priorities.
- [ADR.md](./ADR.md)
  - Records accepted architectural decisions and why the project chose them.
- [CHANGELOG.md](./CHANGELOG.md)
  - Records completed, user-visible changes across releases and unreleased work.
