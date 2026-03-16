# GraDOS (`phd-pro`)

**Graduate Literature and Document Operating System**

GraDOS is an MCP server that gives AI agents (Claude, Codex, etc.) the ability to search academic databases, download full-text papers through paywalls, and synthesize citation-grounded answers. It is designed for campus network environments where institutional access provides database permissions.

## Architecture

```
User Question
  |
  v
SKILL.md (5-step academic protocol)
  |
  ├─ Step 1: Query Decomposition
  ├─ Step 2: Relevance Screening (abstract / title filtering)
  ├─ Step 3: Full-Text Extraction
  │    ├─ Waterfall Fetch: TDM API → Unpaywall OA → Sci-Hub → Headless Browser
  │    ├─ Progressive Parse: LlamaParse → Marker (local GPU) → pdf-parse
  │    └─ QA Validation: length + paywall detection + structure + title match
  ├─ Step 4: Synthesis & Citation (Chinese output)
  └─ Step 5: Double-Check Protocol (anti-hallucination)
```

**MCP Tools exposed:**

| Tool | Description |
|---|---|
| `search_academic_papers` | Waterfall search across Scopus, Web of Science, Springer, Crossref, PubMed. Deduplicates by DOI. |
| `extract_paper_full_text` | 4-stage fetch + 3-stage parse + QA validation. Returns Markdown. |

## Installation

### Option A: npm (recommended)

```bash
npm install -g grados-mcp-server

# Generate config file in your working directory
grados-mcp --init

# Edit the config with your API keys
# (see mcp-config.example.json for all options)
```

### Option B: From source

```bash
git clone https://github.com/STSna/phd-pro.git
cd phd-pro/GraDos
npm install
npm run build

cp mcp-config.example.json mcp-config.json
# Edit mcp-config.json with your API keys
```

### Configure your MCP client

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "grados": {
      "command": "npx",
      "args": ["grados-mcp-server"],
      "cwd": "/path/to/directory/containing/mcp-config.json"
    }
  }
}
```

**Claude Code** (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "grados": {
      "command": "npx",
      "args": ["grados-mcp-server"],
      "cwd": "/path/to/directory/containing/mcp-config.json"
    }
  }
}
```

### Optional: Install Marker (high-quality local PDF parsing)

Marker uses deep learning models to convert PDFs to Markdown with much better accuracy than the built-in parser. Requires Python 3.12.

```powershell
cd GraDos
.\install-marker.ps1              # Auto-detect CPU/GPU
.\install-marker.ps1 -Torch cuda  # Force GPU (CUDA)
.\install-marker.ps1 -Torch cpu   # Force CPU
```

## Configuration

All configuration lives in a single file: `mcp-config.json`. Run `grados-mcp --init` to generate one from the template.

### API Keys

| Key | Source | Required | Free |
|---|---|---|---|
| `ELSEVIER_API_KEY` | [Elsevier Developer Portal](https://dev.elsevier.com/) | No | Yes (institutional) |
| `WOS_API_KEY` | [Clarivate Developer Portal](https://developer.clarivate.com/) | No | Yes (starter) |
| `SPRINGER_meta_API_KEY` | [Springer Nature API](https://dev.springernature.com/) | No | Yes |
| `SPRINGER_OA_API_KEY` | Same as above (OpenAccess endpoint) | No | Yes |
| `LLAMAPARSE_API_KEY` | [LlamaCloud](https://cloud.llamaindex.ai/) | No | Free tier |

Crossref and PubMed require no API keys. Sci-Hub and Unpaywall require no keys either.

**No API keys are strictly required** -- GraDOS will use whichever services are configured and skip the rest. At minimum, Crossref + PubMed + Sci-Hub work with zero configuration.

### Search Priority

The `search.order` array controls which databases are queried first. GraDOS searches in order and stops as soon as it has enough unique results:

```json
{
  "search": {
    "order": ["Elsevier", "Springer", "WebOfScience", "Crossref", "PubMed"]
  }
}
```

### Extraction Waterfall

The `extract.fetchStrategy.order` controls the full-text extraction priority:

```json
{
  "extract": {
    "fetchStrategy": {
      "order": ["TDM", "OA", "SciHub", "Headless"]
    }
  }
}
```

## SKILL.md

The `skills/GraDOS/SKILL.md` file is a structured prompt that teaches the AI agent the 5-step research protocol. Copy it into your agent's skill/prompt directory to enable the full workflow.

## License

MIT
