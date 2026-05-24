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

The enrichment-grade MCP server for academic paper workflows. For science.

GRaDOS gives AI agents (Claude, Codex, Cursor, and similar clients) a single stdio MCP server that can search academic databases, fetch papers through paywalls, parse PDFs into canonical Markdown, and revisit saved papers for citation-grounded writing.

## Architecture 🧭

GRaDOS is designed to sit inside an agent research workflow:

1. Check the local paper library first with `search_saved_papers`, `get_saved_paper_structure`, or `grados://papers/{safe_doi}`
2. Search remote academic sources in configured priority order
3. Resolve optional Unpaywall OA locations, then fetch full text through the configured `api`, `browser`, optional `codex`, and `scihub` routes
4. Parse PDFs through `Docling -> MinerU -> PyMuPDF` by default
5. Save raw PDFs to `downloads/`, canonical Markdown to `papers/`, parser provenance sidecars to `papers/_parsed/`, parser assets to `papers/_assets/`, semantic search to `database/chroma/`, lexical FTS fallback to `database/fts.sqlite3`, and remote metadata to `database/remote_metadata/`
6. Re-open saved papers with low-token structure cards and deep-reading windows before citing them

Host agents may use their own reasoning model to plan queries, screen candidates, rerank anchors, judge support, and synthesize prose. GRaDOS does not call that model directly: snippets, scores, evidence grids, comparisons, and audits are navigation material until the agent rereads the canonical paragraph window with `read_saved_paper`.

For handoff-safe citation work, `prepare_evidence_pack` materializes canonical blocks from `papers/*.md` into a persisted pack. A pack becomes current citation evidence only when `verify_evidence_pack` reports `current_valid=true`; strict pack audits never search the whole library to silently patch missing evidence.

When external synthesis is enabled, GRaDOS can turn a current-valid evidence pack into a compact host-side ChatGPT Pro packet, save the returned advisory response, and audit it back against the saved packet when linked, otherwise the source pack. The Pro response remains recovery/review material until accepted claims are reread through canonical GRaDOS paragraph windows.

For run-level recovery, a `research_run_manifest` is a lightweight directory page for one research run. It can link search queries, candidates, extraction/parser receipts, `paper_summary`, `research_checkpoint`, `evidence_checkpoint`, `evidence_pack`, audit result IDs, canonical anchors, and failure records. It may keep an append-only event ledger plus a redacted config/provenance snapshot; append correction events instead of rewriting past events, and never store secrets. The run manifest is navigation/provenance only and must never replace canonical rereading of `papers/*.md` or current-valid evidence packs for final citation support.

For evidence-grounded writing, the bundled skill includes `references/paper_writing.md` as the workflow router. It points host agents to task-specific profiles for experiment/simulation protocols, literature reviews, experiment reports, and manuscripts, plus a mechanics/elastic-metamaterials domain profile. These profiles guide planning, claim matrices, section gates, and delivery checks; they do not create a second evidence source or a separate MCP runtime.

### MCP Tools 🔧

