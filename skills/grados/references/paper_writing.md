# GRaDOS Evidence-Grounded Writing Workflow

This is a GRaDOS reference profile. It extends the `grados` skill for tasks where
the user asks an agent to design experiments, write research reports, draft
literature reviews, or prepare manuscripts while requiring real paper evidence.

Use this reference as a workflow router, not as a separate skill. GRaDOS remains
the deterministic evidence substrate; the host agent plans, writes, revises, and
decides scope.

## When To Use

Use this workflow when the user asks for any of these:

- an experiment or simulation protocol grounded in literature;
- a literature review, related-work section, or state-of-the-art survey;
- an experiment report that needs paper-backed framing or method comparison;
- a full manuscript, paper draft, thesis-style chapter, or proposal section;
- any writing task where factual claims must be traceable to real papers.

Do not use this workflow for ordinary short research answers unless the user asks
for a durable writing artifact, claim ledger, or section-by-section audit.

## Design Sources And Adaptation

Borrow workflow patterns from mature research-writing systems, but keep GRaDOS
evidence rules stricter:

- ARIS-style Markdown workflows: use plain reference files, artifacts, external
  critique, and assurance gates instead of a monolithic runtime.
- STORM-style pre-writing: collect references, ask perspective-guided questions,
  and build an outline before drafting.
- PaperQA-style scientific grounding: answer from local/full-text documents with
  citations, but add manuscript workflow and GRaDOS canonical rereads.
- GPT Researcher-style planning: separate planner, question generation,
  source gathering, and final report aggregation.
- AutoResearchClaw-style gates: use human-reviewable checkpoints, citation
  verification, LaTeX/BibTeX helpers, and provenance reports, but avoid full
  autonomous science runtime in GRaDOS core.
- AI-Scientist-style experiment/writeup separation: keep ideation, experiment
  execution, analysis, writeup, and review distinct; do not assume ML benchmark
  workflows fit mechanics or metamaterials papers.
- Agent Skills guidance: keep `SKILL.md` concise, reference supporting files only
  when needed, and use deterministic scripts/tools for validation.

These projects are workflow inspiration only. They are not evidence sources for
the user's paper.

## Profile Router

Load exactly the profiles needed for the request:

| User request | Writing profile |
| --- | --- |
| "design an experiment", "experimental workflow", "simulation protocol" | [writing_profiles/experimental_protocol.md](writing_profiles/experimental_protocol.md) |
| "write a review", "related work", "state of the art" | [writing_profiles/literature_review.md](writing_profiles/literature_review.md) |
| "write my experiment/simulation report", "analyze results into a paper section" | [writing_profiles/experiment_report.md](writing_profiles/experiment_report.md) |
| "write a paper", "draft a manuscript", "prepare submission-style paper" | [writing_profiles/manuscript.md](writing_profiles/manuscript.md) |

When the topic concerns mechanics, elastic/acoustic/mechanical metamaterials,
phononic crystals, wave band gaps, vibration control, or similar systems, also
load [domain_profiles/mechanics_elastic_metamaterials.md](domain_profiles/mechanics_elastic_metamaterials.md).

If multiple profiles apply, start with the lightest profile that satisfies the
user's request. For example, an experiment design request should not silently
expand into a full manuscript workflow.

## Common Workflow

### 1. Intake

Record the task shape before searching:

- `profile`: `experimental_protocol`, `literature_review`, `experiment_report`,
  or `manuscript`;
- research question or writing objective;
- domain and mechanism keywords;
- expected deliverable and length;
- whether user has data, PDFs, figures, code, or draft text;
- target venue/style only when relevant;
- explicit constraints, missing data, and stop conditions.

If the user wants writing but has not specified a concrete research question,
create a short clarification plan and then proceed with a conservative initial
search. Do not invent the user's experimental results, figures, or data.

### 2. Search And Full-Text Acquisition

Use normal GRaDOS evidence routes:

1. Search local saved papers for reuse/context.
2. Search remote academic databases for current coverage.
3. Extract or import relevant full text.
4. Use `get_saved_paper_structure` and `read_saved_paper` for canonical reading.
5. Use evidence grids, comparisons, and draft audits as helpers only.

Search and helper snippets are navigation material. Final factual claims must be
grounded in canonical paragraph windows read through GRaDOS.

### 3. Build A Claim Matrix

For durable writing, create a `claim_matrix` artifact. It is a claim ledger, not
the draft itself.

Recommended artifact save route:

```text
grados:save_research_artifact(kind="claim_matrix", content={...}, metadata={"research_run_id": "..."})
```

Current note: `save_research_artifact` only stores the artifact. It does not
validate the schema or verify canonical pointers. Until `validate_claim_matrix`
and `prepare_claim_evidence_pack` are live MCP tools, the host agent must
manually reread the saved canonical windows and use `prepare_evidence_pack`,
`verify_evidence_pack`, and `audit_answer_against_pack` for the actual gate.

### 4. Claim Matrix V1 Contract

Use this shape unless the user or codebase provides a newer schema:

