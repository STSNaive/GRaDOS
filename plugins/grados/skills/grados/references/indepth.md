# GRaDOS Indepth Research Workflow

`indepth` is an opt-in workflow for turning a metadata search into a recoverable full-text research pass. It is disabled by default in `research.indepth.enabled`.

## Runtime Surface

- Config: `research.indepth.enabled` defaults to `false`.
- CLI: `grados search "query" --indepth` overrides the default for one request.
- MCP: `search_academic_papers(indepth=true)` does the same for one tool call.
- Candidate count: `indepth` uses the same `limit` as metadata search. There is no separate hidden top-N cap; control breadth with the search `limit` and, for large reviews, follow-up batches.
- Scope: `indepth` only processes the returned search candidates. It does not fetch papers outside the current search page.

## Checkpoint Folder

Each indepth run writes one checkpoint folder under `GRADOS_HOME/research_checkpoints/`:

`{started_at}_{slug}_{short_hash}/`

The folder contains:

- `checkpoint.json`: machine-readable workflow state.
- `checkpoint.md`: rendered human-readable recovery note.

Each indepth run also creates or updates a `research_run_manifest` artifact in `database/research.sqlite3`. The manifest records the run id, search/candidate/extract/summary/checkpoint events, linked artifact index, and a redacted config/provenance snapshot. It is a directory page only, not citation evidence.

`research_checkpoints/` and `paper_summaries/` are ignored by git when `GRADOS_HOME` points at a project checkout. They may contain local research state, acquisition status, and derived notes.

## `research_checkpoint` Schema

`research_checkpoint` is conversation-level workflow state. It supports multiple papers and is not a paper folder.

Required shape:

```json
{
  "schema_version": 1,
  "research_run_id": "run_xxxxx",
  "conversation_id": "research_xxxxx",
  "user_question": "original user question",
  "search_queries": ["english query"],
  "papers": [
    {
      "doi": "10.xxxx/example",
      "safe_doi": "10_xxxx_example__abc123def456",
      "paper_id": "10_xxxx_example__abc123def456",
      "title": "Paper title",
      "screening_status": "candidate",
      "fetch_status": "fulltext",
      "paper_uri": "grados://papers/10_xxxx_example__abc123def456",
      "paper_summary_id": "summary_10_xxxx_example__abc123def456_abcdef",
      "index_status": "indexed",
      "failure_reason": ""
    }
  ],
  "current_findings": [],
  "evidence_anchors": [],
  "open_questions": [],
  "next_actions": [],
  "warnings": [],
  "started_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

## `paper_summary` Schema And Invalidation

`paper_summary` is a paper-level, query-independent, reusable derived artifact. It is not citation evidence.

Required shape:

```json
{
  "schema_version": 1,
  "summary_id": "summary_10_xxxx_example__abc123def456_abcdef",
  "doi": "10.xxxx/example",
  "safe_doi": "10_xxxx_example__abc123def456",
  "paper_id": "10_xxxx_example__abc123def456",
  "paper_uri": "grados://papers/10_xxxx_example__abc123def456",
  "content_hash": "sha256-of-canonical-markdown",
  "summary_prompt_version": "paper-summary-extractive-v1",
  "summary_model": "grados-extractive-v1",
  "generated_at": "ISO-8601",
  "methods": [],
  "key_findings": [],
  "limitations": [],
  "quality_flags": [],
  "evidence_anchors": []
}
```

Invalidation rules:

- `missing`: no summary exists for a saved paper.
- `stale`: `content_hash` no longer matches `papers/*.md`, or `summary_prompt_version` changed.
- `valid`: the summary exists and matches the canonical paper content and prompt version.
- `not_applicable`: no canonical full text exists yet.

Do not store long-lived `topic_note` fields in `paper_summary`. Current task-specific interpretation belongs in `research_checkpoint.current_findings` and `research_checkpoint.evidence_anchors`.

## Status Linkage

Even when `indepth` is off, `search_academic_papers` exposes local state for each returned DOI:

- `already_saved`
- `fetch_status`
- `has_fulltext`
- `paper_uri`
- `paper_summary_status`
- `paper_id`

`extract_paper_full_text` receipts expose `paper_id`, `safe_doi`, `fetch_status`, and `has_fulltext`.

## Failure Semantics

The fetch/checkpoint status vocabulary includes:

- `metadata_only`
- `failed`
- `partial_success`
- `summary_failed`
- `fulltext`

## Evidence Discipline

Search snippets, extraction receipts, paper summaries, and research checkpoints are navigation and recovery material only. Final answers, citations, audits, and comparisons must reread canonical `papers/*.md` content with `read_saved_paper`.