| Server | Tool | Description |
| --- | --- | --- |
| GRaDOS | `search_academic_papers` | Search remote academic databases for paper metadata, DOI deduplication, resumable continuation tokens, and local saved/full-text/summary state. Optional `indepth=true` materializes returned candidates with the same `limit`; default config is off. |
| GRaDOS | `search_saved_papers` | Search the local saved-paper library with semantic retrieval, SQLite FTS/BM25 fallback, exact lookup, metadata filters, and hybrid RRF. Returned snippets and Evidence Anchor JSON blocks are screening/reranking material, not citation evidence. |
| GRaDOS | `extract_paper_full_text` | Fetch, parse, and save one paper's canonical full text by DOI. If the DOI is already saved, default `force_refresh=false` returns an already-saved receipt; set `force_refresh=true` to refetch/reparse. |
| GRaDOS | `read_saved_paper` | Read paragraph windows from one saved paper for canonical deep reading and citation verification. Accepts a DOI, safe DOI, or `grados://papers/...` URI. |
| GRaDOS | `get_saved_paper_structure` | Return a low-token structure card for one saved paper with preview text, headings, asset summary, and parser provenance summary when available. Use it for screening before deep reading, not as the final citation source. |
| GRaDOS | `read_paper_asset` | List or read parser-generated figures, tables, formulas, page images, and debug/source assets for a saved paper. Images are returned inline only on request and within configured size limits. |
| GRaDOS | `import_local_pdf_library` | Import a local PDF file or directory into the canonical paper store and retrieval index. Returns an import summary plus the first 25 item results. |
| GRaDOS | `parse_pdf_file` | Parse a local PDF into markdown. Without a DOI it returns a truncated preview; with a DOI it saves the paper into the canonical library and materializes the managed PDF when `copy_to_library=true`. |
| GRaDOS | `ingest_codex_downloaded_pdf` | Complete a `codex` Chrome-extension handoff by validating either `downloaded_file_path` or one scanned watch-dir candidate, then reuse the same canonical parse/save path. Ambiguous, missing, or invalid candidates are recorded as recoverable failures. |
| GRaDOS | `plan_library_pdf_cleanup` | Dry-run duplicate PDF cleanup under `downloads/`, reporting noncanonical publisher-name PDFs that have the same hash as a DOI's managed `downloads/{safe_doi}.pdf`. It never deletes files. |
| GRaDOS | `save_paper_to_zotero` | Save one paper to the configured Zotero library through the Web API, typically for papers that actually support the final answer. |
| GRaDOS | `save_research_artifact` | Persist reusable intermediate outputs such as search snapshots, extraction receipts, evidence grids, compression-safe evidence checkpoints, and run-linked artifacts in the local SQLite state store. Include `metadata.research_run_id` to attach an artifact to a run manifest. |
| GRaDOS | `query_research_artifacts` | Query previously saved research artifacts by id, kind, or keyword. `detail=true` returns the full stored content. |
| GRaDOS | `prepare_evidence_pack` | Retrieve candidate anchors, reread canonical blocks from `papers/*.md`, and persist a minimal `evidence_pack` artifact with pack hash, block hashes, and answerability status. |
| GRaDOS | `read_evidence_pack` | Restore a persisted evidence pack by pack id or artifact id. |
| GRaDOS | `verify_evidence_pack` | Rebuild canonical block manifests from current `papers/*.md` and report snapshot/current validity, missing papers, document changes, relocation, and hash mismatches. |
| GRaDOS | `preview_external_synthesis_packet` | Dry-run a compact external-synthesis packet from one current-valid evidence pack without saving artifacts or contacting external services. |
| GRaDOS | `prepare_external_synthesis_packet` | Persist an `external_synthesis_packet` artifact with verified anchor ids, canonical paragraph coordinates, excerpts, candidate claims, limitations, and prompt hash, returning the host prompt as a regenerable view. |
| GRaDOS | `prepare_external_synthesis_from_topic` | Prepare a fresh evidence pack from a topic and persist a verified external-synthesis packet in one route, returning both pack and packet ids plus the host prompt. |
| GRaDOS | `run_external_synthesis` | Run the default GRaDOS-native ChatGPT Pro browser route: prepare or verify a packet, use the private ChatGPT profile, confirm GRaDOS-validated Pro model and Pro Extended thinking route, capture the advisory response, save it, and audit it before canonical reread. |
| GRaDOS | `save_external_synthesis_result` | Save a host-provided ChatGPT Pro response as advisory `external_synthesis_result` state linked to its source pack, optional packet, prompt hash, and session metadata. Defaults to `audit=true`. |
| GRaDOS | `audit_external_synthesis_result` | Audit a saved external synthesis result against its linked packet when available, otherwise its source pack, using structured `claims[].anchor_ids` as the primary handoff contract while still reporting prose risks. |
| GRaDOS | `audit_answer_against_pack` | Audit draft claims using only evidence items inside one verified pack. It returns `verified`, `minor_distortion`, `major_distortion`, `unverifiable`, or `unverifiable_access` verdicts and does not search the full library to fill gaps. Optional `include_suggestions=true` attaches follow-up planning. |
| GRaDOS | `suggest_missing_evidence` | Suggest follow-up evidence or revision work for non-verified pack-audit claims without changing strict audit results. |
| GRaDOS | `manage_failure_cases` | Record, inspect, and summarize failed fetch, parse, search, or citation attempts. Can also suggest conservative retry steps from local failure memory. |
| GRaDOS | `get_citation_graph` | Return lightweight local citation relationships, including citation neighbors, common references, and reverse citing-paper lookups. |
| GRaDOS | `get_papers_full_context` | Return structured full-context material for context-budgeted saved-paper batches, with token estimates or actual section content for CAG-style deep reading. |
| GRaDOS | `build_evidence_grid` | Build topic- or subquestion-centered evidence grids from the local paper library before drafting. Rows carry reread anchors for agent-side reranking before citation verification. |
| GRaDOS | `compare_papers` | Extract aligned comparison material across multiple saved papers, focused on methods, results, or full text. Returned excerpts carry per-axis reread anchors. |
| GRaDOS | `audit_draft_support` | Audit draft claims against the local paper library and return first-pass `verified`, `minor_distortion`, `major_distortion`, `unverifiable`, or `unverifiable_access` verdicts with candidate evidence snippets, issue types, revision actions, and anchors. `candidate_limit` controls candidates per claim. |