```json
{
  "schema_name": "claim_matrix",
  "schema_version": 1,
  "research_run_id": "",
  "writing_profile": "experimental_protocol|literature_review|experiment_report|manuscript",
  "domain_profile": "mechanics_elastic_metamaterials",
  "sections": [
    {
      "section_id": "related_work",
      "title": "Related Work",
      "scope": "What this section is allowed to claim.",
      "acceptance_criteria": ["All factual claims have canonical support."]
    }
  ],
  "claims": [
    {
      "claim_id": "c001",
      "section_id": "related_work",
      "claim_text": "One factual claim, not a whole paragraph.",
      "claim_role": "background|method|result|comparison|limitation|interpretation",
      "status": "proposed|drafted|audited|accepted|needs_revision|retired",
      "support": [
        {
          "support_relation": "supports|qualifies|contradicts|method_context|limitation",
          "pack_id": "",
          "anchor_id": "",
          "block_pointer": {
            "safe_doi": "",
            "doi": "",
            "canonical_uri": "grados://papers/...#block=...",
            "block_id": "paragraph-000001-...",
            "text_sha256": "",
            "doc_sha256": "",
            "source_paragraph_index": 0,
            "heading_path": ["Results"]
          },
          "read_selector": {
            "safe_doi": "",
            "start_paragraph": 0,
            "max_paragraphs": 3
          },
          "support_note": "Why this window supports or limits the claim.",
          "limitation": "Known scope or caveat."
        }
      ],
      "audit": {
        "verdict": "verified|minor_distortion|major_distortion|unverifiable|unverifiable_access",
        "issue_type": "",
        "revision_action": "keep|revise_wording_or_add_locator|rewrite_or_replace_citation|search_and_prepare_evidence_pack|delete_claim",
        "audit_artifact_id": "",
        "audited_at": ""
      },
      "revision_version": 1,
      "cross_chapter_refs": [],
      "external_advisory_ids": []
    }
  ],
  "open_questions": [],
  "warnings": []
}
```

Store `block_id`, `text_sha256`, and `doc_sha256` when available. Paragraph
numbers alone are not stable enough across parsing or source updates.

### 5. Section Gate

For each section:

1. Select a claim slice from the claim matrix.
2. Reread or materialize only the relevant canonical evidence.
3. Draft the section using only the selected evidence and user-provided data.
4. Run pack-scoped audit when a pack exists; otherwise run first-pass draft audit
   and then reread accepted anchors.
5. Resolve failures by one of three actions: gather more evidence, revise the
   claim, or delete/retire the claim.
6. Save section artifacts if the task is long or handoff-prone.

Recommended section artifacts:

- `section_claim_slice`;
- `section_evidence_pack`;
- `section_draft`;
- `section_audit_report`;
- `section_revision_log`.

### 6. Submission Or Delivery Gate

Before marking a deliverable ready, check:

Hard fail:

- cited factual claim has no claim id;
- claim has no canonical evidence;
- evidence pack is stale or invalid;
- any required claim remains `major_distortion`, `unverifiable`, or
  `unverifiable_access`;
- citation marker conflicts with the supporting source;
- external reviewer prose is treated as evidence;
- figure/table claim lacks provenance.

Warning or limitation:

- `minor_distortion` is retained with explicit limitation;
- background or motivation citation lacks ideal metadata but is not central;
- LaTeX/BibTeX formatting warning does not affect factual support;
- missing figure/table is explicitly marked as placeholder.

## Planned Deterministic Helpers

The following helper contracts are the intended P0 implementation. Do not invoke
them unless they are present in the live MCP tool list.

### `validate_claim_matrix`

Purpose: validate schema and canonical evidence pointers without writing prose.

Minimum checks:

- `claim_id` uniqueness;
- section references exist;
- allowed `claim_role`, `status`, verdict, and `revision_action`;
- support entries include DOI or `safe_doi`, `canonical_uri`, `block_id`,
  `text_sha256`, and `doc_sha256` when available;
- current `papers/*.md` can resolve each pointer;
- block hash and document hash are current-valid or explicitly reported stale;
- external advisory artifacts are not marked as evidence.

### `prepare_claim_evidence_pack`

Purpose: materialize a claim slice as a normal GRaDOS `evidence_pack` so existing
pack verification and pack audit tools can be reused.

Rules:

- read from canonical `papers/*.md`;
- preserve block ids and hashes;
- do not create a second evidence truth layer;
- return ordinary pack ids usable by `verify_evidence_pack` and
  `audit_answer_against_pack`.

## External Reviewer Boundary

ChatGPT Pro or another reviewer may be used only as an advisory reviewer when
the GRaDOS external synthesis gate is enabled. Send only current-valid evidence
pack material and the relevant claim slice. Request structured outputs with
`claims[].anchor_ids`, gaps, caveats, and revision suggestions.

The reviewer must not add papers, facts, DOIs, citations, figures, or numerical
results outside the provided evidence packet. Save and audit the advisory result
before using it, then reread accepted canonical windows before final citation.

## Output Rule

Every final deliverable should include a compact evidence status section:

- what GRaDOS searched or read;
- which artifacts/packs/audits were produced;
- which claims are verified, limited, or unresolved;
- what remains placeholder or user-supplied data;
- where the next agent should resume.
