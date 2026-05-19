# GRaDOS Manuscript Profile

Use this profile when the user asks for a paper, manuscript, thesis-style
chapter, proposal paper section, or submission-style draft.

The manuscript profile is the heaviest writing workflow. Use a lighter profile
when the user only needs an experiment protocol, review section, or report.

## Deliverable

Default outputs for a full manuscript task:

- paper plan and section registry;
- claim matrix;
- section evidence packs;
- section drafts;
- section audit reports;
- revision log;
- citation coverage report;
- figure/table manifest;
- final evidence status report.

Only produce LaTeX, BibTeX, or submission-ready formatting when the user asks
for it or when the target venue/style makes it necessary.

## Workflow

1. Intake:
   define contribution type, target audience, available user data, target venue
   if any, and whether the manuscript is review, method, experiment, simulation,
   or theory oriented.
2. Plan:
   create a section registry with scope, allowed claim roles, expected evidence,
   and acceptance criteria for each section.
3. Evidence:
   run GRaDOS search, extraction, canonical reading, evidence grid, and claim
   matrix construction before drafting factual sections.
4. Draft by section:
   write only from the current section's claim slice, evidence pack, and
   user-provided data. Do not let one section borrow unaudited claims from
   another section.
5. Audit by section:
   resolve failed claims by gathering more evidence, revising wording, or
   deleting/retiring the claim.
6. Assemble:
   check cross-section consistency, repeated claims, terminology, citation
   coverage, figure/table provenance, and unresolved placeholders.
7. Deliver:
   include an evidence status report. Mark the manuscript as a draft unless the
   submission gate passes.

## Manuscript Gates

Hard fail:

- factual citation claim has no claim id;
- claim lacks canonical evidence or current-valid evidence pack support;
- any required claim remains `major_distortion`, `unverifiable`, or
  `unverifiable_access`;
- introduction, novelty, method, result, or limitation claims rely on reviewer
  prose, helper snippets, abstracts, or model memory;
- figures/tables lack provenance or are generated as if real data;
- results section contains values not supplied by the user or read from a
  verified data source.

Warning:

- `minor_distortion` is retained with explicit limitation;
- the manuscript uses placeholders for missing user data, figures, or venue
  details;
- BibTeX/LaTeX formatting is incomplete but factual support is intact.

## Section Registry Template

```json
{
  "section_id": "introduction",
  "title": "Introduction",
  "scope": "Problem framing and motivation only.",
  "allowed_claim_roles": ["background", "limitation", "comparison"],
  "required_artifacts": ["claim_matrix_slice", "evidence_pack", "audit_report"],
  "acceptance_criteria": [
    "Every factual claim has a claim id.",
    "Every citation claim has canonical evidence.",
    "No novelty claim exceeds the reviewed evidence."
  ]
}
```

## Output Rule

For long manuscripts, do not try to finish all sections in one pass if evidence
or user data is missing. Save the current artifacts, report the next section and
blocking evidence gaps, then resume from the manifest or claim matrix later.