### MCP Resources 📚

| Resource | Description |
| --- | --- |
| `grados://papers/index` | Low-token index of all saved papers. |
| `grados://papers/{safe_doi}` | Canonical overview card for one saved paper. |

`safe_doi` is an opaque GRaDOS paper ID returned by save receipts, search results, or resource URIs. New saves include a short normalized-DOI hash suffix to avoid filename collisions; older IDs such as `10_1234_demo` still resolve. Prefer passing the DOI itself or the returned URI instead of deriving a paper ID by replacing DOI punctuation.

### Local Paper Library 🗂️

After extraction or import, GRaDOS keeps papers in a visible on-disk layout:

| Directory | Content | Purpose |
| --- | --- | --- |
| `config.json` | Runtime configuration | One config file for the whole install |
| `papers/` | Canonical Markdown papers with YAML front-matter | Deep reading, structure cards, and retrieval |
| `papers/_parsed/` | Parser provenance sidecars keyed by safe DOI | PDF/parser provenance, source/canonical hashes, block mapping, and asset manifest pointers; not citation content |
| `papers/_assets/` | Parser-generated assets and manifests | Figures, tables, formulas, page images, and source/debug assets fetched with `read_paper_asset`; not indexed as text |
| `downloads/` | Raw `.pdf` files | Archival copies of fetched or imported papers |
| `database/chroma/` | ChromaDB collections | Built-in semantic retrieval store |
| `database/fts.sqlite3` | Rebuildable SQLite FTS5/BM25 index | Deterministic lexical fallback and hybrid retrieval candidate generation |
| `database/remote_metadata/` | ChromaDB collection | Remote paper metadata, fetch status, and browser-resume cache |
| `database/research.sqlite3` | Research artifacts and failure memory | Evidence packs, run manifests, checkpoints, extraction receipts, and recoverable failure records |
| `research_checkpoints/` | `checkpoint.json` and rendered `checkpoint.md` files | Recoverable indepth research workflow state |
| `paper_summaries/` | Query-independent derived paper summaries | Navigation and context recovery, never citation evidence |
| `browser/` | Managed Chromium, publisher/ChatGPT profiles, session records | Browser strategy assets for publisher PDF access and gated ChatGPT external synthesis |
| `models/` | Embedding and OCR model caches | Runtime assets warmed by setup |

### Repository Map 🗺️

- `README.md` / `README.zh-CN.md`: primary installation and usage guides
- `.mcp.json`: repo-local MCP wiring example
- `.claude-plugin/`: native Claude Code plugin manifests
- `.agents/plugins/marketplace.json`: repo-hosted Codex marketplace manifest
- `plugin.mcp.json`: root plugin-scoped MCP config used by the Claude Code plugin
- `plugins/grados/.codex-plugin/`: self-contained Codex plugin bundle used by the marketplace
- `plugins/grados/plugin.mcp.json`: plugin-scoped MCP config copied into the Codex bundle
- `skills/grados/SKILL.md`: structured research workflow built on top of the MCP tools
- `skills/grados/references/paper_writing.md`: evidence-grounded writing workflow router
- `skills/grados/references/writing_profiles/`: task profiles for protocols, reviews, reports, and manuscripts
- `skills/grados/references/domain_profiles/`: domain-specific writing guardrails, currently including mechanics and elastic metamaterials

## Installation 🚀

### Option A: `uv tool install` (recommended)

```bash
uv tool install grados
grados setup
grados client install all
```

This creates `~/GRaDOS/config.json`, prepares the visible directory layout, installs managed browser assets, and warms the default Harrier embedding runtime. `docling` is now included in the default install because the canonical parsing pipeline is Docling-first. MinerU is an optional authenticated cloud parser in the same waterfall; it runs only when `MINERU_API_KEY` is configured.
Use `grados auth set <provider>` to store API keys in the OS keychain. Plaintext keys placed in `config.json` are treated as a one-time import path and are cleared after a successful migration.

### Option B: extras, zero-install, or pip

```bash
# Default install (includes Docling)
uv tool install grados

# Zero-install run
uvx grados version

# Traditional Python install
pip install grados
```

Extras in the current package:

- `grados`: core MCP server, CLI, ChromaDB storage, Docling-first parser, optional MinerU cloud fallback, PyMuPDF fallback, browser automation, and built-in Zotero save support
- `grados[docling]`: compatibility alias for the built-in Docling runtime
- `grados[marker]`: compatibility alias only; Marker is no longer bundled because the current `marker-pdf` release pins vulnerable parser dependencies
- `grados[full]`: compatibility alias only

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
4. Run `grados auth set elsevier` (and any other providers you need)
5. Run `grados status` to confirm dependencies, browser assets, keychain health, and API-key sources
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

GRaDOS now ships native plugins for Codex and Claude Code.

Claude Code:

```text
/plugin marketplace add STSNaive/GRaDOS
/plugin install grados@grados-plugins
/reload-plugins
```

Codex:

```text
codex plugin marketplace add STSNaive/GRaDOS
codex
/plugins
```

Then choose the `GRaDOS Plugins` marketplace, install the `GRaDOS` plugin, and start a new thread. You can call `@grados` explicitly or just describe the research task directly.

### Companion Skill 🤖

GRaDOS still ships a repo-local skill in `skills/grados/`. The `grados client install ...` flow above is now the preferred path for local use. Plugin install remains the alternative when you specifically want the native plugin packaging.

- `skills/grados/SKILL.md` contains the current `search -> structure -> deep read -> cite -> verify` workflow
- `skills/grados/references/tools.md` documents the current MCP tools and 2 resources
- `skills/grados/references/paper_writing.md` routes evidence-grounded writing tasks to focused profiles for protocols, reviews, reports, and manuscripts
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

Keep [grados-config.example.json](./grados-config.example.json) as the commented reference; edits take effect on the next CLI run or MCP server restart.

### Research Workflow Knobs

- `research.indepth`: disabled by default; controls whether remote search immediately materializes returned candidates for checkpointed full-text review.
- `research.external_synthesis`: disabled by default; a GRaDOS-native ChatGPT Pro browser reviewer/synthesizer with only `enabled`. Gate automation with `grados external-synthesis is-enabled --quiet`; inspect details with `grados external-synthesis status --json`; initialize the private profile with `grados external-synthesis setup-browser`. When enabled, GRaDOS can prepare verified external-synthesis packets, use its private ChatGPT browser profile, save returned advisory responses, and audit them against the linked packet or source pack. When this is off, GRaDOS does not call ChatGPT, open Chrome, or change evidence reading.

### Timeout / Retry Knobs

- `search`: `connect_timeout`, `read_timeout`
- `extract`: `fetch_connect_timeout`, `fetch_read_timeout`, `pdf_read_timeout`
- `extract.headless_browser`: legacy-named config section for the `browser` strategy (`deadline_seconds`, `networkidle_timeout`, `pdf_backfill_timeout`, `poll_min_seconds`, `poll_max_seconds`)
- `extract.codex_handoff`: watch-dir ingest controls used only after a `codex` Chrome-extension handoff (`download_watch_dir`, `download_max_age_seconds`, `download_settle_seconds`, `download_settle_max_wait_seconds`, `download_scan_recursive`)
- `retry_policy`: `max_attempts`, `max_wait`, `respect_retry_after`

### Size Guards

- `extract.security`: byte ceilings for remote PDFs, remote text/XML/HTML responses, local PDFs, browser PDF captures, MinerU result zips, and MinerU `full.md`. Defaults are intentionally generous for normal paper PDFs; raise them only for trusted oversized inputs.
- `extract.assets`: controls parser asset bundles under `papers/_assets/{safe_doi}/` (`mode=all|referenced|none`), Docling image scale, per-file/total asset size ceilings, inline image ceiling, and max asset count. Asset bytes are stored beside canonical Markdown and are fetched with `read_paper_asset`, not indexed into Chroma.

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
| `grados auth set/status/migrate/clear` | Manage provider API keys in the OS keychain |
| `grados browser status --json` | Inspect the publisher PDF browser runtime, managed executable, profile status, lock, and session directory |
| `grados browser doctor [--live --doi DOI]` | Check publisher browser prerequisites; `--live` runs a PDF-acquisition probe without saving `papers/*.md` |
| `grados external-synthesis is-enabled --quiet` | Predicate gate for the optional external synthesis protocol; exit 0 means enabled, exit 1 means disabled |
| `grados external-synthesis status --json` | Show the same external synthesis gate plus config path details as structured diagnostics; profile initialization means Chrome profile markers only, not ChatGPT login readiness |
| `grados external-synthesis setup-browser [--keep-open]` | Open the private GRaDOS ChatGPT profile for first-time ChatGPT login; closes after stable login detection by default, while `--keep-open` keeps the command and profile lock alive until the setup browser closes |
| `grados external-synthesis doctor [--live]` | Check external synthesis browser prerequisites; `--live` also probes ChatGPT login |
| `grados import-pdfs --from /path/to/papers --recursive` | Import an existing local PDF library into the canonical paper store |
| `grados eval-retrieval --fixture cases.jsonl` | Evaluate saved-paper retrieval against local golden cases using dense, FTS/BM25, exact lookup, and RRF unless `--dense-only` is set |
| `grados status` | Show config, dependency, runtime-asset, and API-key health |
| `grados paths` | Show the resolved GRaDOS filesystem layout |
| `grados update-db` | Incrementally refresh the ChromaDB index from `papers/` when the active indexing config is unchanged |
| `grados reindex` | Rebuild the semantic index from scratch after embedding-model or chunking changes |
| `grados version` | Show package versions |

If you change `indexing.model_id`, `indexing.max_length`, or the section-aware chunking settings in `config.json`, use `grados reindex` instead of `grados update-db`.

Changing only `indexing.batch_size` is a runtime-only tuning knob and does not require a rebuild.

### Indexing Defaults 🧠

- Default model: `microsoft/harrier-oss-v1-270m`
- Heavier opt-in model: `microsoft/harrier-oss-v1-0.6b`
- Default `indexing.max_length`: `4096`
- Default `indexing.batch_size`: `0` (`auto`, conservative on CPU/MPS and wider on CUDA)
- Overlong single paragraphs are re-split by sentence or clause before embedding so `grados reindex` does not send giant chunks into `SentenceTransformer.encode()`

GRaDOS does not assume FlashAttention is available on local macOS / CPU setups. If your runtime says it can use SDPA, that still does not guarantee a fused CUDA FlashAttention path; the safer default is smaller chunks, a shorter indexing length, and conservative batching.

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
│   ├── pdf-sessions/
│   ├── chatgpt-profile/
│   ├── chatgpt-sessions/
│   └── extensions/
├── models/
├── database/
│   ├── chroma/
│   └── remote_metadata/
├── logs/
└── cache/
```

Root selection priority:

1. `GRADOS_HOME`
2. `~/GRaDOS`

Local PDF tools such as `parse_pdf_file`, `ingest_codex_downloaded_pdf(downloaded_file_path=...)`, and `import_local_pdf_library` read host file paths from a trusted local MCP/CLI session and enforce `extract.security.max_local_pdf_bytes` before and while loading the file.

### API Keys 🔑

| Key | Source | Required |
| --- | --- | --- |
| `ELSEVIER_API_KEY` | Elsevier Developer Portal | No |
| `PUBMED_API_KEY` | NCBI E-utilities API key | No |
| `WOS_API_KEY` | Clarivate Developer Portal | No |
| `SPRINGER_meta_API_KEY` | Springer Nature Metadata API | No |
| `SPRINGER_OA_API_KEY` | Springer Nature Open Access API | No |
| `MINERU_API_KEY` | MinerU API token | No |
| `ZOTERO_API_KEY` | Zotero Settings -> Keys | No |

Crossref works without an API key. PubMed also works without one, but `PUBMED_API_KEY` is available as an optional pacing upgrade for E-utilities. GRaDOS will use whichever services are configured and skip the rest; the default remote search flow still works with the free sources, and the local paper workflow works without any third-party key.

The preferred path is `grados auth set <provider>`, which stores the secret in the OS keychain. If you temporarily place a plaintext key in `~/GRaDOS/config.json`, GRaDOS will import it into the keychain on the next run and then clear the plaintext value from the file.

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
    "fetch_strategy": {
      "order": ["api", "browser", "codex", "scihub"],
      "enabled": {
        "api": true,
        "browser": true,
        "codex": false,
        "scihub": true
      }
    },
    "unpaywall": {
      "enabled": true
    }
  }
}
```

Unpaywall is an optional DOI-to-OA-location resolver, not a download strategy. When `extract.unpaywall.enabled=true`, GRaDOS resolves `best_oa_location` / `oa_locations` before `codex` or `browser` runs and uses the best `url_for_pdf` or `url_for_landing_page` as that route's start URL. It does not affect the `api` or `scihub` routes. Legacy `oa` entries left in old `fetch_strategy.order` or `enabled` maps are ignored.

Legacy fetch-strategy aliases such as `TDM`, `SciHub`, and `Headless` are still accepted while existing configs migrate. The current `scihub` runtime uses `extract.sci_hub.endpoints` as an ordered access list: the first endpoint is tried first, and later entries are fallbacks. The legacy `extract.sci_hub.fallback_mirror` value is still accepted when `endpoints` is omitted or empty.

The browser strategy is a first-class path for institutional publisher access. It uses the GRaDOS-managed publisher profile (`browser/profile`), profile locking, operational PDF browser session records under `browser/pdf-sessions`, and response/download/CDP/backfill PDF capture. Browser acquisition never writes `papers/*.md` directly: it returns PDF bytes or a challenge plus browser capture metadata, then `extract_paper_full_text` sends the PDF through the normal materialization, parser, QA, and canonical Markdown persistence pipeline. If a publisher verification page blocks PDF capture, GRaDOS records a `challenge` with manual-resume metadata in `remote_metadata`; complete the verification in the managed browser profile, then call `extract_paper_full_text` again with `resume_browser=true` to continue from the saved browser URL/profile instead of restarting at `api`.

`codex` is disabled by default. When enabled and placed in `extract.fetch_strategy.order`, it acts as a Codex Chrome extension host-agent handoff at that exact point in the order: `extract_paper_full_text` returns a Chrome download receipt, then the host agent uses the Codex `@chrome` plugin / [Codex Chrome extension](https://developers.openai.com/codex/app/chrome-extension) as the acquisition route. If the host knows the absolute PDF path, call `ingest_codex_downloaded_pdf(doi=..., downloaded_file_path=...)` or `parse_pdf_file(file_path=..., doi=..., copy_to_library=true, acquisition_via="codex")`; otherwise `ingest_codex_downloaded_pdf` scans `extract.codex_handoff.download_watch_dir`. That watch dir is scan-only: it does not configure Chrome, and an empty scan means pass the real path rather than click the publisher download button again. If Unpaywall finds an OA URL, the receipt starts from that URL instead of `https://doi.org/{doi}`.

All PDF acquisition routes that copy into the library now share one materialization boundary. The managed raw PDF for a DOI is `downloads/{safe_doi}.pdf`; publisher filenames and external local PDFs are acquisition inputs. Same-DOI same-hash candidates reuse, rename, or copy to the managed path. Same-DOI different-hash candidates return a conflict receipt that keeps both the existing canonical PDF and the candidate input. New `papers/*.md` frontmatter keeps only reading metadata and pointers such as `parsed_manifest_path` / `assets_manifest_path`; PDF paths, hashes, acquisition route, and parser/materialization provenance live in the receipt, `remote_metadata.fetch_via`, and `papers/_parsed/{safe_doi}.json`.

If `research.external_synthesis.enabled=true`, GRaDOS may use ChatGPT Pro only after it has prepared and verified an evidence pack. The default tool is `run_external_synthesis`: from a topic it prepares the evidence pack and packet, from an existing pack id it verifies and packets that pack, then it opens the dedicated GRaDOS ChatGPT profile, verifies GRaDOS-validated Pro model route (`gpt-5.5-pro`) and Pro Extended thinking route before sending, captures the response, saves it with `save_external_synthesis_result(audit=true)`, and returns the audit and canonical reread next action. `preview_external_synthesis_packet`, `prepare_external_synthesis_from_topic`, `prepare_external_synthesis_packet`, `save_external_synthesis_result`, and `audit_external_synthesis_result` remain available for dry runs, recovery, and explicit reruns. When a packet id is linked, audit accepts only anchors, DOIs, block ids, and canonical URIs from that saved packet; structured `claims[].anchor_ids` are the primary claim contract, and prose audit output is retained as a risk scan. Model and thinking choices are fixed protocol defaults, not configurable GRaDOS keys. In localized ChatGPT UIs, GRaDOS records the raw labels it confirmed. This does not remove the separate `extract.fetch_strategy.codex` PDF acquisition route.

PDF parsing priority:

```json
{
  "extract": {
    "parsing": {
      "order": ["Docling", "MinerU", "PyMuPDF"],
      "enabled": {
        "Docling": true,
        "MinerU": true,
        "PyMuPDF": true
      }
    }
  }
}
```

`MinerU` is an authenticated cloud parser. When enabled and `MINERU_API_KEY` is present, GRaDOS uploads the local PDF through MinerU's signed upload API, polls for the extraction zip, reads `full.md` as the parser output, and saves allowed images, tables, formulas, page/debug files, and source JSON into the paper's asset bundle. GRaDOS enforces `extract.security.max_mineru_zip_bytes`, `extract.security.max_mineru_full_md_bytes`, and `extract.assets.*` size/count limits before exposing assets. Use `grados auth set mineru` to store the token in the OS keychain.

### Importing Existing PDF Libraries ♻️

If you already have a local PDF library, use `grados import-pdfs` to parse and copy those files into the canonical `papers/` + `downloads/` layout:

```bash
grados import-pdfs --from /path/to/papers --recursive
grados status
```

## Development 🛠️

```bash
uv sync --all-extras
uv run grados version
uv run pytest
uv build
```

## Project Docs 📚

- [ADR.md](./ADR.md)
  - Records accepted architectural decisions and why the project chose them.
- [CHANGELOG.md](./CHANGELOG.md)
  - Records completed, user-visible changes across releases and unreleased work.
